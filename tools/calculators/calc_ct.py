"""
calc_ct retrofitted to the quantity pattern — the template for every other
calculator.

Diff against the old signature:

    BEFORE  calc_ct(volume_m3, flow_lps, residual_mgl, temp_c, ph, baffling_factor)
            -> model must convert "1 MGD" to L/s in its head. That is
               arithmetic, which system prompt rule 2 forbids, and it is
               invisible to the eval harness.

    AFTER   calc_ct(volume, flow, residual, temperature, ph, baffling_factor)
            where each is {"value": ..., "unit": ...}
            -> model reports what the operator said; units.parse converts,
               dimension-checks, and flags ambiguity. All of it tested.

Three habits to copy into every calculator:
  1. parse() every quantity before touching it. A swapped argument raises.
  2. Lead the result with echo_all() so the conversion is visible, auditable,
     and assertable in an eval via must_include.
  3. Let UnitError propagate. The agent loop hands it back to the model, which
     fixes its arguments or asks the operator.
  4. Return {"summary": <the string>, ...} and capture each intermediate as a
     step as it is computed. The summary is what the model and the CLI see;
     the trace is for a UI that renders the working. Never ask the model to
     narrate the steps — model-generated arithmetic is untrusted.
"""

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

# CT required (mg.min/L) for 0.5-log Giardia inactivation by free chlorine,
# pH 6.0-7.5, residual <= 1.0 mg/L.
# PLACEHOLDER SUBSET — replace with the full table from the MECP Procedure for
# Disinfection of Drinking Water in Ontario, and match its interpolation rule,
# before this is shown to any operator.
CT_GIARDIA_0_5_LOG = {0.5: 27.0, 5.0: 19.0, 10.0: 14.0, 15.0: 9.0, 20.0: 7.0}


def _interp_ct(temp_c: float) -> float:
    temps = sorted(CT_GIARDIA_0_5_LOG)
    if temp_c <= temps[0]:
        return CT_GIARDIA_0_5_LOG[temps[0]]
    if temp_c >= temps[-1]:
        return CT_GIARDIA_0_5_LOG[temps[-1]]
    for lo, hi in zip(temps, temps[1:]):
        if lo <= temp_c <= hi:
            f = (temp_c - lo) / (hi - lo)
            return CT_GIARDIA_0_5_LOG[lo] + f * (
                CT_GIARDIA_0_5_LOG[hi] - CT_GIARDIA_0_5_LOG[lo]
            )
    raise ValueError("temperature out of range")


@tool(
    name="calc_ct",
    description=(
        "Calculate CT achieved versus CT required for 0.5-log Giardia "
        "inactivation by free chlorine. Use for any disinfection compliance "
        "question. Never compute CT yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "volume": quantity_schema(
                "volume", "Effective contact volume of the chamber."
            ),
            "flow": quantity_schema("flow", "Flow through the contact chamber."),
            "residual": quantity_schema(
                "concentration",
                "Free chlorine residual at the end of the contact zone.",
            ),
            "temperature": quantity_schema("temperature", "Water temperature."),
            "ph": {"type": "number", "description": "pH is dimensionless."},
            "baffling_factor": {
                "type": "number",
                "description": (
                    "0.1 unbaffled, 0.3 poor, 0.5 average, 0.7 superior, "
                    "1.0 plug flow. Ask the operator if unknown — do not assume."
                ),
            },
        },
        "required": ["volume", "flow", "residual", "temperature", "ph",
                     "baffling_factor"],
    },
)
def calc_ct(volume, flow, residual, temperature, ph, baffling_factor):
    """CT achieved vs CT required for 0.5-log Giardia inactivation.

    Returns {"summary", "result", "steps", "conversions", "caveats"}. "summary"
    is the operator-facing string, unchanged from what this returned before the
    trace was added; everything else is the same intermediates captured as they
    are computed, for a UI that renders an expandable "show working" panel.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    v = parse(volume, "volume")
    q = parse(flow, "flow")
    c = parse(residual, "concentration")
    t = parse(temperature, "temperature")

    if q.canonical <= 0:
        raise ValueError("Flow must be positive.")
    if not 0 < baffling_factor <= 1:
        raise ValueError(
            "Baffling factor must be between 0 and 1 "
            "(0.1 unbaffled, 0.3 poor, 0.5 average, 0.7 superior, 1.0 plug flow). "
            "If unknown, ask the operator about the tank's inlet/outlet "
            "configuration rather than assuming a value."
        )

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing below is recalculated for the trace — the step entries only
    # round, for display, the value the next line goes on to use.
    steps = []

    flow_m3_min = q.canonical * 60 / 1000          # L/s -> m3/min
    steps.append({
        "label": "Flow in working units",
        "formula": "Q x 60 / 1000",
        "substituted": f"{q.canonical:g} L/s x 60 / 1000",
        "value": round(flow_m3_min, 2),
        "unit": "m3/min",
    })

    theoretical_min = v.canonical / flow_m3_min
    steps.append({
        "label": "Theoretical detention time",
        "formula": "V / Q",
        "substituted": f"{v.canonical:g} m3 / {flow_m3_min:.2f} m3/min",
        "value": round(theoretical_min, 1),
        "unit": "min",
    })

    t10_min = theoretical_min * baffling_factor
    steps.append({
        "label": "T10 contact time",
        "formula": "theoretical detention time x baffling factor",
        "substituted": f"{theoretical_min:.1f} min x {baffling_factor}",
        "value": round(t10_min, 1),
        "unit": "min",
    })

    ct_actual = c.canonical * t10_min
    steps.append({
        "label": "CT achieved",
        "formula": "C x T10",
        "substituted": f"{c.canonical:g} mg/L x {t10_min:.1f} min",
        "value": round(ct_actual, 1),
        "unit": "mg.min/L",
    })

    ct_required = _interp_ct(t.canonical)
    steps.append({
        "label": "CT required (0.5-log Giardia)",
        "formula": "interpolated from the CT table",
        "substituted": f"table lookup at {t.canonical:.1f} degC, pH {ph}",
        "value": round(ct_required, 1),
        "unit": "mg.min/L",
    })

    ratio = ct_actual / ct_required
    steps.append({
        "label": "CT ratio",
        "formula": "CT achieved / CT required",
        "substituted": f"{ct_actual:.1f} / {ct_required:.1f}",
        "value": round(ratio, 2),
        "unit": "",                                # dimensionless
    })

    verdict = "PASS" if ratio >= 1.0 else "FAIL"

    caveats = []
    if ph > 7.5:
        caveats.append(
            "pH is above 7.5, which requires a different CT table than the one "
            "used here — this result understates the requirement."
        )
    if c.canonical > 1.0:
        caveats.append("Residual exceeds 1.0 mg/L, outside this table's range.")

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(v, q, c, t), ""]
    out += [
        f"Theoretical detention time: {theoretical_min:.1f} min",
        f"T10 (x baffling factor {baffling_factor}): {t10_min:.1f} min",
        f"CT achieved: {ct_actual:.1f} mg.min/L",
        f"CT required (0.5-log Giardia, {t.canonical:.1f} C, pH {ph}): "
        f"{ct_required:.1f} mg.min/L",
        f"CT ratio: {ratio:.2f} — {verdict}",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (v, q, c, t)]
    conversions.append(
        f"{q.value:g} {q.unit} = {flow_m3_min:.2f} m3/min (working units)"
    )
    conversions += [
        f"NOTE: {n}"
        for n in dict.fromkeys(n for x in (v, q, c, t) for n in x.notes)
    ]

    return {
        # Byte-for-byte what this function returned before the trace existed.
        "summary": "\n".join(out),
        "result": {
            "ct_actual": round(ct_actual, 1),
            "ct_required": round(ct_required, 1),
            "ct_ratio": round(ratio, 2),
            "verdict": verdict,
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # Same case as eval ct-calc-01, in the original metric units.
    print(calc_ct(
        volume={"value": 150, "unit": "m3"},
        flow={"value": 45, "unit": "L/s"},
        residual={"value": 0.8, "unit": "mg/L"},
        temperature={"value": 5, "unit": "degC"},
        ph=7.2, baffling_factor=0.5,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Identical physical case stated in US units — must give the same answer.
    print(calc_ct(
        volume={"value": 39626, "unit": "gal"},
        flow={"value": 713.4, "unit": "gpm"},
        residual={"value": 0.8, "unit": "ppm"},
        temperature={"value": 41, "unit": "degF"},
        ph=7.2, baffling_factor=0.5,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_ct(
            volume={"value": 45, "unit": "L/s"},      # flow in the volume slot
            flow={"value": 150, "unit": "m3"},
            residual={"value": 0.8, "unit": "mg/L"},
            temperature={"value": 5, "unit": "degC"},
            ph=7.2, baffling_factor=0.5,
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
