# ---------------------------------------------------------------------------
# Retrieval — stubbed fixture today, pgvector tomorrow
#
# search_plant_docs used to live here as a keyword search over a two-chunk
# fixture. It is gone: the real implementation is rag/retrieval.py, backed by
# pgvector, and it is deliberately NOT registered as a tool yet. The eval
# harness calls it directly so the model's query reformulation cannot confound
# a comparison between two chunking configs. Register it here once the
# comparison is settled and the tool description can be written against
# retrieval that actually works.
# ---------------------------------------------------------------------------

from tools.registry import tool

LOGS_CHUNKS = [
    {
        "doc": "Plant Operations Log Summary 2023",
        "page": 12,
        "text": "April 2023 freshet event: raw turbidity peaked at 44 NTU. "
        "Alum dose increased from 22 to 48 mg/L over 36 hours; "
        "settled water turbidity held below 2 NTU. Alkalinity "
        "dropped to 34 mg/L as CaCO3, soda ash feed started.",
    },
]


@tool(
    name="search_plant_logs",
    wants_plant_id=True,
    description=(
        "Search this plant's dated operator log entries and event records — "
        "what actually happened here and what operators did about it. Each "
        "result is a timestamp plus the operator's note. Use this for prior "
        "occurrences of a problem, what dose or setting was used during a "
        "past event, or seasonal patterns. "
        "NOT for how a procedure is supposed to be done — this returns what "
        "was done, not what should be done. "
        "Cite the entry date when you use a result."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms."},
            "date_from": {
                "type": "string",
                "description": "ISO date (YYYY-MM-DD). Omit to search all history.",
            },
            "date_to": {"type": "string", "description": "ISO date (YYYY-MM-DD)."},
            "month": {
                "type": "integer",
                "description": (
                    "1-12. Searches this month across ALL years — use for "
                    "seasonal questions like spring runoff or summer THM peaks."
                ),
            },
        },
        "required": ["query"],
    },
)
def search_plant_logs(
    query,
    plant_id,
    date_from=None,
    date_to=None,
    month=None,
):
    """Keyword search over the fixture corpus. Replace with hybrid retrieval."""
    terms = [t.lower() for t in query.split() if len(t) > 3]
    scored = []
    for c in LOGS_CHUNKS:
        hay = (c["text"] + " " + c["doc"]).lower()
        score = sum(1 for t in terms if t in hay)
        if score:
            scored.append((score, c))
    if not scored:
        return (
            "No matching documents found for this plant. Do not fabricate "
            "plant-specific details; say the information is not in the "
            "uploaded documents."
        )
    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(
        f"[{c['doc']}, p.{c['page']}]\n{c['text']}" for _, c in scored[:3]
    )


@tool(
    name="search_manuals",
    description=(
        "Search the shared library of manufacturer documentation: equipment "
        "manuals, spec sheets, parts breakdowns, fault code tables. Covers "
        "equipment generally, not this plant specifically. "
        "Call lookup_equipment FIRST to get the exact model number, then search "
        "with it. Returns chunks with manual title and page number."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "manufacturer": {"type": "string"},
            "model": {
                "type": "string",
                "description": "Exact model string from the equipment registry or nameplate.",
            },
        },
        "required": ["query"],
    },
)
def search_manuals(query):
    return "No manuals found for this equipment"


@tool(
    name="search_regulations",
    description=(
        "Search regulations, regulatory guidance, and standards applicable to "
        "drinking water treatment: O. Reg. 170/03 and 169/03, MECP procedures "
        "and guidance documents, Health Canada drinking water guidelines. Use "
        "this for sampling frequencies, parameter limits, reporting and "
        "notification obligations, operator certification requirements, and "
        "approved procedures. "
        "This is authoritative text — quote section numbers exactly and never "
        "paraphrase a numeric limit or deadline without citing the section. "
        "NOT for how this plant does something in practice — this is the "
        "obligation, not the site procedure. Some sources are reference-only (licensed "
        "standards); for those you will get a citation and summary but no "
        "full text, and you must tell the operator to consult their copy."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search terms. Include the parameter, obligation, or "
                    "section number if known (e.g. 'THM quarterly sampling', "
                    "'Schedule 10-2', 'adverse result notification')."
                ),
            },
            "jurisdiction": {
                "type": "string",
                "enum": ["ontario", "canada_federal", "us_federal", "any"],
                "description": "Defaults to the plant's jurisdiction if omitted.",
            },
            "source_type": {
                "type": "string",
                "enum": ["regulation", "guidance", "standard", "any"],
                "description": (
                    "'regulation' is legally binding; 'guidance' is the "
                    "regulator's interpretation; 'standard' is industry "
                    "consensus and may be reference-only."
                ),
            },
        },
        "required": ["query"],
    },
)
def search_regulations(query):
    """Search for relevant regulations"""
    return "No relevant regulations for this query"
