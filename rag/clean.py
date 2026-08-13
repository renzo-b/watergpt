"""Whitespace and rule-character normalisation for chunk text.

Justified PDF text arrives with the inter-word padding baked in as literal
spaces - "Notify  the  ORO  (call  twice  ten  minutes  apart" - and page
furniture arrives as long runs of underscores or dashes standing in for a
horizontal rule. Neither carries meaning. Both cost embedding budget and make
a retrieved chunk unpleasant to read in a tool result, which matters because
an operator eventually reads these.

Applied AFTER chunking, not before. Cleaning the DoclingDocument first would be
better in principle - the chunker's token budget would then be spent on real
text - but it means mutating docling's parse, and the gain does not justify
that. The practical cost is that chunks are sized against the dirty text and
come out slightly under budget once cleaned.

The two rules below are deliberately conservative, because the markdown table
serializer is one of the two things under comparison and its output is made
almost entirely of whitespace and punctuation:

  - Spaces collapse WITHIN a line; line breaks survive. Markdown tables are
    newline-structured, so flattening them would gut one config and hand the
    other a win it did not earn.
  - A line is dropped as a rule only when it is one character repeated. A
    markdown separator row is '|---|---|', which has two distinct characters
    and therefore survives; a run of underscores has one and does not.
"""

import re

# Characters that show up as horizontal rules in extracted documents. `|` is
# pointedly absent: a line of pipes is a degenerate table row, not a rule.
RULE_CHARS = frozenset("_-=*~.·—–+#")

MIN_RULE_LENGTH = 4

# A markdown separator row - pipes, dashes, colons and spaces, nothing else.
# docling pads these out to the width of the widest cell, so a wide table
# carries a couple of hundred characters of dashes in every chunk it lands in,
# conveying exactly what '|---|---|' conveys. Shortened rather than dropped:
# remove the row and the table stops being valid markdown, which would defeat
# the point of serializing it as markdown in the first place.
SEPARATOR_ROW = re.compile(r"^\|[\s\-:|]+\|$")
DASH_RUN = re.compile(r"-{3,}")


def is_rule(line):
    """True for a line that is nothing but a repeated rule character."""
    stripped = line.strip()
    return (
        len(stripped) >= MIN_RULE_LENGTH
        and len(set(stripped)) == 1
        and stripped[0] in RULE_CHARS
    )


def shrink_separator(line):
    """Collapse a padded markdown separator row to its minimal valid form."""
    return DASH_RUN.sub("---", line) if SEPARATOR_ROW.match(line) else line


def normalise(text):
    """Collapse intra-line whitespace, drop rule lines and repeated blanks."""
    lines = []
    for line in text.splitlines():
        # split() with no argument handles tabs, non-breaking runs, and the
        # double-spacing from justified text in one pass.
        collapsed = shrink_separator(" ".join(line.split()))
        if is_rule(collapsed):
            continue
        if not collapsed and lines and not lines[-1]:
            continue  # never more than one blank line in a row
        lines.append(collapsed)
    return "\n".join(lines).strip()
