# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Retrofitted to the quantity pattern from calc_ct.py: every quantity comes in
# as {"value": ..., "unit": ...} and goes through units.parse() before any
# arithmetic. That is what makes a swapped flow/dose argument raise instead of
# silently returning a plausible wrong feed rate.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

# Assumed when the operator does not state them: neat chemical at water
# density. Each assumption that fires adds a caveat to the result, because a
# silently-assumed strength or SG moves the feed rate by tens of percent.
DEFAULT_STRENGTH_PCT = 100.0
DEFAULT_SG = 1.0


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
                "description": (
                    "Percent w/w. 100 for neat chemical. Ask the operator for "
                    "the strength on the delivery ticket rather than guessing — "
                    "if omitted this defaults to 100% and the result says so."
                ),
            },
            "solution_sg": {
                "type": "number",
                "description": (
                    "Specific gravity of the solution. Ask the operator or read "
                    "it off the product data sheet — if omitted this defaults "
                    "to 1.0 (water) and the result says so."
                ),
            },
        },
        "required": ["flow", "dose"],
    },
)
def calc_chemical_feed(flow, dose, solution_strength_pct=None, solution_sg=None):
    """Chemical feed rate from flow and target dose.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py. "summary" is
    the operator-facing string and is what the model and the CLI see.

    solution_strength_pct and solution_sg default to neat chemical at water
    density. The defaults are None rather than 100.0/1.0 so that an omitted
    value can be told from an operator who actually said 100% w/w or SG 1.0 —
    only the former earns a caveat. Both resolve to the same numbers, so
    omitting them behaves exactly as before.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    q = parse(flow, "flow")
    c = parse(dose, "concentration")

    strength_assumed = solution_strength_pct is None
    sg_assumed = solution_sg is None
    if strength_assumed:
        solution_strength_pct = DEFAULT_STRENGTH_PCT
    if sg_assumed:
        solution_sg = DEFAULT_SG

    if not 0 < solution_strength_pct <= 100:
        raise ValueError("solution strength must be between 0 and 100 percent")
    if solution_sg <= 0:
        raise ValueError("solution specific gravity must be positive")

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []

    flow_mld = q.canonical * 86400 / 1e6            # L/s -> ML/d
    steps.append({
        "label": "Flow in working units",
        "formula": "Q x 86400 / 1e6",
        "substituted": f"{q.canonical:g} L/s x 86400 / 1e6",
        "value": round(flow_mld, 3),
        "unit": "ML/d",
    })

    kg_per_day = flow_mld * c.canonical              # ML/d * mg/L == kg/d
    steps.append({
        "label": "Neat chemical required",
        "formula": "Q x dose",
        "substituted": f"{flow_mld:.3g} ML/d x {c.canonical:g} mg/L",
        "value": round(kg_per_day, 2),
        "unit": "kg/day",
    })

    neat_lpd = kg_per_day / (solution_sg * 1000) * 1000
    steps.append({
        "label": "Neat chemical volume",
        "formula": "mass / solution density",
        "substituted": f"{kg_per_day:.2f} kg/day / ({solution_sg} x 1000 kg/m3) x 1000",
        "value": round(neat_lpd, 1),
        "unit": "L/day",
    })

    solution_lpd = neat_lpd / (solution_strength_pct / 100)
    steps.append({
        "label": "Solution feed rate",
        "formula": "neat volume / strength fraction",
        "substituted": f"{neat_lpd:.1f} L/day / {solution_strength_pct / 100:g}",
        "value": round(solution_lpd, 1),
        "unit": "L/day",
    })

    solution_lpm = solution_lpd / 1440
    steps.append({
        "label": "Solution feed rate per minute",
        "formula": "solution L/day / 1440",
        "substituted": f"{solution_lpd:.1f} L/day / 1440 min/day",
        "value": round(solution_lpm, 2),
        "unit": "L/min",
    })

    # Each caveat states the assumption and which way it moves the answer, so
    # the operator can tell whether the number is safe to act on. The two are
    # independent and can both fire; they push in opposite directions and do
    # not cancel.
    caveats = []
    if strength_assumed:
        caveats.append(
            f"Solution strength was not given — assumed {DEFAULT_STRENGTH_PCT:g}% "
            "w/w (neat chemical). Delivered solutions are weaker (12% sodium "
            "hypochlorite, 50% caustic soda), and a weaker solution needs MORE "
            "volume than shown. State the strength from the delivery ticket."
        )
    if sg_assumed:
        caveats.append(
            f"Specific gravity was not given — assumed {DEFAULT_SG:g} (water). "
            "Concentrated solutions are denser (12% hypochlorite ~1.17, 50% "
            "caustic soda ~1.53), and a denser solution needs LESS volume than "
            "shown. State the SG from the product data sheet."
        )

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(q, c), ""]
    out += [
        f"Neat chemical required: {kg_per_day:.2f} kg/day",
        f"Solution feed rate ({solution_strength_pct}% w/w, SG {solution_sg}): "
        f"{solution_lpd:.1f} L/day ({solution_lpm:.2f} L/min)",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (q, c)]
    conversions.append(
        f"{q.value:g} {q.unit} = {flow_mld:.3g} ML/d (working units)"
    )
    conversions += [
        f"NOTE: {n}" for n in dict.fromkeys(n for x in (q, c) for n in x.notes)
    ]

    return {
        # Byte-for-byte what this function returned before the trace existed.
        "summary": "\n".join(out),
        "result": {
            "neat_kg_per_day": round(kg_per_day, 2),
            "solution_l_per_day": round(solution_lpd, 1),
            "solution_l_per_min": round(solution_lpm, 2),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # Same case, stated in metric units.
    print(calc_chemical_feed(
        flow={"value": 10, "unit": "MLD"},
        dose={"value": 2.5, "unit": "mg/L"},
        solution_strength_pct=12.5, solution_sg=1.15,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Identical physical case stated in US units — must give the same answer.
    print(calc_chemical_feed(
        flow={"value": 2.6417, "unit": "MGD"},
        dose={"value": 2.5, "unit": "ppm"},
        solution_strength_pct=12.5, solution_sg=1.15,
    )["summary"])

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
