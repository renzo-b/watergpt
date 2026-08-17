#!/usr/bin/env python3
"""Answer the retrieval cases with the whole corpus in context. No retrieval.

    python scripts/eval_fullcontext.py --plant-id demo

The premise this tests: for the common plant - a small SOP set, no O&M manual -
the entire document collection fits in context, so chunking, embedding and
top-k retrieval are machinery solving a problem that plant does not have. If
that holds, retrieval is not the default path; it is the exception for the one
plant with a 660-page manual.

Scored with the SAME matcher as scripts/eval_retrieval.py, deliberately. The
question is whether full context beats the two RAG configs on identical cases,
and that comparison is only honest if "found the right document and page" means
exactly the same thing on both sides.

Two differences from the retrieval harness, both inherent rather than chosen:

  There is no top-k, so there is no recall@k. A case either resolves to the
  right source or it does not. Reported as a pass rate, not a curve.

  The answer text is checkable here. Retrieval could only ask "did the right
  chunk come back"; with the model actually answering, `expects.contains` can
  be tested too - and the two can disagree. Citing the right page while
  fumbling the number is a different failure from citing the wrong page, and
  they are reported in separate columns.

The corpus is sent as a cached system prompt, so it is paid for once per run
rather than once per case. Cost is printed at the end, because whether this
approach is affordable is half the question being asked.
"""

import argparse
import json
import sys
import time
from collections import namedtuple
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))

from eval_retrieval import alternatives, load_cases, matches  # noqa: E402

MODEL = "claude-opus-5"

# Claude Opus 5 list price, dollars per million tokens. Cache writes cost 1.25x
# input and reads 0.1x; both are reported because the write is a one-off and
# the read is what a real session would pay per turn.
PRICE_IN, PRICE_OUT = 5.0, 25.0

# A document larger than this is the case full context is NOT for. Excluding by
# a property of the document rather than by corpus ordering keeps the decision
# explainable: Rutledge is out because it is 635k tokens, not because it sorted
# late. This is the boundary between the two strategies, so it is a flag.
DEFAULT_MAX_DOC_TOKENS = 120_000
DEFAULT_MAX_TOTAL_TOKENS = 200_000

SYSTEM_INSTRUCTIONS = """\
You are answering questions from a water treatment plant's own documents, which \
are supplied in full below. Every document is delimited by a DOCUMENT: line. \
Pages within a document are marked [p.N]. A spreadsheet has no pages: its \
statements are grouped under a [sheet 'NAME'] marker instead.

Answer only from these documents. Report the source as the exact filename from \
the DOCUMENT: line and the location as the marker containing the text you used, \
copied verbatim - p.4 for a page, sheet 'CT' for a spreadsheet, quotes included.

If the documents do not contain the answer, set found to false and say so in the \
answer field. Do not infer a plausible answer from general water treatment \
knowledge - a confident answer that is not in these documents is the failure \
this is checking for."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {
            "type": "boolean",
            "description": "True only if the answer is present in the documents.",
        },
        "document": {
            "type": "string",
            "description": "Exact filename from the DOCUMENT: line, or empty if not found.",
        },
        "location": {
            "type": "string",
            "description": "Page as 'p.N', or sheet as \"sheet 'NAME'\", copied from the marker containing the text used. Empty if not found.",
        },
        "answer": {"type": "string"},
    },
    "required": ["found", "document", "location", "answer"],
    "additionalProperties": False,
}

# The matcher reads only these two fields.
Source = namedtuple("Source", "document location")


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def document_text(doc):
    """Page-delimited text for one parsed document.

    Page markers are what make a citation checkable. Without them the model can
    name a document but not a page, and every case would collapse to the
    document-only column.
    """
    from docling_core.types.doc.document import TableItem

    pages = {}
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None)
        page = prov[0].page_no if prov else 0
        text = (
            item.export_to_markdown(doc=doc)
            if isinstance(item, TableItem)
            else (getattr(item, "text", "") or "")
        )
        if text.strip():
            pages.setdefault(page, []).append(text.strip())

    from rag import clean

    parts = []
    for page in sorted(pages):
        parts.append(f"[p.{page}]")
        parts.append(clean.normalise("\n".join(pages[page])))
    return "\n".join(parts)


def interpreted_text(interp_dir, workbooks, log):
    """Durable spreadsheet statements, same source the RAG path uses."""
    import build_index

    blocks, unmatched = [], []
    for json_path in sorted(Path(interp_dir).glob("*.json")):
        path, sheet = build_index.resolve_sheet(workbooks, json_path.stem)
        if path is None:
            unmatched.append(json_path.stem)
            continue
        result = json.loads(json_path.read_text(encoding="utf-8"))
        lines = [
            f"- {item['statement']}  ({build_index.statement_location(sheet, item.get('source_cells'))})"
            for item in result.get("durable", [])
        ]
        if lines:
            blocks.append(f"DOCUMENT: {path.name}\n[sheet '{sheet}']\n" + "\n".join(lines))
    if unmatched:
        # A sheet case that scores zero because its workbook is not in --input
        # looks exactly like a sheet case the model got wrong. build_index
        # reports this condition; so does this.
        log(f"  {len(unmatched)} interpretation file(s) belong to workbooks "
            f"outside --input and were skipped: {', '.join(unmatched[:3])}"
            + (" ..." if len(unmatched) > 3 else ""))
    return blocks


def count_tokens(client, text):
    return client.messages.count_tokens(
        model=MODEL, messages=[{"role": "user", "content": text or "."}]
    ).input_tokens


def assemble(client, input_dir, parsed_dir, interp_dir, max_doc, max_total, log):
    """Build the context payload. Returns (text, included, excluded)."""
    from docling_core.types.doc import DoclingDocument

    blocks, included, excluded = [], [], []
    for pdf in sorted(Path(input_dir).iterdir()):
        if pdf.suffix.lower() not in {".pdf", ".docx"}:
            continue
        cached = Path(parsed_dir) / f"{pdf.stem}.json"
        if not cached.is_file():
            excluded.append((pdf.name, None, "not parsed - run inspect_parse.py"))
            continue
        doc = DoclingDocument.model_validate_json(cached.read_text(encoding="utf-8"))
        body = document_text(doc)
        tokens = count_tokens(client, body)
        if tokens > max_doc:
            excluded.append((pdf.name, tokens, f"over --max-doc-tokens ({max_doc:,})"))
            continue
        included.append((pdf.name, tokens))
        blocks.append(f"DOCUMENT: {pdf.name}\n{body}")

    workbooks = [
        p
        for p in Path(input_dir).iterdir()
        if p.suffix.lower() in {".xlsx", ".xlsm"}
    ]
    for block in interpreted_text(interp_dir, workbooks, log):
        name = block.splitlines()[0].removeprefix("DOCUMENT: ")
        included.append((name, count_tokens(client, block)))
        blocks.append(block)

    text = "\n\n".join(blocks)
    total = sum(t for _, t in included)
    log(f"\nassembled {len(included)} document(s), {total:,} tokens")
    for name, tokens in sorted(included, key=lambda x: -x[1])[:5]:
        log(f"    {tokens:8,}  {name[:66]}")
    if len(included) > 5:
        log(f"    ... {len(included) - 5} more")
    for name, tokens, why in excluded:
        size = f"{tokens:,} tokens" if tokens else "?"
        log(f"  EXCLUDED {name[:52]}  ({size}) - {why}")

    if total > max_total:
        raise SystemExit(
            f"\nassembled corpus is {total:,} tokens, over --max-tokens "
            f"({max_total:,}). This is the signal that this plant needs "
            "retrieval rather than full context - lower --max-doc-tokens to "
            "exclude the outsized documents, or raise --max-tokens deliberately."
        )
    return text, included, excluded


# --------------------------------------------------------------------------


def ask(client, corpus, question, effort, max_tokens):
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
        },
        system=[
            {"type": "text", "text": SYSTEM_INSTRUCTIONS},
            # The corpus is the cached prefix: stable across every case, so it
            # is written once and read at a tenth of the price thereafter.
            {"type": "text", "text": corpus, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": question}],
    )
    if response.stop_reason == "refusal":
        return None, response.usage
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text), response.usage


def main():
    parser = argparse.ArgumentParser(
        description="Answer retrieval_set.yaml with the whole corpus in context."
    )
    parser.add_argument("--input", type=Path, default=ROOT / "documents" / "retrieval_test")
    parser.add_argument("--parsed-dir", type=Path, default=ROOT / "data" / "parsed")
    parser.add_argument("--interp-dir", type=Path, default=ROOT / "data" / "interp")
    parser.add_argument("--set", type=Path, default=ROOT / "evals" / "retrieval_set.yaml")
    parser.add_argument("--plant-id", default="demo", help="recorded in the report only")
    parser.add_argument("--max-doc-tokens", type=int, default=DEFAULT_MAX_DOC_TOKENS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOTAL_TOKENS)
    parser.add_argument("--effort", default="medium",
                        choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--answer-tokens", type=int, default=8000)
    parser.add_argument("--id", help="run a single case id")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--dump-context", type=Path, default=None,
                        help="write the assembled corpus and make no model call")
    args = parser.parse_args()

    import anthropic
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    client = anthropic.Anthropic()

    def log(line):
        print(line, flush=True)

    corpus, included, excluded = assemble(
        client, args.input, args.parsed_dir, args.interp_dir,
        args.max_doc_tokens, args.max_tokens, log,
    )

    if args.dump_context:
        args.dump_context.parent.mkdir(parents=True, exist_ok=True)
        args.dump_context.write_text(corpus, encoding="utf-8")
        log(f"\ncontext written -> {args.dump_context}   (no model call made)")
        return 0

    cases = load_cases(args.set)
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
    if not cases:
        raise SystemExit("no cases matched that filter")
    log(f"\n{args.set.name}: {len(cases)} active case(s)")
    if not any(c["expects"].get("source") is None for c in cases):
        log("  WARNING: no negative cases (expects.source: null). Nothing here "
            "measures whether the model declines to invent an answer.")

    started = time.time()
    results = []
    cache_write = cache_read = plain_in = out_tokens = 0
    for case in cases:
        parsed, usage = ask(client, corpus, case["question"].strip(),
                            args.effort, args.answer_tokens)
        cache_write += usage.cache_creation_input_tokens or 0
        cache_read += usage.cache_read_input_tokens or 0
        plain_in += usage.input_tokens or 0
        out_tokens += usage.output_tokens or 0

        source = case["expects"].get("source")
        if parsed is None:
            cited = contains_ok = False
            note = "REFUSED"
        elif source is None:
            cited = not parsed["found"]
            contains_ok = cited
            note = "negative case"
        else:
            _doc_ok, cited = matches(
                Source(parsed["document"], parsed["location"]), source
            )
            wanted = [str(s) for s in (case["expects"].get("contains") or [])]
            missing = [w for w in wanted if w.lower() not in parsed["answer"].lower()]
            contains_ok = not missing
            note = "" if not missing else f"missing {missing}"

        results.append({"case": case, "parsed": parsed, "cited": cited,
                        "contains_ok": contains_ok, "note": note})
        mark = "PASS" if cited else "FAIL"
        got = f"{parsed['document']} @ {parsed['location']}" if parsed else "-"
        log(f"  {mark}  {case['id']:24s} -> {got[:58]}  {note}")

    n = len(results)
    cited_n = sum(1 for r in results if r["cited"])
    contains_n = sum(1 for r in results if r["contains_ok"])
    log(f"\ncited correctly {cited_n}/{n}   answer content {contains_n}/{n}")

    by_type = {}
    for r in results:
        key = r["case"].get("type", "untyped")
        hit, tot = by_type.get(key, (0, 0))
        by_type[key] = (hit + (1 if r["cited"] else 0), tot + 1)
    for key, (hit, tot) in sorted(by_type.items()):
        log(f"    {key:20s} {hit}/{tot}")

    cost = (
        cache_write * 1.25 * PRICE_IN
        + cache_read * 0.1 * PRICE_IN
        + plain_in * PRICE_IN
        + out_tokens * PRICE_OUT
    ) / 1_000_000
    log(f"\ntokens: {cache_write:,} cache write, {cache_read:,} cache read, "
        f"{plain_in:,} uncached in, {out_tokens:,} out")
    log(f"list-price cost for this run: ${cost:.2f}   "
        f"(~${cache_read * 0.1 * PRICE_IN / 1_000_000 / max(n, 1):.4f}/question once warm)")

    RUNS = ROOT / "evals" / "runs"
    RUNS.mkdir(parents=True, exist_ok=True)
    out = args.out or RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-fullcontext.md"
    lines = [
        "# Full-context detail", "",
        f"- corpus: {len(included)} documents, {sum(t for _, t in included):,} tokens",
        f"- model: {MODEL}, effort {args.effort}", "",
    ]
    for name, tokens, why in excluded:
        lines.append(f"- EXCLUDED {name} ({tokens or '?'} tokens) - {why}")
    lines.append("")
    for r in results:
        case, parsed = r["case"], r["parsed"]
        lines += [f"## {case['id']} — {'PASS' if r['cited'] else 'FAIL'}", "",
                  f"> {case['question'].strip()}", ""]
        source = case["expects"].get("source")
        lines.append(
            "expected: "
            + " or ".join(
                f"`{o['document']}` @ `{o['location']}`" for o in alternatives(source)
            )
            if source
            else "expected: not in corpus (negative case)"
        )
        if parsed:
            lines += [
                f"got: `{parsed['document']}` @ `{parsed['location']}`  (found={parsed['found']})",
                "", "```", parsed["answer"], "```", "",
            ]
            if r["note"]:
                lines += [f"note: {r['note']}", ""]
        else:
            lines += ["", "model refused", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    log(f"\n{time.time() - started:.0f}s   detail -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
