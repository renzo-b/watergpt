# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Hydraulic retention time (HRT), a.k.a. hydraulic detention time: tank volume
# divided by flow. Follows the quantity and trace patterns from calc_ct.py —
# see tools/calculators/__init__.py for the return shape.
#
# This is the THEORETICAL retention time. Real tanks short-circuit, so the time
# an average parcel of water actually spends in the tank is less. Where that
# distinction decides the answer — disinfection contact time — use calc_ct,
# which applies a baffling factor to get T10. This tool does not, because for
# an aeration basin, EQ tank, or digester the theoretical figure is the one the
# process design is stated in.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool


@tool(
    name="calc_hydraulic_retention_time",
    description=(
        "Calculate hydraulic retention time (HRT / detention time) of a tank: "
        "volume divided by flow. Use for aeration basins, equalisation tanks, "
        "digesters, contact tanks, or any 'how long does water sit in this "
        "tank' question. For DISINFECTION contact time use calc_ct instead — "
        "it applies a baffling factor, which this does not. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "volume": quantity_schema(
                "volume",
                "Working liquid volume of the tank, not the structural volume. "
                "If the tank runs at a variable level, ask which level applies.",
            ),
            "flow": quantity_schema(
                "flow",
                "Flow through the tank. Ask whether recycle or return streams "
                "are included — for an aeration basin, RAS often is not, and "
                "including it changes the answer substantially.",
            ),
        },
        "required": ["volume", "flow"],
    },
)
def calc_hydraulic_retention_time(volume, flow):
    """Theoretical hydraulic retention time from tank volume and flow.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    v = parse(volume, "volume")
    q = parse(flow, "flow")

    if q.canonical <= 0:
        raise ValueError("Flow must be positive.")
    if v.canonical <= 0:
        raise ValueError("Volume must be positive.")

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []

    flow_m3_h = q.canonical * 3.6                   # L/s -> m3/h
    steps.append({
        "label": "Flow in working units",
        "formula": "Q x 3.6",
        "substituted": f"{q.canonical:g} L/s x 3.6",
        "value": round(flow_m3_h, 2),
        "unit": "m3/h",
    })

    hrt_h = v.canonical / flow_m3_h
    steps.append({
        "label": "Hydraulic retention time",
        "formula": "V / Q",
        "substituted": f"{v.canonical:g} m3 / {flow_m3_h:.2f} m3/h",
        "value": round(hrt_h, 2),
        "unit": "h",
    })

    hrt_min = hrt_h * 60
    steps.append({
        "label": "HRT in minutes",
        "formula": "HRT x 60",
        "substituted": f"{hrt_h:.2f} h x 60 min/h",
        "value": round(hrt_min, 1),
        "unit": "min",
    })

    hrt_d = hrt_h / 24
    steps.append({
        "label": "HRT in days",
        "formula": "HRT / 24",
        "substituted": f"{hrt_h:.2f} h / 24 h/d",
        "value": round(hrt_d, 3),
        "unit": "d",
    })

    caveats = []

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(v, q), ""]
    out += [
        f"Hydraulic retention time: {hrt_h:.2f} h",
        f"  {hrt_min:.1f} min",
        f"  {hrt_d:.3f} d",
        "",
        "Theoretical detention time — assumes ideal plug flow. A real tank "
        "short-circuits, so actual contact time is shorter. For disinfection "
        "compliance use calc_ct, which applies a baffling factor.",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (v, q)]
    conversions.append(f"{q.value:g} {q.unit} = {flow_m3_h:.2f} m3/h (working units)")
    conversions += [
        f"NOTE: {n}" for n in dict.fromkeys(n for x in (v, q) for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "hrt_h": round(hrt_h, 2),
            "hrt_min": round(hrt_min, 1),
            "hrt_d": round(hrt_d, 3),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # 5000 m3 aeration basin at 10 ML/d — around 12 h, a typical design point.
    print(calc_hydraulic_retention_time(
        volume={"value": 5000, "unit": "m3"},
        flow={"value": 10, "unit": "MLD"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Identical physical case stated in US units — must give the same answer.
    print(calc_hydraulic_retention_time(
        volume={"value": 1.3209, "unit": "MG"},
        flow={"value": 2.6417, "unit": "MGD"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_hydraulic_retention_time(
            volume={"value": 10, "unit": "MLD"},     # flow in the volume slot
            flow={"value": 5000, "unit": "m3"},
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
