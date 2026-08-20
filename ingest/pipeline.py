"""The upload pipeline: one file in, one catalogued document out.

    python -m ingest.pipeline --input documents/test
    python -m ingest.pipeline --input techsheet.pdf --dry-run   # no model call

    document -> extract (structural, free) -> interpret (model) -> catalogue

Run on every upload. A file is keyed by content hash, so re-running is a no-op
until the file changes; --force reconverts.

Failure is per document and quiet. One bad file is recorded with its error and
the run continues, because ingesting nineteen of twenty uploads is a good
outcome and taking down the batch for the twentieth is not.

Spreadsheet sheets the model calls operating logs are handed to
logs/convert.py, which writes a real parquet per sheet and computes its facts
with pandas. That is deliberate reuse rather than a second implementation: the
question "how many days did chlorine residual sit below the limit in March" has
to be answered by reading a dataframe, and no description of a sheet, however
good, can answer it.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import plant as plant_paths  # noqa: E402
from ingest import extract as extract_mod  # noqa: E402
from ingest.extract import extract, file_hash, file_type  # noqa: E402
from ingest.interpret import interpret  # noqa: E402
from ingest.schema import Component, DocEntry, Statement  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:  # optional - the shell environment works just as well
    def load_dotenv(*_args, **_kwargs):
        return False

DEFAULT_MODEL = "claude-opus-5"

SUPPORTED = ("*.pdf", "*.docx", "*.xlsx", "*.xlsm", "*.csv")


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

def read_manifest(plant=None):
    """Every ingested document; the newest write per file_hash wins."""
    manifest = plant_paths.documents_manifest(plant)
    if not manifest.exists():
        return []
    entries = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = DocEntry.model_validate_json(line)
        except Exception:  # noqa: BLE001 - one bad line must not blind the rest
            continue
        entries[entry.file_hash] = entry
    return list(entries.values())


def append_manifest(entry, plant=None):
    manifest = plant_paths.documents_manifest(plant)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write(entry.model_dump_json() + "\n")


def catalogue(include_failed=False, plant=None):
    """Every ingested document, described, for dropping into context."""
    entries = sorted(read_manifest(plant), key=lambda e: e.file_path)
    blocks = [
        e.catalogue_entry() for e in entries
        if e.status == "ingested" or include_failed
    ]
    if not blocks:
        return "No documents have been ingested yet."
    return "\n\n---\n\n".join(blocks)


def dump_catalogue(path=None, plant=None):
    """Write the catalogue out so a human can read exactly what the model sees.

    The catalogue is assembled in memory on every question and never stored,
    which makes it the one part of the pipeline you cannot inspect by opening a
    file. This writes it down. Failed documents are included, because a
    document missing from context is the thing you most want to notice and the
    thing least likely to announce itself.

    Debug output, so it goes to scratch/ rather than into the plant directory.
    """
    text = catalogue(include_failed=True, plant=plant)
    out = Path(path) if path else plant_paths.scratch_dir() / "catalogue.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out, text


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------

def attach_logs(path, components, client, model, log, plant=None):
    """Convert sheets the model called logs, and point components at them.

    logs/convert.py works a workbook at a time, so one call covers every log
    sheet in the file. Sheets it fails to convert keep their description and
    lose only the dataframe, which is the same quiet per-sheet failure that
    module already makes on its own.
    """
    from logs.convert import convert_workbook

    log("    log sheets found; converting with logs/convert.py")
    entries = convert_workbook(path, client, model=model,
                               log=lambda m: log("    " + m), plant=plant)
    by_sheet = {e.sheet_name: e for e in entries}

    for component in components:
        if component.kind != "log":
            continue
        sheet = component.title
        entry = by_sheet.get(sheet)
        if entry is None or entry.status != "converted":
            component.status = "failed"
            component.error = (
                entry.error if entry is not None else "sheet not converted"
            )
            continue
        component.payload_path = entry.parquet_path
        # Facts pandas computed, which the model was forbidden from guessing.
        if entry.coverage_start and entry.coverage_end:
            component.locator += (
                f" ({entry.coverage_start:%Y-%m-%d} to {entry.coverage_end:%Y-%m-%d}, "
                f"{entry.row_count} rows)"
            )


def ingest_file(path, client, model=DEFAULT_MODEL, log=print, plant=None):
    """Extract, interpret, and return the DocEntry. Does not write the manifest."""
    path = Path(path)
    kind = file_type(path)
    digest = file_hash(path)
    common = dict(
        file_path=str(path),
        file_hash=digest,
        file_type=kind,
        generated_at=datetime.now(timezone.utc),
    )

    try:
        parts, meta = extract(path, plant)
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal to the run
        return DocEntry(status="failed", error=f"extract: {exc}", **common)

    log(f"    {len(parts)} parts "
        f"({sum(1 for p in parts if p.image)} figures, "
        f"{meta['source_chars']:,} chars"
        f"{', from cached parse' if meta['from_cache'] else ''})")

    if not parts:
        return DocEntry(status="failed", error="no parts extracted", **common)

    # Oversized documents are interpreted coarsely rather than partially. Every
    # page still gets covered; the descriptions just span more of them, and the
    # figures are recorded with their locations but not described.
    oversize = meta["source_chars"] > extract_mod.COARSE_CHARS
    undescribed = []
    if oversize:
        figures = [p for p in parts if p.kind == "image"]
        parts = extract_mod.coarsen([p for p in parts if p.kind != "image"])
        undescribed = figures
        log(f"    over {extract_mod.COARSE_CHARS:,} chars: coarsened to "
            f"{len(parts)} parts, {len(figures)} figures recorded but not described")

    try:
        head, described, usage = interpret(str(path), parts, kind, client, model, log)
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal to the run
        return DocEntry(status="failed", error=f"interpret: {exc}", **common)

    mode = head.get("mode", "interpreted")
    components = []
    for part in parts:
        entry = described.get(part.component_id, {})
        components.append(Component(
            component_id=part.component_id,
            kind=entry.get("kind", part.kind),
            locator=part.locator,
            page_start=part.page_start,
            page_end=part.page_end,
            title=part.title,
            description=entry.get("description", ""),
            indexed=entry.get("indexed", True),
            statements=[Statement(**st) for st in entry.get("statements", [])],
            # Verbatim carries the words themselves; interpreted carries
            # nothing and is read from the source file at fetch time.
            content=part.text if (mode == "verbatim" and part.text) else None,
            status="ok" if entry else "failed",
            error=None if entry else "not described by the model",
        ))

    for part in undescribed:
        # Not a failure: a decision. They keep their locator so a question
        # about a figure can still be routed to a page and fetched, but they
        # carry no description and stay out of context.
        components.append(Component(
            component_id=part.component_id,
            kind="image",
            locator=part.locator,
            page_start=part.page_start,
            page_end=part.page_end,
            title=part.title,
            description="",
            indexed=False,
            status="ok",
        ))

    if any(c.kind == "log" for c in components):
        try:
            attach_logs(path, components, client, model, log, plant)
        except Exception as exc:  # noqa: BLE001 - the descriptions still stand
            log(f"    log conversion failed: {exc}")

    if undescribed:
        head["notes"] = (
            f"Document is over the {extract_mod.COARSE_CHARS:,}-character "
            f"threshold: sections were merged into chapter-sized blocks and "
            f"{len(undescribed)} figures were recorded with their page numbers "
            f"but not described. " + head.get("notes", "")
        )

    covered = sum(len(p.text) + len(p.title) for p in parts)
    entry = DocEntry(
        mode=mode,
        summary=head.get("summary", ""),
        notes=head.get("notes", ""),
        components=components,
        text_coverage=(
            round(min(covered / meta["source_chars"], 1.0), 3)
            if meta["source_chars"] else None
        ),
        **common,
    )
    log(f"    {usage[0]:,} in / {usage[1]:,} out, mode={mode}, "
        f"coverage={entry.text_coverage}")
    return entry


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def find_files(src):
    src = Path(src)
    if src.is_file():
        return [src]
    files = []
    for pattern in SUPPORTED:
        files += list(src.glob(pattern))
    return sorted(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    # Not required: --catalogue reads the manifest and ingests nothing, so
    # demanding an input directory to dump the context would be theatre.
    parser.add_argument("--input", help="file or directory")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--plant", default=plant_paths.DEFAULT_PLANT,
                        help="which plant's data directory to write into")
    parser.add_argument("--force", action="store_true",
                        help="re-ingest files already in the manifest")
    parser.add_argument("--dry-run", action="store_true",
                        help="extract and report the decomposition, call nothing")
    parser.add_argument("--catalogue", nargs="?", const="", metavar="PATH",
                        help="write the context the agent sees to a file and "
                             "exit (default: scratch/catalogue.txt)")
    args = parser.parse_args(argv)

    if args.catalogue is not None:
        out, text = dump_catalogue(args.catalogue or None, args.plant)
        entries = read_manifest(args.plant)
        print(f"{out} ({len(text):,} chars, ~{len(text) // 4:,} tokens, "
              f"{len(entries)} documents)")
        return 0

    if not args.input:
        parser.error("--input is required unless --catalogue is given")

    files = find_files(args.input)
    if not files:
        raise SystemExit(f"no supported files under {args.input}")

    if args.dry_run:
        # The decomposition is the thing to check first: if the parts are
        # wrong, no amount of prompting fixes the descriptions, and you would
        # have paid to find that out.
        for path in files:
            print(path.name)
            try:
                parts, meta = extract(path, args.plant)
            except Exception as exc:  # noqa: BLE001
                print(f"  EXTRACT FAILED: {exc}")
                continue
            kinds = {}
            for part in parts:
                kinds[part.kind] = kinds.get(part.kind, 0) + 1
            figures = sum(1 for p in parts if p.image)
            print(f"  {len(parts)} parts {kinds}, {figures} figures cropped, "
                  f"{meta['source_chars']:,} chars")
            for part in parts[:8]:
                print(f"    {part.component_id:<14} {part.locator:<18} "
                      f"{part.title[:44]}")
            if len(parts) > 8:
                print(f"    ... and {len(parts) - 8} more")
        return 0

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")

    import anthropic

    client = anthropic.Anthropic()
    # Only a SUCCESSFUL ingest counts as done. A failed one is recorded so the
    # failure is visible, not so it is remembered as finished - and its causes
    # are usually transient (an API outage, an exhausted credit balance, one
    # malformed file since replaced). Counting failures here would make a run
    # that died partway unresumable except with --force, which would re-pay for
    # every document that already worked.
    done = {
        e.file_hash for e in read_manifest(args.plant) if e.status == "ingested"
    }

    for path in files:
        if not args.force and file_hash(path) in done:
            print(f"{path.name}: already ingested (use --force)")
            continue
        print(path.name)
        entry = ingest_file(path, client, args.model, plant=args.plant)
        append_manifest(entry, args.plant)
        if entry.status != "ingested":
            print(f"  FAILED: {entry.error}")
            continue
        ok = sum(1 for c in entry.components if c.status == "ok")
        dropped = [c for c in entry.components if c.status == "ok" and not c.indexed]
        print(f"  {ok}/{len(entry.components)} components described"
              + (f", {len(dropped)} not indexed" if dropped else ""))
        for component in dropped:
            # Printed so a model dropping something it should not is visible
            # here, rather than only as an answer that never comes.
            print(f"    dropped {component.locator} ({component.kind}): "
                  f"{component.description[:70]}")
        if entry.notes:
            print(f"  notes: {entry.notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
