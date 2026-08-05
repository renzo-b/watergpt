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
# PARTIAL READINGS — the eight readings feed THREE independent metrics, so a
# missing reading disables only the metric it feeds:
#
#   metric        needs
#   ------------  -------------------------------------------------------------
#   dp            feed_pressure, concentrate_pressure
#   salt_passage  feed_tds, permeate_tds, recovery_pct
#   flow          permeate_flow, all three pressures, feed_tds, permeate_tds,
#                 temperature, recovery_pct  (via NDP)
#
# An operator with pressures but no TDS lab data still gets the dP answer, which
# is often the one the cleaning decision turns on. The distinction that drives
# the logic:
#
#   MISSING reading  -> skip the metric it feeds, emit a caveat naming exactly
#                       what is absent and in which state. Not an error.
#   INVALID reading  -> raise. A present-but-wrong reading (concentrate P above
#                       feed P, zero permeate TDS) is a mistake to surface, not
#                       a capability to degrade around.
#
# The only hard failure for absent data is when NO metric is computable. A
# metric also needs its readings in BOTH states — there is nothing to normalize
# against a baseline you do not have.
#
# KNOWN LIMITATIONS — each one is emitted as a caveat, not just documented here:
#   1. dP normalization uses permeate flow as a proxy for feed flow (exponent
#      1.5). Proper practice corrects on feed flow. At constant recovery the
#      proxy is a scale factor; it drifts when recovery changes, so a recovery
#      difference between the two states escalates the caveat.
#   2. Osmotic pressure comes from TDS, so it cannot see ion composition. The
#      correlation is chosen by feed strength — see calc_osmotic_pressure —
#      and which one was used is reported with the answer.
#   3. The 10-15% / 15% triggers are industry rules of thumb. A plant's O&M
#      manual or the membrane manufacturer's criteria override them.
#   4. System-level readings cannot localize the cause — front-end and tail-end
#      dP mean different things, and this sees neither.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

import math

from units import UnitError, echo, echo_all, parse, quantity_schema
from tools.registry import tool
from tools.calculators.calc_osmotic_pressure import (
    BRACKISH_TDS_LIMIT_MG_L, OSMOTIC_MODELS, OVERLAP_HI_MG_L, OVERLAP_LO_MG_L,
    osmotic_pressure_bar, select_osmotic_model,
)

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

# Above this feed TDS the brackish TDS approximation is out of range and the
# seawater correlation takes over — see limitation 2 and calc_osmotic_pressure.
# Re-exported under the old name because it is this module's public boundary.
OSMOTIC_TDS_LIMIT_MG_L = BRACKISH_TDS_LIMIT_MG_L

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

# Which readings each metric consumes. This table is the whole degradation
# policy: a metric runs when its readings are present in both states, and is
# skipped with a caveat when they are not.
METRIC_FIELDS = {
    "dp": ("feed_pressure", "concentrate_pressure"),
    "salt_passage": ("feed_tds", "permeate_tds", "recovery_pct"),
    "flow": ("permeate_flow", "feed_pressure", "concentrate_pressure",
             "permeate_pressure", "feed_tds", "permeate_tds", "temperature",
             "recovery_pct"),
}

METRIC_LABEL = {
    "flow": "normalized permeate flow",
    "dp": "normalized differential pressure",
    "salt_passage": "salt passage",
}

# Phrased the way an operator would be asked for them, not as field names.
METRIC_NEEDS = {
    "flow": ("permeate flow, all three pressures, feed and permeate TDS, "
             "temperature, and recovery"),
    "dp": "feed and concentrate pressure",
    "salt_passage": "feed and permeate TDS, and recovery",
}


def _tcf(temp_c: float) -> float:
    """Temperature correction factor to 25 C for polyamide membranes.

    Returns the multiplier that converts measured flow at temp_c to the
    equivalent flow at 25 C. Cold water permeates less, so TCF > 1 below 25 C.
    """
    coeff = TCF_COEFF_WARM if temp_c >= 25.0 else TCF_COEFF_COLD
    return math.exp(coeff * (1.0 / (273.15 + temp_c) - 1.0 / 298.15))


def _available(state, label):
    """Parse whatever readings this state actually has.

    Returns (quantities, names present, recovery as a fraction or None).

    A missing reading is NOT an error here — that is the whole point; which
    metrics survive is decided by _computable. A reading that is present but
    will not parse still raises, because that is a real input error.
    """
    if not isinstance(state, dict):
        raise UnitError(
            f"{label}: expected an object holding this state's readings, got "
            f"{state!r}."
        )

    qs, present = {}, set()
    for name, dimension in STATE_FIELDS:
        if state.get(name) is None:
            continue
        try:
            qs[name] = parse(state[name], dimension)
        except UnitError as e:
            raise UnitError(f"{label}.{name}: {e}")
        present.add(name)

    recovery = None
    if state.get("recovery_pct") is not None:
        recovery = state["recovery_pct"] / 100.0
        if not 0.0 < recovery < 1.0:
            raise ValueError(
                f"{label}: recovery must be between 0 and 100 percent "
                f"(got {state['recovery_pct']}). It is permeate flow divided "
                f"by feed flow."
            )
        present.add("recovery_pct")

    return qs, present, recovery


def _computable(present):
    """The metrics whose readings are all present in one state."""
    return {m for m, needs in METRIC_FIELDS.items() if set(needs) <= present}


def _shortfall(metric, cur_present, base_present):
    """Which side is short of which readings, so the caveat names them.

    'current missing feed_tds, permeate_tds' is something an operator can go and
    fetch; 'insufficient data' is not.
    """
    needs = set(METRIC_FIELDS[metric])
    parts = []
    for label, present in (("current", cur_present), ("baseline", base_present)):
        short = sorted(needs - present)
        if short:
            parts.append(f"{label} missing {', '.join(short)}")
    return "; ".join(parts)


def _derive(qs, recovery, metrics, label, steps, osmotic_model=None):
    """Derive dP, TCF, NDP and salt passage for one state, tracing as it goes.

    Only the intermediates the requested metrics need are computed, and each
    validity check fires only for readings a requested metric actually uses — a
    zero permeate TDS is not an error in a run that never looks at TDS.

    osmotic_model is chosen once by the caller and passed in, never selected
    here: the two states must be evaluated on the SAME correlation or the
    crossover between them shows up in the answer as a performance change that
    never happened.

    Every intermediate is appended as a step immediately after it is computed;
    nothing here is recalculated for the trace.
    """
    out = {"osmotic_notes": []}

    # dP is a metric in its own right and also the feed-brine average in NDP.
    if "dp" in metrics or "flow" in metrics:
        feed_p = qs["feed_pressure"].canonical
        conc_p = qs["concentrate_pressure"].canonical
        dp = feed_p - conc_p
        if dp < 0:
            raise ValueError(
                f"{label}: concentrate pressure exceeds feed pressure — check "
                "the readings."
            )
        if dp == 0:
            raise ValueError(
                f"{label}: feed and concentrate pressures are equal, so the "
                "differential pressure across the train is zero. That is a "
                "reading error on a running train, and the dP comparison "
                "divides by it."
            )
        steps.append({
            "label": f"{label.capitalize()} differential pressure",
            "formula": "feed pressure - concentrate pressure",
            "substituted": f"{feed_p:g} bar - {conc_p:g} bar",
            "value": round(dp, 3),
            "unit": "bar",
        })
        out["dp"] = dp

    if "flow" in metrics:
        flow = qs["permeate_flow"].to("m3/h")
        if flow <= 0:
            raise ValueError(
                f"{label}: permeate flow must be positive — the normalized flow "
                "comparison divides by it."
            )
        out["q"] = flow

        temp_c = qs["temperature"].canonical
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
        out["tcf"] = tcf

    # Log-mean concentration factor across the element train: feed water is
    # progressively concentrated as permeate is removed, so the membrane sees
    # more than the feed TDS. Salt passage and the NDP term both need it.
    if "salt_passage" in metrics or "flow" in metrics:
        feed_tds = qs["feed_tds"].canonical
        perm_tds = qs["permeate_tds"].canonical
        if feed_tds <= 0:
            raise ValueError(f"{label}: feed TDS must be positive.")
        if perm_tds <= 0:
            raise ValueError(
                f"{label}: permeate TDS must be positive. Salt passage is "
                "measured against it, so a zero reading is an instrument or "
                "lab error, not an operating condition."
            )
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
        out["c_avg"] = c_avg

    if "flow" in metrics:
        perm_p = qs["permeate_pressure"].canonical
        pf_avg = (feed_p + conc_p) / 2.0
        osmotic_feed, _, feed_notes = osmotic_pressure_bar(
            c_avg, temp_c, osmotic_model)
        osmotic_perm, _, _ = osmotic_pressure_bar(
            perm_tds, temp_c, osmotic_model)
        # The permeate is always far below the correlation's fitted floor, so
        # only the concentrate-side note is worth surfacing.
        out["osmotic_notes"] += [f"{label.capitalize()}: {n}" for n in feed_notes]
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
                f"{label}: net driving pressure is zero or negative — feed "
                f"pressure cannot overcome osmotic pressure at these readings. "
                f"Verify feed TDS, pressures, and recovery."
            )
        out["ndp"] = ndp

    if "salt_passage" in metrics:
        sp = perm_tds / c_avg * 100.0
        steps.append({
            "label": f"{label.capitalize()} salt passage",
            "formula": "permeate TDS / average concentrate-side TDS x 100",
            "substituted": f"{perm_tds:g} mg/L / {c_avg:.1f} mg/L x 100",
            "value": round(sp, 3),
            "unit": "%",
        })
        out["sp"] = sp

    return out


# ---------------------------------------------------------------------------
# Nested state objects keep a baseline reading from being passed as a current
# one.
#
# Only the two pressures are required. Marking all eight required would make
# the partial-answer path unreachable — the model would believe it had to hold
# out for readings the operator may simply not have, and the operator would get
# nothing instead of the dP answer their cleaning decision usually turns on.
# The pressure on the model to go and ASK for the rest lives in the tool
# description, where it can say why, rather than in a schema constraint that
# can only say no.
# ---------------------------------------------------------------------------

def _state_schema(which: str) -> dict:
    return {
        "type": "object",
        "description": (
            f"The {which} operating state. Send every reading the operator "
            "has: all eight answer the whole question, feed and concentrate "
            "pressure alone answer only the differential-pressure part. Omit "
            "what the operator does not have — never fill it in."
        ),
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
        # The minimum that computes anything at all — see the note above.
        "required": ["feed_pressure", "concentrate_pressure"],
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
        "ALWAYS needs a baseline state to compare against — normally the "
        "readings taken immediately after the last successful cleaning, or at "
        "commissioning. If the operator has not provided one, ASK for it. Raw "
        "uncorrected readings cannot answer this question, because temperature "
        "and pressure changes alone shift permeate flow substantially. "
        "ASK for all eight readings in both states; that is what answers the "
        "whole question. If the operator genuinely does not have some of them — "
        "no TDS lab data is common — send what they do have and the tool "
        "returns the metrics it can compute and names the ones it could not. "
        "Feed and concentrate pressure in both states are the minimum. NEVER "
        "assume, estimate, or substitute design values for a missing reading; "
        "leave it out instead, and report what the tool says was not computed "
        "rather than presenting a partial comparison as a complete one. "
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
    successful cleaning, or at commissioning.

    Readings may be partial. Each of the three metrics — normalized flow,
    normalized dP, salt passage — is computed when its readings are present in
    BOTH states and skipped with a caveat when they are not. Only a pair of
    states with no computable metric at all raises for missing data; a reading
    that is present but invalid always raises.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse whatever is there. Dimension errors surface here, before any math.
    cur_q, cur_present, cur_recovery = _available(current, "current")
    base_q, base_present, base_recovery = _available(baseline, "baseline")

    # A metric needs its readings on both sides — there is nothing to normalize
    # against a baseline you do not have.
    computable = _computable(cur_present) & _computable(base_present)
    if not computable:
        raise ValueError(
            "Cannot normalize: not enough readings to compute even one of the "
            "three metrics. At minimum, feed and concentrate pressure in BOTH "
            "states give the differential-pressure comparison ("
            + _shortfall("dp", cur_present, base_present) + "). Ask the "
            "operator for the missing readings — do not assume, estimate, or "
            "substitute design values."
        )
    skipped = set(METRIC_FIELDS) - computable

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing below is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []
    caveats = []

    # The gaps come first: an operator reading this needs to know what was NOT
    # answered before deciding what the numbers below are worth.
    for metric in sorted(skipped):
        caveats.append(
            f"{METRIC_LABEL[metric].capitalize()} not computed — it needs "
            f"{METRIC_NEEDS[metric]} in both states "
            f"({_shortfall(metric, cur_present, base_present)}). Provide those "
            "readings for a complete assessment."
        )

    # One osmotic correlation for both states, selected from the stronger feed.
    # Selection is on FEED TDS rather than the concentrate-side average because
    # what it really picks is an assumption about ion composition — whether
    # this is seawater or not — and that is a property of the water, not of the
    # recovery the plant happens to be running.
    osmotic_model = None
    if "flow" in computable:
        peak_feed_tds = max(cur_q["feed_tds"].canonical,
                            base_q["feed_tds"].canonical)
        osmotic_model = select_osmotic_model(peak_feed_tds)

    base = _derive(base_q, base_recovery, computable, "baseline", steps,
                   osmotic_model)
    cur = _derive(cur_q, cur_recovery, computable, "current", steps,
                  osmotic_model)

    result = {}
    lines = []
    flags = []

    # 2. Normalize both states to 25 C and the baseline net driving pressure.
    if "flow" in computable:
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

        result["normalized_flow_change_pct"] = round(d_flow, 1)
        result["normalized_flow_baseline_m3_per_h"] = round(qn_base, 2)
        result["normalized_flow_current_m3_per_h"] = round(qn_cur, 2)
        result["ndp_baseline_bar"] = round(base["ndp"], 2)
        result["ndp_current_bar"] = round(cur["ndp"], 2)
        result["osmotic_model"] = osmotic_model
        result["tcf_baseline"] = round(base["tcf"], 4)
        result["tcf_current"] = round(cur["tcf"], 4)
        lines.append(
            f"  Permeate flow:  {qn_base:.1f} -> {qn_cur:.1f} m3/h  ({d_flow:+.1f}%)"
        )

    if "dp" in computable:
        # dP corrected for the flow change; see limitation 1. The correction IS
        # the flow ratio, so with no flow readings there is nothing to correct
        # with — report the raw change and label it, rather than implying a
        # normalization that did not happen.
        if "flow" in computable:
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
            dp_note = ""
            dp_step_label = "Change in normalized differential pressure"
            dp_flag_name = "Normalized dP"
        else:
            dpn_cur = cur["dp"]
            dp_note = " (raw dP; not flow-normalized — flow readings absent)"
            dp_step_label = "Change in raw differential pressure"
            dp_flag_name = "Raw dP"

        d_dp = (dpn_cur - base["dp"]) / base["dp"] * 100.0
        steps.append({
            "label": dp_step_label,
            "formula": "(current - baseline) / baseline x 100",
            "substituted": f"({dpn_cur:.3g} - {base['dp']:.3g}) / {base['dp']:.3g} x 100",
            "value": round(d_dp, 1),
            "unit": "%",
        })

        result["normalized_dp_change_pct"] = round(d_dp, 1)
        result["normalized_dp_current_bar"] = round(dpn_cur, 3)
        result["dp_baseline_bar"] = round(base["dp"], 3)
        lines.append(
            f"  Differential P: {base['dp']:.2f} -> {dpn_cur:.2f} bar  "
            f"({d_dp:+.1f}%){dp_note}"
        )

    if "salt_passage" in computable:
        d_sp = (cur["sp"] - base["sp"]) / base["sp"] * 100.0
        steps.append({
            "label": "Change in salt passage",
            "formula": "(current - baseline) / baseline x 100",
            "substituted": f"({cur['sp']:.3g} - {base['sp']:.3g}) / {base['sp']:.3g} x 100",
            "value": round(d_sp, 1),
            "unit": "%",
        })
        result["salt_passage_change_pct"] = round(d_sp, 1)
        result["salt_passage_baseline_pct"] = round(base["sp"], 3)
        result["salt_passage_current_pct"] = round(cur["sp"], 3)
        lines.append(
            f"  Salt passage:   {base['sp']:.2f} -> {cur['sp']:.2f} %  ({d_sp:+.1f}%)"
        )

    # 3. Triggers, for the metrics that actually ran. Thresholds are the module
    #    constants, not literals, so a plant's own criteria change in one place.
    if "flow" in computable:
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
    if "dp" in computable and d_dp >= DP_TRIGGER_PCT:
        flags.append(
            f"{dp_flag_name} up {d_dp:.0f}% — EXCEEDS the "
            f"{DP_TRIGGER_PCT:.0f}% cleaning trigger."
        )
    if "salt_passage" in computable:
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

    # Pattern interpretation drives cleaning chemistry choice. It reads two
    # metrics against each other, so it only means anything when both ran.
    if {"dp", "flow"} <= computable:
        if d_dp >= DP_TRIGGER_PCT and d_flow > FLOW_TRIGGER_PCT:
            flags.append(
                "dP-dominant pattern: suggests particulate or biological "
                "fouling rather than scaling."
            )
    if {"dp", "salt_passage"} <= computable:
        if d_sp >= SP_TRIGGER_HARD_PCT and d_dp < DP_TRIGGER_PCT:
            flags.append(
                "Salt-passage-dominant pattern: suggests scaling or membrane "
                "damage rather than fouling."
            )

    # 4. The limitations, as caveats rather than documentation nobody reads.
    #    Each is emitted only when the metric it qualifies actually ran.
    if {"dp", "flow"} <= computable:
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

    if "flow" in computable:
        # Which correlation produced the NDP is stated with the answer. It is
        # the largest single source of spread in the normalized flow figure,
        # and it is invisible in the numbers themselves.
        spec = OSMOTIC_MODELS[osmotic_model]
        caveats.append(
            f"Net driving pressure uses the {spec['label']} "
            f"({spec['source']}) for both states, selected from a feed TDS of "
            f"{peak_feed_tds:g} mg/L. Osmotic pressure comes from TDS alone "
            "and cannot see ion composition."
        )
        caveats += [f"Source: {ref}" for ref in spec["references"]]
        caveats += cur["osmotic_notes"] + base["osmotic_notes"]
        if OVERLAP_LO_MG_L <= peak_feed_tds <= OVERLAP_HI_MG_L:
            caveats.append(
                f"At a feed TDS of {peak_feed_tds:g} mg/L this train sits near "
                f"the {OSMOTIC_TDS_LIMIT_MG_L:g} mg/L boundary between the two "
                "osmotic correlations, which disagree by roughly a third. The "
                "COMPARISON is still sound — both states use the same "
                "correlation — but the absolute NDP is uncertain by about that "
                "much, and a feed that drifts across the boundary between one "
                "run of this tool and the next will step. Run "
                "calc_osmotic_pressure on this feed to see the spread."
            )

    caveats.append(
        "The cleaning triggers used here are industry rules of thumb. Defer to "
        "the membrane manufacturer's dP limit and this plant's cleaning SOP."
    )

    caveats.append(
        "This compares system-level readings and does not localize the cause. "
        "Front-end dP suggests fouling; tail-end dP with rising salt passage "
        "suggests scaling. Per-stage dP and flow are needed to tell them apart."
    )

    # 5. Conversions first, so the operator sees what was assumed. Only the
    #    readings actually supplied appear.
    cur_order = [name for name, _ in STATE_FIELDS if name in cur_q]
    base_order = [name for name, _ in STATE_FIELDS if name in base_q]

    out = ["Inputs (current):", echo_all(*(cur_q[n] for n in cur_order))]
    if cur_recovery is not None:
        out.append(f"  recovery {current['recovery_pct']:g}%")
    out += ["Inputs (baseline):", echo_all(*(base_q[n] for n in base_order))]
    if base_recovery is not None:
        out.append(f"  recovery {baseline['recovery_pct']:g}%")
    out.append("")

    if "flow" in computable:
        out.append(f"Normalized to 25 C and baseline NDP ({base['ndp']:.1f} bar).")
    else:
        out.append("Comparison from the readings supplied:")
    out += lines
    if "flow" in computable:
        out.append(
            f"  TCF current {cur['tcf']:.3f}, baseline {base['tcf']:.3f}; "
            f"NDP current {cur['ndp']:.1f} bar"
        )
    if skipped:
        out.append(
            "  Not computed: "
            + ", ".join(METRIC_LABEL[m] for m in sorted(skipped))
            + " — see caveats."
        )
    out += (
        ["  ! " + f for f in flags]
        if flags
        else ["  No cleaning trigger exceeded. Continue trending."]
    )
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = (
        [echo(cur_q[n]) for n in cur_order] + [echo(base_q[n]) for n in base_order]
    )
    if "flow" in computable:
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

    # What was and was not answered, so the agent can say so instead of
    # presenting a partial comparison as a complete one.
    result["metrics_computed"] = sorted(computable)
    result["metrics_skipped"] = sorted(skipped)
    result["cleaning_triggered"] = bool(flags)

    return {
        "summary": "\n".join(out),
        "result": result,
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

    # No TDS lab data. The dP answer is still the one a cleaning decision often
    # turns on, so it is still given — with the two gaps named, rather than the
    # whole call refused.
    def _no_tds(state):
        return {k: v for k, v in state.items()
                if k not in ("feed_tds", "permeate_tds")}

    print(calc_ro_normalization(
        current=_no_tds(CURRENT), baseline=_no_tds(BASELINE)
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
