"""
Tests for the calculator trace — python test_calc_ct_steps.py

calc_ct is covered in detail, then every calculator is checked against the
shape documented in tools/calculators/__init__.py. That shape is a convention
rather than an enforced contract, so this file is what keeps it honest.

The point of the summary assertions is regression, not arithmetic: "summary"
must stay byte-for-byte what each calculator returned before the trace was
added, because the eval harness and the CLI read that string. Both expected
summaries were captured from the pre-change functions, not written by hand.

Plain asserts to match units.py and the calculators — this repo has no pytest.
"""

from tools.calculators.calc_ct import calc_ct
from tools.calculators.calc_surface_overflow_rate import GPD_FT2_PER_M_PER_D
from tools.registry import ToolResult, dispatch

KNOWN_CASE = dict(
    volume={"value": 150, "unit": "m3"},
    flow={"value": 45, "unit": "L/s"},
    residual={"value": 0.8, "unit": "mg/L"},
    temperature={"value": 5, "unit": "degC"},
    ph=7.2,
    baffling_factor=0.5,
)

# — is the em-dash, spelled with an escape so this file's encoding can
# never silently change what the test is pinning.
EXPECTED_SUMMARY = (
    "Inputs:\n"
    "  150 m3\n"
    "  45 L/s\n"
    "  0.8 mg/L\n"
    "  5 degC\n"
    "\n"
    "Theoretical detention time: 55.6 min\n"
    "T10 (x baffling factor 0.5): 27.8 min\n"
    "CT achieved: 22.2 mg.min/L\n"
    "CT required (0.5-log Giardia, 5.0 C, pH 7.2): 19.0 mg.min/L\n"
    "CT ratio: 1.17 — PASS"
)

STEP_KEYS = {"label", "formula", "substituted", "value", "unit"}

out = calc_ct(**KNOWN_CASE)

# 1. The operator-facing string is unchanged.
assert out["summary"] == EXPECTED_SUMMARY, (
    "summary drifted from the pre-change output:\n"
    f"  expected: {EXPECTED_SUMMARY!r}\n"
    f"  got:      {out['summary']!r}"
)
print("summary is byte-for-byte unchanged")

# 2. The headline numbers are exposed structurally.
assert out["result"]["ct_actual"] == 22.2, out["result"]
assert out["result"]["ct_required"] == 19.0, out["result"]
assert out["result"]["ct_ratio"] == 1.17, out["result"]
assert out["result"]["verdict"] == "PASS", out["result"]
print("result block correct")

# 3. Every step is fully populated — a UI can render any entry without a
#    None check, and a step added later cannot quietly omit a field.
assert out["steps"], "expected at least one step"
for i, step in enumerate(out["steps"]):
    assert set(step) == STEP_KEYS, f"step {i} has keys {set(step)}, want {STEP_KEYS}"
    assert isinstance(step["value"], (int, float)), f"step {i} value not numeric"
print(f"all {len(out['steps'])} steps have the five required keys")

# 4. The trace agrees with the summary rather than being a parallel truth.
by_label = {s["label"]: s for s in out["steps"]}
assert by_label["CT achieved"]["value"] == out["result"]["ct_actual"]
assert by_label["CT ratio"]["value"] == out["result"]["ct_ratio"]
print("steps agree with the result block")

# 5. dispatch hands the model a string, with the trace alongside.
dispatched = dispatch("calc_ct", KNOWN_CASE)
assert isinstance(dispatched, str), "the agent loop requires a string"
assert str(dispatched) == EXPECTED_SUMMARY, "dispatch must not reshape the summary"
assert isinstance(dispatched, ToolResult)
assert dispatched.trace["result"]["ct_actual"] == 22.2
print("dispatch returns the summary string with .trace attached")

# 6. A tool that still returns a plain string is untouched by the unwrapping.
#    Retrievals and lookups have no trace, and dispatch must not invent one.
plain = dispatch("search_manuals", {"query": "actuator torque"})
assert isinstance(plain, str)
assert not isinstance(plain, ToolResult), "string tools should pass straight through"
print("plain-string tools pass through unchanged")

# 7. Every calculator follows the shape documented in tools/calculators/__init__.py.
#    This is a convention rather than an enforced contract, so it is checked here.
CALCULATOR_CASES = {
    "calc_ct": KNOWN_CASE,
    "calc_chemical_feed": {
        "flow": {"value": 10, "unit": "MLD"},
        "dose": {"value": 2.5, "unit": "mg/L"},
        "solution_strength_pct": 12.5,
        "solution_sg": 1.15,
    },
    "calc_surface_overflow_rate": {
        "flow": {"value": 45, "unit": "L/s"},
        "area": {"value": 707, "unit": "m2"},
    },
}
SHAPE = {"summary": str, "result": dict, "steps": list,
         "conversions": list, "caveats": list}

for tool_name, case in CALCULATOR_CASES.items():
    got = dispatch(tool_name, case)
    assert isinstance(got, ToolResult), f"{tool_name} should carry a trace"
    trace = got.trace
    assert set(trace) == set(SHAPE), f"{tool_name} keys: {set(trace)}"
    for key, want_type in SHAPE.items():
        assert isinstance(trace[key], want_type), f"{tool_name}.{key} is not {want_type}"
    assert str(got) == trace["summary"], f"{tool_name} summary must reach the model"
    assert trace["steps"], f"{tool_name} has no steps"
    for i, step in enumerate(trace["steps"]):
        assert set(step) == STEP_KEYS, f"{tool_name} step {i}: {set(step)}"
        assert isinstance(step["value"], (int, float)), f"{tool_name} step {i} value"
    assert trace["result"], f"{tool_name} result is empty"
    print(f"{tool_name} conforms ({len(trace['steps'])} steps)")

# 8. calc_chemical_feed's summary is also unchanged by its retrofit.
FEED_SUMMARY = (
    "Inputs:\n"
    "  10 MLD = 115.7 L/s\n"
    "  2.5 mg/L\n"
    "\n"
    "Neat chemical required: 25.00 kg/day\n"
    "Solution feed rate (12.5% w/w, SG 1.15): 173.9 L/day (0.12 L/min)"
)
feed = dispatch("calc_chemical_feed", CALCULATOR_CASES["calc_chemical_feed"])
assert str(feed) == FEED_SUMMARY, f"feed summary drifted:\n  got: {str(feed)!r}"
assert feed.trace["result"]["neat_kg_per_day"] == 25.0, feed.trace["result"]
print("calc_chemical_feed summary is byte-for-byte unchanged")

# 9. Surface overflow rate: known value, and the same physical case in US units.
#    45 L/s = 3888 m3/d over 707 m2 = 5.499... m3/m2/d.
sor = dispatch("calc_surface_overflow_rate", CALCULATOR_CASES["calc_surface_overflow_rate"])
assert sor.trace["result"]["sor_m3_m2_d"] == 5.5, sor.trace["result"]
assert sor.trace["result"]["sor_m_per_h"] == 0.229, sor.trace["result"]

us = dispatch("calc_surface_overflow_rate", {
    "flow": {"value": 1.0271, "unit": "MGD"},
    "area": {"value": 7610, "unit": "ft2"},
})
assert abs(us.trace["result"]["sor_m3_m2_d"] - 5.5) < 0.05, us.trace["result"]
print("surface overflow rate agrees across metric and US units")

# The hardcoded gpd/ft2 constant must match pint, not a remembered number.
from units import UREG  # noqa: E402

expected_gpd_ft2 = (
    (1 * UREG("meter**3/day") / UREG("meter**2"))
    .to("US_liquid_gallon/day/foot**2").magnitude
)
assert abs(GPD_FT2_PER_M_PER_D - expected_gpd_ft2) < 1e-9, (
    f"constant {GPD_FT2_PER_M_PER_D} != pint's {expected_gpd_ft2}"
)
print("gpd/ft2 conversion constant matches pint")

# 10. calc_chemical_feed caveats an assumed strength/SG, but not a stated one.
#     The None sentinels are what make that distinction possible, so the
#     "explicitly 1.0" case below is the one that would regress if they went.
bare = dispatch("calc_chemical_feed", {
    "flow": {"value": 10, "unit": "MLD"},
    "dose": {"value": 10, "unit": "mg/L"},
})
assert len(bare.trace["caveats"]) == 2, bare.trace["caveats"]
assert "Solution strength was not given" in bare.trace["caveats"][0]
assert "Specific gravity was not given" in bare.trace["caveats"][1]
assert "CAVEAT:" in str(bare), "caveats must reach the operator, not just the trace"

stated = dispatch("calc_chemical_feed", {
    "flow": {"value": 10, "unit": "MLD"},
    "dose": {"value": 10, "unit": "mg/L"},
    "solution_strength_pct": 100,
    "solution_sg": 1.0,
})
assert stated.trace["caveats"] == [], (
    "stating 100% w/w / SG 1.0 explicitly must not be caveated as an assumption: "
    f"{stated.trace['caveats']}"
)
# Same numbers either way — the sentinels change what is said, not what is computed.
assert stated.trace["result"] == bare.trace["result"], "defaults changed the maths"

one = dispatch("calc_chemical_feed", {
    "flow": {"value": 10, "unit": "MLD"},
    "dose": {"value": 10, "unit": "mg/L"},
    "solution_sg": 1.53,
})
assert len(one.trace["caveats"]) == 1, one.trace["caveats"]
assert "Solution strength" in one.trace["caveats"][0]
print("chemical feed caveats fire only on assumed values")

print("\nall tests passed")
