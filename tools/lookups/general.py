from tools.registry import tool

EQUIPMENT = [
    {
        "tag": "CP-1",
        "name": "Sodium hypochlorite metering pump",
        "manufacturer": "Grundfos",
        "model": "DDA 7.5-16 AR-PVC/V/C",
        "installed": "2011",
    },
]


@tool(
    name="lookup_equipment",
    description=(
        "Look up an asset in THIS plant's equipment registry by tag, name, "
        "manufacturer, or model. Call this FIRST whenever a question names "
        "or shows a piece of equipment, so you can then search manuals with "
        "the exact model number rather than a description. Returns the "
        "registry entry: tag, name, manufacturer, model, install year."
    ),
    input_schema={
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
)
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
