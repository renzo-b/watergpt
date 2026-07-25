# ---------------------------------------------------------------------------
# Retrieval — stubbed fixture today, pgvector tomorrow
# ---------------------------------------------------------------------------

from tools.registry import tool

DOC_CHUNKS = [
    {
        "doc": "Grundfos DDA Metering Pump O&M Manual",
        "page": 47,
        "text": "Service kit selection: for DDA 7.5-16 models with PVDF liquid "
        "end, order service kit 98546712 (diaphragm, valve balls, "
        "seats, O-rings). Replace diaphragm every 8000 operating "
        "hours or upon leak detection at the drain port.",
    },
    {
        "doc": "Plant SOP-14 Filter Media Inspection",
        "page": 3,
        "text": "Filter drain-down: close influent valve FV-301, open waste "
        "valve WV-303, allow level to drop to 150 mm above media "
        "before isolating backwash supply. Confirm lockout of "
        "backwash pump BP-2 prior to entry.",
    },
]


@tool(
    name="search_plant_docs",
    wants_plant_id=True,
    description=(
        "Search documents THIS plant uploaded: O&M manuals specific to this "
        "site, SOPs, as-built drawings, design reports. Static reference "
        "material describing how the plant is built and how procedures are "
        "meant to be performed. "
        "NOT for dated operator entries or past events — use search_plant_logs. "
        "NOT for generic manufacturer documentation — use search_manuals. "
        "Returns text chunks with document title and page number for citation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search terms. Include equipment tags (e.g. 'FV-301'), "
                    "model numbers, or fault codes verbatim if known."
                ),
            },
            "doc_type": {
                "type": "string",
                "enum": ["sop", "manual", "drawing", "any"],
                "description": "Narrow the corpus. Omit or use 'any' if unsure.",
            },
        },
        "required": ["query"],
    },
)
def search_plant_docs(query, plant_id, doc_type=None):
    """Keyword search over the fixture corpus. Replace with hybrid retrieval."""
    terms = [t.lower() for t in query.split() if len(t) > 3]
    scored = []
    for c in DOC_CHUNKS:
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
        "NOT for how a procedure is supposed to be done — use search_plant_docs. "
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
        "NOT for how this plant does something in practice — use "
        "search_plant_docs. Some sources are reference-only (licensed "
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
