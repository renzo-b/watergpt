# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Food-to-microorganism ratio for an activated-sludge process:
#
#     F:M = (Q_WW x BOD) / (V_AB x MLVSS)
#
# The numerator is a mass per unit time (kg BOD/d), the denominator a mass
# (kg MLVSS), so F:M comes out in 1/day. Because it is a ratio of two masses
# derived the same way, it is unit-system independent — the same plant gives
# the same F:M whether stated in m3/mg/L or MGD/lb. The demo block asserts that.
#
# Volatile solids, not total solids: MLVSS is used specifically to exclude the
# inert fraction, so the denominator approximates the living biomass. Passing
# MLSS instead understates F:M by roughly the volatile fraction (typically
# 70-80%), which is why the parameter description tells the model to confirm.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

# Verified against pint in test_calc_ct_steps.py rather than remembered.
LB_PER_KG = 2.2046226218487757


@tool(
    name="calc_food_to_microorganism_ratio",
    description=(
        "Calculate the food-to-microorganism (F:M) ratio of an activated-sludge "
        "process from influent flow and BOD, reactor volume, and MLVSS. Use for "
        "F:M, organic loading, or solids-inventory questions on an aeration "
        "basin. Never compute it yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow": quantity_schema(
                "flow",
                "Wastewater flow TO the activated-sludge process. RAS and other "
                "recycle streams are not part of this flow — if the operator "
                "quotes a figure that includes return activated sludge, ask for "
                "the influent flow instead.",
            ),
            "bod": quantity_schema(
                "concentration",
                "Influent BOD concentration to the process. Ask whether it is "
                "total or soluble BOD if the operator does not say — they are "
                "not interchangeable here.",
            ),
            "reactor_volume": quantity_schema(
                "volume",
                "Total liquid volume of the biological reactors IN SERVICE. "
                "Exclude any basin that is offline, and use liquid volume "
                "rather than structural volume.",
            ),
            "mlvss": quantity_schema(
                "concentration",
                "Mixed liquor VOLATILE suspended solids in the reactor. Confirm "
                "this is MLVSS and not MLSS — volatile is typically 70-80% of "
                "total, so using MLSS understates F:M by that much. If the "
                "operator says only 'mixed liquor solids', ask which one.",
            ),
        },
        "required": ["flow", "bod", "reactor_volume", "mlvss"],
    },
)
def calc_food_to_microorganism_ratio(flow, bod, reactor_volume, mlvss):
    """F:M ratio from influent BOD load and mixed liquor volatile solids.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    q = parse(flow, "flow")
    c_bod = parse(bod, "concentration")
    v = parse(reactor_volume, "volume")
    c_mlvss = parse(mlvss, "concentration")

    if q.canonical <= 0:
        raise ValueError("Flow must be positive.")
    if c_bod.canonical <= 0:
        raise ValueError("BOD must be positive.")
    if v.canonical <= 0:
        raise ValueError("Reactor volume must be positive.")
    if c_mlvss.canonical <= 0:
        raise ValueError(
            "MLVSS must be positive — it is the denominator of F:M. A zero or "
            "negative mixed liquor volatile solids reading is an instrument or "
            "lab error, not an operating condition."
        )

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

    food_kg_d = flow_m3_d * c_bod.canonical / 1000
    steps.append({
        "label": "Food (BOD load)",
        "formula": "Q x BOD",
        "substituted": f"{flow_m3_d:.1f} m3/d x {c_bod.canonical:g} mg/L / 1000",
        "value": round(food_kg_d, 1),
        "unit": "kg BOD/day",
    })

    food_lb_d = food_kg_d * LB_PER_KG
    steps.append({
        "label": "Food in US units",
        "formula": "food x 2.20462",
        "substituted": f"{food_kg_d:.1f} kg/day x {LB_PER_KG:.5f}",
        "value": round(food_lb_d, 1),
        "unit": "lb BOD/day",
    })

    microorganisms_kg = v.canonical * c_mlvss.canonical / 1000
    steps.append({
        "label": "Microorganisms (MLVSS inventory)",
        "formula": "V x MLVSS",
        "substituted": f"{v.canonical:g} m3 x {c_mlvss.canonical:g} mg/L / 1000",
        "value": round(microorganisms_kg, 1),
        "unit": "kg MLVSS",
    })

    microorganisms_lb = microorganisms_kg * LB_PER_KG
    steps.append({
        "label": "Microorganism inventory in US units",
        "formula": "inventory x 2.20462",
        "substituted": f"{microorganisms_kg:.1f} kg x {LB_PER_KG:.5f}",
        "value": round(microorganisms_lb, 1),
        "unit": "lb MLVSS",
    })

    fm = food_kg_d / microorganisms_kg
    steps.append({
        "label": "F:M ratio",
        "formula": "food / microorganisms",
        "substituted": f"{food_kg_d:.1f} kg/day / {microorganisms_kg:.1f} kg",
        "value": round(fm, 3),
        "unit": "1/day",
    })

    caveats = []

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(q, c_bod, v, c_mlvss), ""]
    out += [
        f"Food (BOD load): {food_kg_d:.1f} kg/day ({food_lb_d:.1f} lb/day)",
        f"Microorganisms (MLVSS inventory): {microorganisms_kg:.1f} kg "
        f"({microorganisms_lb:.1f} lb)",
        f"F:M ratio: {fm:.3f} /day",
        "",
        "Reference ranges: 0.25-0.45 /day for BOD removal only, around "
        "0.10 /day or less where nitrification is required. These are typical "
        "literature values, NOT a target — the target F:M is specific to this "
        "plant's design and load, and should be set individually.",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (q, c_bod, v, c_mlvss)]
    conversions.append(f"{q.value:g} {q.unit} = {flow_m3_d:.1f} m3/d (working units)")
    conversions.append(
        "F:M is a ratio of two masses, so it is the same number in metric or "
        "US units — only the intermediate masses differ."
    )
    conversions += [
        f"NOTE: {n}"
        for n in dict.fromkeys(
            n for x in (q, c_bod, v, c_mlvss) for n in x.notes
        )
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "fm_ratio_per_day": round(fm, 3),
            "food_kg_per_day": round(food_kg_d, 1),
            "food_lb_per_day": round(food_lb_d, 1),
            "mlvss_inventory_kg": round(microorganisms_kg, 1),
            "mlvss_inventory_lb": round(microorganisms_lb, 1),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # 10 ML/d at 200 mg/L BOD into 3000 m3 of reactor at 2500 mg/L MLVSS.
    print(calc_food_to_microorganism_ratio(
        flow={"value": 10, "unit": "MLD"},
        bod={"value": 200, "unit": "mg/L"},
        reactor_volume={"value": 3000, "unit": "m3"},
        mlvss={"value": 2500, "unit": "mg/L"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Identical plant stated in US units. F:M is a mass ratio, so the number
    # must be identical even though every intermediate mass differs.
    print(calc_food_to_microorganism_ratio(
        flow={"value": 2.6417, "unit": "MGD"},
        bod={"value": 200, "unit": "ppm"},
        reactor_volume={"value": 0.79252, "unit": "MG"},
        mlvss={"value": 2500, "unit": "ppm"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_food_to_microorganism_ratio(
            flow={"value": 3000, "unit": "m3"},      # volume in the flow slot
            bod={"value": 200, "unit": "mg/L"},
            reactor_volume={"value": 10, "unit": "MLD"},
            mlvss={"value": 2500, "unit": "mg/L"},
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
