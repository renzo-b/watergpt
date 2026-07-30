# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Retrofitted to the quantity pattern from calc_ct.py: every quantity comes in
# as {"value": ..., "unit": ...} and goes through units.parse() before any
# arithmetic. That is what makes a swapped flow/dose argument raise instead of
# silently returning a plausible wrong feed rate.

from units import echo_all, parse, quantity_schema
from tools.registry import tool


@tool(
    name="calc_chemical_feed",
    description=(
        "Convert a target chemical dose and plant flow into a feed rate in "
        "kg/day and L/day of solution. Use for any dosing setpoint question. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow": quantity_schema("flow", "Plant flow."),
            "dose": quantity_schema("concentration", "Target chemical dose."),
            "solution_strength_pct": {
                "type": "number",
                "description": "Percent w/w. 100 for neat chemical.",
            },
            "solution_sg": {
                "type": "number",
                "description": "Specific gravity of the solution.",
            },
        },
        "required": ["flow", "dose"],
    },
)
def calc_chemical_feed(flow, dose, solution_strength_pct=100.0, solution_sg=1.0):
    """Chemical feed rate from flow and target dose."""
    # 1. Parse first. Dimension errors surface here, before any math.
    q = parse(flow, "flow")
    c = parse(dose, "concentration")

    if not 0 < solution_strength_pct <= 100:
        raise ValueError("solution strength must be between 0 and 100 percent")
    if solution_sg <= 0:
        raise ValueError("solution specific gravity must be positive")

    flow_mld = q.canonical * 86400 / 1e6            # L/s -> ML/d
    kg_per_day = flow_mld * c.canonical              # ML/d * mg/L == kg/d
    neat_lpd = kg_per_day / (solution_sg * 1000) * 1000
    solution_lpd = neat_lpd / (solution_strength_pct / 100)

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(q, c), ""]
    out += [
        f"Neat chemical required: {kg_per_day:.2f} kg/day",
        f"Solution feed rate ({solution_strength_pct}% w/w, SG {solution_sg}): "
        f"{solution_lpd:.1f} L/day ({solution_lpd / 1440:.2f} L/min)",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    from units import UnitError

    # Same case, stated in metric units.
    print(calc_chemical_feed(
        flow={"value": 10, "unit": "MLD"},
        dose={"value": 2.5, "unit": "mg/L"},
        solution_strength_pct=12.5, solution_sg=1.15,
    ))

    print("\n" + "=" * 68 + "\n")

    # Identical physical case stated in US units — must give the same answer.
    print(calc_chemical_feed(
        flow={"value": 2.6417, "unit": "MGD"},
        dose={"value": 2.5, "unit": "ppm"},
        solution_strength_pct=12.5, solution_sg=1.15,
    ))

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_chemical_feed(
            flow={"value": 2.5, "unit": "mg/L"},   # dose in the flow slot
            dose={"value": 10, "unit": "MLD"},
            solution_strength_pct=12.5, solution_sg=1.15,
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
