"""
Tests for calc_osmotic_pressure — python tests/test_calc_osmotic_pressure.py

Plain asserts to match units.py and the other calculators — this repo has no
pytest. Schema conformance is covered by the CALCULATOR_CASES loop in
test_calc_ct_steps.py; this file covers the physics.

The point of this module is that it is right OUTSIDE the range the old inline
approximation covered, so the tests that matter are the ones checking against
published values rather than against the code's own arithmetic. Standard
seawater is the anchor: 35 g/kg at 25 C is one of the most-measured numbers in
physical oceanography, and any correlation that misses it is wrong no matter
how self-consistent it is.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.calculators.calc_osmotic_pressure import (
    BRACKISH_TDS_LIMIT_MG_L, REFERENCES, SEAWATER_TDS_LIMIT_MG_L,
    SEA_SALT_MOLAR_MASS_G_MOL, calc_osmotic_pressure, osmotic_coefficient,
    osmotic_pressure_bar, salinity_kg_kg, seawater_density_kg_m3,
    select_osmotic_model,
)
from tools.registry import all_schemas, dispatch

STEP_KEYS = {"label", "formula", "substituted", "value", "unit"}


def close(got, want, tol, what):
    assert abs(got - want) <= abs(want) * tol, \
        f"{what}: {got} vs {want} (tol {tol:.1%})"


# ---------------------------------------------------------------------------
# 1. Density, Sharqawy Eq. (8). Checked against handbook values rather than
#    against itself — it is the conversion from the operator's mg/L to the
#    mass-fraction salinity the osmotic correlation is written in, so an error
#    here biases every seawater answer.
# ---------------------------------------------------------------------------

close(seawater_density_kg_m3(0.0, 25.0), 997.05, 0.001, "pure water at 25 C")
close(seawater_density_kg_m3(0.0, 100.0), 958.35, 0.001, "pure water at 100 C")
close(seawater_density_kg_m3(0.035, 25.0), 1023.6, 0.001, "seawater 35 g/kg 25 C")
close(seawater_density_kg_m3(0.035, 0.0), 1028.1, 0.002, "seawater 35 g/kg 0 C")
# Salt raises density, heat lowers it. Both directions, or a sign error hides.
assert seawater_density_kg_m3(0.035, 25) > seawater_density_kg_m3(0.0, 25)
assert seawater_density_kg_m3(0.035, 25) > seawater_density_kg_m3(0.035, 60)
print("density: pure water and standard seawater match handbook values")

# ---------------------------------------------------------------------------
# 2. Osmotic coefficient, Sharqawy Eq. (49). Standard seawater sits near 0.90;
#    Fig. 18 of the paper puts the S = 20 and S = 40 g/kg curves either side of
#    it at 25 C, which is the shape asserted here.
# ---------------------------------------------------------------------------

phi_35 = osmotic_coefficient(0.035, 25.0)
close(phi_35, 0.907, 0.01, "osmotic coefficient of seawater at 25 C")
assert 0.85 < phi_35 < 0.95, phi_35
# Monotonic in salinity at fixed temperature, per Fig. 18.
assert (osmotic_coefficient(0.020, 25.0) < osmotic_coefficient(0.040, 25.0)
        < osmotic_coefficient(0.080, 25.0)), "phi must rise with salinity"
# And a shallow maximum in temperature, also per Fig. 18 — not monotonic.
assert osmotic_coefficient(0.035, 50.0) > osmotic_coefficient(0.035, 0.0)
assert osmotic_coefficient(0.035, 50.0) > osmotic_coefficient(0.035, 120.0)
print(f"osmotic coefficient: {phi_35:.4f} for standard seawater at 25 C")

# ---------------------------------------------------------------------------
# 3. mg/L to g/kg. An operator reads TDS per litre; the correlations are per
#    kilogram. At seawater strength the two differ by 2.3%, which is a
#    systematic bias if skipped, so the conversion is pinned directly.
# ---------------------------------------------------------------------------

s = salinity_kg_kg(35000.0, 25.0) * 1000.0
close(s, 34.19, 0.005, "35000 mg/L as g/kg")
assert s < 35.0, "mass per litre must exceed mass per kilogram in seawater"
# It has to be a fixed point of its own definition, not merely close.
close(35000.0 / seawater_density_kg_m3(s / 1000.0, 25.0) / 1000.0,
      s / 1000.0, 1e-9, "salinity iteration converged")
print(f"salinity: 35000 mg/L at 25 C is {s:.2f} g/kg")

# ---------------------------------------------------------------------------
# 4. THE anchor. Standard seawater, 35 g/kg at 25 C, has an osmotic pressure of
#    about 2.5-2.6 MPa. This is the number the whole seawater path exists to
#    get right, and the old brackish approximation missed it by ~30%.
# ---------------------------------------------------------------------------

# Fed as a mass fraction, so this is the textbook 35 g/kg and not 35000 mg/L.
tds_for_35_g_kg = 0.035 * seawater_density_kg_m3(0.035, 25.0) * 1000.0
pi_sw, model, _ = osmotic_pressure_bar(tds_for_35_g_kg, 25.0)
assert model == "seawater", model
close(pi_sw, 25.9, 0.03, "standard seawater osmotic pressure")
print(f"standard seawater (35 g/kg, 25 C): {pi_sw:.2f} bar")

# The same water read off a TDS meter in mg/L is slightly less salt per kg of
# water, and must come out slightly lower.
pi_35000, _, _ = osmotic_pressure_bar(35000.0, 25.0)
close(pi_35000, 25.3, 0.03, "35000 mg/L osmotic pressure")
assert pi_35000 < pi_sw, (pi_35000, pi_sw)

# The brackish shortcut on the same water — this is the error that made SWRO
# normalization wrong, so its size is pinned, not just its direction.
pi_shortcut, _, notes = osmotic_pressure_bar(35000.0, 25.0, "brackish_tds")
close(pi_shortcut, 33.2, 0.02, "brackish shortcut on seawater")
assert pi_shortcut / pi_35000 > 1.25, (pi_shortcut, pi_35000)
assert any("above the" in n for n in notes), notes
print(f"brackish shortcut on seawater: {pi_shortcut:.2f} bar, "
      f"{(pi_shortcut / pi_35000 - 1) * 100:.0f}% high — the reason for this module")

# The offset is close to CONSTANT across the range rather than something that
# only appears at seawater strength. That is why a comparison of two states
# survived the old shortcut while an absolute NDP did not, and it is worth
# pinning so nobody "fixes" the brackish default on the assumption that the
# shortcut is accurate down there.
ratios = [osmotic_pressure_bar(c, 25.0, "brackish_tds")[0]
          / osmotic_pressure_bar(c, 25.0, "seawater")[0]
          for c in (500, 2000, 10000, 35000, 70000)]
assert all(1.25 < r < 1.40 for r in ratios), ratios
assert max(ratios) - min(ratios) < 0.06, ratios

# ---------------------------------------------------------------------------
# 5. Physical behaviour across the range. Osmotic pressure rises with both
#    concentration and temperature, and in the dilute limit it is linear in
#    concentration (van 't Hoff) — non-linearity is a concentrated-solution
#    effect and must not appear at 1000 mg/L.
# ---------------------------------------------------------------------------

series = [osmotic_pressure_bar(c, 25.0)[0]
          for c in (1000, 5000, 10000, 35000, 70000, 100000)]
assert series == sorted(series), series
warm = [osmotic_pressure_bar(35000.0, t)[0] for t in (5, 15, 25, 40)]
assert warm == sorted(warm), warm

dilute_1, _, _ = osmotic_pressure_bar(500.0, 25.0, "seawater")
dilute_2, _, _ = osmotic_pressure_bar(1000.0, 25.0, "seawater")
close(dilute_2 / dilute_1, 2.0, 0.01, "van 't Hoff linearity when dilute")
# Concentrated solutions are NOT linear — 70 g/kg is not twice 35 g/kg.
conc_2, _, _ = osmotic_pressure_bar(70000.0, 25.0)
assert conc_2 / pi_35000 > 2.02, (conc_2, pi_35000)
print("osmotic pressure rises with TDS and temperature; linear only when dilute")

# ---------------------------------------------------------------------------
# 6. The brackish path must not have moved. calc_ro_normalization's pinned
#    figures were hand-checked against this exact expression, so it is asserted
#    to the bit rather than to a tolerance.
# ---------------------------------------------------------------------------

for tds, temp in [(800, 15), (1478.7, 15), (2000, 25), (9999, 30)]:
    want = (0.0385 * tds * (temp + 320.0) / (1000.0 - tds / 1000.0)) / 14.5038
    got, model, _ = osmotic_pressure_bar(tds, temp)
    assert model == "brackish_tds", (tds, model)
    assert got == want, f"brackish formula moved at {tds} mg/L: {got} vs {want}"
print("brackish shortcut is bit-identical to the formula it replaced")

# ---------------------------------------------------------------------------
# 7. Model selection. The boundary is where the shortcut's usual upper limit
#    and Eq. (49)'s stated lower limit meet, so it is one number and both sides
#    of it are pinned.
# ---------------------------------------------------------------------------

assert select_osmotic_model(BRACKISH_TDS_LIMIT_MG_L - 1) == "brackish_tds"
assert select_osmotic_model(BRACKISH_TDS_LIMIT_MG_L) == "seawater"
assert select_osmotic_model(45000) == "seawater"
# An explicit model overrides selection, and says so in the result.
forced = calc_osmotic_pressure(
    concentration={"value": 35000, "unit": "mg/L"},
    temperature={"value": 25, "unit": "degC"},
    model="brackish_tds",
)
assert forced["result"]["model"] == "brackish_tds", forced["result"]
assert forced["result"]["model_auto_selected"] is False, forced["result"]
assert any("set explicitly by the caller" in c for c in forced["caveats"])
auto = calc_osmotic_pressure(
    concentration={"value": 35000, "unit": "mg/L"},
    temperature={"value": 25, "unit": "degC"},
)
assert auto["result"]["model_auto_selected"] is True, auto["result"]
print("model selected at the 10000 mg/L boundary; explicit override reported")

# Near the boundary both models are arguable, so both numbers are reported.
straddle = calc_osmotic_pressure(
    concentration={"value": 12000, "unit": "mg/L"},
    temperature={"value": 20, "unit": "degC"},
)
assert "cross-check" in straddle["summary"], straddle["summary"]
assert any("disagree" in c for c in straddle["caveats"]), straddle["caveats"]
# Far from it, there is nothing to cross-check against.
assert "cross-check" not in auto["summary"], auto["summary"]
print("overlap band reports both correlations instead of a false precision")

# ---------------------------------------------------------------------------
# 8. Error paths. Readings no correlation covers must raise rather than
#    extrapolate silently — a confident osmotic pressure from a brine past the
#    fit is worse than none, because it feeds an NDP that looks decisive.
# ---------------------------------------------------------------------------

CASES = [
    ((SEAWATER_TDS_LIMIT_MG_L + 1, 25), "ceiling of the seawater correlation",
     "brine past the fit"),
    ((35000, -5), "outside the", "sub-zero temperature"),
    ((35000, 250), "outside the", "steam-range temperature"),
    ((-1, 25), "must not be negative", "negative TDS"),
]
for (tds, temp), fragment, why in CASES:
    try:
        osmotic_pressure_bar(tds, temp)
        raise AssertionError(f"expected a raise for: {why}")
    except ValueError as e:
        assert fragment in str(e), f"{why}: {e}"

try:
    osmotic_pressure_bar(35000, 25, "vant_hoff")
    raise AssertionError("an unknown model must raise")
except ValueError as e:
    assert "Unknown osmotic model" in str(e), e
print(f"{len(CASES) + 1} out-of-range cases all raise")

# A swapped dimension is caught by the quantity layer, as everywhere else.
try:
    calc_osmotic_pressure(
        concentration={"value": 13.5, "unit": "bar"},
        temperature={"value": 25, "unit": "degC"},
    )
    raise AssertionError("expected a UnitError for pressure as concentration")
except ValueError as e:
    assert "not a concentration unit" in str(e), e

# ---------------------------------------------------------------------------
# 9. The trace and the mean molar mass it rests on. 31.4038 g/mol is per mole
#    of IONS, not per mole of salt — using a per-molecule mass would overstate
#    the particle count and the pressure with it.
# ---------------------------------------------------------------------------

assert 31.0 < SEA_SALT_MOLAR_MASS_G_MOL < 32.0, SEA_SALT_MOLAR_MASS_G_MOL
sw = calc_osmotic_pressure(
    concentration={"value": 35000, "unit": "mg/L"},
    temperature={"value": 25, "unit": "degC"},
)
labels = [s["label"] for s in sw["steps"]]
assert labels == ["Salinity as a mass fraction", "Osmotic coefficient",
                  "Total ion molality", "Osmotic pressure"], labels
for step in sw["steps"]:
    assert set(step) == STEP_KEYS, step
# Steps are captured, not recalculated.
assert sw["steps"][-1]["value"] == sw["result"]["osmotic_pressure_bar"]

br = calc_osmotic_pressure(
    concentration={"value": 2000, "unit": "mg/L"},
    temperature={"value": 25, "unit": "degC"},
)
assert [s["label"] for s in br["steps"]] == [
    "Osmotic pressure", "Osmotic pressure in bar"], br["steps"]
assert br["steps"][-1]["value"] == br["result"]["osmotic_pressure_bar"]
# The two paths trace different working, which is the honest thing — they are
# different correlations, not one formula with a switch.
assert len(sw["steps"]) != len(br["steps"])
print(f"trace covers both correlations ({len(sw['steps'])} and "
      f"{len(br['steps'])} steps)")

# ---------------------------------------------------------------------------
# 10. Unit independence and registration.
# ---------------------------------------------------------------------------

ppm = calc_osmotic_pressure(
    concentration={"value": 35000, "unit": "ppm"},
    temperature={"value": 77, "unit": "degF"},
)
close(ppm["result"]["osmotic_pressure_bar"],
      sw["result"]["osmotic_pressure_bar"], 1e-9, "ppm/degF must agree")
assert any("ppm treated as mg/L" in c for c in ppm["conversions"]), \
    ppm["conversions"]

# ---------------------------------------------------------------------------
# 10b. Provenance. A correlation with no stated source cannot be checked, so
#      the citation travels in the caveats an operator actually reads — not
#      only in a module comment. The brackish shortcut's honest non-attribution
#      is asserted too: naming a manual that merely repeats it would be worse
#      than saying it is unestablished.
# ---------------------------------------------------------------------------

sw_sources = [c for c in sw["caveats"] if c.startswith("Source:")]
assert len(sw_sources) == 2, sw_sources
assert any("Sharqawy" in c and "Eq. (49)" in c and "Desalination and Water "
           "Treatment 16(1-3) 354-380" in c for c in sw_sources), sw_sources
assert any("Millero" in c and "31.4038" in c and "Deep-Sea Research I 55(1)"
           in c for c in sw_sources), sw_sources

br_sources = [c for c in br["caveats"] if c.startswith("Source:")]
assert len(br_sources) == 1, br_sources
assert "primary source not established" in br_sources[0], br_sources[0]
assert "0.0385" in br_sources[0], br_sources[0]

for ref in REFERENCES.values():
    assert len(ref) > 80, ref              # a bare name is not a citation
print("sources reach the operator in the caveats, brackish one unattributed")

schema = next(s for s in all_schemas() if s["name"] == "calc_osmotic_pressure")
assert schema["input_schema"]["required"] == ["concentration", "temperature"]
assert schema["input_schema"]["properties"]["model"]["enum"] == [
    "auto", "brackish_tds", "seawater"]
assert "Never compute it yourself" in schema["description"]

got = dispatch("calc_osmotic_pressure", {
    "concentration": {"value": 35000, "unit": "mg/L"},
    "temperature": {"value": 25, "unit": "degC"},
})
assert str(got) == sw["summary"], "dispatch must hand the model the summary"
assert got.trace["result"] == sw["result"], got.trace["result"]
print("g/L, ppm and degF all agree; registered with dispatch")

print("\nall calc_osmotic_pressure tests passed")
