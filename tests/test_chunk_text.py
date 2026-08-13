"""
Tests for chunk text normalisation — python tests/test_chunk_text.py

Plain asserts to match the calculator tests; this repo has no pytest.

The stakes here are not cosmetic. One of the two ingestion configs under
comparison serialises tables as markdown, which is structured entirely out of
whitespace and punctuation. A normaliser that flattens newlines or eats
separator rows would quietly destroy that config's output and hand the triplet
config a win it did not earn — and the resulting recall gap would look exactly
like a real finding. Most of what follows guards that boundary.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.clean import is_rule, normalise, shrink_separator  # noqa: E402

# 1. The observed defect: justified PDF text arrives double-spaced.
dirty = "Notify  the  ORO  (call  twice  ten  minutes  apart  on  a  home  phone"
assert normalise(dirty) == "Notify the ORO (call twice ten minutes apart on a home phone"

# 2. Tabs and mixed runs collapse the same way.
assert normalise("a\t\tb   c") == "a b c"

# 3. Line breaks SURVIVE. This is the one that protects the markdown config.
assert normalise("heading\nbody text") == "heading\nbody text"

# 4. A markdown table round-trips untouched apart from padding. Both the
#    separator row and the row structure must come through intact.
table = "| METHOD | WHAT TO CHECK |\n|---|---|\n| F/M | MLVSS & Influent COD |"
assert normalise(table) == table, normalise(table)

# 5. Wider padding inside a markdown table collapses, rows still survive, and
#    the separator shrinks to its minimal form (see 13).
padded = "| a    |  b   |\n|------|------|\n| c    |  d   |"
assert normalise(padded) == "| a | b |\n|---|---|\n| c | d |", normalise(padded)

# 6. The horizontal rule from chunk 0000 of the real corpus is dropped.
assert is_rule("________________________________________")
assert normalise("Title\n____________________\nBody") == "Title\nBody"

# 7. Other rule characters too.
for rule in ("----------", "==========", "**********", "~~~~~~~~", "..........", "++++++"):
    assert is_rule(rule), rule

# 8. A markdown separator row is NOT a rule — two distinct characters. This is
#    the assertion standing between the markdown config and silent mutilation.
assert not is_rule("|---|---|")
assert not is_rule("| --- | --- |")

# 9. Short runs are punctuation, not furniture, and stay.
assert not is_rule("---")  # below MIN_RULE_LENGTH
assert not is_rule("...")
assert normalise("wait...") == "wait..."

# 10. Rule characters inside a line are left alone — only whole lines go.
assert normalise("see page 3 ---- appendix") == "see page 3 ---- appendix"

# 11. Repeated blank lines collapse to one; leading/trailing whitespace goes.
assert normalise("a\n\n\n\nb") == "a\n\nb"
assert normalise("\n\n  padded  \n\n") == "padded"

# 12. A chunk that was nothing but a rule normalises to empty. build_index.py
#     drops these and says how many, because the embeddings API rejects
#     empty input.
assert normalise("__________") == ""
assert normalise("   \n  \n ") == ""

# 13. Padded separator rows collapse to minimal valid markdown. docling pads
#     these to the widest cell, which on a wide table is ~200 characters of
#     dashes carried in every chunk the table lands in.
wide = "|-----------|--------------------|------------|"
assert shrink_separator(wide) == "|---|---|---|", shrink_separator(wide)
assert normalise(f"| a | b | c |\n{wide}\n| 1 | 2 | 3 |") == (
    "| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |"
)

# 14. Alignment colons survive — they are semantic in markdown, unlike padding.
assert shrink_separator("|:----------|----------:|") == "|:---|---:|"

# 15. Only genuine separator rows are touched. A content row containing dashes
#     keeps them, or a table of date ranges would be quietly rewritten.
assert shrink_separator("| 2024-01-01 | ----- |") == "| 2024-01-01 | ----- |"
assert normalise("| item | note |\n| a---b | see ----- |") == (
    "| item | note |\n| a---b | see ----- |"
)

# 16. Idempotent — running it twice changes nothing, so a re-index of already
#     clean text is a no-op rather than a slow drift.
for sample in (dirty, table, "Title\n____\nBody", padded, wide):
    assert normalise(normalise(sample)) == normalise(sample), sample

print("test_chunk_text: all assertions passed")
