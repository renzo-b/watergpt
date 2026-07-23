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


def search_manuals(query):
    return "No manuals found for this equipment"


def search_regulations(query):
    """Search for relevant regulations"""
    return "No relevant regulations for this query"
