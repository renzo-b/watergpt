# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Solids loading rate (SLR) on a secondary clarifier:
#
#     SLR = (Q_WW + Q_RAS) x MLSS x conversion / SA
#
# The conversion factor in the source is unit-system bookkeeping, not physics:
# 0.001 kg/m3 per mg/L in metric, or 8.34 lb/mil.gal per mg/L in US units.
# This tool computes in metric canonically — units.parse has already converted
# whatever the operator said — and then converts the finished SLR to lb/ft2/d,
# so the 8.34 factor never has to be applied by hand.
#
# SLR is the companion to surface overflow rate: SOR is a hydraulic loading
# (flow per area, a velocity), SLR is a solids loading (mass per area per day).
# A clarifier can be within its SOR and still fail on SLR, which is why both
# exist — see calc_surface_overflow_rate.
#
# Note RAS is INCLUDED here and excluded from SOR. The solids in the return
# stream load the clarifier; they do not add to its overflow.
#
# No reference ranges are quoted. Typical design SLR values differ by clarifier
# type, sludge settleability (SVI), and jurisdiction, and inventing a threshold
# would repeat the placeholder-CT-table mistake. Add a sourced table if a
# verdict is wanted.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

# 1 kg/(m2.d) in lb/(ft2.d). Verified against pint in test_calc_ct_steps.py.
LB_FT2_PER_KG_M2 = 0.20481614362252168


@tool(
    name="calc_solids_loading_rate",
    description=(
        "Calculate the solids loading rate (SLR) on a secondary clarifier: "
        "combined wastewater plus RAS flow times MLSS, divided by clarifier "
        "surface area. Use for solids loading, clarifier solids handling, or "
        "'is my clarifier overloaded on solids' questions. For HYDRAULIC "
        "loading use calc_surface_overflow_rate instead — a clarifier can pass "
        "one and fail the other. Never compute it yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "wastewater_flow": quantity_schema(
                "flow", "Wastewater flow to the clarifier, excluding RAS."
            ),
            "ras_flow": quantity_schema(
                "flow",
                "Return activated sludge flow. Required — RAS is typically "
                "50-100% of wastewater flow, so omitting it would understate "
                "the loading badly. Ask the operator rather than assuming.",
            ),
            "mlss": quantity_schema(
                "concentration",
                "Mixed liquor suspended solids entering the clarifier. Total "
                "solids here, not volatile — SLR is a mass loading, so MLSS "
                "is correct and MLVSS is not.",
            ),
            "clarifier_area": quantity_schema(
                "area",
                "Clarifier surface area. Use the effective settling area, "
                "excluding inlet/outlet structures and the centre well.",
            ),
        },
        "required": [
            "wastewater_flow", "ras_flow", "mlss", "clarifier_area",
        ],
    },
)
def calc_solids_loading_rate(wastewater_flow, ras_flow, mlss, clarifier_area):
    """Solids loading rate on a clarifier from combined flow, MLSS, and area.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    q_ww = parse(wastewater_flow, "flow")
    q_ras = parse(ras_flow, "flow")
    c = parse(mlss, "concentration")
    a = parse(clarifier_area, "area")

    if q_ww.canonical <= 0:
        raise ValueError("Wastewater flow must be positive.")
    if q_ras.canonical < 0:
        raise ValueError("RAS flow cannot be negative.")
    if c.canonical <= 0:
        raise ValueError("MLSS must be positive.")
    if a.canonical <= 0:
        raise ValueError("Clarifier area must be positive.")

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []

    ww_m3_d = q_ww.canonical * 86.4                 # L/s -> m3/d
    ras_m3_d = q_ras.canonical * 86.4
    total_m3_d = ww_m3_d + ras_m3_d
    steps.append({
        "label": "Combined flow to clarifier",
        "formula": "Q_WW + Q_RAS",
        "substituted": f"{ww_m3_d:.1f} m3/d + {ras_m3_d:.1f} m3/d",
        "value": round(total_m3_d, 1),
        "unit": "m3/d",
    })

    solids_kg_d = total_m3_d * c.canonical / 1000
    steps.append({
        "label": "Solids load",
        "formula": "combined flow x MLSS",
        "substituted": f"{total_m3_d:.1f} m3/d x {c.canonical:g} mg/L / 1000",
        "value": round(solids_kg_d, 1),
        "unit": "kg/d",
    })

    slr_kg_m2_d = solids_kg_d / a.canonical
    steps.append({
        "label": "Solids loading rate",
        "formula": "solids load / area",
        "substituted": f"{solids_kg_d:.1f} kg/d / {a.canonical:g} m2",
        "value": round(slr_kg_m2_d, 2),
        "unit": "kg/m2/d",
    })

    slr_lb_ft2_d = slr_kg_m2_d * LB_FT2_PER_KG_M2
    steps.append({
        "label": "Solids loading rate in US units",
        "formula": "SLR x 0.204816",
        "substituted": f"{slr_kg_m2_d:.2f} kg/m2/d x {LB_FT2_PER_KG_M2:.6f}",
        "value": round(slr_lb_ft2_d, 2),
        "unit": "lb/ft2/d",
    })

    ras_pct = ras_m3_d / ww_m3_d * 100
    steps.append({
        "label": "RAS as a percentage of wastewater flow",
        "formula": "Q_RAS / Q_WW x 100",
        "substituted": f"{ras_m3_d:.1f} / {ww_m3_d:.1f} x 100",
        "value": round(ras_pct, 1),
        "unit": "%",
    })

    caveats = []

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(q_ww, q_ras, c, a), ""]
    out += [
        f"Combined flow: {total_m3_d:.1f} m3/d "
        f"(RAS is {ras_pct:.1f}% of wastewater flow)",
        f"Solids load: {solids_kg_d:.1f} kg/day",
        f"Solids loading rate: {slr_kg_m2_d:.2f} kg/m2/day "
        f"({slr_lb_ft2_d:.2f} lb/ft2/day)",
        "",
        "This is solids loading only. A clarifier can be within its solids "
        "loading and still fail on hydraulic loading — check surface overflow "
        "rate as well, with calc_surface_overflow_rate.",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (q_ww, q_ras, c, a)]
    conversions.append(
        f"combined flow = {ww_m3_d:.1f} + {ras_m3_d:.1f} = {total_m3_d:.1f} m3/d"
    )
    conversions += [
        f"NOTE: {n}"
        for n in dict.fromkeys(n for x in (q_ww, q_ras, c, a) for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "slr_kg_m2_per_day": round(slr_kg_m2_d, 2),
            "slr_lb_ft2_per_day": round(slr_lb_ft2_d, 2),
            "solids_load_kg_per_day": round(solids_kg_d, 1),
            "combined_flow_m3_per_day": round(total_m3_d, 1),
            "ras_pct_of_flow": round(ras_pct, 1),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # 10 000 m3/d wastewater, 5000 m3/d RAS, 3000 mg/L MLSS, 1500 m2 clarifier.
    print(calc_solids_loading_rate(
        wastewater_flow={"value": 10000, "unit": "m3/d"},
        ras_flow={"value": 5000, "unit": "m3/d"},
        mlss={"value": 3000, "unit": "mg/L"},
        clarifier_area={"value": 1500, "unit": "m2"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Identical physical case stated in US units — must give the same answer.
    print(calc_solids_loading_rate(
        wastewater_flow={"value": 2.6417, "unit": "MGD"},
        ras_flow={"value": 1.3209, "unit": "MGD"},
        mlss={"value": 3000, "unit": "ppm"},
        clarifier_area={"value": 16145.9, "unit": "ft2"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_solids_loading_rate(
            wastewater_flow={"value": 1500, "unit": "m2"},   # area as flow
            ras_flow={"value": 5000, "unit": "m3/d"},
            mlss={"value": 3000, "unit": "mg/L"},
            clarifier_area={"value": 10000, "unit": "m3/d"},
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
