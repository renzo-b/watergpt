"""
Tests for calc_ro_normalization — python tests/test_calc_ro_normalization.py

Plain asserts to match units.py and the other calculators — this repo has no
pytest. Schema conformance is covered by the CALCULATOR_CASES loop in
test_calc_ct_steps.py; this file covers the RO-specific behaviour.

Structured around what the tool exists to prevent rather than around the
arithmetic. The whole reason it is a tool is that raw readings mislead: a 10 C
swing moves permeate flow by roughly 25%, so the tests that matter are the ones
that pin *direction* and *invariance* — identity, temperature-only change, and
the same readings restated in US units. Arithmetic that agrees with itself
would pass every one of those while being wrong.
"""

import math
import sys
from pathlib import Path

# Running a file in tests/ puts tests/ on sys.path, not the repo root, so
# `import units` would fail. Same trap the demo blocks use -m to avoid.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.calculators.calc_ro_normalization import (
    DP_TRIGGER_PCT, OSMOTIC_TDS_LIMIT_MG_L, RECOVERY_DRIFT_LIMIT_PCT,
    _tcf, calc_ro_normalization,
)
from tools.registry import all_schemas, dispatch
from units import UnitError

# The reference train, used throughout: a post-cleaning baseline and the same
# train three months later, colder and fouled.
BASELINE = {
    "permeate_flow": {"value": 44, "unit": "m3/h"},
    "feed_pressure": {"value": 12.0, "unit": "bar"},
    "concentrate_pressure": {"value": 10.95, "unit": "bar"},
    "permeate_pressure": {"value": 0.3, "unit": "bar"},
    "feed_tds": {"value": 800, "unit": "mg/L"},
    "permeate_tds": {"value": 25, "unit": "mg/L"},
    "temperature": {"value": 15, "unit": "degC"},
    "recovery_pct": 75,
}
CURRENT = {
    "permeate_flow": {"value": 37, "unit": "m3/h"},
    "feed_pressure": {"value": 13.5, "unit": "bar"},
    "concentrate_pressure": {"value": 12.1, "unit": "bar"},
    "permeate_pressure": {"value": 0.3, "unit": "bar"},
    "feed_tds": {"value": 820, "unit": "mg/L"},
    "permeate_tds": {"value": 31, "unit": "mg/L"},
    "temperature": {"value": 12, "unit": "degC"},
    "recovery_pct": 75,
}

STEP_KEYS = {"label", "formula", "substituted", "value", "unit"}


def step_by_label(out, label):
    for s in out["steps"]:
        if s["label"] == label:
            return s
    return None


# ---------------------------------------------------------------------------
# 1. Identity. A state compared with itself must show zero change on all three
#    indicators. This is the single most important test in the file — it is
#    what catches a sign or direction error, and every other number here is
#    meaningless if it fails.
# ---------------------------------------------------------------------------

identity = calc_ro_normalization(current=BASELINE, baseline=BASELINE)
r = identity["result"]
assert r["normalized_flow_change_pct"] == 0.0, r
assert r["normalized_dp_change_pct"] == 0.0, r
assert r["salt_passage_change_pct"] == 0.0, r
assert r["cleaning_triggered"] is False, r
assert "No cleaning trigger exceeded" in identity["summary"], identity["summary"]
assert "+0.0%" in identity["summary"], identity["summary"]
print("identity: a state against itself is 0.0% on flow, dP and salt passage")

# ---------------------------------------------------------------------------
# 2. The worked case. These figures were produced by the pre-registry version
#    of this calculator at the repo root and hand-checked, so they pin the
#    physics across the move onto the quantity layer, not just against itself.
# ---------------------------------------------------------------------------

out = calc_ro_normalization(current=CURRENT, baseline=BASELINE)
r = out["result"]
assert r["normalized_flow_change_pct"] == -17.1, r
assert r["normalized_dp_change_pct"] == 72.9, r
assert r["salt_passage_change_pct"] == 21.0, r
assert r["ndp_baseline_bar"] == 9.88, r
assert r["ndp_current_bar"] == 11.19, r
assert r["tcf_baseline"] == 1.4212, r
assert r["tcf_current"] == 1.5869, r
assert r["cleaning_triggered"] is True, r
assert r["normalized_flow_baseline_m3_per_h"] == 62.53, r
assert r["normalized_flow_current_m3_per_h"] == 51.85, r
# A complete set answers all three metrics and skips none.
assert r["metrics_computed"] == ["dp", "flow", "salt_passage"], r
assert r["metrics_skipped"] == [], r
print(f"worked case: flow {r['normalized_flow_change_pct']}%, "
      f"dP {r['normalized_dp_change_pct']}%, "
      f"salt passage {r['salt_passage_change_pct']}%")

# TCF is the correction the whole tool rests on, so it gets pinned directly:
# 25 C is the reference, colder water permeates less.
assert _tcf(25.0) == 1.0, _tcf(25.0)
assert _tcf(15.0) > 1.0 and _tcf(35.0) < 1.0, (_tcf(15.0), _tcf(35.0))

# ---------------------------------------------------------------------------
# 3. Unit independence. The comparison is a ratio of two states, so restating
#    the baseline in US units must not move a single result value. This is what
#    the quantity layer buys over the old m3h/bar/mgl argument names.
# ---------------------------------------------------------------------------

us_baseline = {
    **BASELINE,
    "permeate_flow": {"value": 193.7, "unit": "gpm"},
    "feed_pressure": {"value": 174.05, "unit": "psi"},
    "concentrate_pressure": {"value": 158.82, "unit": "psi"},
    "permeate_pressure": {"value": 4.351, "unit": "psi"},
    "feed_tds": {"value": 800, "unit": "ppm"},
    "temperature": {"value": 59, "unit": "degF"},
}
us = calc_ro_normalization(current=CURRENT, baseline=us_baseline)
# The four figures an operator acts on must be identical, not merely close.
for key in ("normalized_flow_change_pct", "normalized_dp_change_pct",
            "salt_passage_change_pct", "cleaning_triggered"):
    assert us["result"][key] == out["result"][key], (
        f"US units moved {key}: {us['result'][key]} vs {out['result'][key]}"
    )
# The intermediates carry the rounding of the restated inputs — 193.7 gpm is
# 43.98 m3/h, not 44 — so they agree to a tenth of a percent, not exactly.
for key, want in out["result"].items():
    # cleaning_triggered is a bool and metrics_computed/skipped are lists;
    # both are compared above, and neither has a tolerance.
    if not isinstance(want, (int, float)) or isinstance(want, bool):
        assert us["result"][key] == want, (key, us["result"][key], want)
        continue
    assert abs(us["result"][key] - want) <= abs(want) * 0.001, (
        f"{key}: {us['result'][key]} vs {want}"
    )
# The ambiguity notes must still reach the operator — gpm and ppm both carry one.
assert any("igpm" in c for c in us["conversions"]), us["conversions"]
assert any("ppm treated as mg/L" in c for c in us["conversions"]), us["conversions"]
print("same case in gpm/psi/degF/ppm gives an identical result dict")

# ---------------------------------------------------------------------------
# 4. The reason this is a tool at all: a temperature-only change must very
#    nearly vanish after normalization. Here the train is unchanged but 10 C
#    colder, with flow and dP moved exactly as the physics says they would be.
#    Raw flow is down 30%; an operator reading the raw trend would call for a
#    cleaning that the normalized figures say is not due.
# ---------------------------------------------------------------------------

WARM = {**BASELINE, "temperature": {"value": 25, "unit": "degC"}}
_ratio = 1 / _tcf(15.0)                     # flow at 15 C relative to 25 C
COLD = {
    **WARM,
    "temperature": {"value": 15, "unit": "degC"},
    "permeate_flow": {"value": 44 * _ratio, "unit": "m3/h"},
    # dP falls with feed flow, or the dP correction would be normalizing a
    # reading the rest of the state contradicts.
    "concentrate_pressure": {"value": 12.0 - 1.05 * _ratio ** 1.5, "unit": "bar"},
}
raw_change = (44 * _ratio - 44) / 44 * 100
assert raw_change < -29, raw_change

cold = calc_ro_normalization(current=COLD, baseline=WARM)["result"]
assert abs(cold["normalized_flow_change_pct"]) < 3, cold
assert cold["normalized_dp_change_pct"] == 0.0, cold
assert cold["salt_passage_change_pct"] == 0.0, cold
assert cold["cleaning_triggered"] is False, cold
print(f"temperature alone: raw flow {raw_change:.0f}%, normalized "
      f"{cold['normalized_flow_change_pct']}% — no trigger")

# ---------------------------------------------------------------------------
# 5. Trigger wording. "at the" and "EXCEEDS" are different operational
#    instructions, so the boundary between them is worth pinning. Scaling
#    permeate flow alone moves the flow figure by exactly that factor, because
#    NDP depends on the pressures and TDS, not on flow.
# ---------------------------------------------------------------------------

def flow_scaled(factor):
    return calc_ro_normalization(
        current={**BASELINE,
                 "permeate_flow": {"value": 44 * factor, "unit": "m3/h"}},
        baseline=BASELINE,
    )


at_trigger = flow_scaled(0.88)
assert at_trigger["result"]["normalized_flow_change_pct"] == -12.0, at_trigger["result"]
assert "flow down 12% — at the 10-15% cleaning trigger" in at_trigger["summary"], \
    at_trigger["summary"]

over_trigger = flow_scaled(0.80)
assert over_trigger["result"]["normalized_flow_change_pct"] == -20.0, over_trigger["result"]
assert "flow down 20% — EXCEEDS the 10-15% cleaning trigger" in over_trigger["summary"], \
    over_trigger["summary"]

under_trigger = flow_scaled(0.95)
assert under_trigger["result"]["normalized_flow_change_pct"] == -5.0, under_trigger["result"]
assert "flow down" not in under_trigger["summary"], under_trigger["summary"]
assert "No cleaning trigger exceeded" in under_trigger["summary"], under_trigger["summary"]
print("flow triggers: -5% silent, -12% 'at the', -20% 'EXCEEDS'")

# ---------------------------------------------------------------------------
# 6. Pattern interpretation. Which indicator moved is what picks the cleaning
#    chemistry, so a dP-dominant result must not be reported as scaling and
#    vice versa. Both cases below hold the average feed-brine pressure fixed,
#    so NDP and normalized flow do not move and the pattern is unambiguous.
# ---------------------------------------------------------------------------

dp_dominant = calc_ro_normalization(
    current={**BASELINE,
             "feed_pressure": {"value": 12.3, "unit": "bar"},
             "concentrate_pressure": {"value": 10.65, "unit": "bar"}},
    baseline=BASELINE,
)["summary"]
assert "dP-dominant pattern" in dp_dominant, dp_dominant
assert "particulate or biological fouling" in dp_dominant, dp_dominant
assert "Salt-passage-dominant" not in dp_dominant, dp_dominant

sp_dominant = calc_ro_normalization(
    current={**BASELINE, "permeate_tds": {"value": 30, "unit": "mg/L"}},
    baseline=BASELINE,
)["summary"]
assert "Salt-passage-dominant pattern" in sp_dominant, sp_dominant
assert "scaling or membrane damage" in sp_dominant, sp_dominant
assert "dP-dominant" not in sp_dominant, sp_dominant
print("dP-dominant and salt-passage-dominant patterns are reported separately")

# ---------------------------------------------------------------------------
# 7. Caveats carry the known limitations. The dP proxy is the one that can
#    silently mislead, so a recovery change has to escalate it from a note to a
#    warning — that is the condition under which the proxy stops holding.
# ---------------------------------------------------------------------------

steady = calc_ro_normalization(current=CURRENT, baseline=BASELINE)["caveats"]
assert any("proxy for feed flow" in c for c in steady), steady
assert not any("UNRELIABLE" in c for c in steady), steady

drifted = calc_ro_normalization(
    current={**CURRENT, "recovery_pct": 75 + RECOVERY_DRIFT_LIMIT_PCT + 1},
    baseline=BASELINE,
)["caveats"]
assert any("UNRELIABLE" in c and "recovery moved" in c for c in drifted), drifted

# Which osmotic correlation produced the NDP is reported with the answer, in
# both directions — it is invisible in the numbers and it moves them by ~30%.
brackish = calc_ro_normalization(current=CURRENT, baseline=BASELINE)
assert brackish["result"]["osmotic_model"] == "brackish_tds", brackish["result"]
assert any("brackish TDS approximation" in c for c in brackish["caveats"]), \
    brackish["caveats"]

salty = {
    **BASELINE,
    "feed_tds": {"value": OSMOTIC_TDS_LIMIT_MG_L + 1000, "unit": "mg/L"},
    "feed_pressure": {"value": 40.0, "unit": "bar"},
    "concentrate_pressure": {"value": 38.95, "unit": "bar"},
}
salty_out = calc_ro_normalization(current=salty, baseline=salty)
assert salty_out["result"]["osmotic_model"] == "seawater", salty_out["result"]
# The citation follows the correlation into this tool's output too, or the
# number is uncheckable wherever it happens to be read.
assert any(c.startswith("Source:") and "Sharqawy" in c
           for c in salty_out["caveats"]), salty_out["caveats"]
assert any(c.startswith("Source:") and "primary source not established" in c
           for c in brackish["caveats"]), brackish["caveats"]
assert any("seawater osmotic coefficient" in c for c in salty_out["caveats"]), \
    salty_out["caveats"]
# Just past the boundary the two correlations still disagree materially, and
# saying so is the honest thing rather than quoting the NDP to two decimals.
assert any("disagree by roughly a third" in c for c in salty_out["caveats"]), \
    salty_out["caveats"]

# The manufacturer's own limits always win, and that has to reach the operator.
assert any("membrane manufacturer's dP limit" in c for c in steady), steady
assert "CAVEAT: The cleaning triggers used here are industry rules of thumb" in \
    calc_ro_normalization(current=CURRENT, baseline=BASELINE)["summary"]
print("caveats: dP proxy escalates on a recovery change, osmotic range flagged")

# ---------------------------------------------------------------------------
# 8. Error paths. Every one of these must raise rather than return a confident
#    number — a normalized figure derived from a bad reading is worse than no
#    figure, because it looks like a decision.
# ---------------------------------------------------------------------------

CASES = [
    ({**CURRENT, "recovery_pct": 0}, "between 0 and 100", "zero recovery"),
    ({**CURRENT, "recovery_pct": 100}, "between 0 and 100", "100% recovery"),
    ({**CURRENT, "concentrate_pressure": {"value": 20.0, "unit": "bar"}},
     "concentrate pressure exceeds feed pressure", "concentrate above feed"),
    ({**CURRENT, "concentrate_pressure": {"value": 13.5, "unit": "bar"}},
     "differential pressure across the train is zero", "zero dP"),
    ({**CURRENT,
      "feed_pressure": {"value": 1.0, "unit": "bar"},
      "concentrate_pressure": {"value": 0.9, "unit": "bar"}},
     "net driving pressure is zero or negative", "NDP non-positive"),
    ({**CURRENT, "permeate_tds": {"value": 0, "unit": "mg/L"}},
     "permeate TDS must be positive", "zero permeate TDS"),
    ({**CURRENT, "permeate_flow": {"value": 0, "unit": "m3/h"}},
     "permeate flow must be positive", "zero permeate flow"),
]
for bad, fragment, why in CASES:
    try:
        calc_ro_normalization(current=bad, baseline=BASELINE)
        raise AssertionError(f"expected a raise for: {why}")
    except ValueError as e:                      # UnitError is a ValueError
        assert fragment in str(e), f"{why}: {e}"
        assert "current" in str(e), f"{why}: the message must say which state: {e}"
print(f"{len(CASES)} bad-reading cases all raise, each naming the state")

# A gap on one side only still disables the metrics that need it, because a
# metric with no baseline has nothing to be normalized against.
one_sided = calc_ro_normalization(
    current=CURRENT,
    baseline={k: v for k, v in BASELINE.items() if k != "permeate_tds"},
)["result"]
assert one_sided["metrics_computed"] == ["dp"], one_sided
assert one_sided["metrics_skipped"] == ["flow", "salt_passage"], one_sided

# Swapped dimensions raise instead of normalizing against a different quantity.
for field, bad_value, why in [
    ("feed_tds", {"value": 13.5, "unit": "bar"}, "pressure as TDS"),
    ("feed_pressure", {"value": 800, "unit": "mg/L"}, "TDS as pressure"),
    ("permeate_flow", {"value": 44, "unit": "m3"}, "volume as flow"),
]:
    try:
        calc_ro_normalization(
            current={**CURRENT, field: bad_value}, baseline=BASELINE
        )
        raise AssertionError(f"expected a UnitError for: {why}")
    except UnitError as e:
        assert f"current.{field}" in str(e), f"{why}: {e}"
print("a one-sided gap disables its metrics; swapped dimensions raise")

# ---------------------------------------------------------------------------
# 9. Graceful degradation. A missing reading is not the same failure as a wrong
#    one. The eight readings feed three independent metrics, and an operator
#    without TDS lab data still has a cleaning decision to make on dP — so a
#    gap must disable only the metric it feeds, while a reading that is present
#    and impossible must still raise.
# ---------------------------------------------------------------------------

def without(state, *fields):
    return {k: v for k, v in state.items() if k not in fields}


# A complete set answers everything — the baseline for the cases below.
assert out["result"]["metrics_computed"] == ["dp", "flow", "salt_passage"]
assert out["result"]["metrics_skipped"] == []

# No TDS lab data. dP is still answered; flow and salt passage are not, because
# both go through the average concentrate-side TDS.
no_tds = calc_ro_normalization(
    current=without(CURRENT, "feed_tds", "permeate_tds"),
    baseline=without(BASELINE, "feed_tds", "permeate_tds"),
)
r = no_tds["result"]
assert r["metrics_computed"] == ["dp"], r
assert r["metrics_skipped"] == ["flow", "salt_passage"], r
assert "normalized_flow_change_pct" not in r, r
assert "salt_passage_change_pct" not in r, r
# 1.05 bar to 1.40 bar, uncorrected: the flow ratio that would correct it is
# exactly what is missing, so the figure is raw and has to say so.
assert r["normalized_dp_change_pct"] == 33.3, r
assert "raw dP; not flow-normalized" in no_tds["summary"], no_tds["summary"]
assert step_by_label(no_tds, "Change in raw differential pressure") is not None
assert step_by_label(no_tds, "Normalized differential pressure, current") is None
# Steps for a skipped metric are not emitted at all.
for absent in ["Baseline temperature correction factor",
               "Baseline net driving pressure", "Baseline salt passage",
               "Normalized permeate flow, baseline"]:
    assert step_by_label(no_tds, absent) is None, absent
# The caveat has to name the missing readings and the state, or the operator
# cannot act on it.
gaps = [c for c in no_tds["caveats"] if "not computed" in c]
assert len(gaps) == 2, gaps
assert any("feed_tds" in c and "permeate_tds" in c and "current missing" in c
           and "baseline missing" in c for c in gaps), gaps
# Caveats that qualify a skipped metric must not be emitted for it.
assert not any("proxy for feed flow" in c for c in no_tds["caveats"]), \
    no_tds["caveats"]
assert any("industry rules of thumb" in c for c in no_tds["caveats"])
assert any("does not localize the cause" in c for c in no_tds["caveats"])

# Two pressures and nothing else is still a usable answer.
pressures_only = calc_ro_normalization(
    current={k: CURRENT[k] for k in ("feed_pressure", "concentrate_pressure")},
    baseline={k: BASELINE[k] for k in ("feed_pressure", "concentrate_pressure")},
)
assert pressures_only["result"]["metrics_computed"] == ["dp"], pressures_only["result"]
assert pressures_only["result"]["normalized_dp_change_pct"] == 33.3, pressures_only["result"]
assert "raw dP; not flow-normalized" in pressures_only["summary"]

# Nothing that feeds a metric in both states: the one remaining hard failure
# for missing data.
try:
    calc_ro_normalization(
        current={"temperature": {"value": 12, "unit": "degC"}},
        baseline={"temperature": {"value": 15, "unit": "degC"}},
    )
    raise AssertionError("no computable metric must raise")
except ValueError as e:
    assert "not enough readings" in str(e), e
    assert "feed_pressure" in str(e) and "concentrate_pressure" in str(e), e
    assert "do not assume" in str(e), e

# A present-but-impossible reading is a mistake to surface, not a capability to
# degrade around — it raises even when the rest of the state is partial.
for bad_state, why in [
    ({**CURRENT, "concentrate_pressure": {"value": 20.0, "unit": "bar"}},
     "full readings"),
    ({**without(CURRENT, "feed_tds", "permeate_tds"),
      "concentrate_pressure": {"value": 20.0, "unit": "bar"}},
     "dP-only readings"),
]:
    try:
        calc_ro_normalization(
            current=bad_state,
            baseline=without(BASELINE, "feed_tds", "permeate_tds")
            if why == "dP-only readings" else BASELINE,
        )
        raise AssertionError(f"invalid concentrate pressure must raise ({why})")
    except ValueError as e:
        assert "concentrate pressure exceeds feed pressure" in str(e), (why, e)
        assert "current" in str(e), (why, e)
print("degradation: dP survives missing TDS, invalid readings still raise")

# ---------------------------------------------------------------------------
# 10. Seawater. The whole train is at 35 g/kg, where the brackish TDS shortcut
#     overstates osmotic pressure by about 30% — enough to halve the net
#     driving pressure, and on a lower-pressure train enough to drive it
#     negative and refuse the calculation outright. This is the case the
#     osmotic module exists for.
# ---------------------------------------------------------------------------

SWRO_BASE = {
    "permeate_flow": {"value": 30, "unit": "m3/h"},
    "feed_pressure": {"value": 55.0, "unit": "bar"},
    "concentrate_pressure": {"value": 53.5, "unit": "bar"},
    "permeate_pressure": {"value": 0.5, "unit": "bar"},
    "feed_tds": {"value": 35000, "unit": "mg/L"},
    "permeate_tds": {"value": 200, "unit": "mg/L"},
    "temperature": {"value": 25, "unit": "degC"},
    "recovery_pct": 45,
}
swro = calc_ro_normalization(current=SWRO_BASE, baseline=SWRO_BASE)
r = swro["result"]
assert r["osmotic_model"] == "seawater", r
# Identity still holds on a seawater train — the model change must not have
# introduced an asymmetry between the two states.
assert r["normalized_flow_change_pct"] == 0.0, r
assert r["normalized_dp_change_pct"] == 0.0, r
assert r["salt_passage_change_pct"] == 0.0, r
# The concentrate side averages ~46 g/kg at 45% recovery; osmotic pressure
# there is ~34 bar, leaving ~20 bar of the 54 bar average feed pressure.
assert 15.0 < r["ndp_baseline_bar"] < 25.0, r
# What the old brackish-only code produced for the same train, for scale.
_old_osmotic = lambda c, t: (0.0385 * c * (t + 320) / (1000 - c / 1000)) / 14.5038
_c_avg = 35000 * math.log(1 / (1 - 0.45)) / 0.45
_old_ndp = (55.0 + 53.5) / 2 - 0.5 - (_old_osmotic(_c_avg, 25)
                                      - _old_osmotic(200, 25))
assert _old_ndp < r["ndp_baseline_bar"] / 1.8, (_old_ndp, r["ndp_baseline_bar"])
print(f"seawater train: NDP {r['ndp_baseline_bar']} bar, against "
      f"{_old_ndp:.1f} bar from the brackish shortcut it replaced")

# A real fouling case on the same train still reads as fouling.
SWRO_CUR = {
    **SWRO_BASE,
    "permeate_flow": {"value": 26, "unit": "m3/h"},
    "feed_pressure": {"value": 58.0, "unit": "bar"},
    "concentrate_pressure": {"value": 55.8, "unit": "bar"},
    "permeate_tds": {"value": 260, "unit": "mg/L"},
}
fouled = calc_ro_normalization(current=SWRO_CUR, baseline=SWRO_BASE)
assert fouled["result"]["osmotic_model"] == "seawater", fouled["result"]
assert fouled["result"]["normalized_flow_change_pct"] < 0, fouled["result"]
assert fouled["result"]["normalized_dp_change_pct"] > 0, fouled["result"]
assert fouled["result"]["cleaning_triggered"] is True, fouled["result"]

# A brackish feed and a seawater feed must never be compared on different
# correlations — the crossover would read as a performance change. One model is
# selected from the stronger feed and pinned for both states.
mixed = calc_ro_normalization(
    current={**BASELINE, "feed_tds": {"value": 30000, "unit": "mg/L"},
             "feed_pressure": {"value": 45.0, "unit": "bar"},
             "concentrate_pressure": {"value": 43.95, "unit": "bar"}},
    baseline={**BASELINE, "feed_pressure": {"value": 45.0, "unit": "bar"},
              "concentrate_pressure": {"value": 43.95, "unit": "bar"}},
)
assert mixed["result"]["osmotic_model"] == "seawater", mixed["result"]
assert any("35000" not in c and "30000 mg/L" in c
           for c in mixed["caveats"]), mixed["caveats"]
print("one correlation pinned across both states, chosen by the stronger feed")

# ---------------------------------------------------------------------------
# 11. The trace. Steps are the working an operator can check, so both states
#     have to appear in it — a trace that shows only the current readings would
#     hide half of the comparison.
# ---------------------------------------------------------------------------

for label in [
    "Baseline differential pressure", "Baseline temperature correction factor",
    "Baseline average concentrate-side TDS", "Baseline net driving pressure",
    "Baseline salt passage",
    "Current differential pressure", "Current temperature correction factor",
    "Current average concentrate-side TDS", "Current net driving pressure",
    "Current salt passage",
    "Normalized permeate flow, baseline", "Normalized permeate flow, current",
    "Change in normalized permeate flow",
    "Normalized differential pressure, current",
    "Change in normalized differential pressure", "Change in salt passage",
]:
    step = step_by_label(out, label)
    assert step is not None, f"missing step {label!r}: {[s['label'] for s in out['steps']]}"
    assert set(step) == STEP_KEYS, (label, set(step))

# Steps are captured, not recalculated: each traced value must equal the
# result value it feeds.
assert step_by_label(out, "Change in normalized permeate flow")["value"] == \
    out["result"]["normalized_flow_change_pct"]
assert step_by_label(out, "Current net driving pressure")["value"] == \
    out["result"]["ndp_current_bar"]
assert step_by_label(out, "Baseline salt passage")["value"] == \
    out["result"]["salt_passage_baseline_pct"]

# Baseline is derived before current, so the operator reads the comparison in
# the same order the summary states it.
labels = [s["label"] for s in out["steps"]]
assert labels.index("Baseline salt passage") < labels.index("Current differential pressure"), labels
print(f"trace covers both states in order ({len(out['steps'])} steps)")

# ---------------------------------------------------------------------------
# 12. Registration. The whole point of the move into tools/calculators/ is that
#     the agent can reach it, with a schema that demands both states — and that
#     does NOT demand all eight readings, or the degradation path above would
#     be unreachable through the agent no matter how the function behaves.
# ---------------------------------------------------------------------------

schema = next(s for s in all_schemas() if s["name"] == "calc_ro_normalization")
assert schema["input_schema"]["required"] == ["current", "baseline"], schema
for state in ("current", "baseline"):
    props = schema["input_schema"]["properties"][state]
    # Both states are still mandatory; the readings inside them are not.
    assert props["required"] == ["feed_pressure", "concentrate_pressure"], \
        props["required"]
    # All eight are still offered, so the model knows what to ask the operator
    # for — the schema stops compelling them, it does not stop describing them.
    assert len(props["properties"]) == 8, sorted(props["properties"])
    assert "recovery_pct" in props["properties"], sorted(props["properties"])
    # Every reading but recovery is a quantity, so the model states the unit
    # the operator used instead of converting.
    assert props["properties"]["feed_pressure"]["required"] == ["value", "unit"]

# The push to go and get the missing readings moved from the schema into the
# description, so that is where it is now asserted. Losing any of these turns
# graceful degradation into an excuse to stop asking.
for phrase in ["ASK for all eight readings", "ASK for it",
               "NEVER assume, estimate, or substitute design values",
               "names the ones it could not",
               "rather than presenting a partial comparison as a complete one"]:
    assert phrase in schema["description"], phrase

got = dispatch("calc_ro_normalization", {"current": CURRENT, "baseline": BASELINE})
assert str(got) == out["summary"], "dispatch must hand the model the summary"
assert got.trace["result"] == out["result"], got.trace["result"]
print("registered; both states required, individual readings are not")

print("\nall calc_ro_normalization tests passed")
