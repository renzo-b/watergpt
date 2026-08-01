"""
Tests for calc_ph_adjustment — python test_calc_ph_adjustment.py

Plain asserts to match units.py and the other calculators — this repo has no
pytest. Schema conformance is covered by the CALCULATOR_CASES loop in
test_calc_ct_steps.py; this file covers the pH-adjustment behaviour.

Organised around the traps rather than the arithmetic. Stages 2 and 3 are
calc_chemical_feed's, already tested there, so what is worth testing here is
which stages run, on which basis, and what is refused.
"""

from tools.calculators.calc_chemical_feed import calc_chemical_feed
from tools.calculators.calc_ph_adjustment import (
    DENSITY_MAX, DENSITY_MIN, DOSE_SANITY_LIMIT_MG_L, PH_DIVERGENCE_LIMIT,
    REAGENTS, VOLUME_FRACTION_LIMIT, calc_ph_adjustment,
)
from tools.registry import all_schemas, dispatch
from units import UnitError

# The caustic worked example: 6.5 mL of 0.02 N NaOH into a 1000 mL sample is
# 0.13 meq, x 40.00 mg/meq = 5.2 mg, / 1 L = 5.2 mg/L. At 7.5 MGD
# (28.3906 ML/d) that is 147.63 kg/d of pure NaOH, 590.52 kg/d of 25% product,
# and 461.3 L/d at 1.28 kg/L.
CAUSTIC = {
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
}

# The lime worked example: 4.4 mg weighed into a 1000 mL sample is 4.4 mg/L,
# which at the same flow is 124.92 kg/d — with NO strength correction, because
# the operator weighed the product they actually feed.
LIME = {
    "reagent": "hydrated_lime",
    "input_mode": "from_titration",
    "titrant_basis": "as_delivered_product",
    "bench_method": "gravimetric",
    "mass_used": {"value": 4.4, "unit": "mg"},
    "sample_volume": {"value": 1000, "unit": "mL"},
    "plant_flow": {"value": 7.5, "unit": "MGD"},
    "endpoint_ph": 7.0,
    "target_ph": 7.0,
    "plant_nitrifies": False,
    "sample_source": "primary effluent",
    "dosing_point": "primary effluent",
}


def case(base, **overrides):
    """base with fields replaced, or dropped when the override is None."""
    merged = dict(base)
    merged.update(overrides)
    return {k: v for k, v in merged.items() if v is not None}


def warns(out, phrase):
    return any(phrase in w for w in out["result"]["warnings"])


# ---------------------------------------------------------------------------
# 1. Both bench methods.
# ---------------------------------------------------------------------------

# Volumetric: titrant volume x normality x equivalent weight / sample volume.
caustic = calc_ph_adjustment(**CAUSTIC)
r = caustic["result"]
assert r["dose_mg_per_l"] == 5.2, r
assert r["pure_reagent_kg_per_day"] == 147.63, r
assert r["product_kg_per_day"] == 590.52, r
assert r["product_l_per_day"] == 461.3, r
assert r["product_l_per_hour"] == 19.22, r
assert r["bench_method"] == "volumetric", r
# The source's English column: 325 lb/d pure, 1300 lb/d of product.
assert abs(r["pure_reagent_kg_per_day"] * 2.20462 - 325) < 1
assert abs(r["product_kg_per_day"] * 2.20462 - 1300) < 3
assert "0.02 N, 6.5 mL into 1000 mL sample" in caustic["summary"]
print(f"volumetric: 6.5 mL of 0.02 N NaOH -> {r['dose_mg_per_l']} mg/L -> "
      f"{r['product_kg_per_day']} kg/d of 25% product")

# Gravimetric by difference, and by a directly supplied mass, must agree.
lime = calc_ph_adjustment(**LIME)
assert lime["result"]["dose_mg_per_l"] == 4.4, lime["result"]
assert lime["result"]["pure_reagent_kg_per_day"] == 124.92, lime["result"]
assert lime["result"]["bench_method"] == "gravimetric", lime["result"]
assert abs(lime["result"]["product_kg_per_day"] * 2.20462 - 275) < 1
by_difference = calc_ph_adjustment(**case(
    LIME, mass_used=None,
    mass_before={"value": 10.0, "unit": "mg"},
    mass_after={"value": 5.6, "unit": "mg"},
))
assert by_difference["result"]["dose_mg_per_l"] == 4.4, by_difference["result"]
assert by_difference["result"] == lime["result"], "the two routes must agree"
print("gravimetric: 4.4 mg into 1000 mL -> 4.4 mg/L, by mass and by difference")

# The equivalent weight table, transcribed from the spec independently of the
# tool's own copy, so a typo in either surfaces here rather than as a quietly
# wrong dose.
SPEC_EQ_WEIGHTS = {
    "caustic_soda": ("NaOH", 40.00),
    "hydrated_lime": ("Ca(OH)2", 37.05),
    "quicklime": ("CaO", 28.04),
    "soda_ash": ("Na2CO3", 53.00),
}
assert set(SPEC_EQ_WEIGHTS) == set(REAGENTS), set(REAGENTS)
for reagent, (formula, eq_wt) in SPEC_EQ_WEIGHTS.items():
    assert REAGENTS[reagent]["formula"] == formula, reagent
    assert REAGENTS[reagent]["eq_weight"] == eq_wt, reagent
    # 6.5 mL x 0.02 eq/L = 0.13 meq, x the equivalent weight, into 1 L.
    got = calc_ph_adjustment(**case(CAUSTIC, reagent=reagent))
    assert abs(got["result"]["dose_mg_per_l"] - 0.13 * eq_wt) < 0.001, \
        (reagent, got["result"]["dose_mg_per_l"], 0.13 * eq_wt)
    assert got["result"]["reagent_formula"] == formula
# The four are far enough apart that using the wrong one cannot pass as noise.
assert 0.13 * 53.00 / (0.13 * 28.04) > 1.8
# A gravimetric result is already a mass, so the equivalent weight must NOT
# enter it — every reagent gives the same dose from the same weighing.
for reagent in REAGENTS:
    got = calc_ph_adjustment(**case(LIME, reagent=reagent))
    assert got["result"]["dose_mg_per_l"] == 4.4, (reagent, got["result"])
print("equivalent weights applied on the volumetric path only")

# Swapped weights are a swap, not a negative dose.
try:
    calc_ph_adjustment(**case(LIME, mass_used=None,
                              mass_before={"value": 5.6, "unit": "mg"},
                              mass_after={"value": 10.0, "unit": "mg"}))
    raise AssertionError("mass_after > mass_before must raise")
except ValueError as e:
    assert "probably swapped" in str(e), e

# The two gravimetric routes are alternatives, not a pair to reconcile.
try:
    calc_ph_adjustment(**case(LIME, mass_before={"value": 10.0, "unit": "mg"},
                              mass_after={"value": 5.6, "unit": "mg"}))
    raise AssertionError("mass_used plus before/after must raise")
except ValueError as e:
    assert "not both" in str(e), e

# Inputs belonging to the other bench method are refused rather than ignored.
try:
    calc_ph_adjustment(**case(CAUSTIC, mass_used={"value": 4.4, "unit": "mg"}))
    raise AssertionError("volumetric with a mass must raise")
except ValueError as e:
    assert "mass_used" in str(e) and "gravimetric" in str(e), e
try:
    calc_ph_adjustment(**case(LIME, titrant_normality=0.02))
    raise AssertionError("gravimetric with a normality must raise")
except ValueError as e:
    assert "titrant_normality" in str(e), e
print("bench methods keep their own inputs; swapped weights rejected")


# ---------------------------------------------------------------------------
# 2. Both titrant bases — the core trap.
# ---------------------------------------------------------------------------

# Same bench result, both bases, so the gap is exactly the strength inverse.
pure = calc_ph_adjustment(**case(LIME, titrant_basis="pure_reagent",
                                 product_strength_percent=25))
delivered = lime
assert pure["result"]["strength_correction_applied"] is True
assert delivered["result"]["strength_correction_applied"] is False
assert delivered["result"]["product_strength_percent"] is None
# Stage 3 ran on one and not the other; the dose and pure mass are identical.
assert pure["result"]["pure_reagent_kg_per_day"] == \
    delivered["result"]["pure_reagent_kg_per_day"]
ratio = pure["result"]["product_kg_per_day"] / delivered["result"]["product_kg_per_day"]
assert abs(ratio - 4.0) < 1e-6, ratio
assert "NO strength correction applied" in delivered["summary"]
assert "no correction (basis is as-delivered product)" in delivered["summary"]
assert "x 100 / 25" in pure["summary"], pure["summary"]
print(f"titrant_basis: pure vs as-delivered differ by exactly {ratio:.1f}x "
      f"on 25% product")

# THE DOUBLE-COUNT. An as-delivered result already contains the purity, so a
# strength correction on top of it is refused — not silently applied.
try:
    calc_ph_adjustment(**case(LIME, product_strength_percent=93))
    raise AssertionError("as-delivered plus a strength must raise")
except ValueError as e:
    assert "DOUBLE-COUNTS" in str(e), e
    assert "already inside the measured mass" in str(e), e
    assert "1.08x" in str(e), e          # 100/93, the size of the error
    assert "pure_reagent" in str(e), e   # and how to say what they meant

# The mirror: a pure-reagent basis with no strength has nothing to correct by,
# and guessing one is exactly what 2.3 forbids.
try:
    calc_ph_adjustment(**case(CAUSTIC, product_strength_percent=None))
    raise AssertionError("pure_reagent without a strength must raise")
except ValueError as e:
    assert "product_strength_percent is required" in str(e), e
    assert "no default" in str(e), e
    assert "Do not infer it from the reagent" in str(e), e
for bad in (0, -5, 101):
    try:
        calc_ph_adjustment(**case(CAUSTIC, product_strength_percent=bad))
        raise AssertionError(f"strength {bad} must raise")
    except ValueError as e:
        assert "between 0 and 100" in str(e), e
print("double-count refused in one direction, guessed purity in the other")


# ---------------------------------------------------------------------------
# 3. Mass is not volume, and the two sanity guards.
# ---------------------------------------------------------------------------

# No density means no volume anywhere in the result — not a volume computed
# against an assumed density of 1.0.
no_rho = calc_ph_adjustment(**case(CAUSTIC, solution_density=None))
assert no_rho["result"]["product_l_per_day"] is None, no_rho["result"]
assert no_rho["result"]["product_l_per_hour"] is None, no_rho["result"]
assert no_rho["result"]["product_kg_per_day"] == 590.52, no_rho["result"]
assert "solution density was not supplied. Mass only." in no_rho["summary"]
assert "L/d" not in no_rho["summary"], no_rho["summary"]
assert "L/h" not in no_rho["summary"], no_rho["summary"]
# Every mass key says mass; every volume key says volume.
for key in no_rho["result"]:
    if key.endswith("_kg_per_day") or key.endswith("_kg_per_hour"):
        assert isinstance(no_rho["result"][key], float), key
assert "product_m3_per_day" not in no_rho["result"]
print("no density -> masses only, and no volume appears anywhere")

# The volume-versus-flow guard. The fraction is dose x 100 / (strength x
# density x 1e6) — plant flow cancels out — so a dilute solution trips it at a
# dose the sanity limit still allows, which isolates the two guards.
big_vol = calc_ph_adjustment(**case(
    CAUSTIC, input_mode="from_known_dose", bench_method=None,
    titrant_volume=None, titrant_normality=None, sample_volume=None,
    endpoint_ph=None, dose={"value": 600, "unit": "mg/L"},
    product_strength_percent=5, solution_density={"value": 1.0, "unit": "kg/L"},
))
assert big_vol["result"]["dose_mg_per_l"] < DOSE_SANITY_LIMIT_MG_L
assert warns(big_vol, "of the plant flow"), big_vol["result"]["warnings"]
assert warns(big_vol, "almost always a units error"), big_vol["result"]["warnings"]
assert not warns(big_vol, "far above the tens")
assert not warns(caustic, "of the plant flow")

# The dose guard, which is the only thing watching the mass-only path — the
# volume guard cannot fire without a density. A bench weight in grams instead
# of milligrams is off by a thousand.
grams_slip = calc_ph_adjustment(**case(LIME, mass_used={"value": 4.4, "unit": "g"}))
assert grams_slip["result"]["dose_mg_per_l"] == 4400.0
assert grams_slip["result"]["product_l_per_day"] is None   # no density at all
assert warns(grams_slip, "far above the tens"), grams_slip["result"]["warnings"]
assert warns(grams_slip, "grams instead of"), grams_slip["result"]["warnings"]
assert not warns(lime, "far above the tens")
print(f"volume guard fires above {VOLUME_FRACTION_LIMIT * 100:g}% of flow; "
      f"dose guard covers the mass-only path above "
      f"{DOSE_SANITY_LIMIT_MG_L:g} mg/L")

# Density and concentration share a dimension, so units.parse cannot catch a
# dose in the density slot. The range check is what catches it.
for bad, unit in [(5.2, "mg/L"), (1280, "kg/L"), (0.1, "kg/L")]:
    try:
        calc_ph_adjustment(**case(CAUSTIC,
                                  solution_density={"value": bad, "unit": unit}))
        raise AssertionError(f"density {bad} {unit} must raise")
    except ValueError as e:
        assert "outside the" in str(e) and "kg/L range" in str(e), e
        assert "share a dimension" in str(e), e
# The plausible band is accepted, in whatever unit the operator used.
for good, unit in [(1.28, "kg/L"), (1.28, "g/mL"), (1280, "kg/m3"),
                   (10.68, "lb/gal")]:
    got = calc_ph_adjustment(**case(CAUSTIC,
                                    solution_density={"value": good, "unit": unit}))
    assert abs(got["result"]["product_l_per_day"] - 461.3) < 0.5, (good, unit)
assert DENSITY_MIN < 1.0 < DENSITY_MAX
print("density range-checked because dimensions cannot separate it from a dose")


# ---------------------------------------------------------------------------
# 4. Endpoint versus target divergence. Never scaled, only warned.
# ---------------------------------------------------------------------------

near = calc_ph_adjustment(**case(CAUSTIC, endpoint_ph=7.2, target_ph=7.5))
assert abs(7.5 - 7.2) <= PH_DIVERGENCE_LIMIT + 1e-9
assert not warns(near, "Carbonate buffering"), near["result"]["warnings"]

far = calc_ph_adjustment(**case(CAUSTIC, endpoint_ph=7.2, target_ph=7.8))
assert warns(far, "Carbonate buffering"), far["result"]["warnings"]
assert warns(far, "has NOT been scaled"), far["result"]["warnings"]
assert warns(far, "Repeat the titration to pH 7.8"), far["result"]["warnings"]
# The dose is untouched — extrapolating to an untested pH is the thing being
# refused, so the number must be identical to the on-target case.
assert far["result"]["dose_mg_per_l"] == caustic["result"]["dose_mg_per_l"]
assert far["result"]["product_kg_per_day"] == caustic["result"]["product_kg_per_day"]
# It fires in both directions, since overshooting is equally untested.
assert warns(calc_ph_adjustment(**case(CAUSTIC, endpoint_ph=7.8, target_ph=7.2)),
             "Carbonate buffering")
# from_known_dose has no endpoint to diverge from.
known = calc_ph_adjustment(**case(
    CAUSTIC, input_mode="from_known_dose", bench_method=None,
    titrant_volume=None, titrant_normality=None, sample_volume=None,
    endpoint_ph=None, dose={"value": 5.2, "unit": "mg/L"}, target_ph=9.0,
))
assert not warns(known, "Carbonate buffering")
assert "not titrated / 9" in known["summary"], known["summary"]
print(f"endpoint/target divergence warns above {PH_DIVERGENCE_LIMIT} pH units "
      f"without ever scaling the dose")


# ---------------------------------------------------------------------------
# 5. from_known_dose mode, including the missing-density path.
# ---------------------------------------------------------------------------

# Skips stage 1 but not stage 3 — the basis still applies.
assert known["result"]["dose_mg_per_l"] == 5.2, known["result"]
assert known["result"]["product_kg_per_day"] == 590.52, known["result"]
assert known["result"]["bench_method"] is None, known["result"]
assert known["result"]["input_mode"] == "from_known_dose"
assert "not applicable (dose supplied directly)" in known["summary"]
# With a density it produces the same volume as the titration route, because
# stages 2 and 3 are identical once the dose is known.
assert known["result"]["product_l_per_day"] == caustic["result"]["product_l_per_day"]

# Without a density: masses only, and that is stated rather than left blank.
known_no_rho = calc_ph_adjustment(**case(
    CAUSTIC, input_mode="from_known_dose", bench_method=None,
    titrant_volume=None, titrant_normality=None, sample_volume=None,
    endpoint_ph=None, solution_density=None,
    dose={"value": 5.2, "unit": "mg/L"},
))
assert known_no_rho["result"]["product_l_per_day"] is None
assert known_no_rho["result"]["product_kg_per_hour"] == 24.605, known_no_rho["result"]
assert "Mass only." in known_no_rho["summary"]
assert "kg/h flow-paced across 24 h" in known_no_rho["summary"]

# Bench inputs in from_known_dose mode are a contradiction, not surplus.
KNOWN_DOSE = {
    "input_mode": "from_known_dose", "bench_method": None,
    "titrant_volume": None, "titrant_normality": None, "sample_volume": None,
    "endpoint_ph": None, "dose": {"value": 5.2, "unit": "mg/L"},
}
for field, value in [("bench_method", "volumetric"),
                     ("sample_volume", {"value": 1000, "unit": "mL"}),
                     ("titrant_volume", {"value": 6.5, "unit": "mL"})]:
    try:
        calc_ph_adjustment(**case(CAUSTIC, **{**KNOWN_DOSE, field: value}))
        raise AssertionError(f"from_known_dose with {field} must raise")
    except ValueError as e:
        assert field in str(e), e
# And a dose on the titration path is the same contradiction the other way.
try:
    calc_ph_adjustment(**case(CAUSTIC, dose={"value": 5.2, "unit": "mg/L"}))
    raise AssertionError("from_titration with a dose must raise")
except ValueError as e:
    assert "dose was also supplied" in str(e), e
print("from_known_dose skips stage 1, keeps stage 3, and reports mass only "
      "without a density")


# ---------------------------------------------------------------------------
# 6. Required enums and inputs. No guesses anywhere.
# ---------------------------------------------------------------------------

for field in ("reagent", "input_mode", "titrant_basis"):
    try:
        calc_ph_adjustment(**case(CAUSTIC, **{field: None}))
        raise AssertionError(f"missing {field} must raise")
    except ValueError as e:
        assert "is required and has no default" in str(e), e
        assert "Ask the operator" in str(e), e
for field, bad in [("reagent", "alum"), ("input_mode", "guess"),
                   ("titrant_basis", "product"), ("bench_method", "colorimetric")]:
    try:
        calc_ph_adjustment(**case(CAUSTIC, **{field: bad}))
        raise AssertionError(f"{field}={bad!r} must be rejected")
    except ValueError as e:
        assert "is not one of" in str(e), e
# Case tolerance without value tolerance, as elsewhere in the repo.
assert calc_ph_adjustment(**case(CAUSTIC, reagent="CAUSTIC_SODA"))["result"]["reagent"] \
    == "caustic_soda"

# plant_nitrifies is required rather than defaulting to false, because false is
# the value that silently drops the alkalinity-destruction warning.
try:
    calc_ph_adjustment(**case(CAUSTIC, plant_nitrifies=None))
    raise AssertionError("missing plant_nitrifies must raise")
except ValueError as e:
    assert "plant_nitrifies is required" in str(e), e
    assert "assuming false" in str(e), e
nitrifying = calc_ph_adjustment(**case(CAUSTIC, plant_nitrifies=True))
assert warns(nitrifying, "7.14 mg CaCO3"), nitrifying["result"]["warnings"]
assert warns(nitrifying, "do not dose this once and walk away")
assert not warns(caustic, "7.14 mg CaCO3")

# The schema must not carry defaults that let the model skip any of them.
schema = next(s for s in all_schemas() if s["name"] == "calc_ph_adjustment")
props = schema["input_schema"]["properties"]
for field in ("reagent", "input_mode", "titrant_basis"):
    assert field in schema["input_schema"]["required"], field
    assert "default" not in props[field], field
    assert props[field]["enum"], field
for field in ("plant_flow", "target_ph", "plant_nitrifies"):
    assert field in schema["input_schema"]["required"], field
assert "product_strength_percent" not in schema["input_schema"]["required"]
assert "solution_density" not in schema["input_schema"]["required"]
# The description has to carry the trap, since that is what the model reads.
assert "titrant_basis" in schema["description"]
assert "factor of four" in schema["description"]
print("every enum required and undefaulted; plant_nitrifies cannot default false")


# ---------------------------------------------------------------------------
# 7. Sample matrix versus dosing point.
# ---------------------------------------------------------------------------

assert not warns(caustic, "different matrices")     # both "mixed liquor"
mismatch = calc_ph_adjustment(**case(CAUSTIC, sample_source="mixed liquor",
                                     dosing_point="aeration basin influent"))
assert warns(mismatch, "different matrices"), mismatch["result"]["warnings"]
assert warns(mismatch, "Retitrate a sample from the dosing point")
# Whitespace and case are not a real difference.
assert not warns(calc_ph_adjustment(**case(CAUSTIC, sample_source=" Mixed Liquor ",
                                           dosing_point="mixed liquor")),
                 "different matrices")
# Unstated is its own named line, not a silent pass.
unstated = calc_ph_adjustment(**case(CAUSTIC, sample_source=None, dosing_point=None))
assert warns(unstated, "could not be checked"), unstated["result"]["warnings"]
assert "Sample / dose point:  not stated" in unstated["summary"]
print("sample/dosing mismatch warns; unstated is stated rather than passed")


# ---------------------------------------------------------------------------
# 8. Delegation, hazards, the always-on caveat, and the dispatch contract.
# ---------------------------------------------------------------------------

# Stages 2 and 3 really are calc_chemical_feed's, not a second implementation.
feed = calc_chemical_feed(
    flow=CAUSTIC["plant_flow"], dose={"value": 5.2, "unit": "mg/L"},
    solution_strength_pct=25, solution_sg=1.28,
)
assert feed["result"]["neat_kg_per_day"] == caustic["result"]["pure_reagent_kg_per_day"]
assert abs(feed["result"]["solution_l_per_day"] - caustic["result"]["product_l_per_day"]) < 0.1
# And the product mass is consistent with that volume times the density.
assert abs(caustic["result"]["product_l_per_day"] * 1.28
           - caustic["result"]["product_kg_per_day"]) < 0.5
print("stages 2 and 3 agree with calc_chemical_feed called directly")

# US and metric statements of the same plant agree.
metric = calc_ph_adjustment(**case(CAUSTIC,
                                   plant_flow={"value": 28.3906, "unit": "MLD"}))
assert abs(metric["result"]["product_kg_per_day"]
           - caustic["result"]["product_kg_per_day"]) < 0.1
assert any("MGD assumed to be US gallons" in c for c in caustic["conversions"])

# The starting-point caveat is on every result, without exception.
for out in (caustic, lime, known, big_vol, far, no_rho, unstated):
    joined = " ".join(out["caveats"])
    assert "STARTING POINT" in joined, out["result"]["input_mode"]
    assert "flow-paced across the full 24 hours" in joined
    assert "trend pH and alkalinity" in joined
# Hazard notes on the two reagents that need them, and not on the two that
# do not.
assert any("corrosive" in c for c in caustic["caveats"])
assert not any("corrosive" in c for c in lime["caveats"])
quick = calc_ph_adjustment(**case(CAUSTIC, reagent="quicklime"))
assert any("slakes exothermically" in c for c in quick["caveats"])
assert any("37.05" in c for c in quick["caveats"]), "slaked-lime basis note"
assert not any("slakes" in c for c in
               calc_ph_adjustment(**case(CAUSTIC, reagent="soda_ash"))["caveats"])

# The model gets the summary string; the trace rides alongside on .trace.
d = dispatch("calc_ph_adjustment", CAUSTIC)
assert isinstance(d, str), "the agent loop requires a string"
assert str(d) == d.trace["summary"], "dispatch must not reshape the summary"
assert d.trace["result"]["product_kg_per_day"] == 590.52, d.trace["result"]
assert "steps" not in str(d), "the trace must not reach the model as text"
for step in d.trace["steps"]:
    assert step["label"] and isinstance(step["value"], (int, float)), step
labels = [s["label"] for s in d.trace["steps"]]
assert labels[0] == "Equivalents of titrant", labels
assert "Commercial product per day" in labels, labels
# Stage 3 leaves no trace when it does not run.
assert "Commercial product per day" not in [s["label"] for s in lime["steps"]]
print("dispatch returns the summary string with .trace attached")

print("\nall tests passed")
