"""
Tests for calc_srt — python test_calc_srt.py

Plain asserts to match units.py and the other calculators — this repo has no
pytest. Schema conformance is covered by the CALCULATOR_CASES loop in
test_calc_ct_steps.py, along with the removal of calc_mean_cell_residence_time
and the hand-off that replaced it; this file covers the SRT-specific behaviour.

calc_srt is the only tool for SRT, MCRT and sludge age — three names for one
quantity.

Structured around the traps the tool exists to prevent, because those are what
break silently. The arithmetic is two multiplications and a divide — if only
that were tested, every test would pass while the tool returned confident wrong
numbers.
"""

from tools.calculators.calc_srt import RAS_MLSS_SIMILARITY, calc_srt
from tools.registry import all_schemas, dispatch
from units import UnitError

# The reference plant, used throughout. 5000 m3 at 3000 mg/L is 15000 kg of
# inventory; wasting 200 m3/d of 8000 mg/L RAS removes 1600 kg/d, and 9800 m3/d
# of effluent at 15 mg/L removes another 147 kg/d.
#   15000 / 1747 = 8.586 d
PLANT = {
    "basis": "aerobic",
    "waste_location": "ras_line",
    "solids_basis": "TSS",
    "aeration_volume": {"value": 5000, "unit": "m3"},
    "mlss": {"value": 3000, "unit": "mg/L"},
    "waste_flow": {"value": 200, "unit": "m3/d"},
    "waste_solids": {"value": 8000, "unit": "mg/L"},
    "influent_flow": {"value": 10, "unit": "MLD"},
    "effluent_solids": {"value": 15, "unit": "mg/L"},
}
BLANKET = {
    "blanket_depth": {"value": 0.6, "unit": "m"},
    "clarifier_surface_area": {"value": 450, "unit": "m2"},
    "blanket_solids": {"value": 6000, "unit": "mg/L"},
    "clarifiers_in_service": 2,
}


def case(**overrides):
    """PLANT with fields replaced, or dropped when the override is None."""
    merged = dict(PLANT)
    merged.update(overrides)
    return {k: v for k, v in merged.items() if v is not None}


def warns(out, phrase):
    return any(phrase in w for w in out["result"]["warnings"])


# ---------------------------------------------------------------------------
# 1. Each enum branch.
# ---------------------------------------------------------------------------

# basis: aerobic counts the aeration basin alone.
aerobic = calc_srt(**PLANT)
assert aerobic["result"]["aeration_inventory_kg"] == 15000.0, aerobic["result"]
assert aerobic["result"]["blanket_inventory_kg"] is None, aerobic["result"]
assert aerobic["result"]["solids_inventory_kg"] == 15000.0, aerobic["result"]
assert aerobic["result"]["total_removal_kg_per_day"] == 1747.0, aerobic["result"]
assert aerobic["result"]["srt_days"] == 8.59, aerobic["result"]
# The line that resolved to nothing still appears, so the exclusion is auditable.
assert "Blanket inventory:    excluded (aerobic basis)" in aerobic["summary"]

# basis: total adds 2 x 0.6 m x 450 m2 x 6000 mg/L = 3240 kg of blanket.
total = calc_srt(**case(basis="total", **BLANKET))
assert total["result"]["blanket_inventory_kg"] == 3240.0, total["result"]
assert total["result"]["solids_inventory_kg"] == 18240.0, total["result"]
assert total["result"]["srt_days"] == 10.44, total["result"]
# The two bases differ by 22% on the same plant — this is why basis has no
# default and is never inferred.
assert total["result"]["srt_days"] > aerobic["result"]["srt_days"]
assert "ESTIMATED" in total["summary"], total["summary"]
assert warns(total, "blanket inventory is an ESTIMATE"), total["result"]["warnings"]
assert not warns(aerobic, "blanket inventory is an ESTIMATE")
print(f"basis: aerobic {aerobic['result']['srt_days']} d vs total "
      f"{total['result']['srt_days']} d on the same plant")

# waste_location: aeration_basin prices the waste at MLSS, not RAS solids.
basin = calc_srt(**case(waste_location="aeration_basin", waste_solids=None))
assert basin["result"]["was_removal_kg_per_day"] == 600.0, basin["result"]
assert basin["result"]["srt_days"] == 20.08, basin["result"]
assert "= MLSS" in basin["summary"], basin["summary"]
assert "from RAS line" in aerobic["summary"], aerobic["summary"]
print(f"waste_location: ras_line {aerobic['result']['srt_days']} d vs "
      f"aeration_basin {basin['result']['srt_days']} d")

# solids_basis: carried through untouched, and VSS warns about the effluent
# figure, which is the term plants actually report on the other basis.
assert aerobic["result"]["solids_basis"] == "TSS"
assert not warns(aerobic, "solids basis is VSS")
vss = calc_srt(**case(solids_basis="VSS"))
assert vss["result"]["solids_basis"] == "VSS"
assert warns(vss, "solids basis is VSS"), vss["result"]["warnings"]
assert "Nothing was converted" in " ".join(vss["result"]["warnings"])
# No conversion happened — the number is identical, only the labelling differs.
assert vss["result"]["srt_days"] == aerobic["result"]["srt_days"]
assert "Solids basis:         VSS" in vss["summary"]
print("solids_basis: VSS labelled, warned, and never converted")

# Enums are case-insensitive but not value-permissive.
assert calc_srt(**case(basis="AEROBIC"))["result"]["basis"] == "aerobic"
assert calc_srt(**case(solids_basis="tss"))["result"]["solids_basis"] == "TSS"
for field, bad in [("basis", "digester"), ("waste_location", "clarifier"),
                   ("solids_basis", "MLSS")]:
    try:
        calc_srt(**case(**{field: bad}))
        raise AssertionError(f"{field}={bad!r} must be rejected")
    except ValueError as e:
        assert "not one of" in str(e), e

# A missing enum is an error, never a guess — the point of the whole tool.
for field in ("basis", "waste_location", "solids_basis"):
    try:
        calc_srt(**case(**{field: None}))
        raise AssertionError(f"missing {field} must raise")
    except ValueError as e:
        assert "is required and has no default" in str(e), e
        assert "Ask the operator" in str(e), e

# And the schema must not carry a default that would let the model skip them.
srt_schema = next(s for s in all_schemas() if s["name"] == "calc_srt")
props = srt_schema["input_schema"]["properties"]
for field in ("basis", "waste_location", "solids_basis"):
    assert field in srt_schema["input_schema"]["required"], field
    assert "default" not in props[field], f"{field} must have no default"
    assert props[field]["enum"], field
assert "effluent_solids" in srt_schema["input_schema"]["required"]
print("enums are required, undefaulted, case-tolerant and value-strict")


# ---------------------------------------------------------------------------
# 2. The RAS/MLSS mismatch warning. Spec trap 2: pairing a RAS-line waste flow
#    with an MLSS-level concentration inflates SRT by the thickening factor.
# ---------------------------------------------------------------------------

mismatch = calc_srt(**case(waste_solids={"value": 3000, "unit": "mg/L"}))
assert warns(mismatch, "within 0% of MLSS"), mismatch["result"]["warnings"]
assert warns(mismatch, "OVERSTATED"), mismatch["result"]["warnings"]
# It still returns a number, and that number is 2.3x the truth — which is
# exactly why the warning has to be loud.
assert mismatch["result"]["srt_days"] == 20.08, mismatch["result"]
assert mismatch["result"]["srt_days"] / aerobic["result"]["srt_days"] > 2
# The warning is a line in the working, not prose bolted on the end.
assert "WARNING:" in mismatch["summary"], mismatch["summary"]
assert mismatch["summary"].index("WARNING:") > mismatch["summary"].index("SRT:")

# The boundary: 20% above MLSS still warns, comfortably above it does not.
edge = 3000 * (1 + RAS_MLSS_SIMILARITY)
assert warns(calc_srt(**case(waste_solids={"value": edge, "unit": "mg/L"})),
             "but wasting is from the RAS line")
assert not warns(calc_srt(**case(waste_solids={"value": edge * 1.05, "unit": "mg/L"})),
                 "but wasting is from the RAS line")
assert not warns(aerobic, "but wasting is from the RAS line")

# RAS-line wasting with no waste concentration at all must not fall back to
# MLSS. That silent default is the bug this tool was built to remove.
try:
    calc_srt(**case(waste_solids=None))
    raise AssertionError("ras_line without waste_solids must raise")
except ValueError as e:
    assert "waste_solids is required" in str(e), e
    assert "Do not substitute" in str(e), e

# The mirror mistake: claiming basin wasting while handing over RAS solids.
try:
    calc_srt(**case(waste_location="aeration_basin"))
    raise AssertionError("aeration_basin with RAS-level solids must raise")
except ValueError as e:
    assert "differs from MLSS" in str(e), e
# Within tolerance it is accepted as the same measurement, stated twice.
near = calc_srt(**case(waste_location="aeration_basin",
                       waste_solids={"value": 3200, "unit": "mg/L"}))
assert near["result"]["srt_days"] > 0
print("RAS/MLSS mismatch warns in both directions and never defaults silently")


# ---------------------------------------------------------------------------
# 3. The zero-effluent-TSS warning. Spec trap 3: required, zero allowed, but
#    never silent.
# ---------------------------------------------------------------------------

zero_eff = calc_srt(**case(effluent_solids={"value": 0, "unit": "mg/L"}))
assert zero_eff["result"]["effluent_removal_kg_per_day"] == 0.0
assert zero_eff["result"]["srt_days"] == 9.38, zero_eff["result"]  # 15000/1600
assert warns(zero_eff, "EXCLUDED from the denominator"), zero_eff["result"]["warnings"]
assert warns(zero_eff, "OVERSTATES SRT"), zero_eff["result"]["warnings"]
assert "[excluded: zero entered]" in zero_eff["summary"], zero_eff["summary"]
# Zero effluent overstates SRT by 9%, and by far more at short SRT.
assert zero_eff["result"]["srt_days"] > aerobic["result"]["srt_days"]
assert not warns(aerobic, "EXCLUDED from the denominator")

# Omitting it entirely is an error — optional-and-omitted is how the term gets
# dropped in the first place.
try:
    calc_srt(**case(effluent_solids=None))
    raise AssertionError("missing effluent_solids must raise")
except ValueError as e:
    assert "not optional in this tool" in str(e), e
    assert "explicit zero" in str(e), e
print("zero effluent solids allowed, flagged, and never merely omitted")


# ---------------------------------------------------------------------------
# 4. Reverse solves. Each must round-trip: feed the answer back in forwards and
#    land on the target. That is the only assertion that catches a sign error.
# ---------------------------------------------------------------------------

TARGET = {"value": 10, "unit": "d"}

# 4a. Solve for waste flow.
rev_q = calc_srt(**case(waste_flow=None, target_srt=TARGET))
assert rev_q["result"]["solved_for"] == "waste_flow", rev_q["result"]
assert abs(rev_q["result"]["waste_flow_m3_per_day"] - 169.1) < 0.1, rev_q["result"]
assert rev_q["result"]["srt_days"] == 10.0, rev_q["result"]
assert "SOLVED FOR:" in rev_q["summary"]
# Round-trip through the forward direction.
back = calc_srt(**case(
    waste_flow={"value": rev_q["result"]["waste_flow_m3_per_day"], "unit": "m3/d"}))
assert abs(back["result"]["srt_days"] - 10.0) < 0.01, back["result"]
# Wasting less than the plant does today lengthens SRT past 8.59 — direction check.
assert rev_q["result"]["waste_flow_m3_per_day"] < 200

# 4b. Solve for MLSS, RAS-line wasting (removal is independent of MLSS).
rev_m = calc_srt(**case(mlss=None, target_srt=TARGET))
assert rev_m["result"]["solved_for"] == "mlss", rev_m["result"]
assert abs(rev_m["result"]["mlss_mg_per_l"] - 3494.0) < 0.1, rev_m["result"]
assert rev_m["result"]["srt_days"] == 10.0, rev_m["result"]
back = calc_srt(**case(
    mlss={"value": rev_m["result"]["mlss_mg_per_l"], "unit": "mg/L"}))
assert abs(back["result"]["srt_days"] - 10.0) < 0.01, back["result"]
assert rev_m["result"]["mlss_mg_per_l"] > 3000       # more inventory, longer SRT

# 4c. Solve for MLSS, basin wasting — the hard one. MLSS sits in the numerator
#     AND the waste term, so it does not cancel only because effluent solids
#     are non-zero.
rev_mb = calc_srt(**case(waste_location="aeration_basin", waste_solids=None,
                         mlss=None, target_srt=TARGET))
assert abs(rev_mb["result"]["mlss_mg_per_l"] - 490.0) < 0.1, rev_mb["result"]
assert rev_mb["result"]["srt_days"] == 10.0, rev_mb["result"]
back = calc_srt(**case(
    waste_location="aeration_basin", waste_solids=None,
    mlss={"value": rev_mb["result"]["mlss_mg_per_l"], "unit": "mg/L"}))
assert abs(back["result"]["srt_days"] - 10.0) < 0.01, back["result"]

# 4d. Reverse solve on the total basis carries the blanket through.
rev_total = calc_srt(**case(basis="total", waste_flow=None, target_srt=TARGET,
                            **BLANKET))
assert rev_total["result"]["srt_days"] == 10.0, rev_total["result"]
# More inventory for the same target means MORE wasting than the aerobic case.
assert rev_total["result"]["waste_flow_m3_per_day"] > rev_q["result"]["waste_flow_m3_per_day"]

# The degenerate case: basin wasting, aerobic basis, zero effluent solids. MLSS
# cancels completely and SRT is V/Q_WAS at any value, so there is nothing to
# solve. Returning a number here would be fabrication.
try:
    calc_srt(**case(waste_location="aeration_basin", waste_solids=None,
                    mlss=None, target_srt=TARGET,
                    effluent_solids={"value": 0, "unit": "mg/L"}))
    raise AssertionError("cancelling MLSS must raise, not return a value")
except ValueError as e:
    assert "MLSS cancels out" in str(e), e
    assert "25.00 d" in str(e), e          # V/Q_WAS = 5000/200

# Unreachable targets are named as unreachable rather than solved to nonsense.
try:
    calc_srt(**case(waste_flow=None, target_srt={"value": 500, "unit": "d"}))
    raise AssertionError("unreachable target must raise")
except ValueError as e:
    assert "not reachable by wasting" in str(e), e
try:
    calc_srt(**case(waste_location="aeration_basin", waste_solids=None,
                    mlss=None, target_srt={"value": 40, "unit": "d"}))
    raise AssertionError("target above the V/Q_WAS cap must raise")
except ValueError as e:
    assert "caps SRT at" in str(e), e
print("all three reverse directions round-trip; degenerate and unreachable "
      "targets raise")


# ---------------------------------------------------------------------------
# 5. The unknown-count contract. Exactly one, or a clear error.
# ---------------------------------------------------------------------------

# Zero unknowns: everything supplied, nothing asked for.
try:
    calc_srt(**case(target_srt=TARGET))
    raise AssertionError("zero unknowns must raise")
except ValueError as e:
    assert "Nothing was left unknown" in str(e), e

# SRT and MLSS both unknown is the removed calc_mean_cell_residence_time's use
# case — someone with a volume and a waste flow and no solids data. It gets a
# message naming what to ask for and why no V/Q_waste approximation is offered,
# rather than a generic unknown count.
try:
    calc_srt(**case(mlss=None))
    raise AssertionError("SRT without MLSS must raise")
except ValueError as e:
    assert "MLSS was not supplied" in str(e), e
    assert "Ask the operator" in str(e), e
    assert "no short-cut in this tool" in str(e), e
    assert "thickening factor" in str(e), e
    # It points at the reverse solve too, since that is the other reason MLSS
    # would legitimately be absent.
    assert "supply target_srt" in str(e), e
    # And it does not quietly hand back the short-cut it just declined.
    assert "25.0" not in str(e) and "unknowns" not in str(e), e

# Two unknowns, in the pairings that are genuinely ambiguous.
for overrides, names in [
    ({"waste_flow": None}, ("srt", "waste_flow")),
    ({"waste_flow": None, "mlss": None, "target_srt": TARGET},
     ("waste_flow", "mlss")),
]:
    try:
        calc_srt(**case(**overrides))
        raise AssertionError(f"{names} unknown must raise")
    except ValueError as e:
        assert "2 unknowns" in str(e), e
        for n in names:
            assert n in str(e), (n, str(e))

# Three unknowns.
try:
    calc_srt(**case(waste_flow=None, mlss=None))
    raise AssertionError("three unknowns must raise")
except ValueError as e:
    assert "3 unknowns" in str(e), e
print("unknown count enforced: zero, two and three all rejected by name")


# ---------------------------------------------------------------------------
# 6. Blanket inputs must match the declared basis in both directions.
# ---------------------------------------------------------------------------

for drop in ("blanket_depth", "clarifier_surface_area", "blanket_solids",
             "clarifiers_in_service"):
    partial = {k: v for k, v in BLANKET.items() if k != drop}
    try:
        calc_srt(**case(basis="total", **partial))
        raise AssertionError(f"total basis without {drop} must raise")
    except ValueError as e:
        assert drop in str(e), e

# Blanket inputs under an aerobic basis mean one of the two is a mistake, and
# silently discarding them would hide it.
try:
    calc_srt(**case(**BLANKET))
    raise AssertionError("aerobic basis with blanket inputs must raise")
except ValueError as e:
    assert "blanket inputs were supplied" in str(e), e

for bad_n in (0, -1, 2.5):
    try:
        calc_srt(**case(basis="total", **{**BLANKET, "clarifiers_in_service": bad_n}))
        raise AssertionError(f"clarifiers_in_service={bad_n} must raise")
    except ValueError as e:
        assert "clarifiers_in_service must" in str(e), e

# The blanket term scales with the clarifier count, since it is summed over the
# units in service.
one = calc_srt(**case(basis="total", **{**BLANKET, "clarifiers_in_service": 1}))
assert one["result"]["blanket_inventory_kg"] == 1620.0, one["result"]
assert total["result"]["blanket_inventory_kg"] == 2 * 1620.0
print("blanket inputs tied to the basis in both directions and summed over units")


# ---------------------------------------------------------------------------
# 7. The units layer. Same plant, US units, same answer.
# ---------------------------------------------------------------------------

us = calc_srt(
    basis="aerobic", waste_location="ras_line", solids_basis="TSS",
    aeration_volume={"value": 1.320860, "unit": "MG"},
    mlss={"value": 3000, "unit": "ppm"},
    waste_flow={"value": 0.0528344, "unit": "MGD"},
    waste_solids={"value": 8000, "unit": "ppm"},
    influent_flow={"value": 2.641721, "unit": "MGD"},
    effluent_solids={"value": 15, "unit": "ppm"},
)
assert abs(us["result"]["srt_days"] - 8.59) < 0.01, us["result"]
assert any("MGD assumed to be US gallons" in c for c in us["conversions"])
assert any("ppm treated as mg/L" in c for c in us["conversions"])

# Mixed systems in one call: m3 of inventory against MGD of flow.
mixed = calc_srt(**case(influent_flow={"value": 2.641721, "unit": "MGD"}))
assert abs(mixed["result"]["srt_days"] - 8.59) < 0.01, mixed["result"]

# lb/d is reported alongside kg/d rather than left to the model to convert.
assert abs(aerobic["result"]["total_removal_lb_per_day"] - 3851.5) < 0.5

# A swapped argument raises instead of returning a plausible wrong number.
try:
    calc_srt(**case(aeration_volume={"value": 200, "unit": "m3/d"},
                    waste_flow={"value": 5000, "unit": "m3"}))
    raise AssertionError("swapped volume/flow must raise")
except UnitError as e:
    assert "not a volume unit" in str(e), e
# Blanket depth is a length, not a volume or a concentration.
try:
    calc_srt(**case(basis="total",
                    **{**BLANKET, "blanket_depth": {"value": 0.6, "unit": "m3"}}))
    raise AssertionError("blanket depth as a volume must raise")
except UnitError as e:
    assert "not a length unit" in str(e), e
print("US and mixed units agree with metric; swapped arguments rejected")


# ---------------------------------------------------------------------------
# 8. No interpretation, and the dispatch contract.
# ---------------------------------------------------------------------------

# Spec trap 6: the tool returns arithmetic and refuses to judge it. A pass/fail
# verdict or a "typical range" here would be a temperature-dependent claim made
# without temperature.
for out in (aerobic, total, basin, zero_eff, rev_q):
    # Scan the working block only. The trailing caveat necessarily contains
    # some of these words in order to say that no judgment is being made.
    working = out["summary"].split("CAVEAT:")[0].lower()
    for banned in ("pass", "fail", "adequate", "typical range", "target range",
                   "too low", "too high", "acceptable", "nitrif"):
        assert banned not in working, (banned, out["result"]["solved_for"])
    # And the refusal itself is stated, so the model has no vacuum to fill.
    refusal = out["caveats"][-1].lower()
    assert "no judgment is offered" in refusal, refusal
    assert "nitrification" in refusal and "temperature" in refusal, refusal
assert "srt_verdict" not in aerobic["result"]
assert "verdict" not in aerobic["result"]
print("no verdict, no reference range; the refusal to judge is stated")

# The model gets the summary string; the trace rides alongside on .trace.
d = dispatch("calc_srt", PLANT)
assert isinstance(d, str), "the agent loop requires a string"
assert str(d) == d.trace["summary"], "dispatch must not reshape the summary"
assert d.trace["result"]["srt_days"] == 8.59, d.trace["result"]
assert "steps" not in str(d), "the trace must not reach the model as text"

# Every step's value survives into the summary the operator reads, so the
# working cannot drift from the arithmetic behind it.
for step in d.trace["steps"]:
    assert step["label"], step
    assert isinstance(step["value"], (int, float)), step
assert d.trace["steps"][-1]["label"] == "SRT"
assert d.trace["steps"][-1]["value"] == d.trace["result"]["srt_days"]
print("dispatch returns the summary string with .trace attached")

print("\nall tests passed")
