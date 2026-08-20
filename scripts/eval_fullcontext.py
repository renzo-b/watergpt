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

import plant as plant_paths  # noqa: E402
from eval_retrieval import alternatives, load_cases, matches  # noqa: E402

# The model to answer with defaults to whatever agent.py ships, imported
# rather than repeated so the two cannot drift. Measuring one model while
# shipping another is the quietest way for an eval to stop meaning anything:
# a catalogue a stronger model routes correctly may not be enough for the
# model actually answering users.
from agent import MODEL as DEFAULT_MODEL  # noqa: E402

# List price, dollars per million tokens, per model. Kept as a table rather
# than two constants because the cost line is only honest if it tracks the
# model actually used - a hardcoded Opus price under a Sonnet run reports a
# number that is wrong by nearly half and says so with total confidence.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),   # $2/$10 introductory through 2026-08-31
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Cache writes cost 1.25x input and reads 0.1x. Both are reported because the
# write is a one-off per run and the read is what a real session pays per turn.
CACHE_WRITE, CACHE_READ = 1.25, 0.1


def prices(model):
    if model not in PRICES:
        raise SystemExit(
            f"no price on file for {model!r}. Add it to PRICES rather than "
            "letting the run report a cost computed from another model's rates."
        )
    return PRICES[model]


DEFAULT_MAX_DOC_TOKENS = 120_000
DEFAULT_MAX_TOTAL_TOKENS = 200_000

# Fetch turns allowed per case. A question may legitimately need two or
# three - locate a chapter, then narrow a truncated range - but a model
# looping on fetches is not converging and should be cut off rather than
# billed indefinitely.
MAX_TOOL_TURNS = 6

SYSTEM_INSTRUCTIONS = """\
You are answering questions from a water treatment plant's own documents, \
catalogued below. Every document is delimited by a DOCUMENT: line followed \
by an `about:` summary of what it covers.

A short document is given in full under `full text follows, with locators:`. \
A long one is given as `contents:` - one line per part, each a location and \
a DESCRIPTION of what is there rather than the text itself. A description \
tells you where to look; it is not the source and cannot be quoted from.

Use fetch_document_part to read the actual contents of any part you were \
only given a description of. Pass the filename from the DOCUMENT: line and \
a location copied from the catalogue. Answering a question about a long \
document without fetching means answering from a summary, which is the \
failure this is checking for.

Locations are written [page N], [pages N-M], or [sheet 'NAME'] for a \
spreadsheet. Report the source as the exact filename and the location as \
the marker containing the text you used, copied verbatim without the \
brackets - for a range you fetched inside, cite the page the answer was on.

Answer only from these documents. If they do not contain the answer, set \
found to false and say so. Do not infer a plausible answer from general \
water treatment knowledge - a confident answer that is not in these \
documents is the failure this is checking for."""

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


def assemble(client, plant, max_doc, max_total, log, model, terse=True):
    """Build the context payload from the ingest catalogue. Returns (text, included, excluded).

    The corpus is no longer re-derived from docling parses here. ingest/ already
    decomposed every uploaded file, decided per document whether to carry it
    verbatim or as descriptions, and wrote the result to the plant manifest -
    so this reads that and stops. Two implementations of "what does the model
    see" would drift, and the catalogue is the one the product will ship.

    The per-document token caps stay. They are the boundary between the two
    strategies: a document too large to carry is the case for retrieval, and
    excluding it here by a property of the document keeps that decision
    explainable rather than an artefact of corpus ordering.
    """
    from ingest import read_manifest

    blocks, included, excluded = [], [], []
    for entry in sorted(read_manifest(plant), key=lambda e: e.file_path):
        name = Path(entry.file_path).name
        if entry.status != "ingested":
            excluded.append((name, None, entry.error or "not ingested"))
            continue

        # The matcher compares on filename, and SYSTEM_INSTRUCTIONS tells the
        # model to cite the DOCUMENT: line, so the catalogue's own "document:
        # <full path>" header is replaced with the bare filename.
        body = entry.catalogue_entry(terse).split("\n", 1)[1]
        block = f"DOCUMENT: {name}\n{body}"

        tokens = count_tokens(client, block, model)
        if tokens > max_doc:
            excluded.append((name, tokens, f"over --max-doc-tokens ({max_doc:,})"))
            continue
        included.append((name, tokens))
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


def count_tokens(client, text, model):
    return client.messages.count_tokens(
        model=model, messages=[{"role": "user", "content": text or "."}]
    ).input_tokens


# --------------------------------------------------------------------------


class Usage:
    """Usage summed across the turns of one case's tool loop."""

    def __init__(self):
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, u):
        self.cache_creation_input_tokens += u.cache_creation_input_tokens or 0
        self.cache_read_input_tokens += u.cache_read_input_tokens or 0
        self.input_tokens += u.input_tokens or 0
        self.output_tokens += u.output_tokens or 0
        return self


def ask(client, corpus, question, effort, max_tokens, plant_id, log, model):
    """Answer one case, letting the model fetch document parts as it goes.

    The catalogue in the system prompt says what exists and where; it does not
    carry the text of a long document. Without a fetch tool the model can only
    answer such a question from a description, which is the failure this
    harness is supposed to detect rather than encode. So the tool is offered
    and the calls it makes are recorded - a case that passes without fetching
    was answerable from the catalogue alone, and that is worth knowing
    separately from a case that needed the source.
    """
    import tools as tool_pkg
    from tools.registry import dispatch

    schemas = [s for s in tool_pkg.TOOL_SCHEMAS if s["name"] == "fetch_document_part"]
    messages = [{"role": "user", "content": question}]
    usage = Usage()
    fetched = []

    for _ in range(MAX_TOOL_TURNS):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            tools=schemas,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            },
            system=[
                {"type": "text", "text": SYSTEM_INSTRUCTIONS},
                # The corpus is the cached prefix: stable across every case, so
                # it is written once and read at a tenth of the price after.
                {"type": "text", "text": corpus,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=messages,
        )
        usage.add(response.usage)

        if response.stop_reason == "refusal":
            return None, usage, fetched
        if response.stop_reason != "tool_use":
            text = next((b.text for b in response.content if b.type == "text"), "")
            return (json.loads(text) if text else None), usage, fetched

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            fetched.append(
                f"{block.input.get('document', '?')} @ {block.input.get('locator', '?')}"
            )
            out = dispatch(block.name, block.input, plant_id=plant_id)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(out),
            })
        messages.append({"role": "user", "content": results})

    log(f"    tool loop hit {MAX_TOOL_TURNS} turns without a final answer")
    return None, usage, fetched


def main():
    parser = argparse.ArgumentParser(
        description="Answer retrieval_set.yaml with the whole corpus in context."
    )
    parser.add_argument("--set", type=Path, default=ROOT / "evals" / "retrieval_set.yaml")
    parser.add_argument("--plant-id", default=plant_paths.DEFAULT_PLANT,
                        help="which plant's catalogue to answer from")
    parser.add_argument("--max-doc-tokens", type=int, default=DEFAULT_MAX_DOC_TOKENS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOTAL_TOKENS)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"model to answer with (default: {DEFAULT_MODEL}, "
                             "the model agent.py ships)")
    parser.add_argument("--full-catalogue", action="store_true",
                        help="render every component description, not "
                             "just those of untitled parts (~50%% more tokens)")
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
        client, args.plant_id, args.max_doc_tokens, args.max_tokens, log,
        args.model, terse=not args.full_catalogue,
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
        parsed, usage, fetched = ask(
            client, corpus, case["question"].strip(),
            args.effort, args.answer_tokens, args.plant_id, log, args.model,
        )
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
                        "contains_ok": contains_ok, "note": note,
                        "fetched": fetched})
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

    price_in, price_out = prices(args.model)
    cost = (
        cache_write * CACHE_WRITE * price_in
        + cache_read * CACHE_READ * price_in
        + plain_in * price_in
        + out_tokens * price_out
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
        f"- model: {args.model}, effort {args.effort}", "",
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
