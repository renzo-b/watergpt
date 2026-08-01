"""
Tests for calc_svi — python test_calc_svi.py

Plain asserts to match units.py and the other calculators — this repo has no
pytest. Schema conformance for calc_svi is covered by the CALCULATOR_CASES loop
in test_calc_ct_steps.py; this file covers the SVI-specific behaviour.
"""

from tools.calculators.calc_svi import SETTLING_LIMIT_ML_L, calc_svi
from tools.registry import dispatch
from units import UnitError

STEP_KEYS = {"label", "formula", "substituted", "value", "unit"}


def step_by_label(out, label):
    for s in out["steps"]:
        if s["label"] == label:
            return s
    return None


# 1. Worked example from the source: a 250 mL/L reading taken with 850 mL/L of
#    effluent is 15% mixed liquor, so it corrects to 250 x 1000 / 150 = 1667.
out = calc_svi(
    settled_volume={"value": 250, "unit": "mL/L"},
    mlss={"value": 2500, "unit": "mg/L"},
    dilution_ml=850,
)
correction = step_by_label(out, "Dilution correction")
assert correction is not None, [s["label"] for s in out["steps"]]
assert abs(correction["value"] - 1667) < 1, correction
assert correction["unit"] == "mL/L", correction
assert correction is out["steps"][0], "the correction must come first"
assert set(correction) == STEP_KEYS, set(correction)
assert abs(out["result"]["settled_volume_used_ml_per_l"] - 1667) < 1, out["result"]
assert out["result"]["observed_settled_volume_ml_per_l"] == 250.0, out["result"]
print(f"dilution correction: 250 mL/L at 850 mL/L dilution -> "
      f"{correction['value']} mL/L")

# 2. Undiluted: 200 mL/L over 2500 mg/L is 80 mL/g, and no threshold caveat.
out = calc_svi(
    settled_volume={"value": 200, "unit": "mL/L"},
    mlss={"value": 2500, "unit": "mg/L"},
)
assert out["result"]["svi_ml_per_g"] == 80.0, out["result"]
assert out["result"]["above_direct_reading_limit"] is False, out["result"]
assert step_by_label(out, "Dilution correction") is None, "no dilution was given"
threshold_caveats = [c for c in out["caveats"] if "250" in c]
assert not threshold_caveats, threshold_caveats
# The interpretation caveat still fires, and 80 mL/g is the "good" band.
assert len(out["caveats"]) == 1, out["caveats"]
assert "good settling" in out["caveats"][0], out["caveats"][0]
print("undiluted: 200 mL/L over 2500 mg/L -> 80.0 mL/g, no threshold caveat")

# 3. Over the limit with no dilution: still answered, but clearly flagged.
out = calc_svi(
    settled_volume={"value": 750, "unit": "mL/L"},
    mlss={"value": 2500, "unit": "mg/L"},
)
assert out["result"]["svi_ml_per_g"] == 300.0, out["result"]
assert out["result"]["above_direct_reading_limit"] is True, out["result"]
flagged = " ".join(out["caveats"])
assert "250" in flagged, flagged
assert "diluted settling test" in flagged, flagged
assert "200 and 250" in flagged, flagged
assert "UNRELIABLE" in flagged, flagged
# A value is still returned rather than the tool refusing.
assert out["summary"].count("SVI: 300.0 mL/g") == 1, out["summary"]
print("750 mL/L undiluted -> 300.0 mL/g returned, flagged against the "
      f"{SETTLING_LIMIT_ML_L:g} mL/L limit")

# 4. A dilution of 1000 mL/L would leave no mixed liquor and divide by zero.
for bad in (1000, 1200):
    try:
        calc_svi(
            settled_volume={"value": 250, "unit": "mL/L"},
            mlss={"value": 2500, "unit": "mg/L"},
            dilution_ml=bad,
        )
        raise AssertionError(f"dilution_ml={bad} must raise")
    except ValueError as e:
        assert "less than 1000" in str(e), e
try:
    calc_svi(
        settled_volume={"value": 250, "unit": "mL/L"},
        mlss={"value": 2500, "unit": "mg/L"},
        dilution_ml=-1,
    )
    raise AssertionError("negative dilution must raise")
except ValueError as e:
    assert "negative" in str(e), e
print("dilution_ml of 1000, 1200 and -1 all rejected")

# ---------------------------------------------------------------------------
# Beyond the four required cases: the properties that would break quietly.
# ---------------------------------------------------------------------------

# Settled volume is a ratio, not a concentration. Swapping it with MLSS must
# raise rather than compute — this is the whole reason for the volume_ratio
# dimension, since mL/L is dimensionless and mg/L is not.
try:
    calc_svi(
        settled_volume={"value": 2500, "unit": "mg/L"},
        mlss={"value": 200, "unit": "mL/L"},
    )
    raise AssertionError("swapped arguments must raise")
except UnitError as e:
    assert "volume_ratio" in str(e), e
print("swapped settled_volume/MLSS rejected on dimensionality")

# 20% settled volume is 200 mL/L, so it must give the same SVI as case 2.
pct = calc_svi(
    settled_volume={"value": 20, "unit": "%"},
    mlss={"value": 2500, "unit": "mg/L"},
)
assert pct["result"]["svi_ml_per_g"] == 80.0, pct["result"]
print("20% settled volume == 200 mL/L -> same 80.0 mL/g")

# A diluted test whose OBSERVED reading is still over the limit is a different
# failure from a corrected value over the limit, and must say so.
still_high = calc_svi(
    settled_volume={"value": 400, "unit": "mL/L"},
    mlss={"value": 2500, "unit": "mg/L"},
    dilution_ml=500,
)
joined = " ".join(still_high["caveats"])
assert "UNRELIABLE" in joined, joined
assert "Dilute further" in joined, joined

in_range = calc_svi(
    settled_volume={"value": 250, "unit": "mL/L"},
    mlss={"value": 2500, "unit": "mg/L"},
    dilution_ml=850,
)
joined = " ".join(in_range["caveats"])
assert "UNRELIABLE" not in joined, joined
assert "expected for a diluted test" in joined, joined
print("diluted test distinguishes an out-of-range reading from a high correction")

# Interpretation bands land on the right side of each boundary.
for settled, expect in [(99, "good settling"), (100, "acceptable settling"),
                        (150, "acceptable settling"), (151, "bulking")]:
    # MLSS of 1000 mg/L makes SVI numerically equal to the settled volume.
    r = calc_svi(
        settled_volume={"value": settled, "unit": "mL/L"},
        mlss={"value": 1000, "unit": "mg/L"},
    )
    assert r["result"]["svi_ml_per_g"] == float(settled), r["result"]
    assert expect in r["caveats"][-1], (settled, r["caveats"][-1])
print("interpretation bands correct at 99 / 100 / 150 / 151 mL/g")

# Zero and negative MLSS must raise, not divide by zero.
for bad_mlss in (0, -100):
    try:
        calc_svi(
            settled_volume={"value": 200, "unit": "mL/L"},
            mlss={"value": bad_mlss, "unit": "mg/L"},
        )
        raise AssertionError(f"MLSS={bad_mlss} must raise")
    except ValueError as e:
        assert "MLSS must be positive" in str(e), e
print("zero and negative MLSS rejected")

# The model must receive only the summary string, with the trace alongside.
d = dispatch("calc_svi", {
    "settled_volume": {"value": 200, "unit": "mL/L"},
    "mlss": {"value": 2500, "unit": "mg/L"},
})
assert isinstance(d, str), "the agent loop requires a string"
assert str(d) == d.trace["summary"], "dispatch must not reshape the summary"
assert d.trace["result"]["svi_ml_per_g"] == 80.0, d.trace["result"]
assert "steps" not in str(d), "the trace must not reach the model as text"
print("dispatch returns the summary string with .trace attached")

print("\nall tests passed")
