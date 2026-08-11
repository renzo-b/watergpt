#!/usr/bin/env python3
"""One-off docling parsing sanity check.

Converts every supported document in --input and writes, per document,
<stem>.json (the full DoclingDocument) and <stem>.outline.txt (one line per
structural item), plus a summary.txt with versions, timings, item counts and
per-format anomaly flags.

Deliberately one flat script with no abstractions - meant to be read and then
thrown away, not grown into a pipeline. What differs by input format lives in
the EXPECTATIONS table below, not in a class hierarchy: docling returns the
same DoclingDocument for every format, so only the *expected shape* varies.

    python scripts/inspect_parse.py --input data/corpus --output data/parsed

Documents only - .xlsx/.csv are deliberately NOT handled here. Spreadsheets go
through inspect_sheets.py (openpyxl), because docling flattens them into a bare
grid and drops fill colour, merged ranges and formulas, which is exactly the
information that decides what a sheet is for. Point this script at a directory
containing spreadsheets and it will skip them with a note.

The PDF pipeline flags (--table-mode, --no-cell-matching, --no-ocr) affect PDFs
only; Word never touches the layout or table models. Run the same corpus twice
into different --output directories to compare settings.

Exits 1 if any document failed to convert. A clean exit does NOT mean the
parse is good - read summary.txt, then the flagged outlines against the
originals.
"""

import os

# Must precede any torch/docling import: dynamo reads these at import time.
# torch.compile shells out to cl.exe on Windows CPU, which isn't installed.
os.environ["TORCHDYNAMO_DISABLE"] = "1"
os.environ["TORCH_LOGS"] = "-dynamo"

import argparse  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from collections import Counter  # noqa: E402
from importlib.metadata import PackageNotFoundError, version  # noqa: E402
from pathlib import Path  # noqa: E402

import torch._dynamo  # noqa: E402

from docling.datamodel.base_models import InputFormat  # noqa: E402
from docling.datamodel.pipeline_options import (  # noqa: E402
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import (  # noqa: E402
    DocumentConverter,
    PdfFormatOption,
)
from docling_core.types.doc import ContentLayer  # noqa: E402

torch._dynamo.config.suppress_errors = True
logging.getLogger("RapidOCR").setLevel(logging.WARNING)
logging.getLogger("docling").setLevel(logging.WARNING)


OUTLINE_TEXT_CHARS = 100

# Suffix -> short format name used for expectations and reporting.
FORMATS = {
    ".pdf": "pdf",
    ".docx": "word",
    ".pptx": "powerpoint",
    ".html": "html",
    ".md": "markdown",
}

# Handled by inspect_sheets.py, not here. Listed so the run can say so out loud
# rather than silently ignoring files the user expected to be processed.
SHEET_FORMATS = {".xlsx", ".xlsm", ".csv"}

# What a healthy parse looks like per format. `min` is a floor on the count of
# a given item label; anything below it gets flagged. Absent label = no opinion.
EXPECTATIONS = {
    "pdf": {
        "min": {"text": 1, "section_header": 1},
        "note": "headings are inferred by the layout model - the fragile case",
    },
    "word": {
        "min": {"text": 1, "section_header": 1},
        "note": "headings come from Word styles; zero means something is very wrong",
    },
    "powerpoint": {
        "min": {"text": 1},
        "note": "one group per slide; reading order within a slide can be arbitrary",
    },
    "html": {"min": {"text": 1}, "note": ""},
    "markdown": {"min": {"text": 1}, "note": ""},
}


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def build_converter(args):
    """Converter with explicit PDF pipeline options.

    ACCURATE runs a heavier TableFormer variant - markedly better on merged
    cells, spanning headers and dense rulings, at real cost in time per page.
    do_cell_matching=False makes TableFormer use its own predicted text instead
    of snapping cells to the PDF's text layer; sometimes better on heavily
    ruled grids, sometimes worse. Both are worth A/B-ing on your own worst
    document rather than trusting anyone's default.

    Only InputFormat.PDF is configured: the Office and CSV backends are
    deterministic parsers with no models to tune.
    """
    opts = PdfPipelineOptions()
    opts.do_ocr = not args.no_ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = (
        TableFormerMode.ACCURATE
        if args.table_mode == "accurate"
        else TableFormerMode.FAST
    )
    opts.table_structure_options.do_cell_matching = not args.no_cell_matching
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def describe_table(item):
    """Tables have no .text, so they render blank in an outline unless we ask.

    Shows dimensions, fill count, and the distinct non-empty cell values.

    NOT export_to_markdown(): it pads every cell to its column's widest value,
    and on a wide sparse sheet the padding alone eats the whole line budget
    before any content appears. One real case here: an 18-column sheet whose
    first row is 72 characters of content padded out to 257, pushing the second
    row's only value past the truncation entirely.

    Values are deduped in reading order because a checkbox matrix is a hundred
    cells containing "X" and seeing one of them is enough. The full grid is in
    the JSON; this line exists to answer "did the words survive", not to
    reproduce the table.
    """
    data = getattr(item, "data", None)
    if data is None:
        return "<?x?>"
    seen, values = set(), []
    for cell in data.table_cells:
        value = " ".join((getattr(cell, "text", "") or "").split())
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    filled = sum(
        1 for c in data.table_cells if (getattr(c, "text", "") or "").strip()
    )
    dims = f"{data.num_rows}x{data.num_cols} {filled} filled"
    return f"<{dims}> " + " | ".join(values)


def norm_box(prov_item):
    """(page, x0, y0, x1, y1) with axes normalised, or None.

    coord_origin differs by format - TOPLEFT for spreadsheets, BOTTOMLEFT for
    PDF - so take min/max rather than trusting that t < b. For xlsx these are
    sheet cell coordinates (column, row), not points.
    """
    box = getattr(prov_item, "bbox", None)
    if box is None:
        return None
    return (
        getattr(prov_item, "page_no", 0),
        min(box.l, box.r), min(box.t, box.b),
        max(box.l, box.r), max(box.t, box.b),
    )


def find_contained(boxes):
    """[(line, outer_line)] for tables sitting wholly inside another table.

    docling can emit the same cells twice: once as part of a large region and
    again as their own small fragment. Chunking per table item then yields a
    chunk holding a bare "X" with no headers beside the real matrix. Quadratic,
    but there are tens of tables, not thousands.
    """
    found = []
    for line, box in boxes:
        if box is None:
            continue
        page, x0, y0, x1, y1 = box
        area = (x1 - x0) * (y1 - y0)
        for other_line, other in boxes:
            if other is None or other_line == line:
                continue
            opage, ox0, oy0, ox1, oy1 = other
            if opage != page or (ox1 - ox0) * (oy1 - oy0) <= area:
                continue
            if ox0 <= x0 and oy0 <= y0 and ox1 >= x1 and oy1 >= y1:
                found.append((line, other_line))
                break
    return found


def build_outline(doc):
    """Return (outline lines, label counts, furniture count, jump index).

    Built in one pass so the summary counts can never disagree with the
    outline. `jump` maps a concern -> [(outline line number, label)] so the
    summary can point at an exact line to open.
    """
    lines, labels = [], Counter()
    furniture = 0
    jump = {"table": [], "picture": [], "uncaptioned": [], "single_cell": [],
            "contained": []}
    table_boxes = []

    items = doc.iterate_items(
        # Page headers/footers live in the FURNITURE layer and are excluded by
        # default. They carry the SOP number and revision - the metadata that
        # later becomes chunk provenance - so we verify they parsed. They are
        # tagged, not merged: repeating them in chunk *text* would be noise.
        included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
    )

    for item, level in items:
        # Labels are str-Enums; .value avoids the "DocItemLabel.SECTION_HEADER"
        # spelling that f-strings produce on some Python versions.
        label = getattr(item.label, "value", str(item.label))
        labels[label] += 1

        layer = getattr(item, "content_layer", None)
        is_furniture = getattr(layer, "value", str(layer)) == "furniture"
        if is_furniture:
            furniture += 1

        prov = getattr(item, "prov", None)  # absent on some items
        page = prov[0].page_no if prov else "-"

        # Collapse whitespace BEFORE truncating: keeps every line the same
        # width and guarantees no newline is smuggled into the outline.
        text = " ".join((getattr(item, "text", "") or "").split())

        if label in ("table", "picture"):
            # A caption is a separate TextItem linked to the float. If the link
            # broke, the caption is loose body text and the figure has no
            # searchable handle - the failure most likely to pass unnoticed.
            try:
                caption = item.caption_text(doc)
            except Exception:
                caption = None
            body = describe_table(item) if label == "table" else text
            text = f"[caption: {caption or 'NONE'}] {body}".strip()

        prefix = "~" if is_furniture else " "
        lines.append(
            f"{prefix}{'  ' * level}[{label}] p{page} | {text[:OUTLINE_TEXT_CHARS]}"
        )

        if label in ("table", "picture"):
            jump[label].append((len(lines), label))
            if not caption:
                jump["uncaptioned"].append((len(lines), label))
        if label == "table":
            data = getattr(item, "data", None)
            if data is not None and data.num_rows * data.num_cols == 1:
                jump["single_cell"].append((len(lines), "1x1"))
            table_boxes.append((len(lines), norm_box(prov[0]) if prov else None))

    jump["contained"] = find_contained(table_boxes)
    return lines, labels, furniture, jump


def check(fmt, labels, furniture, jump, pages):
    """Compare a parse against EXPECTATIONS for its format. Returns warnings."""
    warnings = []
    spec = EXPECTATIONS.get(fmt, {"min": {}, "note": ""})

    for label, floor in spec["min"].items():
        if labels.get(label, 0) < floor:
            warnings.append(
                f"{label}: {labels.get(label, 0)} (expected >= {floor} for {fmt})"
            )

    # A born-digital page yields text items; a scanned page yields one picture
    # and little else. One picture per page across the document = scan.
    if fmt == "pdf" and pages >= 2 and labels.get("picture", 0) >= 0.8 * pages:
        warnings.append(
            f"picture: {labels.get('picture', 0)} across {pages} pages - "
            "looks scanned; check whether OCR actually ran"
        )

    if fmt in ("pdf", "word") and furniture == 0:
        warnings.append(
            "no furniture items - no page header/footer detected, so document "
            "number and revision will not be available as chunk metadata"
        )

    # Many tables usually means one real grid was fragmented. The --table-mode
    # advice is PDF-only on purpose: TableFormer runs in the PDF pipeline, so
    # fast vs accurate has NO effect on a spreadsheet, whose cells come
    # straight off the sheet.
    if labels.get("table", 0) >= 6:
        fix = (
            "compare --table-mode fast vs accurate"
            if fmt == "pdf"
            else "TableFormer does not run on this format, so --table-mode "
            "will not change it; the split comes from the backend's own "
            "region detection"
        )
        warnings.append(
            f"table: {labels.get('table', 0)} tables detected - if the document "
            f"has few real tables, a grid was probably fragmented; {fix}"
        )

    # A one-cell table is a value with no row or column header attached: a bare
    # "X" that cannot be retrieved or answered from. It hides in the item
    # counts because it still counts as a table.
    single = jump["single_cell"]
    if len(single) >= 3:
        where = ", ".join(f"line {n}" for n, _ in single[:5])
        more = "" if len(single) <= 5 else f" (+{len(single) - 5} more)"
        warnings.append(
            f"{len(single)} of {labels.get('table', 0)} tables are a SINGLE "
            f"CELL: {where}{more} - a value with no row or column header. "
            "Check the containment line below before assuming anything is lost."
        )

    # The usual cause of the above: the same cells emitted twice, once inside a
    # real grid and again as their own fragment. Worth separating from genuine
    # loss, because the fix is opposite - drop the fragment, keep the parent -
    # and because chunking per table item would otherwise emit a contextless
    # "X" chunk alongside the intact matrix.
    dup = jump["contained"]
    if dup:
        where = ", ".join(f"{n} in {m}" for n, m in dup[:5])
        more = "" if len(dup) <= 5 else f" (+{len(dup) - 5} more)"
        warnings.append(
            f"{len(dup)} of {labels.get('table', 0)} tables sit ENTIRELY "
            f"INSIDE another table (outline lines {where}){more}. Those cells "
            "are duplicated, not lost. Drop the contained fragments and keep "
            "the parent, or every chunker will emit both."
        )

    if jump["uncaptioned"]:
        where = ", ".join(f"line {n} ({lab})" for n, lab in jump["uncaptioned"][:5])
        more = (
            ""
            if len(jump["uncaptioned"]) <= 5
            else f" (+{len(jump['uncaptioned']) - 5} more)"
        )
        warnings.append(
            f"{len(jump['uncaptioned'])} float(s) with no caption: {where}{more}"
        )

    return warnings


def convert_one(converter, path, out_dir):
    """Convert one document, write its two artefacts, return its summary row."""
    fmt = FORMATS.get(path.suffix.lower(), "unknown")

    start = time.perf_counter()
    doc = converter.convert(path).document
    seconds = time.perf_counter() - start

    (out_dir / f"{path.stem}.json").write_text(
        doc.model_dump_json(indent=2), encoding="utf-8"
    )
    lines, labels, furniture, jump = build_outline(doc)
    (out_dir / f"{path.stem}.outline.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    pages = doc.num_pages()
    return {
        "fmt": fmt,
        "pages": pages,
        "seconds": seconds,
        "labels": labels,
        "furniture": furniture,
        "outline": f"{path.stem}.outline.txt",
        "tables": [n for n, _ in jump["table"]],
        "warnings": check(fmt, labels, furniture, jump, pages),
    }


def summary_text(args, rows):
    """Render summary.txt. rows is [(filename, result-or-error-string)]."""
    failed = sum(1 for _, r in rows if isinstance(r, str))
    flagged = sum(1 for _, r in rows if not isinstance(r, str) and r["warnings"])
    out = [
        f"docling:      {package_version('docling')}",
        f"docling-core: {package_version('docling-core')}",
        f"python:       {sys.version.split()[0]}",
        "",
        f"input:        {args.input}",
        f"output:       {args.output}",
        "",
        "PDF pipeline (Office/CSV formats unaffected):",
        f"  table mode:    {args.table_mode}",
        f"  cell matching: {not args.no_cell_matching}",
        f"  ocr:           {not args.no_ocr}",
        "",
        f"documents:    {len(rows)} seen, {failed} FAILED, {flagged} flagged",
        "",
        "Outlines mark furniture (page headers/footers) with a leading ~.",
        "Flags are heuristics, not verdicts - read the outline against the original.",
        "",
        "=" * 72,
    ]
    for name, result in rows:
        out.append("")
        out.append(name)
        if isinstance(result, str):
            out.append(f"  FAILED: {result}")
            continue
        # Commonest label first, ties alphabetical, so reruns are identical.
        counts = sorted(result["labels"].items(), key=lambda kv: (-kv[1], kv[0]))
        out.append(f"  format:    {result['fmt']}")
        out.append(f"  pages:     {result['pages']}")
        out.append(f"  seconds:   {result['seconds']:.1f}")
        out.append(f"  furniture: {result['furniture']}")
        out.append(
            f"  items:     {', '.join(f'{k}: {v}' for k, v in counts) or 'none'}"
        )
        if result["tables"]:
            shown = ", ".join(str(n) for n in result["tables"][:10])
            extra = "" if len(result["tables"]) <= 10 else " ..."
            out.append(f"  tables at: {result['outline']} lines {shown}{extra}")
        if result["warnings"]:
            for w in result["warnings"]:
                out.append(f"  WARN  {w}")
            note = EXPECTATIONS.get(result["fmt"], {}).get("note", "")
            if note:
                out.append(f"  note   {note}")
        else:
            out.append("  ok     nothing anomalous for this format")
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Convert a directory of documents with docling and dump the structure."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/corpus"),
        help="directory of documents (default: data/corpus)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/parsed"),
        help="directory for the dumps (default: data/parsed)",
    )
    parser.add_argument(
        "--table-mode",
        choices=("fast", "accurate"),
        default="accurate",
        help="TableFormer variant for PDFs (default: accurate; slower per page)",
    )
    parser.add_argument(
        "--no-cell-matching",
        action="store_true",
        help="use TableFormer's predicted text instead of snapping to the PDF text layer",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="skip OCR (much faster; safe only for born-digital PDFs)",
    )
    args = parser.parse_args()

    if not args.input.is_dir():
        raise SystemExit(f"input directory not found: {args.input}")
    docs = sorted(p for p in args.input.iterdir() if p.suffix.lower() in FORMATS)
    sheets = sorted(
        p for p in args.input.iterdir() if p.suffix.lower() in SHEET_FORMATS
    )
    if sheets:
        print(
            f"skipping {len(sheets)} spreadsheet(s) - run inspect_sheets.py on "
            f"them instead: {', '.join(p.name for p in sheets[:5])}"
            + (" ..." if len(sheets) > 5 else ""),
            flush=True,
        )
    if not docs:
        raise SystemExit(
            f"no supported documents in {args.input} "
            f"(looking for: {', '.join(sorted(FORMATS))})"
        )
    args.output.mkdir(parents=True, exist_ok=True)

    print(
        f"pdf pipeline: table_mode={args.table_mode}, "
        f"cell_matching={not args.no_cell_matching}, ocr={not args.no_ocr}",
        flush=True,
    )
    converter = build_converter(args)
    rows = []
    for i, path in enumerate(docs, 1):
        print(f"[{i}/{len(docs)}] {path.name}", flush=True)
        try:
            result = convert_one(converter, path, args.output)
            flags = f", {len(result['warnings'])} flag(s)" if result["warnings"] else ""
            print(
                f"    {result['fmt']}, {result['pages']} pages "
                f"in {result['seconds']:.1f}s{flags}",
                flush=True,
            )
        except Exception as exc:
            # One bad scan must not kill the run. Traceback to stderr so it is
            # diagnosable; one line in the summary so it is countable.
            traceback.print_exc()
            result = f"{type(exc).__name__}: {exc}"
            print(f"    FAILED: {result}", file=sys.stderr, flush=True)
        rows.append((path.name, result))

    (args.output / "summary.txt").write_text(summary_text(args, rows), encoding="utf-8")
    failed = sum(1 for _, r in rows if isinstance(r, str))
    print(
        f"\n{len(rows) - failed}/{len(rows)} converted -> {args.output / 'summary.txt'}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
