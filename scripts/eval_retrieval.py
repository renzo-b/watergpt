#!/usr/bin/env python3
"""Measure retrieval, one config against another.

    python scripts/eval_retrieval.py --plant-id demo \\
        --configs triplet_tables markdown_tables

Calls rag.retrieval.search_plant_docs directly rather than going through the
agent. That is the whole design: if a model reformulated the query first, a
difference between two configs could be the reformulation rather than the
chunking, and there would be no way to tell which from the numbers.

Two artefacts, because the aggregate and the detail answer different questions.
The recall table tells you THAT something differs. The per-case file - every
chunk that came back, its location, and its full text - tells you WHY, which is
the only part you can act on.

Grading a retrieval case means deciding whether the right chunk came back, and
that is a question about provenance, not about wording: a case passes when a
retrieved chunk's document and location match what the case says the answer
lives in. Two numbers are reported for every slice:

  strict    document AND location match - the real number
  doc-only  document matches, location does not

The gap between them is diagnostic rather than decorative. A high doc-only with
a low strict means retrieval is finding the right document and the chunk
boundaries are landing somewhere else, which is a chunking problem. Both low
means the query is not finding the document at all, which is an embedding or
phrasing problem. Reporting only the strict number hides which of those you have.
"""

import argparse
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SET = ROOT / "evals" / "retrieval_set.yaml"
RUNS_DIR = ROOT / "evals" / "runs"

# A negative case passes when nothing plausible comes back. "Plausible" needs a
# number, and cosine similarity has no natural zero: unrelated text in the same
# domain still scores well above 0 because it shares vocabulary. This is a
# starting value, not a constant of nature - sweep it with --threshold and read
# the scores in the detail file before trusting any negative-case pass rate.
DEFAULT_THRESHOLD = 0.35


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------
# Kept as free functions with no imports from rag.* so they can be tested
# without a database - see tests/test_retrieval_matching.py.


def norm_document(name):
    """Compare documents by filename, ignoring path and case."""
    return Path(str(name).strip()).name.casefold()


def norm_location(text):
    """Fold the ways a page or sheet reference gets written into one form.

    The eval set is written the way an operator would say it ('page 1'), and
    the index stores what docling reports ('p.1'). Neither spelling is wrong,
    so neither is rewritten - they are compared after normalisation instead.
    """
    s = str(text).strip().casefold()
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\bpages?\b", "p.", s)  # page 7 / pages 7-8 -> p. 7 / p. 7-8
    s = re.sub(r"\bpp?\.", "p.", s)  # pp.7-8 -> p.7-8
    s = re.sub(r"p\.\s+", "p.", s)  # p. 7 -> p.7
    s = re.sub(r"[\s_]+", " ", s)
    return s.strip(" .,;")


def alternatives(source):
    """Normalise expects.source to a list of acceptable {document, location}.

    A single mapping and a list of mappings are both valid in the eval set,
    because some facts genuinely live in more than one document. This corpus
    carries the same free-chlorine corrective action verbatim in three of the
    plant's SOPs; pinning such a case to one of them scores a correct citation
    as a miss and tells you retrieval is broken when it is not. That is not a
    hypothetical - it is how pdf_dosage was written, and it cost a full
    debugging pass to work out that the eval was wrong rather than the system.
    """
    if source is None:
        return []
    return list(source) if isinstance(source, list) else [source]


def page_span(text):
    """The (first, last) page a normalised location refers to, or None.

    'p.7' -> (7, 7);  'p.93-103' -> (93, 103);  "sheet 'ct'" -> None.
    """
    span = re.search(r"p\.(\d+)\s*-\s*(\d+)", text)
    if span:
        return int(span.group(1)), int(span.group(2))
    one = re.search(r"p\.(\d+)", text)
    if one:
        return int(one.group(1)), int(one.group(1))
    return None


def location_matches(want, got):
    """Whether an actual location satisfies an expected one.

    Two tests, because locations are written at different granularities and
    neither spelling is wrong.

    Page spans overlap numerically. An expectation of "pages 93-103" is
    satisfied by a citation of "p.95" - the answer was found inside the range
    the case named. String containment cannot see that ("p.95" is not a
    substring of "p.93-103"), and it started mattering once the agent could
    fetch a range and then cite the exact page it read: the model became more
    precise than the expectation, and was scored wrong for it.

    Everything else falls back to containment in both directions, which is what
    makes an expected "sheet 'CT Calc'" satisfied by "sheet 'CT Calc', cells
    C31, C33".
    """
    if not want or not got:
        return False
    a, b = page_span(want), page_span(got)
    if a and b:
        return a[0] <= b[1] and b[0] <= a[1]
    return want in got or got in want


def matches(chunk, source):
    """Return (document_matches, location_matches) for one chunk.

    `source` may be one expectation or several; the chunk needs to satisfy any
    one of them.
    """
    any_doc = any_strict = False
    for option in alternatives(source):
        doc_ok = norm_document(chunk.document) == norm_document(option["document"])
        loc_ok = location_matches(
            norm_location(option["location"]), norm_location(chunk.location)
        )
        any_doc = any_doc or doc_ok
        any_strict = any_strict or (doc_ok and loc_ok)
    return any_doc, any_strict


def first_hit(chunks, source):
    """1-based rank of the first strict hit, and of the first doc-only hit."""
    strict = doc_only = None
    for rank, chunk in enumerate(chunks, start=1):
        doc_ok, both = matches(chunk, source)
        if both and strict is None:
            strict = rank
        if doc_ok and doc_only is None:
            doc_only = rank
    return strict, doc_only


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def load_cases(path):
    """Accept a bare list or a mapping with 'cases:' - both shapes are in use
    across evals/, and the failure when a file uses the other one is an
    unhelpful TypeError deep in the run."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(spec, list):
        cases = spec
    elif isinstance(spec, dict) and isinstance(spec.get("cases"), list):
        cases = spec["cases"]
    else:
        raise SystemExit(f"{path} must be a list of cases or a mapping with 'cases:'")
    cases = [c for c in cases if c]
    missing = [c.get("id", "<no id>") for c in cases if "expects" not in c]
    if missing:
        raise SystemExit(
            f"{path}: cases with no 'expects' block: {', '.join(missing)}. "
            "A retrieval case with nothing to expect cannot be scored."
        )
    return cases


def recall(results, cutoff, key):
    """Fraction of sourced cases whose first hit lands at or above `cutoff`."""
    sourced = [r for r in results if not r["negative"]]
    if not sourced:
        return None, 0
    hits = sum(1 for r in sourced if r[key] is not None and r[key] <= cutoff)
    return hits / len(sourced), len(sourced)


def fmt(value):
    return "  n/a" if value is None else f"{value * 100:4.0f}%"


def slice_table(results, label_width=22):
    """Overall row plus one row per case `type`."""
    lines = []
    groups = [("ALL", results)]
    for case_type in sorted({r["type"] for r in results}):
        groups.append((case_type, [r for r in results if r["type"] == case_type]))

    header = f"{'slice':<{label_width}} {'n':>3}  {'R@5':>5} {'R@10':>5}   {'doc@5':>5} {'doc@10':>6}   neg"
    lines.append(header)
    lines.append("-" * len(header))
    for label, group in groups:
        r5, n = recall(group, 5, "strict")
        r10, _ = recall(group, 10, "strict")
        d5, _ = recall(group, 5, "doc_only")
        d10, _ = recall(group, 10, "doc_only")
        negatives = [g for g in group if g["negative"]]
        neg = (
            f"{sum(1 for g in negatives if g['passed'])}/{len(negatives)}"
            if negatives
            else "-"
        )
        lines.append(
            f"{label:<{label_width}} {n:>3}  {fmt(r5)} {fmt(r10)}   "
            f"{fmt(d5)} {fmt(d10):>6}   {neg}"
        )
    return lines


# --------------------------------------------------------------------------


def run_config(
    cases, config_id, plant_id, k, threshold, embedding_model, collection, search
):
    results = []
    for case in cases:
        source = case["expects"].get("source")
        negative = source is None
        chunks = search(
            query=case["question"],
            plant_id=plant_id,
            config_id=config_id,
            k=k,
            collection=collection,
            embedding_model=embedding_model,
        )

        if negative:
            # "Passes if nothing above a similarity threshold is returned."
            # Retrieval always returns its k nearest neighbours - there is no
            # such thing as an empty result - so the negative case is a
            # statement about scores, not about count.
            above = [c for c in chunks if c.score >= threshold]
            passed, strict, doc_only = not above, None, None
        else:
            for option in alternatives(source):
                for field in ("document", "location"):
                    if field not in option:
                        raise SystemExit(
                            f"case {case['id']}: expects.source has no {field!r}. "
                            "Both are required - a source without a location "
                            "cannot be scored strictly, only by document."
                        )
            strict, doc_only = first_hit(chunks, source)
            passed = strict is not None and strict <= k

        results.append(
            {
                "id": case["id"],
                "type": case.get("type", "untyped"),
                "question": case["question"].strip(),
                "negative": negative,
                "source": source,
                "passed": passed,
                "strict": strict,
                "doc_only": doc_only,
                "chunks": chunks,
            }
        )
    return results


def write_detail(path, args, per_config, threshold):
    out = [
        "# Retrieval detail",
        "",
        f"- set: `{args.set.relative_to(ROOT) if args.set.is_relative_to(ROOT) else args.set}`",
        f"- plant: `{args.plant_id}`  k: {args.k}  threshold: {threshold}",
        f"- embedding model: `{args.embedding_model}`",
        "",
        "Every chunk returned, in rank order, with the text that was embedded.",
        "A chunk marked HIT matched the case's expected document and location.",
        "",
    ]
    for config_id, results in per_config.items():
        out += [f"## config `{config_id}`", ""]
        for r in results:
            verdict = "PASS" if r["passed"] else "FAIL"
            out.append(f"### {r['id']} — {verdict}  ({r['type']})")
            out.append("")
            out.append(f"> {r['question']}")
            out.append("")
            if r["negative"]:
                out.append(
                    f"Negative case: passes when nothing scores >= {threshold}. "
                    f"Top score was "
                    + (f"{r['chunks'][0].score:.3f}." if r["chunks"] else "n/a (no rows).")
                )
            else:
                wanted = " or ".join(
                    f"`{o['document']}` @ `{o['location']}`"
                    for o in alternatives(r["source"])
                )
                out.append(
                    f"Expected {wanted} — first strict hit at rank "
                    f"{r['strict'] or '-'}, first document match at rank "
                    f"{r['doc_only'] or '-'}."
                )
            out.append("")
            if not r["chunks"]:
                out += ["_nothing returned — is this config indexed for this plant?_", ""]
                continue
            for rank, chunk in enumerate(r["chunks"], start=1):
                tag = ""
                if not r["negative"]:
                    doc_ok, both = matches(chunk, r["source"])
                    tag = "  **HIT**" if both else ("  *(document only)*" if doc_ok else "")
                out.append(
                    f"**{rank}.** `{chunk.score:.3f}` {chunk.cite()} "
                    f"·  {chunk.content_type} ·  `{chunk.chunk_id}`{tag}"
                )
                if chunk.section:
                    out.append(f"section: {chunk.section}")
                out += ["", "```", chunk.text, "```", ""]
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Compare retrieval configs against evals/retrieval_set.yaml."
    )
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--plant-id", required=True)
    parser.add_argument(
        "--configs",
        nargs="+",
        required=True,
        help="config_id values to compare, as passed to build_index.py",
    )
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument(
        "--store-path", type=Path, default=None, help="Chroma directory"
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--id", help="run a single case id")
    parser.add_argument("--type", help="filter by case type")
    args = parser.parse_args()

    # Deferred so the matching functions above stay importable without chromadb
    # or an embeddings key - tests exercise them directly.
    from rag import embed, store
    from rag.retrieval import search_plant_docs

    args.embedding_model = args.embedding_model or embed.DEFAULT_MODEL

    if args.k < 10:
        raise SystemExit("--k must be at least 10; recall@10 is part of the report")

    cases = load_cases(args.set)
    if args.type:
        cases = [c for c in cases if c.get("type") == args.type]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
    if not cases:
        raise SystemExit("no cases matched that filter")

    active = len(cases)
    print(f"{args.set.name}: {active} active case(s)")
    negatives = sum(1 for c in cases if c["expects"].get("source") is None)
    if not negatives:
        # Worth saying out loud. A set where every case has a findable answer
        # cannot catch the failure that matters most in an operations context:
        # confidently returning something for a question the corpus does not
        # answer. The commented-out rag_not_in_corpus case in the set is
        # exactly this, and it is not currently running.
        print(
            "  WARNING: no negative cases (expects.source: null). Nothing here "
            "measures what happens when the answer is not in the corpus."
        )

    started = time.time()
    _client, collection = store.open_collection(args.store_path, create=False)
    per_config = {}
    for config_id in args.configs:
        print(f"\n{config_id}")
        results = run_config(
            cases,
            config_id,
            args.plant_id,
            args.k,
            args.threshold,
            args.embedding_model,
            collection,
            search_plant_docs,
        )
        per_config[config_id] = results
        for line in slice_table(results):
            print("  " + line)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or RUNS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-retrieval.md"
    write_detail(out, args, per_config, args.threshold)
    print(f"\n{time.time() - started:.0f}s   detail -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
