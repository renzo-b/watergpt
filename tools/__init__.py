from .calculators import *
from .lookups import *
from .retrievals import *

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
        "name": "search_plant_logs",
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
        "name": "lookup_equipment",
        "description": (
            "Look up an asset in THIS plant's equipment registry by tag, name, "
            "manufacturer, or model. Call this FIRST whenever a question names "
            "or shows a piece of equipment, so you can then search manuals with "
            "the exact model number rather than a description. Returns the "
            "registry entry: tag, name, manufacturer, model, install year."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Equipment tag (e.g. 'CP-1'), common name, "
                        "manufacturer, or model string read off a nameplate."
                    ),
                }
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
    "search_plant_logs": search_plant_logs,
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
    if name in ("search_plant_docs", "search_plant_logs"):
        return fn(plant_id=plant_id, **tool_input)
    return fn(**tool_input)
