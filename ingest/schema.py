"""What we record about one uploaded document and its parts.

One document becomes one manifest line: a DocEntry holding an ordered list of
Components. A Component is a thing an operator could ask for by name - a
section, a table, a figure, a formula, a log sheet - carrying the locator that
answers "where is it" and the interpretation that answers "what is it".

The two-tier split this file exists to enforce:

  The CATALOGUE goes into context. It is descriptions and locators, and for a
  short document the full text as well. It is what lets the model say "the pump
  curve is figure 2 on page 3 of techSheet.pdf" without having read page 3.

  The CONTENT is fetched on demand. Section text, table rows, the image itself
  and log dataframes all stay out of context until something asks for them.

A summary can route a question; it cannot answer one. Any design where the
interpretation replaces the source is a design that will confidently answer
"what is the max chlorine residual" from a sentence that never contained a
number. So every interpreted component keeps a pointer back to the thing it
describes, and `catalogue_entry` never emits a statistic the model authored.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# What a component can be. Deliberately short: these are the kinds that need
# different treatment at fetch time, not a taxonomy of document furniture.
#
#   section  contiguous prose under one heading; fetch returns the text
#   table    fetch returns the rows
#   image    fetch returns the cropped page region for the model to look at
#   formula  fetch returns the expression and its surrounding text
#   log      a spreadsheet grid of dated observations; fetch returns a
#            dataframe, and the parquet was written by logs/convert.py
Kind = Literal["section", "table", "image", "formula", "log"]


class Component(BaseModel):
    """One addressable part of a document."""

    component_id: str  # stable within a document, e.g. "p3.picture.1"
    kind: Kind

    # ---- where it is, in the words a person would use to find it ----
    # Rendered verbatim into the catalogue, so it has to read like a citation:
    # "page 3", "pages 11-14", "sheet 'Power'", "sheet 'Jan.' cells B4:F40".
    locator: str
    page_start: int | None = None
    page_end: int | None = None

    title: str = ""  # heading text, table caption, figure caption

    # ---- from the model ----
    # What this part is, for someone trying to find it. Never a statistic:
    # row counts, date ranges and extremes are computed from the content or
    # not stated at all. An approximated fact is worse than a missing one
    # because nothing downstream can tell the difference.
    description: str = ""

    # Whether this part earns a place in the catalogue. A logo, a letterhead
    # crest, a table-of-contents row and a stray fragment of a title block are
    # all real parts of the document and none of them help anyone find
    # anything. They are still described and still stored - dropping them at
    # write time would make the decision unauditable - but they are kept out of
    # context, where every line is read again on every question.
    #
    # Defaults true, and the prompt says to index when unsure, because the
    # failure modes are not symmetric: an indexed logo costs a few tokens per
    # question, while a dropped section that mattered is a question that can
    # never be answered and leaves no error behind to explain why.
    indexed: bool = True

    # ---- content: present when short enough to carry, else fetched ----
    # Set for a document the pipeline chose to carry verbatim. When None, the
    # content lives in the source file and is read at fetch time via the
    # locator. Either way the source file remains the source of truth.
    content: str | None = None

    # Where the extracted payload landed, for kinds that have one. Today only
    # `log` uses it, pointing at the parquet logs/convert.py wrote.
    payload_path: str | None = None

    status: Literal["ok", "failed"] = "ok"
    error: str | None = None


class DocEntry(BaseModel):
    """One manifest row: one uploaded file, decomposed and interpreted."""

    # ---- provenance ----
    file_path: str
    file_hash: str
    file_type: str  # "pdf", "docx", "xlsx", "csv"
    generated_at: datetime

    # ---- the routing decision, made per document by the model ----
    # verbatim:    small enough that the whole text goes into context, so no
    #              summary stands between a question and the words that answer
    #              it. The right default for a one-page SOP.
    # interpreted: too large to carry, so the catalogue holds descriptions and
    #              locators and the content is fetched.
    mode: Literal["verbatim", "interpreted"] = "interpreted"

    # One paragraph: what this document is, in the words an operator would
    # search by. This is the line that decides whether the model opens the
    # document at all, so it matters more than any component description.
    summary: str = ""

    components: list[Component] = Field(default_factory=list)

    # Anything that forced a decision, anything omitted and why. Diagnostic,
    # printed at ingest, never sent to the answering model.
    notes: str = ""

    status: Literal["ingested", "failed"] = "ingested"
    error: str | None = None

    # Characters of source text the components account for, over characters
    # extracted from the file. The lossy-decomposition signal: a document whose
    # sections dropped half its prose shows up here rather than as a surprise
    # months later. Recorded, never enforced - a form that is mostly checkboxes
    # legitimately scores low.
    text_coverage: float | None = None

    def catalogue_entry(self):
        """The compact form that goes into context. Not the whole entry."""
        lines = [
            f"document: {self.file_path}",
            f"about: {self.summary}",
        ]
        if self.status == "failed":
            return "\n".join(lines + [f"NOT INGESTED: {self.error}"])

        ok = [c for c in self.components if c.status == "ok" and c.indexed]

        if self.mode == "verbatim":
            # Carried whole: the locators still matter, because an answer
            # drawn from this text still has to cite a page.
            lines.append("full text follows, with locators:")
            for c in ok:
                head = f"  [{c.locator}]"
                if c.title:
                    head += f" {c.title}"
                lines.append(head)
                if c.content:
                    lines += [f"    {ln}" for ln in c.content.splitlines() if ln.strip()]
                elif c.description:
                    # Images and tables are described even in verbatim mode:
                    # their content is not text and cannot be inlined.
                    lines.append(f"    ({c.kind}) {c.description}")
            return "\n".join(lines)

        lines.append("contents:")
        for c in ok:
            head = f"  [{c.locator}] {c.kind}"
            if c.title:
                head += f": {c.title}"
            lines.append(head)
            if c.description:
                lines.append(f"    {c.description}")
        failed = [c for c in self.components if c.status != "ok"]
        if failed:
            lines.append(
                "not interpreted: "
                + "; ".join(f"{c.locator} ({c.error})" for c in failed)
            )
        return "\n".join(lines)
