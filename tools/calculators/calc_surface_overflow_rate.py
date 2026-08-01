# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Surface overflow rate (SOR), a.k.a. surface loading rate or overflow rate:
# flow divided by the settling surface area of the tank. Follows the quantity
# and trace patterns from calc_ct.py — see tools/calculators/__init__.py for
# the return shape.
#
# Note SOR is dimensionally a velocity: m3/(m2.d) reduces to m/d. That is not a
# coincidence — it is the upflow velocity a particle must out-settle, which is
# why the number is directly comparable to a particle settling velocity.
#
# This tool deliberately does NOT judge the result against design guidance.
# Typical ranges differ by process (primary vs secondary clarifier, DAF, plate
# settler) and by jurisdiction, and inventing a threshold here would be the
# same mistake as a placeholder CT table presented as authoritative.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

# 1 m3/(m2.d) in gpd/ft2. Verified against pint in test_calc_ct_steps.py.
GPD_FT2_PER_M_PER_D = 24.54238674711116


@tool(
    name="calc_surface_overflow_rate",
    description=(
        "Calculate the surface overflow rate (surface loading rate) of a "
        "clarifier, sedimentation basin, or settling tank: flow divided by "
        "surface area. Use for any overflow rate, surface loading, or "
        "'is this clarifier overloaded' question. Never compute it yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow": quantity_schema(
                "flow", "Flow through the tank. Ask whether recycle streams "
                "are included — they change the answer and are often omitted."
            ),
            "area": quantity_schema(
                "area",
                "Effective settling surface area at the water surface. Exclude "
                "inlet/outlet structures and, for a circular clarifier, the "
                "centre well. If the operator gives tank dimensions instead, "
                "ask them for the area rather than multiplying it out.",
            ),
        },
        "required": ["flow", "area"],
    },
)
def calc_surface_overflow_rate(flow, area):
    """Surface overflow rate from flow and settling area.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    q = parse(flow, "flow")
    a = parse(area, "area")

    if q.canonical <= 0:
        raise ValueError("Flow must be positive.")
    if a.canonical <= 0:
        raise ValueError("Surface area must be positive.")

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []

    flow_m3_d = q.canonical * 86.4                  # L/s -> m3/d
    steps.append({
        "label": "Flow in working units",
        "formula": "Q x 86.4",
        "substituted": f"{q.canonical:g} L/s x 86.4",
        "value": round(flow_m3_d, 1),
        "unit": "m3/d",
    })

    sor_m_d = flow_m3_d / a.canonical
    steps.append({
        "label": "Surface overflow rate",
        "formula": "Q / A",
        "substituted": f"{flow_m3_d:.1f} m3/d / {a.canonical:g} m2",
        "value": round(sor_m_d, 2),
        "unit": "m3/m2/d",
    })

    sor_m_h = sor_m_d / 24
    steps.append({
        "label": "Surface overflow rate per hour",
        "formula": "SOR / 24",
        "substituted": f"{sor_m_d:.2f} m/d / 24 h/d",
        "value": round(sor_m_h, 3),
        "unit": "m/h",
    })

    sor_gpd_ft2 = sor_m_d * GPD_FT2_PER_M_PER_D
    steps.append({
        "label": "Surface overflow rate in US units",
        "formula": "SOR x 24.5424",
        "substituted": f"{sor_m_d:.2f} m3/m2/d x {GPD_FT2_PER_M_PER_D:.4f}",
        "value": round(sor_gpd_ft2, 1),
        "unit": "gpd/ft2",
    })

    caveats = []

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(q, a), ""]
    out += [
        f"Flow: {flow_m3_d:.1f} m3/d",
        f"Surface overflow rate: {sor_m_d:.2f} m3/m2/d (= {sor_m_d:.2f} m/d)",
        f"  {sor_m_h:.3f} m/h",
        f"  {sor_gpd_ft2:.1f} gpd/ft2",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (q, a)]
    conversions.append(f"{q.value:g} {q.unit} = {flow_m3_d:.1f} m3/d (working units)")
    conversions.append("m3/m2/d is dimensionally m/d — an upflow velocity")
    conversions += [
        f"NOTE: {n}" for n in dict.fromkeys(n for x in (q, a) for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "sor_m3_m2_d": round(sor_m_d, 2),
            "sor_m_per_h": round(sor_m_h, 3),
            "sor_gpd_ft2": round(sor_gpd_ft2, 1),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # 30 m diameter circular clarifier (707 m2) at 45 L/s.
    print(calc_surface_overflow_rate(
        flow={"value": 45, "unit": "L/s"},
        area={"value": 707, "unit": "m2"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Identical physical case stated in US units — must give the same answer.
    print(calc_surface_overflow_rate(
        flow={"value": 1.0271, "unit": "MGD"},
        area={"value": 7610, "unit": "ft2"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_surface_overflow_rate(
            flow={"value": 707, "unit": "m2"},      # area in the flow slot
            area={"value": 45, "unit": "L/s"},
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
