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
from tools.calculators.calc_food_to_microorganism_ratio import LB_PER_KG
from tools.calculators.calc_solids_loading_rate import LB_FT2_PER_KG_M2
from tools.calculators.calc_surface_overflow_rate import GPD_FT2_PER_M_PER_D
from tools.registry import ToolResult, all_schemas, dispatch

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
    # Behaviour is covered in test_calc_ph_adjustment.py; this entry keeps it
    # in the shared schema-conformance loop with every other calculator.
    "calc_ph_adjustment": {
        "reagent": "caustic_soda",
        "input_mode": "from_titration",
        "titrant_basis": "pure_reagent",
        "bench_method": "volumetric",
        "titrant_volume": {"value": 6.5, "unit": "mL"},
        "titrant_normality": 0.02,
        "sample_volume": {"value": 1000, "unit": "mL"},
        "plant_flow": {"value": 7.5, "unit": "MGD"},
        "product_strength_percent": 25,
        "solution_density": {"value": 1.28, "unit": "kg/L"},
        "endpoint_ph": 7.2,
        "target_ph": 7.2,
        "plant_nitrifies": False,
        "sample_source": "mixed liquor",
        "dosing_point": "mixed liquor",
    },
    "calc_surface_overflow_rate": {
        "flow": {"value": 45, "unit": "L/s"},
        "area": {"value": 707, "unit": "m2"},
    },
    "calc_hydraulic_retention_time": {
        "volume": {"value": 5000, "unit": "m3"},
        "flow": {"value": 10, "unit": "MLD"},
    },
    "calc_sludge_quantity": {
        "flow": {"value": 10, "unit": "MLD"},
        "influent_ss": {"value": 220, "unit": "mg/L"},
        "effluent_ss": {"value": 90, "unit": "mg/L"},
        "sludge_dry_solids_pct": 4.0,
        "sludge_sg": 1.02,
    },
    "calc_food_to_microorganism_ratio": {
        "flow": {"value": 10, "unit": "MLD"},
        "bod": {"value": 200, "unit": "mg/L"},
        "reactor_volume": {"value": 3000, "unit": "m3"},
        "mlvss": {"value": 2500, "unit": "mg/L"},
    },
    # Behaviour is covered in test_calc_srt.py; this entry keeps it in the
    # shared schema-conformance loop with every other calculator.
    "calc_srt": {
        "basis": "aerobic",
        "waste_location": "ras_line",
        "solids_basis": "TSS",
        "aeration_volume": {"value": 5000, "unit": "m3"},
        "mlss": {"value": 3000, "unit": "mg/L"},
        "waste_flow": {"value": 200, "unit": "m3/d"},
        "waste_solids": {"value": 8000, "unit": "mg/L"},
        "influent_flow": {"value": 10, "unit": "MLD"},
        "effluent_solids": {"value": 15, "unit": "mg/L"},
    },
    "calc_solids_loading_rate": {
        "wastewater_flow": {"value": 10000, "unit": "m3/d"},
        "ras_flow": {"value": 5000, "unit": "m3/d"},
        "mlss": {"value": 3000, "unit": "mg/L"},
        "clarifier_area": {"value": 1500, "unit": "m2"},
    },
    # Behaviour is covered in test_calc_svi.py; this entry keeps it in the
    # shared schema-conformance loop with every other calculator.
    "calc_svi": {
        "settled_volume": {"value": 200, "unit": "mL/L"},
        "mlss": {"value": 2500, "unit": "mg/L"},
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

# 11. The duplicate-name guard tolerates a module imported twice (which is what
#     `python -m tools.calculators.calc_ct` does) but still catches a real
#     collision. The second case is the one that matters: relaxing the guard to
#     just "defined in __main__" would let any script silently shadow a tool.
from tools.registry import tool  # noqa: E402

try:
    @tool(name="calc_ct", description="x", input_schema={})
    def impostor():
        pass
    raise AssertionError("a genuinely different function must not be allowed")
except ValueError as e:
    assert "duplicate tool name" in str(e), e

try:
    @tool(name="calc_ct", description="x", input_schema={})
    def calc_ct():  # noqa: F811  — same qualname, different file
        pass
    raise AssertionError("same qualname from another file must not be allowed")
except ValueError as e:
    assert "duplicate tool name" in str(e), e
print("duplicate-name guard still catches real collisions")

# 12. HRT known value: 5000 m3 at 10 ML/d = 12 h exactly. Cross-checks against
#     calc_ct, whose theoretical detention time is the same V/Q calculation.
hrt = dispatch("calc_hydraulic_retention_time",
               CALCULATOR_CASES["calc_hydraulic_retention_time"])
assert hrt.trace["result"] == {"hrt_h": 12.0, "hrt_min": 720.0, "hrt_d": 0.5}, \
    hrt.trace["result"]

ct_detention = dispatch("calc_ct", KNOWN_CASE).trace["steps"][1]  # V / Q, minutes
hrt_same = dispatch("calc_hydraulic_retention_time", {
    "volume": {"value": 150, "unit": "m3"},
    "flow": {"value": 45, "unit": "L/s"},
})
assert abs(hrt_same.trace["result"]["hrt_min"] - ct_detention["value"]) < 0.1, (
    "HRT must agree with calc_ct's theoretical detention time: "
    f"{hrt_same.trace['result']['hrt_min']} vs {ct_detention['value']}"
)
print("HRT known value correct and agrees with calc_ct's detention time")

# 13. Sludge mass balance: 10 ML/d x (220-90) mg/L = 1300 kg/d dry solids;
#     at 4% DS that is 32500 kg/d wet, and at SG 1.02, 31.86 m3/d.
sl = dispatch("calc_sludge_quantity", CALCULATOR_CASES["calc_sludge_quantity"])
r = sl.trace["result"]
assert r["ss_removed_mgl"] == 130.0, r
assert r["dry_solids_kg_per_day"] == 1300.0, r
assert r["wet_sludge_kg_per_day"] == 32500.0, r
assert r["wet_sludge_m3_per_day"] == 31.86, r
assert r["removal_pct"] == 59.1, r

# Omitting SG is allowed but caveated; mass is unaffected, volume is not.
no_sg = dispatch("calc_sludge_quantity", {
    k: v for k, v in CALCULATOR_CASES["calc_sludge_quantity"].items()
    if k != "sludge_sg"
})
assert len(no_sg.trace["caveats"]) == 1, no_sg.trace["caveats"]
assert "specific gravity was not given" in no_sg.trace["caveats"][0].lower()
assert no_sg.trace["result"]["wet_sludge_kg_per_day"] == r["wet_sludge_kg_per_day"]
assert no_sg.trace["result"]["wet_sludge_m3_per_day"] > r["wet_sludge_m3_per_day"]

# Effluent SS above influent is a data error, not a negative sludge yield.
try:
    dispatch("calc_sludge_quantity", {
        "flow": {"value": 10, "unit": "MLD"},
        "influent_ss": {"value": 90, "unit": "mg/L"},
        "effluent_ss": {"value": 220, "unit": "mg/L"},
        "sludge_dry_solids_pct": 4.0,
    })
    raise AssertionError("inverted SS must raise")
except ValueError as e:
    assert "exceeds influent" in str(e), e
print("sludge mass balance correct; SG caveat and inverted-SS guard both fire")

# 14. F:M = (Q x BOD) / (V x MLVSS). 10 ML/d x 200 mg/L = 2000 kg BOD/d over
#     3000 m3 x 2500 mg/L = 7500 kg MLVSS, so 0.2667 -> 0.267 /day.
fm = dispatch("calc_food_to_microorganism_ratio",
              CALCULATOR_CASES["calc_food_to_microorganism_ratio"])
fr = fm.trace["result"]
assert fr["food_kg_per_day"] == 2000.0, fr
assert fr["mlvss_inventory_kg"] == 7500.0, fr
assert fr["fm_ratio_per_day"] == 0.267, fr

# F:M is a ratio of two masses, so it must be identical in US units even
# though every intermediate differs. This is the property worth pinning.
fm_us = dispatch("calc_food_to_microorganism_ratio", {
    "flow": {"value": 2.6417, "unit": "MGD"},
    "bod": {"value": 200, "unit": "ppm"},
    "reactor_volume": {"value": 0.79252, "unit": "MG"},
    "mlvss": {"value": 2500, "unit": "ppm"},
})
assert fm_us.trace["result"]["fm_ratio_per_day"] == fr["fm_ratio_per_day"], (
    f"F:M must be unit-system independent: "
    f"{fm_us.trace['result']['fm_ratio_per_day']} vs {fr['fm_ratio_per_day']}"
)

# The lb conversion must match pint, not a remembered constant.
expected_lb = (1 * UREG("kg")).to("pound").magnitude
assert abs(LB_PER_KG - expected_lb) < 1e-9, f"{LB_PER_KG} != pint's {expected_lb}"
assert fr["food_lb_per_day"] == round(2000.0 * expected_lb, 1), fr

# MLVSS is the denominator: zero must raise, not divide by zero.
try:
    dispatch("calc_food_to_microorganism_ratio", {
        "flow": {"value": 10, "unit": "MLD"},
        "bod": {"value": 200, "unit": "mg/L"},
        "reactor_volume": {"value": 3000, "unit": "m3"},
        "mlvss": {"value": 0, "unit": "mg/L"},
    })
    raise AssertionError("zero MLVSS must raise")
except ValueError as e:
    assert "MLVSS must be positive" in str(e), e
print("F:M correct, unit-system independent, zero-MLVSS guarded")

# 15. Sludge age. calc_mean_cell_residence_time is GONE — SRT, MCRT and sludge
#     age are one quantity, and calc_srt is the only tool that computes it.
#
#     The removed tool offered V / Q_waste for when no solids data was
#     available. That identity holds only when wasting is from the aeration
#     basin (MLSS cancels) and effluent solids are negligible; off the RAS line
#     it overstates sludge age by the thickening factor. It was a convenient
#     door into the exact error calc_srt exists to catch.
assert not any(s["name"] == "calc_mean_cell_residence_time" for s in all_schemas())
try:
    dispatch("calc_mean_cell_residence_time", {
        "reactor_volume": {"value": 35000, "unit": "m3"},
        "waste_flow": {"value": 10000, "unit": "m3/d"},
    })
    raise AssertionError("calc_mean_cell_residence_time must no longer exist")
except ValueError as e:
    assert "unknown tool" in str(e), e

# calc_srt owns the vocabulary, so the model has somewhere to go.
srt_desc = next(s for s in all_schemas() if s["name"] == "calc_srt")["description"]
for term in ("MCRT", "mean cell residence time", "sludge age"):
    assert term in srt_desc, term

# Asking for sludge age without MLSS is the removed tool's use case arriving at
# calc_srt's door. It must name what is missing and why no approximation is
# offered, rather than reporting an unknown count nobody can act on.
try:
    dispatch("calc_srt", {
        "basis": "aerobic", "waste_location": "aeration_basin",
        "solids_basis": "TSS",
        "aeration_volume": {"value": 35000, "unit": "m3"},
        "waste_flow": {"value": 10000, "unit": "m3/d"},
        "influent_flow": {"value": 30000, "unit": "m3/d"},
        "effluent_solids": {"value": 0, "unit": "mg/L"},
    })
    raise AssertionError("SRT without MLSS must raise")
except ValueError as e:
    assert "MLSS was not supplied" in str(e), e
    assert "Ask the operator" in str(e), e
    assert "no short-cut" in str(e), e
    # And it must not hand back the V/Q number it just declined to compute.
    assert "3.5" not in str(e), e

# The arithmetic the short-cut relied on is still correct, and calc_srt
# reproduces it exactly on the settings where the identity actually holds —
# basin wasting, zero effluent solids — at ANY MLSS, since it cancels. This is
# what was lost as a tool and kept as a property.
for any_mlss in (1500, 3000, 4500):
    equiv = dispatch("calc_srt", {
        "basis": "aerobic",
        "waste_location": "aeration_basin",
        "solids_basis": "TSS",
        "aeration_volume": {"value": 35000, "unit": "m3"},
        "mlss": {"value": any_mlss, "unit": "mg/L"},
        "waste_flow": {"value": 10000, "unit": "m3/d"},
        "influent_flow": {"value": 30000, "unit": "m3/d"},
        "effluent_solids": {"value": 0, "unit": "mg/L"},
    })
    assert equiv.trace["result"]["srt_days"] == 3.5, (any_mlss, equiv.trace["result"])
print("calc_mean_cell_residence_time removed; calc_srt owns sludge age and "
      "asks for MLSS rather than approximating")

# 16. SLR = (Q_WW + Q_RAS) x MLSS / area.
#     15000 m3/d x 3000 mg/L = 45000 kg/d over 1500 m2 = 30.00 kg/m2/d.
slr = dispatch("calc_solids_loading_rate",
               CALCULATOR_CASES["calc_solids_loading_rate"])
sr = slr.trace["result"]
assert sr["combined_flow_m3_per_day"] == 15000.0, sr
assert sr["solids_load_kg_per_day"] == 45000.0, sr
assert sr["slr_kg_m2_per_day"] == 30.0, sr
assert sr["ras_pct_of_flow"] == 50.0, sr

expected_lb_ft2 = (1 * UREG("kg/meter**2/day")).to("pound/foot**2/day").magnitude
assert abs(LB_FT2_PER_KG_M2 - expected_lb_ft2) < 1e-12, LB_FT2_PER_KG_M2
assert sr["slr_lb_ft2_per_day"] == round(30.0 * expected_lb_ft2, 2), sr

# The source's US shortcut (MGD x mg/L x 8.34 = lb/d) agrees to within the
# rounding of 8.34 itself — the exact factor is 8.3454, so ~0.07% apart.
via_834 = (15000 / 3785.411784) * 3000 * 8.34 * 0.45359237
assert abs(via_834 - sr["solids_load_kg_per_day"]) / sr["solids_load_kg_per_day"] < 0.001

# RAS is included in SLR but excluded from SOR — the two must differ.
sor = dispatch("calc_surface_overflow_rate", {
    "flow": {"value": 10000, "unit": "m3/d"},
    "area": {"value": 1500, "unit": "m2"},
})
assert sor.trace["result"]["sor_m3_m2_d"] == 6.67, sor.trace["result"]
print("SLR correct, lb/ft2/d matches pint, agrees with the 8.34 shortcut")

print("\nall tests passed")
