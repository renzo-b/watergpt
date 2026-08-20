"""Reading a document part the catalogue has already pointed at.

This is the tool half of the two-tier design. The catalogue in context says
what exists and where; this returns what is actually there. It is the only
document tool registered, and deliberately so: there is no search here, because
the routing has already happened by reading the catalogue. Retrieval that has
to guess which document a question belongs to is solving a problem this corpus
does not have.
"""

from ingest.fetch import fetch
from tools.registry import tool


@tool(
    name="fetch_document_part",
    wants_plant_id=True,
    description=(
        "Read the actual contents of one part of a plant document, using a "
        "location printed in the document catalogue. Call this whenever the "
        "catalogue tells you a document covers something but you need the "
        "words, numbers or rows themselves — a procedure's steps, a table's "
        "values, a specification. "
        "The catalogue lists what each document contains and where; it does "
        "NOT contain the text of a long document, so answering from a "
        "catalogue description alone means answering from a summary. "
        "Returns page text for a document, cell values with their formulas "
        "for a spreadsheet, and for a converted operating log the index of its "
        "columns with the min, max and blank fraction pandas computed from the "
        "data — which answers most questions about a log directly. "
        "Cite the document and location you fetched."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "document": {
                "type": "string",
                "description": (
                    "Filename exactly as it appears on the catalogue's "
                    "DOCUMENT: line, e.g. 'Power-Outage-Procedure.pdf'."
                ),
            },
            "locator": {
                "type": "string",
                "description": (
                    "A location copied from the catalogue: 'page 7', "
                    "'pages 93-103', or \"sheet 'CT'\". Narrow a wide page "
                    "range if the result is truncated."
                ),
            },
            "columns": {
                "type": "string",
                "description": (
                    "Operating logs only. Text matched against column names to "
                    "return the daily rows of those columns instead of the "
                    "column index — e.g. 'Raw Water | Turbidity'. Omit it "
                    "first: the index already carries each column's min and "
                    "max, so an extreme needs no rows at all. A log is far too "
                    "wide to return in full."
                ),
            },
        },
        "required": ["document", "locator"],
    },
)
def fetch_document_part(document, locator, columns=None, plant_id="demo"):
    return fetch(document, locator, columns=columns, plant=plant_id)
