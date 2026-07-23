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
