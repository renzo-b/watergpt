"""
Tests for the retrieval harness's matcher — python tests/test_retrieval_matching.py

Plain asserts to match the calculator tests; this repo has no pytest.

This covers the part of scripts/eval_retrieval.py that decides whether a
retrieved chunk counts as the right one. It is worth testing on its own because
a matcher bug is indistinguishable from a retrieval bug in the aggregate: both
show up as a low recall number, and only one of them is about retrieval.

Deliberately does not import rag.* — the matcher is pure string handling, so
these run with no database, no API key, and none of the ingestion dependencies
installed.
"""

import sys
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_retrieval import (  # noqa: E402
    first_hit,
    matches,
    norm_document,
    norm_location,
    recall,
)

# Stands in for rag.retrieval.Chunk. The matcher only reads these fields, and a
# stub keeps this file runnable without psycopg installed.
Stub = namedtuple("Stub", "document location")


# 1. The spelling the eval set uses and the spelling the index writes are the
#    same reference. This is the mismatch the corpus actually has: retrieval_set
#    says "page 1", docling reports "p.1".
assert norm_location("page 1") == norm_location("p.1") == "p.1"
assert norm_location("Page 7") == norm_location("p. 7") == "p.7"
assert norm_location("pages 7-8") == norm_location("pp.7-8") == "p.7-8"

# 2. Sheet locations survive quoting and spacing differences.
assert norm_location("sheet 'CT Calc'") == "sheet 'ct calc'"
assert norm_location("sheet  'CT   Calc',  Part 3") == "sheet 'ct calc', part 3"

# 3. Documents compare by filename, not by path or case.
assert norm_document("documents/rag_test/SOP.pdf") == "sop.pdf"
assert norm_document("SOP.pdf") == norm_document("  sop.PDF  ")

# 4. A chunk in the right document at the right page is a strict hit.
source = {"document": "Adverse-Water-Condition-Notification-Contingency-Plan.pdf",
          "location": "page 1"}
hit = Stub("Adverse-Water-Condition-Notification-Contingency-Plan.pdf", "p.1")
assert matches(hit, source) == (True, True), matches(hit, source)

# 5. Right document, wrong page: document-only. This is the distinction the
#    report's strict-vs-doc-only columns exist to show.
wrong_page = Stub("Adverse-Water-Condition-Notification-Contingency-Plan.pdf", "p.2")
assert matches(wrong_page, source) == (True, False)

# 6. Wrong document is never a hit, even when the location string agrees —
#    every document has a page 1.
wrong_doc = Stub("Chemical-Spill-Standard-Operating-Procedure.pdf", "p.1")
assert matches(wrong_doc, source) == (False, False)

# 7. Containment runs both ways. The index writes a more precise location than
#    the case does for spreadsheets, and that must not score as a miss.
sheet_source = {"document": "CT.xlsx", "location": "sheet 'CT Calc'"}
precise = Stub("CT.xlsx", "sheet 'CT Calc', cells C31, C33")
assert matches(precise, sheet_source) == (True, True), matches(precise, sheet_source)

# ...and the reverse: the case names a section the index does not record.
page_source = {"document": "SOP.pdf", "location": "p.7, section 4"}
assert matches(Stub("SOP.pdf", "p.7"), page_source) == (True, True)

# 8. An empty location never matches. Without this, a chunk with no location —
#    which is a provenance bug — would silently pass every case in its document.
assert matches(Stub("SOP.pdf", ""), {"document": "SOP.pdf", "location": "p.7"}) == (
    True,
    False,
)

# 9. first_hit reports 1-based ranks, and reports the two independently.
chunks = [
    Stub("Other.pdf", "p.1"),  # rank 1: nothing
    Stub("SOP.pdf", "p.9"),  # rank 2: document only
    Stub("SOP.pdf", "p.7"),  # rank 3: strict
]
assert first_hit(chunks, {"document": "SOP.pdf", "location": "p.7"}) == (3, 2)
assert first_hit(chunks, {"document": "Nope.pdf", "location": "p.7"}) == (None, None)

# 10. Recall counts only sourced cases; negatives are scored separately and must
#     not dilute the denominator.
results = [
    {"negative": False, "strict": 1, "doc_only": 1},
    {"negative": False, "strict": 8, "doc_only": 2},
    {"negative": False, "strict": None, "doc_only": 4},
    {"negative": True, "strict": None, "doc_only": None},
]
assert recall(results, 5, "strict") == (1 / 3, 3), recall(results, 5, "strict")
assert recall(results, 10, "strict") == (2 / 3, 3)
assert recall(results, 5, "doc_only") == (1.0, 3)
assert recall([{"negative": True, "strict": None, "doc_only": None}], 5, "strict") == (
    None,
    0,
)

print("test_retrieval_matching: all assertions passed")
