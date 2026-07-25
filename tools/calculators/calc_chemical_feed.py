# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------

from tools.registry import tool


@tool(
    name="calc_chemical_feed",
    description=(
        "Convert a target chemical dose and plant flow into a feed rate in "
        "kg/day and L/day of solution. Use for any dosing setpoint question."
    ),
    input_schema={
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
