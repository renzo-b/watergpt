# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Sludge volume index (SVI): the volume occupied by one gram of sludge after
# 30 minutes of settling.
#
#     SVI (mL/g) = settled volume (mL/L) x 1000 / MLSS (mg/L)
#
# Diluted settling test. Above roughly 250 mL/L the settling column is crowded
# enough that sidewall effects distort the reading, so the test is re-run with
# the mixed liquor diluted by process effluent until the 30-minute reading
# lands between 200 and 250 mL/L. The observed volume is then scaled back up:
#
#     corrected = observed x 1000 / (1000 - dilution)
#
# where dilution is the mL of effluent per litre of made-up sample. With 850 mL
# of effluent the sample is 15% mixed liquor, so a 250 mL/L reading corresponds
# to 1667 mL/L undiluted.
#
# Note settled volume is a VOLUME RATIO (mL/L), not a concentration. It is
# dimensionless, so it uses the volume_ratio dimension — passing MLSS in the
# settled-volume slot, or vice versa, raises rather than computing nonsense.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

# Above this the 30-minute reading is distorted by sidewall effects and a
# diluted settling test is called for.
SETTLING_LIMIT_ML_L = 250.0

# Interpretation bands for the resulting SVI, in mL/g. General guidance only —
# the workable range is plant-specific and depends on the clarifier.
SVI_GOOD_BELOW = 100.0
SVI_ACCEPTABLE_BELOW = 150.0


@tool(
    name="calc_svi",
    description=(
        "Calculate the sludge volume index (SVI) from a 30-minute settled "
        "sludge volume and MLSS, with optional correction for a diluted "
        "settling test. Use for SVI, settleability, sludge bulking, or "
        "'why won't my sludge settle' questions. Never compute it yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "settled_volume": quantity_schema(
                "volume_ratio",
                "The 30-minute settled sludge volume as read from the "
                "settlometer or cylinder. This is the OBSERVED reading — if a "
                "diluted test was run, pass the reading as observed and give "
                "dilution_ml as well; do not correct it yourself.",
            ),
            "mlss": quantity_schema(
                "concentration",
                "Mixed liquor suspended solids of the undiluted mixed liquor. "
                "Total solids, not volatile — SVI is defined on MLSS.",
            ),
            "dilution_ml": {
                "type": "number",
                "description": (
                    "mL of process effluent used per litre of made-up sample "
                    "in a diluted settling test. 0 (the default) means the "
                    "test was undiluted. Must be less than 1000."
                ),
            },
        },
        "required": ["settled_volume", "mlss"],
    },
)
def calc_svi(settled_volume, mlss, dilution_ml=0):
    """Sludge volume index, with optional diluted-settling-test correction.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    sv = parse(settled_volume, "volume_ratio")
    c_mlss = parse(mlss, "concentration")

    if sv.canonical <= 0:
        raise ValueError("Settled volume must be positive.")
    if c_mlss.canonical <= 0:
        raise ValueError(
            "MLSS must be positive — it is the denominator of SVI. A zero or "
            "negative mixed liquor solids reading is a lab or instrument "
            "error, not an operating condition."
        )
    if dilution_ml < 0:
        raise ValueError("dilution_ml cannot be negative.")
    if dilution_ml >= 1000:
        raise ValueError(
            f"dilution_ml must be less than 1000 (got {dilution_ml}). It is "
            "the mL of effluent per litre of made-up sample, so 1000 would "
            "mean the sample contained no mixed liquor at all and the "
            "correction would divide by zero."
        )

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []
    caveats = []

    diluted = dilution_ml > 0
    if diluted:
        svi_basis = sv.canonical * 1000 / (1000 - dilution_ml)
        steps.append({
            "label": "Dilution correction",
            "formula": "observed x 1000 / (1000 - dilution)",
            "substituted": (
                f"{sv.canonical:g} mL/L x 1000 / (1000 - {dilution_ml:g})"
            ),
            "value": round(svi_basis, 1),
            "unit": "mL/L",
        })
    else:
        svi_basis = sv.canonical

    svi = svi_basis * 1000 / c_mlss.canonical
    steps.append({
        "label": "Sludge volume index",
        "formula": "settled volume x 1000 / MLSS",
        "substituted": (
            f"{svi_basis:g} mL/L x 1000 / {c_mlss.canonical:g} mg/L"
        ),
        "value": round(svi, 1),
        "unit": "mL/g",
    })

    # Guardrail on the settled volume that actually feeds the SVI formula.
    if svi_basis > SETTLING_LIMIT_ML_L:
        if diluted:
            # A corrected volume above the limit is expected — that is the
            # whole point of diluting — so the question is whether the OBSERVED
            # reading was brought into range, not the corrected one.
            if sv.canonical > SETTLING_LIMIT_ML_L:
                caveats.append(
                    f"UNRELIABLE: the observed reading of {sv.canonical:g} mL/L "
                    f"is still above {SETTLING_LIMIT_ML_L:g} mL/L even after "
                    f"diluting with {dilution_ml:g} mL/L of effluent. Sidewall "
                    "effects distort settling above that level. Dilute further "
                    "until the 30-minute reading lands between 200 and "
                    "250 mL/L, then re-run."
                )
            else:
                caveats.append(
                    f"The dilution-corrected settled volume is "
                    f"{svi_basis:.0f} mL/L, above the {SETTLING_LIMIT_ML_L:g} "
                    "mL/L direct-reading limit. That is expected for a diluted "
                    f"test — the observed reading of {sv.canonical:g} mL/L was "
                    "within range, which is what the limit governs. The result "
                    "does depend on the dilution figure being accurate."
                )
        else:
            caveats.append(
                f"UNRELIABLE: the settled volume of {svi_basis:g} mL/L exceeds "
                f"{SETTLING_LIMIT_ML_L:g} mL/L. Above that level sidewall "
                "effects distort settling and the 30-minute reading understates "
                "how poorly the sludge is actually settling, so this SVI cannot "
                "be trusted. Run a diluted settling test: dilute the mixed "
                "liquor with process effluent until the 30-minute result lands "
                "between 200 and 250 mL/L, then pass that reading with the "
                "dilution used."
            )

    # Interpretation of the SVI itself. General guidance, not a plant target.
    if svi < SVI_GOOD_BELOW:
        band = (
            f"below {SVI_GOOD_BELOW:g} mL/g, generally indicating good settling"
        )
    elif svi <= SVI_ACCEPTABLE_BELOW:
        band = (
            f"between {SVI_GOOD_BELOW:g} and {SVI_ACCEPTABLE_BELOW:g} mL/g, "
            "generally considered acceptable settling"
        )
    else:
        band = (
            f"above {SVI_ACCEPTABLE_BELOW:g} mL/g, which generally suggests "
            "bulking sludge — filamentous organisms are the usual cause. A "
            "microscopic examination would distinguish filamentous bulking "
            "from other causes"
        )
    caveats.append(
        f"Interpretation: an SVI of {svi:.1f} mL/g is {band}. These bands are "
        "general guidance, not this plant's targets — the workable SVI range "
        "depends on the clarifier and the plant's own operating history."
    )

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(sv, c_mlss), ""]
    if diluted:
        out += [
            f"Diluted test: {dilution_ml:g} mL/L effluent, so the sample was "
            f"{(1000 - dilution_ml) / 10:g}% mixed liquor",
            f"Settled volume corrected to: {svi_basis:.1f} mL/L "
            f"(observed {sv.canonical:g} mL/L)",
        ]
    else:
        out += [f"Settled volume: {svi_basis:g} mL/L (undiluted test)"]
    out += [f"SVI: {svi:.1f} mL/g"]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (sv, c_mlss)]
    if diluted:
        conversions.append(
            f"{sv.canonical:g} mL/L observed = {svi_basis:.1f} mL/L corrected "
            f"for {dilution_ml:g} mL/L dilution"
        )
    conversions += [
        f"NOTE: {n}" for n in dict.fromkeys(n for x in (sv, c_mlss) for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "svi_ml_per_g": round(svi, 1),
            "settled_volume_used_ml_per_l": round(svi_basis, 1),
            "observed_settled_volume_ml_per_l": round(sv.canonical, 1),
            "dilution_ml": dilution_ml,
            "above_direct_reading_limit": svi_basis > SETTLING_LIMIT_ML_L,
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # Undiluted: 200 mL/L settled at 2500 mg/L MLSS is an SVI of 80 mL/g.
    print(calc_svi(
        settled_volume={"value": 200, "unit": "mL/L"},
        mlss={"value": 2500, "unit": "mg/L"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Diluted test from the source: a 250 mL/L reading with 850 mL/L of
    # effluent corrects to 1667 mL/L.
    print(calc_svi(
        settled_volume={"value": 250, "unit": "mL/L"},
        mlss={"value": 2500, "unit": "mg/L"},
        dilution_ml=850,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Over the direct-reading limit with no dilution — still answered, flagged.
    print(calc_svi(
        settled_volume={"value": 750, "unit": "mL/L"},
        mlss={"value": 2500, "unit": "mg/L"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_svi(
            settled_volume={"value": 2500, "unit": "mg/L"},   # MLSS as volume
            mlss={"value": 200, "unit": "mL/L"},
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
