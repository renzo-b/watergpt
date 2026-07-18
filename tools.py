"""
Entroply tools.

Two rules that shape everything here:

1. Anything numeric is deterministic Python with a pinned formula and a unit
   test. The model extracts parameters and interprets results; it never does
   the math. This is the credibility story with a skeptical senior operator.

2. Every tool returns a STRING the model can reason about, including its
   failure modes. "No documents matched" is a useful result, not an error.

search_docs and lookup_equipment are stubs against an in-memory fixture so you
can exercise the loop today. Swap their bodies for pgvector queries later —
the schemas don't change.
"""

# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------

# CT required (mg·min/L) for 0.5-log Giardia inactivation by free chlorine,
# pH 6.0-7.5, residual <= 1.0 mg/L.
# PLACEHOLDER SUBSET — replace with the full table from the MECP Procedure for
# Disinfection of Drinking Water in Ontario before this is used for anything
# real. Interpolation below is linear in temperature, which is conservative-ish
# but not what the regulation specifies.
CT_GIARDIA_0_5_LOG = {0.5: 27.0, 5.0: 19.0, 10.0: 14.0, 15.0: 9.0, 20.0: 7.0}


def _interp_ct(temp_c: float) -> float:
    temps = sorted(CT_GIARDIA_0_5_LOG)
    if temp_c <= temps[0]:
        return CT_GIARDIA_0_5_LOG[temps[0]]
    if temp_c >= temps[-1]:
        return CT_GIARDIA_0_5_LOG[temps[-1]]
    for lo, hi in zip(temps, temps[1:]):
        if lo <= temp_c <= hi:
            f = (temp_c - lo) / (hi - lo)
            return CT_GIARDIA_0_5_LOG[lo] + f * (
                CT_GIARDIA_0_5_LOG[hi] - CT_GIARDIA_0_5_LOG[lo]
            )
    raise ValueError("temperature out of range")


def calc_ct(volume_m3, flow_lps, residual_mgl, temp_c, ph, baffling_factor):
    """CT achieved vs CT required for 0.5-log Giardia inactivation."""
    if flow_lps <= 0:
        raise ValueError("flow must be positive")
    if not 0 < baffling_factor <= 1:
        raise ValueError("baffling factor must be between 0 and 1")

    flow_m3_min = flow_lps * 60 / 1000
    theoretical_min = volume_m3 / flow_m3_min
    t10_min = theoretical_min * baffling_factor
    ct_actual = residual_mgl * t10_min
    ct_required = _interp_ct(temp_c)
    ratio = ct_actual / ct_required

    caveat = ""
    if ph > 7.5:
        caveat = (
            " NOTE: pH is above 7.5, which requires a different CT table than "
            "the one used here — this result understates the requirement."
        )
    if residual_mgl > 1.0:
        caveat += " NOTE: residual exceeds 1.0 mg/L, outside this table's range."

    return (
        f"Theoretical detention time: {theoretical_min:.1f} min\n"
        f"T10 (x baffling factor {baffling_factor}): {t10_min:.1f} min\n"
        f"CT achieved: {ct_actual:.1f} mg·min/L\n"
        f"CT required (0.5-log Giardia, {temp_c} C, pH {ph}): "
        f"{ct_required:.1f} mg·min/L\n"
        f"CT ratio: {ratio:.2f} — {'PASS' if ratio >= 1.0 else 'FAIL'}"
        f"{caveat}"
    )


def calc_chemical_feed(
    flow_mld, dose_mgl, solution_strength_pct=100.0, solution_sg=1.0
):
    """Chemical feed rate from flow and target dose."""
    if not 0 < solution_strength_pct <= 100:
        raise ValueError("solution strength must be between 0 and 100 percent")
    kg_per_day = flow_mld * dose_mgl  # ML/d * mg/L == kg/d
    neat_lpd = kg_per_day / (solution_sg * 1000) * 1000
    solution_lpd = neat_lpd / (solution_strength_pct / 100)
    return (
        f"Neat chemical required: {kg_per_day:.2f} kg/day\n"
        f"Solution feed rate ({solution_strength_pct}% w/w, SG {solution_sg}): "
        f"{solution_lpd:.1f} L/day ({solution_lpd / 1440:.2f} L/min)"
    )


# ---------------------------------------------------------------------------
# Retrieval — stubbed fixture today, pgvector tomorrow
# ---------------------------------------------------------------------------

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


def search_plant_historical_logs(
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


def search_manuals(query):
    return "No manuals found for this equipment"


def search_regulations(query):
    """Search for relevant regulations"""
    return "No relevant regulations for this query"


EQUIPMENT = [
    {
        "tag": "CP-1",
        "name": "Sodium hypochlorite metering pump",
        "manufacturer": "Grundfos",
        "model": "DDA 7.5-16 AR-PVC/V/C",
        "installed": "2011",
    },
]


def lookup_equipment(query, plant_id="demo"):
    q = query.lower()
    hits = [
        e
        for e in EQUIPMENT
        if any(q in str(v).lower() or str(v).lower() in q for v in e.values())
    ]
    if not hits:
        return "No equipment in this plant's registry matches that query."
    return "\n".join(str(e) for e in hits)


# ---------------------------------------------------------------------------
# Schemas + dispatch
# ---------------------------------------------------------------------------
# Tool descriptions are prompt engineering. When the agent picks the wrong
# tool, edit the description before you touch the system prompt.

TOOL_SCHEMAS = [
    {
        "name": "search_plant_docs",
        "description": (
            "Search documents THIS plant uploaded: O&M manuals specific to this "
            "site, SOPs, as-built drawings, design reports. Static reference "
            "material describing how the plant is built and how procedures are "
            "meant to be performed. "
            "NOT for dated operator entries or past events — use search_plant_logs. "
            "NOT for generic manufacturer documentation — use search_manuals. "
            "Returns text chunks with document title and page number for citation."
        ),
        "input_schema": {
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
    },
    {
        "name": "search_plant_historical_logs",
        "description": (
            "Search this plant's dated operator log entries and event records — "
            "what actually happened here and what operators did about it. Each "
            "result is a timestamp plus the operator's note. Use this for prior "
            "occurrences of a problem, what dose or setting was used during a "
            "past event, or seasonal patterns. "
            "NOT for how a procedure is supposed to be done — use search_plant_docs. "
            "Cite the entry date when you use a result."
        ),
        "input_schema": {
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
    },
    {
        "name": "search_manuals",
        "description": (
            "Search the shared library of manufacturer documentation: equipment "
            "manuals, spec sheets, parts breakdowns, fault code tables. Covers "
            "equipment generally, not this plant specifically. "
            "Call lookup_equipment FIRST to get the exact model number, then search "
            "with it. Returns chunks with manual title and page number."
        ),
        "input_schema": {
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
    },
    {
        "name": "search_regulations",
        "description": (
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
        "input_schema": {
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
    },
    {
        "name": "calc_ct",
        "description": (
            "Calculate CT achieved versus CT required for 0.5-log Giardia "
            "inactivation by free chlorine. Use for any disinfection "
            "compliance question. Never compute CT yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "volume_m3": {
                    "type": "number",
                    "description": "Effective contact volume in cubic metres",
                },
                "flow_lps": {
                    "type": "number",
                    "description": "Flow in litres per second",
                },
                "residual_mgl": {
                    "type": "number",
                    "description": "Free chlorine residual at the end of the contact zone, mg/L",
                },
                "temp_c": {"type": "number"},
                "ph": {"type": "number"},
                "baffling_factor": {
                    "type": "number",
                    "description": "0.1 (unbaffled) to 1.0 (plug flow). Ask the operator if unknown.",
                },
            },
            "required": [
                "volume_m3",
                "flow_lps",
                "residual_mgl",
                "temp_c",
                "ph",
                "baffling_factor",
            ],
        },
    },
    {
        "name": "calc_chemical_feed",
        "description": (
            "Convert a target chemical dose and plant flow into a feed rate in "
            "kg/day and L/day of solution. Use for any dosing setpoint question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "flow_mld": {
                    "type": "number",
                    "description": "Plant flow in megalitres per day",
                },
                "dose_mgl": {"type": "number"},
                "solution_strength_pct": {
                    "type": "number",
                    "description": "Percent w/w. 100 for neat chemical.",
                },
                "solution_sg": {
                    "type": "number",
                    "description": "Specific gravity of the solution.",
                },
            },
            "required": ["flow_mld", "dose_mgl"],
        },
    },
]

_REGISTRY = {
    "search_plant_docs": search_plant_docs,
    "search_plant_historical_logs": search_plant_historical_logs,
    "search_manuals": search_manuals,
    "search_regulations": search_regulations,
    "lookup_equipment": lookup_equipment,
    "calc_ct": calc_ct,
    "calc_chemical_feed": calc_chemical_feed,
}


def dispatch(name, tool_input, plant_id="demo"):
    fn = _REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"unknown tool: {name}")
    if name in ("search_plant_docs", "search_plant_historical_logs"):
        return fn(plant_id=plant_id, **tool_input)
    return fn(**tool_input)
