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
    if isinstance(want, bool):
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

# Seawater-range TDS is outside the osmotic approximation's range.
brackish = calc_ro_normalization(current=CURRENT, baseline=BASELINE)["caveats"]
assert not any("osmotic" in c for c in brackish), brackish

salty = {
    **BASELINE,
    "feed_tds": {"value": OSMOTIC_TDS_LIMIT_MG_L + 1000, "unit": "mg/L"},
    "feed_pressure": {"value": 40.0, "unit": "bar"},
    "concentrate_pressure": {"value": 38.95, "unit": "bar"},
}
salty_caveats = calc_ro_normalization(current=salty, baseline=salty)["caveats"]
assert any("osmotic-pressure approximation" in c for c in salty_caveats), salty_caveats

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

# A partial baseline is the failure mode the schema exists to prevent: the
# model must ask the operator, not fill the gap with a design value.
try:
    calc_ro_normalization(
        current=CURRENT,
        baseline={k: v for k, v in BASELINE.items() if k != "permeate_tds"},
    )
    raise AssertionError("an incomplete baseline must raise")
except ValueError as e:
    assert "permeate_tds" in str(e), e
    assert "do not assume" in str(e), e
    assert "baseline" in str(e), e

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
print("incomplete baseline and swapped dimensions both raise")

# ---------------------------------------------------------------------------
# 9. The trace. Steps are the working an operator can check, so both states
#    have to appear in it — a trace that shows only the current readings would
#    hide half of the comparison.
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
# 10. Registration. The whole point of the move into tools/calculators/ is that
#     the agent can reach it, with a schema that demands both states.
# ---------------------------------------------------------------------------

schema = next(s for s in all_schemas() if s["name"] == "calc_ro_normalization")
assert schema["input_schema"]["required"] == ["current", "baseline"], schema
for state in ("current", "baseline"):
    props = schema["input_schema"]["properties"][state]
    assert len(props["required"]) == 8, props["required"]
    assert "recovery_pct" in props["required"], props["required"]
    # Every reading but recovery is a quantity, so the model states the unit
    # the operator used instead of converting.
    assert props["properties"]["feed_pressure"]["required"] == ["value", "unit"]
assert "ASK for them" in schema["description"], schema["description"]

got = dispatch("calc_ro_normalization", {"current": CURRENT, "baseline": BASELINE})
assert str(got) == out["summary"], "dispatch must hand the model the summary"
assert got.trace["result"] == out["result"], got.trace["result"]
print("registered with dispatch; both states required by the schema")

print("\nall calc_ro_normalization tests passed")
