# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# RO normalization: reverse osmosis performance corrected to reference
# conditions (25 C, baseline net driving pressure) following the ASTM D4516
# approach, then compared against a baseline to see whether standard cleaning
# triggers are met.
#
# Why this is a tool and not prompt text: operators routinely compare RAW
# permeate flow readings and reach wrong conclusions, because a 10 C
# temperature swing alone moves flow by roughly 25%. The correction is
# deterministic and belongs in code.
#
# The comparison is a ratio of two states, so it is unit-independent — the
# quantities are parsed for dimension checking and for the conversion echo, not
# because the arithmetic needs a particular unit. What the dimension check buys
# is that a pressure in the TDS slot raises instead of normalizing against a
# reading that means something else entirely.
#
# KNOWN LIMITATIONS — each one is emitted as a caveat, not just documented here:
#   1. dP normalization uses permeate flow as a proxy for feed flow (exponent
#      1.5). Proper practice corrects on feed flow. At constant recovery the
#      proxy is a scale factor; it drifts when recovery changes, so a recovery
#      difference between the two states escalates the caveat.
#   2. Osmotic pressure uses the standard TDS approximation — adequate for
#      brackish municipal feeds, poor for high-sulphate or seawater.
#   3. The 10-15% / 15% triggers are industry rules of thumb. A plant's O&M
#      manual or the membrane manufacturer's criteria override them.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

import math

from units import UnitError, echo, echo_all, parse, quantity_schema
from tools.registry import tool

# Cleaning triggers, in percent change from baseline. Industry rules of thumb —
# see limitation 3. Named so a plant's own criteria can be swapped in here
# rather than hunted for in the middle of the flag logic.
FLOW_TRIGGER_PCT = -10.0        # normalized permeate flow decline
FLOW_TRIGGER_HARD_PCT = -15.0
DP_TRIGGER_PCT = 15.0           # normalized differential pressure rise
SP_TRIGGER_PCT = 10.0           # normalized salt passage rise
SP_TRIGGER_HARD_PCT = 15.0

# Temperature correction coefficients for polyamide membranes, either side of
# the 25 C reference.
TCF_COEFF_WARM = 2640.0
TCF_COEFF_COLD = 3020.0

# dP scales roughly with feed flow to this power.
DP_FLOW_EXPONENT = 1.5

# Above this feed TDS the TDS-based osmotic approximation is out of its
# brackish-water range — see limitation 2.
OSMOTIC_TDS_LIMIT_MG_L = 10000.0

# A recovery difference wider than this makes the permeate-flow proxy for feed
# flow unreliable, because recovery is what ties the two together.
RECOVERY_DRIFT_LIMIT_PCT = 2.0

# The readings that make up one operating state, with the dimension each is
# parsed as. Recovery is a percentage, so it is handled separately.
STATE_FIELDS = (
    ("permeate_flow", "flow"),
    ("feed_pressure", "pressure"),
    ("concentrate_pressure", "pressure"),
    ("permeate_pressure", "pressure"),
    ("feed_tds", "concentration"),
    ("permeate_tds", "concentration"),
    ("temperature", "temperature"),
)


def _tcf(temp_c: float) -> float:
    """Temperature correction factor to 25 C for polyamide membranes.

    Returns the multiplier that converts measured flow at temp_c to the
    equivalent flow at 25 C. Cold water permeates less, so TCF > 1 below 25 C.
    """
    coeff = TCF_COEFF_WARM if temp_c >= 25.0 else TCF_COEFF_COLD
    return math.exp(coeff * (1.0 / (273.15 + temp_c) - 1.0 / 298.15))


def _osmotic_bar(tds_mgl: float, temp_c: float) -> float:
    """Osmotic pressure from TDS. Standard approximation; brackish-water range."""
    psi = 0.0385 * tds_mgl * (temp_c + 320.0) / (1000.0 - tds_mgl / 1000.0)
    return psi / 14.5038


def _parse_state(state, label):
    """Parse one operating state's readings. Raises before any math happens."""
    if not isinstance(state, dict):
        raise UnitError(
            f"{label}: expected an object holding this state's readings, got "
            f"{state!r}."
        )

    missing = [n for n, _ in STATE_FIELDS if state.get(n) is None]
    if state.get("recovery_pct") is None:
        missing.append("recovery_pct")
    if missing:
        raise ValueError(
            f"{label}: missing {', '.join(missing)}. Normalization needs a "
            "complete set of readings for both states. Ask the operator for "
            "the missing ones — do not assume, estimate, or substitute design "
            "values."
        )

    qs = {}
    for name, dimension in STATE_FIELDS:
        try:
            qs[name] = parse(state[name], dimension)
        except UnitError as e:
            raise UnitError(f"{label}.{name}: {e}")
    return qs


def _derive(qs, recovery_pct, label, steps):
    """Derive dP, TCF, NDP and salt passage for one state, tracing as it goes.

    Every intermediate is appended as a step immediately after it is computed;
    nothing here is recalculated for the trace.
    """
    recovery = recovery_pct / 100.0
    if not 0.0 < recovery < 1.0:
        raise ValueError(
            f"{label}: recovery must be between 0 and 100 percent "
            f"(got {recovery_pct}). It is permeate flow divided by feed flow."
        )

    feed_p = qs["feed_pressure"].canonical
    conc_p = qs["concentrate_pressure"].canonical
    perm_p = qs["permeate_pressure"].canonical
    feed_tds = qs["feed_tds"].canonical
    perm_tds = qs["permeate_tds"].canonical
    temp_c = qs["temperature"].canonical
    flow = qs["permeate_flow"].to("m3/h")

    if flow <= 0:
        raise ValueError(
            f"{label}: permeate flow must be positive — the normalized flow "
            "comparison divides by it."
        )
    if feed_tds <= 0:
        raise ValueError(f"{label}: feed TDS must be positive.")
    if perm_tds <= 0:
        raise ValueError(
            f"{label}: permeate TDS must be positive. Salt passage is measured "
            "against it, so a zero reading is an instrument or lab error, not "
            "an operating condition."
        )

    dp = feed_p - conc_p
    if dp < 0:
        raise ValueError(
            f"{label}: concentrate pressure exceeds feed pressure — check the "
            "readings."
        )
    if dp == 0:
        raise ValueError(
            f"{label}: feed and concentrate pressures are equal, so the "
            "differential pressure across the train is zero. That is a reading "
            "error on a running train, and the dP comparison divides by it."
        )
    steps.append({
        "label": f"{label.capitalize()} differential pressure",
        "formula": "feed pressure - concentrate pressure",
        "substituted": f"{feed_p:g} bar - {conc_p:g} bar",
        "value": round(dp, 3),
        "unit": "bar",
    })

    tcf = _tcf(temp_c)
    steps.append({
        "label": f"{label.capitalize()} temperature correction factor",
        "formula": "exp(coeff x (1 / (273.15 + T) - 1 / 298.15))",
        "substituted": (
            f"exp({TCF_COEFF_WARM if temp_c >= 25.0 else TCF_COEFF_COLD:g} x "
            f"(1 / (273.15 + {temp_c:g}) - 1 / 298.15))"
        ),
        "value": round(tcf, 4),
        "unit": "",                                # dimensionless multiplier
    })

    # Log-mean concentration factor across the element train: feed water is
    # progressively concentrated as permeate is removed, so the membrane sees
    # more than the feed TDS.
    conc_factor = math.log(1.0 / (1.0 - recovery)) / recovery
    c_avg = feed_tds * conc_factor
    steps.append({
        "label": f"{label.capitalize()} average concentrate-side TDS",
        "formula": "feed TDS x ln(1 / (1 - recovery)) / recovery",
        "substituted": (
            f"{feed_tds:g} mg/L x ln(1 / (1 - {recovery:g})) / {recovery:g}"
        ),
        "value": round(c_avg, 1),
        "unit": "mg/L",
    })

    pf_avg = (feed_p + conc_p) / 2.0
    osmotic_feed = _osmotic_bar(c_avg, temp_c)
    osmotic_perm = _osmotic_bar(perm_tds, temp_c)
    ndp = pf_avg - perm_p - (osmotic_feed - osmotic_perm)
    steps.append({
        "label": f"{label.capitalize()} net driving pressure",
        "formula": (
            "(feed P + concentrate P) / 2 - permeate P "
            "- (osmotic P feed side - osmotic P permeate)"
        ),
        "substituted": (
            f"{pf_avg:.2f} bar - {perm_p:g} bar - "
            f"({osmotic_feed:.2f} bar - {osmotic_perm:.2f} bar)"
        ),
        "value": round(ndp, 2),
        "unit": "bar",
    })
    if ndp <= 0:
        raise ValueError(
            f"{label}: net driving pressure is zero or negative — feed pressure "
            f"cannot overcome osmotic pressure at these readings. Verify feed "
            f"TDS, pressures, and recovery."
        )

    sp = perm_tds / c_avg * 100.0
    steps.append({
        "label": f"{label.capitalize()} salt passage",
        "formula": "permeate TDS / average concentrate-side TDS x 100",
        "substituted": f"{perm_tds:g} mg/L / {c_avg:.1f} mg/L x 100",
        "value": round(sp, 3),
        "unit": "%",
    })

    return {"dp": dp, "ndp": ndp, "tcf": tcf, "c_avg": c_avg, "sp": sp, "q": flow}


# ---------------------------------------------------------------------------
# Nested state objects force a complete baseline. Without that, the model will
# happily normalize against invented reference conditions.
# ---------------------------------------------------------------------------

def _state_schema(which: str) -> dict:
    return {
        "type": "object",
        "description": f"The {which} operating state — all eight readings.",
        "properties": {
            "permeate_flow": quantity_schema(
                "flow", f"Permeate flow in the {which} state."
            ),
            "feed_pressure": quantity_schema("pressure", "Feed pressure."),
            "concentrate_pressure": quantity_schema(
                "pressure", "Concentrate (reject) pressure."
            ),
            "permeate_pressure": quantity_schema(
                "pressure",
                "Permeate back-pressure. Often near 0. Ask the operator "
                "rather than assuming.",
            ),
            "feed_tds": quantity_schema(
                "concentration", "Feed TDS, or conductivity converted to TDS."
            ),
            "permeate_tds": quantity_schema("concentration", "Permeate TDS."),
            "temperature": quantity_schema("temperature", "Feed temperature."),
            "recovery_pct": {
                "type": "number",
                "description": (
                    "Permeate flow divided by feed flow, as a percentage."
                ),
            },
        },
        "required": [name for name, _ in STATE_FIELDS] + ["recovery_pct"],
    }


@tool(
    name="calc_ro_normalization",
    description=(
        "Normalize RO performance to reference conditions (25 C, baseline net "
        "driving pressure) per the ASTM D4516 approach, and compare current "
        "performance against a baseline to determine whether cleaning triggers "
        "are met. Use for ANY question about whether RO membranes need "
        "cleaning, or whether performance has declined. Never compute it "
        "yourself. "
        "Requires a COMPLETE baseline set — normally the readings taken "
        "immediately after the last successful cleaning, or at commissioning. "
        "If the operator has not provided baseline values, ASK for them; do not "
        "assume, estimate, or substitute design values. Raw uncorrected readings "
        "cannot answer this question, because temperature and pressure changes "
        "alone shift permeate flow substantially. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "current": _state_schema("current"),
            "baseline": _state_schema("baseline"),
        },
        "required": ["current", "baseline"],
    },
)
def calc_ro_normalization(current, baseline):
    """Compare normalized RO performance against baseline cleaning triggers.

    baseline is normally the readings taken immediately after the last
    successful cleaning, or at commissioning. Both states need all eight
    readings.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    cur_q = _parse_state(current, "current")
    base_q = _parse_state(baseline, "baseline")

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing below is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []
    caveats = []

    base = _derive(base_q, baseline["recovery_pct"], "baseline", steps)
    cur = _derive(cur_q, current["recovery_pct"], "current", steps)

    # 2. Normalize both states to 25 C and the baseline net driving pressure.
    qn_base = base["q"] * base["tcf"]
    steps.append({
        "label": "Normalized permeate flow, baseline",
        "formula": "permeate flow x TCF",
        "substituted": f"{base['q']:.4g} m3/h x {base['tcf']:.4f}",
        "value": round(qn_base, 2),
        "unit": "m3/h",
    })

    qn_cur = cur["q"] * cur["tcf"] * (base["ndp"] / cur["ndp"])
    steps.append({
        "label": "Normalized permeate flow, current",
        "formula": "permeate flow x TCF x (baseline NDP / current NDP)",
        "substituted": (
            f"{cur['q']:.4g} m3/h x {cur['tcf']:.4f} x "
            f"({base['ndp']:.2f} bar / {cur['ndp']:.2f} bar)"
        ),
        "value": round(qn_cur, 2),
        "unit": "m3/h",
    })

    d_flow = (qn_cur - qn_base) / qn_base * 100.0
    steps.append({
        "label": "Change in normalized permeate flow",
        "formula": "(current - baseline) / baseline x 100",
        "substituted": f"({qn_cur:.2f} - {qn_base:.2f}) / {qn_base:.2f} x 100",
        "value": round(d_flow, 1),
        "unit": "%",
    })

    # dP corrected for the flow change; see limitation 1.
    flow_ratio = base["q"] / cur["q"]
    dpn_cur = cur["dp"] * flow_ratio ** DP_FLOW_EXPONENT
    steps.append({
        "label": "Normalized differential pressure, current",
        "formula": "current dP x (baseline flow / current flow) ^ 1.5",
        "substituted": (
            f"{cur['dp']:.3g} bar x ({base['q']:.4g} / {cur['q']:.4g}) ^ "
            f"{DP_FLOW_EXPONENT:g}"
        ),
        "value": round(dpn_cur, 3),
        "unit": "bar",
    })

    d_dp = (dpn_cur - base["dp"]) / base["dp"] * 100.0
    steps.append({
        "label": "Change in normalized differential pressure",
        "formula": "(current - baseline) / baseline x 100",
        "substituted": f"({dpn_cur:.3g} - {base['dp']:.3g}) / {base['dp']:.3g} x 100",
        "value": round(d_dp, 1),
        "unit": "%",
    })

    d_sp = (cur["sp"] - base["sp"]) / base["sp"] * 100.0
    steps.append({
        "label": "Change in salt passage",
        "formula": "(current - baseline) / baseline x 100",
        "substituted": f"({cur['sp']:.3g} - {base['sp']:.3g}) / {base['sp']:.3g} x 100",
        "value": round(d_sp, 1),
        "unit": "%",
    })

    # 3. Triggers. Thresholds are the module constants, not literals, so a
    #    plant's own criteria are changed in one place.
    flags = []
    if d_flow <= FLOW_TRIGGER_HARD_PCT:
        flags.append(
            f"Normalized permeate flow down {abs(d_flow):.0f}% — EXCEEDS the "
            f"{abs(FLOW_TRIGGER_PCT):.0f}-{abs(FLOW_TRIGGER_HARD_PCT):.0f}% "
            "cleaning trigger."
        )
    elif d_flow <= FLOW_TRIGGER_PCT:
        flags.append(
            f"Normalized permeate flow down {abs(d_flow):.0f}% — at the "
            f"{abs(FLOW_TRIGGER_PCT):.0f}-{abs(FLOW_TRIGGER_HARD_PCT):.0f}% "
            "cleaning trigger."
        )
    if d_dp >= DP_TRIGGER_PCT:
        flags.append(
            f"Normalized dP up {d_dp:.0f}% — EXCEEDS the "
            f"{DP_TRIGGER_PCT:.0f}% cleaning trigger."
        )
    if d_sp >= SP_TRIGGER_HARD_PCT:
        flags.append(
            f"Normalized salt passage up {d_sp:.0f}% — EXCEEDS the "
            f"{SP_TRIGGER_PCT:.0f}-{SP_TRIGGER_HARD_PCT:.0f}% trigger."
        )
    elif d_sp >= SP_TRIGGER_PCT:
        flags.append(
            f"Normalized salt passage up {d_sp:.0f}% — at the "
            f"{SP_TRIGGER_PCT:.0f}-{SP_TRIGGER_HARD_PCT:.0f}% trigger."
        )

    # Pattern interpretation drives cleaning chemistry choice.
    if d_dp >= DP_TRIGGER_PCT and d_flow > FLOW_TRIGGER_PCT:
        flags.append(
            "dP-dominant pattern: suggests particulate or biological fouling "
            "rather than scaling."
        )
    if d_sp >= SP_TRIGGER_HARD_PCT and d_dp < DP_TRIGGER_PCT:
        flags.append(
            "Salt-passage-dominant pattern: suggests scaling or membrane "
            "damage rather than fouling."
        )

    # 4. The limitations, as caveats rather than documentation nobody reads.
    recovery_gap = abs(current["recovery_pct"] - baseline["recovery_pct"])
    if recovery_gap > RECOVERY_DRIFT_LIMIT_PCT:
        caveats.append(
            f"The dP comparison is UNRELIABLE here: recovery moved from "
            f"{baseline['recovery_pct']:g}% to {current['recovery_pct']:g}%. "
            "dP is normalized on permeate flow as a proxy for feed flow, which "
            "only holds at constant recovery. Re-take the current readings at "
            "the baseline recovery, or normalize on measured feed flow."
        )
    else:
        caveats.append(
            "dP is normalized on permeate flow as a proxy for feed flow "
            "(exponent 1.5). That holds at constant recovery, which these "
            "readings are; proper practice corrects on measured feed flow."
        )

    high_tds = max(cur_q["feed_tds"].canonical, base_q["feed_tds"].canonical)
    if high_tds > OSMOTIC_TDS_LIMIT_MG_L:
        caveats.append(
            f"Feed TDS of {high_tds:g} mg/L is above the "
            f"{OSMOTIC_TDS_LIMIT_MG_L:g} mg/L brackish range where the TDS "
            "osmotic-pressure approximation used here is adequate. For "
            "seawater or high-sulphate feeds the NDP, and so the normalized "
            "flow, is approximate."
        )

    caveats.append(
        "The cleaning triggers used here are industry rules of thumb. Defer to "
        "the membrane manufacturer's dP limit and this plant's cleaning SOP."
    )

    # 5. Conversions first, so the operator sees what was assumed.
    order = [name for name, _ in STATE_FIELDS]
    out = [
        "Inputs (current):",
        echo_all(*(cur_q[n] for n in order)),
        f"  recovery {current['recovery_pct']:g}%",
        "Inputs (baseline):",
        echo_all(*(base_q[n] for n in order)),
        f"  recovery {baseline['recovery_pct']:g}%",
        "",
        f"Normalized to 25 C and baseline NDP ({base['ndp']:.1f} bar).",
        f"  Permeate flow:  {qn_base:.1f} -> {qn_cur:.1f} m3/h  ({d_flow:+.1f}%)",
        f"  Differential P: {base['dp']:.2f} -> {dpn_cur:.2f} bar  ({d_dp:+.1f}%)",
        f"  Salt passage:   {base['sp']:.2f} -> {cur['sp']:.2f} %  ({d_sp:+.1f}%)",
        f"  TCF current {cur['tcf']:.3f}, baseline {base['tcf']:.3f}; "
        f"NDP current {cur['ndp']:.1f} bar",
    ]
    out += (
        ["  ! " + f for f in flags]
        if flags
        else ["  No cleaning trigger exceeded. Continue trending."]
    )
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(cur_q[n]) for n in order] + [echo(base_q[n]) for n in order]
    conversions.append(
        f"permeate flow {cur_q['permeate_flow'].value:g} "
        f"{cur_q['permeate_flow'].unit} = {cur['q']:.4g} m3/h (working units)"
    )
    conversions += [
        f"NOTE: {n}"
        for n in dict.fromkeys(
            n
            for q in list(cur_q.values()) + list(base_q.values())
            for n in q.notes
        )
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "normalized_flow_change_pct": round(d_flow, 1),
            "normalized_dp_change_pct": round(d_dp, 1),
            "salt_passage_change_pct": round(d_sp, 1),
            "normalized_flow_baseline_m3_per_h": round(qn_base, 2),
            "normalized_flow_current_m3_per_h": round(qn_cur, 2),
            "normalized_dp_current_bar": round(dpn_cur, 3),
            "dp_baseline_bar": round(base["dp"], 3),
            "salt_passage_baseline_pct": round(base["sp"], 3),
            "salt_passage_current_pct": round(cur["sp"], 3),
            "ndp_baseline_bar": round(base["ndp"], 2),
            "ndp_current_bar": round(cur["ndp"], 2),
            "tcf_baseline": round(base["tcf"], 4),
            "tcf_current": round(cur["tcf"], 4),
            "cleaning_triggered": bool(flags),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    BASELINE = dict(
        permeate_flow={"value": 44, "unit": "m3/h"},
        feed_pressure={"value": 12.0, "unit": "bar"},
        concentrate_pressure={"value": 10.95, "unit": "bar"},
        permeate_pressure={"value": 0.3, "unit": "bar"},
        feed_tds={"value": 800, "unit": "mg/L"},
        permeate_tds={"value": 25, "unit": "mg/L"},
        temperature={"value": 15, "unit": "degC"},
        recovery_pct=75,
    )
    CURRENT = dict(
        permeate_flow={"value": 37, "unit": "m3/h"},
        feed_pressure={"value": 13.5, "unit": "bar"},
        concentrate_pressure={"value": 12.1, "unit": "bar"},
        permeate_pressure={"value": 0.3, "unit": "bar"},
        feed_tds={"value": 820, "unit": "mg/L"},
        permeate_tds={"value": 31, "unit": "mg/L"},
        temperature={"value": 12, "unit": "degC"},
        recovery_pct=75,
    )

    # A fouled train against its post-cleaning baseline.
    print(calc_ro_normalization(current=CURRENT, baseline=BASELINE)["summary"])

    print("\n" + "=" * 68 + "\n")

    # The same physical baseline stated in US units — same answer, because the
    # comparison is a ratio.
    print(calc_ro_normalization(
        current=CURRENT,
        baseline={
            **BASELINE,
            "permeate_flow": {"value": 193.7, "unit": "gpm"},
            "feed_pressure": {"value": 174.05, "unit": "psi"},
            "concentrate_pressure": {"value": 158.82, "unit": "psi"},
            "permeate_pressure": {"value": 4.351, "unit": "psi"},
            "feed_tds": {"value": 800, "unit": "ppm"},
            "temperature": {"value": 59, "unit": "degF"},
        },
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_ro_normalization(
            current={**CURRENT, "feed_tds": {"value": 13.5, "unit": "bar"}},
            baseline=BASELINE,
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
