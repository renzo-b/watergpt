# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Bench titration -> chemical feed rate for RAISING pH in activated sludge.
#
# Three stages, and which of them run depends on the input mode and the
# titrant basis:
#
#   1. bench result -> required dose (mg/L)        [skipped in from_known_dose]
#   2. dose -> daily mass of reagent               [always]
#   3. pure reagent -> commercial product          [ONLY on a pure_reagent basis]
#
# Stages 2 and 3 are not reimplemented here. They are exactly what
# calc_chemical_feed already does — flow x dose, then a strength and density
# correction — so this tool calls it and reports its numbers under labels that
# match the contract below. One tested implementation of dose arithmetic, not
# two that can drift.
#
# ONE TOOL, FOUR REAGENTS. Caustic and lime differ only in how the bench result
# is measured, which is the bench_method enum. Separate tools would duplicate
# stages 2 and 3 and let the two copies diverge.
#
# THIS TOOL RAISES pH ONLY. Acid dosing for high pH is not a mirror image: the
# reagents, the hazards and the endpoint logic all differ, and sulphuric acid
# into a nitrifying basin is not caustic with the sign flipped. It gets its own
# tool if it is ever needed.
#
# WHY THIS IS A PINNED TOOL RATHER THAN GENERIC ALGEBRA
#
# The source material this is derived from contains a real, propagated error:
# it labels the metric result m3/d when the quantity computed is kg/d, and
# carries that label through the commercial-strength step. The English column
# is correct. So the first job is that mass is mass:
#
#   1. MASS IS NOT VOLUME. Every mass output is named _kg_per_day. A volume
#      exists only when solution_density was supplied, and is named _l_per_day.
#      There is no path that produces a volume from a mass without a density.
#      Backstop: a product volume above 1% of plant flow is almost always a
#      units error, and warns.
#
#   2. titrant_basis IS THE CORE TRAP, and it has no default. A volumetric
#      titration with standardized NaOH measures PURE REAGENT, so the strength
#      correction must run. A gravimetric titration where the operator weighed
#      the actual lime they feed measures AS-DELIVERED PRODUCT, and the purity
#      is already inside the weighed mass — correcting it again double-counts.
#      Backwards in either direction moves the answer by the inverse of product
#      strength: a factor of four for 25% caustic.
#
#   3. PURITY IS NEVER ASSUMED. On a pure_reagent basis product_strength_percent
#      is required. Hydrated lime is not 100% Ca(OH)2, quicklime is not
#      comparable to hydrated lime by mass until it is slaked, and commercial
#      caustic ships at 25% or 50%. Strength is not inferable from the reagent.
#
#   4. DENSITY IS AN INPUT, NOT A LOOKUP. It varies with strength and
#      temperature, and a wrong one produces a plausible-looking number. No
#      table here. Note that units.parse CANNOT catch a dose passed as a
#      density — both are mass/volume — so the value is range-checked instead.
#
#   5. THE ANSWER IS A STARTING POINT. A titration measures instantaneous
#      buffer demand on a grab sample. It does not see alkalinity destroyed
#      continuously by nitrification (7.14 mg CaCO3 per mg NH3-N oxidized), so
#      on a nitrifying plant a titration-derived dose drifts low with time.
#
# Follows the quantity, step and caveat patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool
from tools.calculators.calc_chemical_feed import calc_chemical_feed

# Equivalent weights in mg/meq, used ONLY on the volumetric path — a gravimetric
# result is already a mass and needs no equivalent weight.
REAGENTS = {
    "caustic_soda": {
        "label": "caustic soda", "formula": "NaOH", "eq_weight": 40.00,
        "hazard": (
            "Caustic soda is corrosive and its dilution is strongly exothermic. "
            "Feed it slowly and at a controlled point: a slug into mixed liquor "
            "raises local pH far above the basin average and shocks the "
            "nitrifiers this dose is meant to protect."
        ),
    },
    "hydrated_lime": {
        "label": "hydrated lime", "formula": "Ca(OH)2", "eq_weight": 37.05,
        "hazard": None,
    },
    "quicklime": {
        "label": "quicklime", "formula": "CaO", "eq_weight": 28.04,
        "hazard": (
            "Quicklime slakes exothermically on contact with water and must be "
            "slaked before it is comparable to hydrated lime on a mass basis. "
            "Feed slowly — a slug raises local pH sharply and shocks the "
            "biology."
        ),
    },
    "soda_ash": {
        "label": "soda ash", "formula": "Na2CO3", "eq_weight": 53.00,
        "hazard": None,
    },
}

INPUT_MODES = ("from_titration", "from_known_dose")
BENCH_METHODS = ("volumetric", "gravimetric")
TITRANT_BASES = ("pure_reagent", "as_delivered_product")

# Beyond this the titration no longer describes the target. Carbonate buffering
# makes the dose/pH relationship nonlinear, so the answer is to retitrate, never
# to scale the dose to an untested endpoint.
PH_DIVERGENCE_LIMIT = 0.3

# A product volume above this fraction of plant flow is almost always a units
# error rather than a real dose.
VOLUME_FRACTION_LIMIT = 0.01

# The volume guard above can only fire when a density was supplied. On the
# mass-only path nothing else catches a units slip, and a gram/milligram
# mix-up in a bench weight is off by a thousand — it turns a 4.4 mg/L lime dose
# into 4400 mg/L and a 125 kg/d feed into 125 tonnes. pH-adjustment doses run
# in the tens of mg/L; nothing legitimate approaches this.
DOSE_SANITY_LIMIT_MG_L = 1000.0

# Plausible range for a chemical solution, in kg/L. Water is 1.0, 50% caustic
# about 1.53, and nothing fed through a metering pump is outside this. Exists
# because units.parse cannot distinguish a density from a concentration.
DENSITY_MIN, DENSITY_MAX = 0.5, 3.0

LABEL_W = 22


def _line(label, text):
    return f"{label + ':':<{LABEL_W}}{text}"


def _enum(value, allowed, name, why):
    """Resolve a required enum. Missing is an error, never a guess."""
    if value is None:
        raise ValueError(
            f"{name} is required and has no default. {why} "
            f"Allowed values: {', '.join(allowed)}. Ask the operator."
        )
    match = {a.lower(): a for a in allowed}
    key = str(value).strip().lower()
    if key not in match:
        raise ValueError(
            f"{name}={value!r} is not one of {', '.join(allowed)}. {why}"
        )
    return match[key]


@tool(
    name="calc_ph_adjustment",
    description=(
        "Convert a bench titration result, or a dose you already have, into a "
        "chemical feed rate for RAISING pH in an activated sludge process. "
        "Handles caustic soda, hydrated lime, quicklime and soda ash. Use for "
        "'how much caustic do I need', alkalinity or pH correction dosing, and "
        "for turning a jar/titration result into a feeder setpoint. "
        "Not for lowering pH — acid dosing has different reagents, hazards and "
        "endpoint logic and is not this tool with the sign flipped. "
        "reagent, input_mode and titrant_basis are required and have NO "
        "defaults. titrant_basis is the one that matters most: a standardized "
        "titrant measures pure reagent and needs the commercial strength "
        "applied, while weighing the actual product the plant feeds already "
        "includes its purity. Getting it backwards moves the answer by a factor "
        "of four on 25% caustic. If the operator has not said which, ASK. "
        "Never compute any of this yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "reagent": {
                "type": "string",
                "enum": list(REAGENTS),
                "description": (
                    "REQUIRED, no default. Which chemical is being fed. Sets "
                    "the equivalent weight used on the volumetric path. Do not "
                    "infer the product strength from this — that is a separate "
                    "required input."
                ),
            },
            "input_mode": {
                "type": "string",
                "enum": list(INPUT_MODES),
                "description": (
                    "REQUIRED, no default. 'from_titration' works from the "
                    "bench result. 'from_known_dose' skips straight to the "
                    "feed rate using a dose the operator already has from "
                    "history or a supplier."
                ),
            },
            "titrant_basis": {
                "type": "string",
                "enum": list(TITRANT_BASES),
                "description": (
                    "REQUIRED, no default. THE critical input. 'pure_reagent' "
                    "means the bench value is pure chemical — a standardized "
                    "titrant of known normality — so the commercial strength "
                    "correction MUST be applied. 'as_delivered_product' means "
                    "the operator weighed the actual product they feed, so its "
                    "purity is already in the number and applying strength "
                    "again double-counts it. Ask the operator which; do not "
                    "infer it from the bench method."
                ),
            },
            "bench_method": {
                "type": "string",
                "enum": list(BENCH_METHODS),
                "description": (
                    "Required when input_mode is 'from_titration'. "
                    "'volumetric' titrates with a solution of known normality "
                    "(typical for caustic). 'gravimetric' weighs dry reagent "
                    "into the sample (typical for lime)."
                ),
            },
            "titrant_volume": quantity_schema(
                "volume",
                "Volume of titrant used to reach the endpoint. Volumetric "
                "method only.",
            ),
            "titrant_normality": {
                "type": "number",
                "description": (
                    "Normality of the titrant in eq/L. Volumetric method only. "
                    "Read it off the standardized solution's label — do not "
                    "assume a value."
                ),
            },
            "sample_volume": quantity_schema(
                "volume",
                "Volume of the sample titrated, e.g. 1000 mL. Required for "
                "both bench methods.",
            ),
            "mass_before": quantity_schema(
                "mass",
                "Mass of reagent before the titration. Gravimetric method; "
                "supply with mass_after, or supply mass_used instead.",
            ),
            "mass_after": quantity_schema(
                "mass",
                "Mass of reagent remaining after the titration. Gravimetric "
                "method; supply with mass_before.",
            ),
            "mass_used": quantity_schema(
                "mass",
                "Mass of reagent consumed, if the operator has the difference "
                "already. Gravimetric method; alternative to "
                "mass_before/mass_after.",
            ),
            "dose": quantity_schema(
                "concentration",
                "Required dose. Only for input_mode 'from_known_dose'; on the "
                "titration path this is calculated.",
            ),
            "plant_flow": quantity_schema(
                "flow", "Flow the chemical is being dosed into."
            ),
            "product_strength_percent": {
                "type": "number",
                "description": (
                    "Percent w/w of active reagent in the delivered product. "
                    "REQUIRED when titrant_basis is 'pure_reagent', with no "
                    "default — ask for the figure on the delivery ticket or "
                    "product data sheet. MUST NOT be supplied when "
                    "titrant_basis is 'as_delivered_product', where the purity "
                    "is already in the weighed mass."
                ),
            },
            "solution_density": quantity_schema(
                "density",
                "Density of the delivered solution, e.g. 1.28 kg/L or "
                "10.7 lb/gal. Optional: supply it to get a volumetric feed "
                "rate, omit it and only masses are reported. There is no "
                "density table here — it moves with strength and temperature.",
            ),
            "endpoint_ph": {
                "type": "number",
                "description": (
                    "pH the bench sample was actually titrated to. Required "
                    "when input_mode is 'from_titration'."
                ),
            },
            "target_ph": {
                "type": "number",
                "description": "pH the plant is aiming to hold. Required.",
            },
            "plant_nitrifies": {
                "type": "boolean",
                "description": (
                    "REQUIRED. Whether the plant nitrifies. Nitrification "
                    "destroys alkalinity continuously (7.14 mg CaCO3 per mg "
                    "NH3-N oxidized), which a grab-sample titration cannot "
                    "see, so a titration-derived dose drifts low over time. "
                    "Ask the operator rather than assuming false — assuming "
                    "false silently drops that warning."
                ),
            },
            "sample_source": {
                "type": "string",
                "description": (
                    "Where the titrated sample came from, e.g. 'mixed liquor', "
                    "'primary effluent'. Compared against dosing_point."
                ),
            },
            "dosing_point": {
                "type": "string",
                "description": (
                    "Where the chemical is actually fed, e.g. 'aeration basin "
                    "influent'. Titrating one matrix and dosing another is not "
                    "equivalent — different buffering, different result."
                ),
            },
        },
        "required": ["reagent", "input_mode", "titrant_basis", "plant_flow",
                     "target_ph", "plant_nitrifies"],
    },
)
def calc_ph_adjustment(
    reagent=None, input_mode=None, titrant_basis=None, bench_method=None,
    titrant_volume=None, titrant_normality=None, sample_volume=None,
    mass_before=None, mass_after=None, mass_used=None,
    dose=None, plant_flow=None, product_strength_percent=None,
    solution_density=None, endpoint_ph=None, target_ph=None,
    plant_nitrifies=None, sample_source=None, dosing_point=None,
):
    """Bench titration or known dose -> pH-raising chemical feed rate.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.

    Stages 2 and 3 are delegated to calc_chemical_feed rather than repeated
    here; see the module header.
    """
    # 1. Enums first. These decide what the number MEANS, so a missing one is
    #    an error before any quantity is parsed.
    reagent = _enum(
        reagent, tuple(REAGENTS), "reagent",
        "It sets the equivalent weight for the volumetric path.",
    )
    input_mode = _enum(
        input_mode, INPUT_MODES, "input_mode",
        "It decides whether the dose is calculated from the bench result or "
        "taken as given.",
    )
    titrant_basis = _enum(
        titrant_basis, TITRANT_BASES, "titrant_basis",
        "It decides whether the commercial strength correction runs. A "
        "standardized titrant measures pure reagent; weighing the delivered "
        "product already includes its purity.",
    )
    spec = REAGENTS[reagent]

    if plant_nitrifies is None:
        raise ValueError(
            "plant_nitrifies is required. Nitrification destroys alkalinity "
            "continuously, which a grab-sample titration cannot see, so the "
            "answer needs a different caveat on a nitrifying plant. Ask the "
            "operator rather than assuming false."
        )
    if not isinstance(plant_nitrifies, bool):
        raise ValueError("plant_nitrifies must be true or false.")
    if plant_flow is None:
        raise ValueError("plant_flow is required.")
    if target_ph is None:
        raise ValueError("target_ph is required.")

    # 2. Parse. Dimension errors surface here, before any math.
    q = parse(plant_flow, "flow")
    v_titrant = parse(titrant_volume, "volume") if titrant_volume is not None else None
    v_sample = parse(sample_volume, "volume") if sample_volume is not None else None
    m_before = parse(mass_before, "mass") if mass_before is not None else None
    m_after = parse(mass_after, "mass") if mass_after is not None else None
    m_used = parse(mass_used, "mass") if mass_used is not None else None
    c_dose = parse(dose, "concentration") if dose is not None else None
    rho = parse(solution_density, "density") if solution_density is not None else None

    if q.canonical <= 0:
        raise ValueError("Plant flow must be positive.")

    # units.parse cannot catch a dose handed over as a density — both are
    # mass/volume — so the magnitude is checked instead. 1.28 kg/L is a
    # solution; 5.2 mg/L arriving here is a dose in the wrong slot.
    if rho is not None and not DENSITY_MIN <= rho.canonical <= DENSITY_MAX:
        raise ValueError(
            f"solution_density of {rho.value:g} {rho.unit} is "
            f"{rho.canonical:.6g} kg/L, outside the {DENSITY_MIN}-{DENSITY_MAX} "
            f"kg/L range any fed chemical solution falls in. Density and "
            f"concentration share a dimension, so this is most likely a dose "
            f"or a concentration in the density slot. Water is 1.0 kg/L and "
            f"50% caustic about 1.53 kg/L."
        )

    # 3. The strength correction, and the double-count it exists to prevent.
    if titrant_basis == "pure_reagent":
        if product_strength_percent is None:
            raise ValueError(
                "titrant_basis is 'pure_reagent', so product_strength_percent "
                "is required and has no default. The bench value is pure "
                f"{spec['formula']}; converting it to the product actually fed "
                "needs that product's strength. Ask for the figure on the "
                "delivery ticket — commercial caustic ships at 25% and 50%, "
                "and hydrated lime is not 100% Ca(OH)2. Do not infer it from "
                "the reagent."
            )
        if not 0 < product_strength_percent <= 100:
            raise ValueError(
                "product_strength_percent must be between 0 and 100."
            )
        strength_pct = float(product_strength_percent)
    else:
        if product_strength_percent is not None:
            raise ValueError(
                f"titrant_basis is 'as_delivered_product' but "
                f"product_strength_percent={product_strength_percent} was "
                f"supplied. That DOUBLE-COUNTS the purity: the operator "
                f"weighed the product they actually feed, so its strength is "
                f"already inside the measured mass. Applying "
                f"{product_strength_percent}% again would inflate the feed "
                f"rate by {100 / float(product_strength_percent):.2f}x. Either "
                f"drop product_strength_percent, or use "
                f"titrant_basis='pure_reagent' if the bench value really was "
                f"pure reagent."
            )
        strength_pct = 100.0          # no correction; the dose IS the product

    steps = []
    caveats = []
    warnings = []
    work = []

    # 4. Stage 1 — bench result to dose. Skipped entirely on from_known_dose.
    if input_mode == "from_known_dose":
        if c_dose is None:
            raise ValueError(
                "input_mode is 'from_known_dose', so dose is required."
            )
        if c_dose.canonical <= 0:
            raise ValueError("dose must be positive.")
        for name, val in (("bench_method", bench_method),
                          ("titrant_volume", titrant_volume),
                          ("sample_volume", sample_volume),
                          ("mass_used", mass_used)):
            if val is not None:
                raise ValueError(
                    f"input_mode is 'from_known_dose', which skips the bench "
                    f"calculation, but {name} was supplied. Use "
                    f"input_mode='from_titration' to work from the bench "
                    f"result, or drop {name}."
                )
        dose_mgl = c_dose.canonical
        bench_note = "not applicable (dose supplied directly)"
        endpoint_ph = None
    else:
        bench_method = _enum(
            bench_method, BENCH_METHODS, "bench_method",
            "It decides whether the dose comes from a titrant volume and "
            "normality or from a weighed mass.",
        )
        if endpoint_ph is None:
            raise ValueError(
                "endpoint_ph is required on the titration path — the pH the "
                "sample was actually titrated to. Without it there is no way "
                "to tell whether the bench result describes the target."
            )
        if c_dose is not None:
            raise ValueError(
                "input_mode is 'from_titration' but dose was also supplied. "
                "Use input_mode='from_known_dose' to work from the dose, or "
                "drop it and let the bench result set it."
            )
        if v_sample is None:
            raise ValueError(
                "sample_volume is required on the titration path — the dose is "
                "reagent per litre of sample."
            )
        if v_sample.canonical <= 0:
            raise ValueError("sample_volume must be positive.")
        sample_l = v_sample.to("L")

        if bench_method == "volumetric":
            if v_titrant is None or titrant_normality is None:
                raise ValueError(
                    "The volumetric method needs titrant_volume and "
                    "titrant_normality. If the operator weighed dry reagent "
                    "instead, use bench_method='gravimetric'."
                )
            if titrant_normality <= 0:
                raise ValueError("titrant_normality must be positive.")
            for name, val in (("mass_before", mass_before),
                              ("mass_after", mass_after),
                              ("mass_used", mass_used)):
                if val is not None:
                    raise ValueError(
                        f"bench_method is 'volumetric' but {name} was "
                        f"supplied. Use 'gravimetric' for a weighed reagent."
                    )
            titrant_ml = v_titrant.to("mL")
            eq_wt = spec["eq_weight"]
            # mL x (meq/mL) = meq; meq x (mg/meq) = mg; mg / L = mg/L
            meq = titrant_ml * float(titrant_normality)
            steps.append({
                "label": "Equivalents of titrant",
                "formula": "titrant volume x normality",
                "substituted": f"{titrant_ml:g} mL x {titrant_normality:g} eq/L",
                "value": round(meq, 4),
                "unit": "meq",
            })
            reagent_mg = meq * eq_wt
            steps.append({
                "label": "Reagent mass in the sample",
                "formula": "equivalents x equivalent weight",
                "substituted": f"{meq:g} meq x {eq_wt:g} mg/meq",
                "value": round(reagent_mg, 4),
                "unit": "mg",
            })
            bench_note = (
                f"volumetric titration, {titrant_normality:g} N, "
                f"{titrant_ml:g} mL into {sample_l * 1000:g} mL sample"
            )
        else:
            if titrant_volume is not None or titrant_normality is not None:
                raise ValueError(
                    "bench_method is 'gravimetric' but titrant_volume or "
                    "titrant_normality was supplied. Use 'volumetric' for a "
                    "standardized titrant."
                )
            if m_used is not None:
                if m_before is not None or m_after is not None:
                    raise ValueError(
                        "Supply either mass_used, or mass_before with "
                        "mass_after — not both. Two routes to the same number "
                        "that disagree cannot be reconciled here."
                    )
                reagent_mg = m_used.to("g") * 1000
                steps.append({
                    "label": "Reagent mass in the sample",
                    "formula": "mass used, as supplied",
                    "substituted": f"{m_used.value:g} {m_used.unit}",
                    "value": round(reagent_mg, 4),
                    "unit": "mg",
                })
            else:
                if m_before is None or m_after is None:
                    raise ValueError(
                        "The gravimetric method needs mass_used, or both "
                        "mass_before and mass_after."
                    )
                before_mg = m_before.to("g") * 1000
                after_mg = m_after.to("g") * 1000
                if after_mg > before_mg:
                    raise ValueError(
                        f"mass_after ({m_after.value:g} {m_after.unit}) is "
                        f"greater than mass_before ({m_before.value:g} "
                        f"{m_before.unit}), so no reagent was consumed. The "
                        f"two are probably swapped."
                    )
                reagent_mg = before_mg - after_mg
                steps.append({
                    "label": "Reagent mass in the sample",
                    "formula": "mass before - mass after",
                    "substituted": f"{before_mg:g} mg - {after_mg:g} mg",
                    "value": round(reagent_mg, 4),
                    "unit": "mg",
                })
            bench_note = (
                f"gravimetric, {reagent_mg:g} mg into {sample_l * 1000:g} mL "
                f"sample"
            )

        if reagent_mg <= 0:
            raise ValueError(
                "The bench result gives a zero or negative reagent mass, so "
                "there is no dose to compute."
            )
        dose_mgl = reagent_mg / sample_l
        steps.append({
            "label": "Required dose",
            "formula": "reagent mass / sample volume",
            "substituted": f"{reagent_mg:g} mg / {sample_l:g} L",
            "value": round(dose_mgl, 3),
            "unit": "mg/L",
        })

    # 5. Stages 2 and 3 — delegated. calc_chemical_feed does flow x dose, then
    #    the strength and density correction, and is already tested. Strength
    #    and SG are passed explicitly so none of its defaults fire.
    #
    #    When no density was given, SG 1.0 goes in and every volume it returns
    #    is discarded — a mass-only answer is the contract, not a volume
    #    computed against an assumed density.
    sg = rho.canonical if rho is not None else 1.0
    feed = calc_chemical_feed(
        flow=plant_flow,
        dose={"value": dose_mgl, "unit": "mg/L"},
        solution_strength_pct=strength_pct,
        solution_sg=sg,
    )
    assert not feed["caveats"], feed["caveats"]     # no assumed values reached it
    pure_kg_d = feed["result"]["neat_kg_per_day"]
    steps.append({
        "label": "Reagent mass per day",
        "formula": "dose x flow  (via calc_chemical_feed)",
        "substituted": f"{dose_mgl:g} mg/L x {q.value:g} {q.unit}",
        "value": pure_kg_d,
        "unit": "kg/d",
    })

    # Stage 3 runs only on a pure_reagent basis. On an as-delivered basis
    # strength_pct is 100, so this is the identity — but it is still reported
    # as "no correction applied" rather than as a 100% correction, because the
    # two mean different things.
    if titrant_basis == "pure_reagent":
        product_kg_d = pure_kg_d * 100 / strength_pct
        steps.append({
            "label": "Commercial product per day",
            "formula": "pure reagent x 100 / strength",
            "substituted": f"{pure_kg_d:g} kg/d x 100 / {strength_pct:g}",
            "value": round(product_kg_d, 2),
            "unit": "kg/d",
        })
    else:
        product_kg_d = pure_kg_d

    product_l_d = product_l_h = None
    if rho is not None:
        product_l_d = product_kg_d / rho.canonical
        steps.append({
            "label": "Product volume per day",
            "formula": "product mass / solution density",
            "substituted": f"{product_kg_d:.2f} kg/d / {rho.canonical:g} kg/L",
            "value": round(product_l_d, 1),
            "unit": "L/d",
        })
        product_l_h = product_l_d / 24
        steps.append({
            "label": "Product volume per hour",
            "formula": "product volume per day / 24",
            "substituted": f"{product_l_d:.1f} L/d / 24 h",
            "value": round(product_l_h, 2),
            "unit": "L/h",
        })

    product_kg_h = product_kg_d / 24
    steps.append({
        "label": "Product mass per hour",
        "formula": "product mass per day / 24",
        "substituted": f"{product_kg_d:.2f} kg/d / 24 h",
        "value": round(product_kg_h, 3),
        "unit": "kg/h",
    })

    # 6. Warnings, each a line in the working rather than trailing prose.
    if input_mode == "from_titration" and abs(endpoint_ph - target_ph) > PH_DIVERGENCE_LIMIT:
        warnings.append(
            f"the sample was titrated to pH {endpoint_ph:g} but the target is "
            f"pH {target_ph:g}, a gap of "
            f"{abs(endpoint_ph - target_ph):.2g} units. Carbonate buffering "
            f"makes dose versus pH nonlinear, so this dose has NOT been scaled "
            f"to reach the target and must not be. Repeat the titration to "
            f"pH {target_ph:g}."
        )

    if sample_source is None or dosing_point is None:
        warnings.append(
            "sample source and dosing point were not both stated, so it could "
            "not be checked that the titrated matrix matches where the "
            "chemical is fed. Titrating mixed liquor and dosing the influent "
            "are not equivalent — different buffering, different dose."
        )
    elif sample_source.strip().lower() != dosing_point.strip().lower():
        warnings.append(
            f"the sample was taken from {sample_source.strip()} but the "
            f"chemical is fed at {dosing_point.strip()}. These are different "
            f"matrices with different buffering, so the bench result may not "
            f"describe the water actually being dosed. Retitrate a sample from "
            f"the dosing point."
        )

    if dose_mgl > DOSE_SANITY_LIMIT_MG_L:
        warnings.append(
            f"the required dose of {dose_mgl:,.0f} mg/L is far above the tens "
            f"of mg/L a pH-adjustment dose runs at. This is almost always a "
            f"units error — a bench weight entered in grams instead of "
            f"milligrams is off by a thousand — rather than a real demand. "
            f"Check the bench figures before setting a feeder to "
            f"{product_kg_d:,.0f} kg/d."
        )

    if product_l_d is not None:
        flow_l_d = q.canonical * 86400
        fraction = product_l_d / flow_l_d
        if fraction > VOLUME_FRACTION_LIMIT:
            warnings.append(
                f"the product volume of {product_l_d:.1f} L/d is "
                f"{fraction * 100:.1f}% of the plant flow "
                f"({flow_l_d:.0f} L/d). Above "
                f"{VOLUME_FRACTION_LIMIT * 100:g}% this is almost always a "
                f"units error — commonly a mass reported as a volume — rather "
                f"than a real dose. Check the dose, the strength and the "
                f"density before setting a feeder to this."
            )

    if plant_nitrifies:
        warnings.append(
            "this plant nitrifies. The titration measured the buffer deficit "
            "in a grab sample at one moment; nitrification destroys alkalinity "
            "CONTINUOUSLY at 7.14 mg CaCO3 per mg NH3-N oxidized, which no "
            "grab sample can capture. This dose covers the immediate deficit "
            "only and will drift low as ammonia load rises. Trend alkalinity "
            "and pH and adjust — do not dose this once and walk away."
        )

    # 7. The working block. Every input that decides the answer gets a named
    #    line, including the ones that resolved to nothing.
    basis_note = (
        f"pure reagent (commercial strength correction applies at "
        f"{strength_pct:g}%)"
        if titrant_basis == "pure_reagent"
        else "as-delivered product (purity already in the measured mass; NO "
             "strength correction applied)"
    )
    work.append(_line("Reagent", f"{spec['label']} ({spec['formula']})"))
    work.append(_line("Bench method", bench_note))
    work.append(_line("Titrant basis", basis_note))
    work.append(_line(
        "Endpoint / target pH",
        f"{endpoint_ph:g} / {target_ph:g}" if endpoint_ph is not None
        else f"not titrated / {target_ph:g}",
    ))
    work.append(_line(
        "Sample / dose point",
        f"{sample_source.strip()} / {dosing_point.strip()}"
        if sample_source and dosing_point else "not stated",
    ))
    work.append(_line(
        "Required dose",
        f"{round(dose_mgl, 3):g} mg/L as "
        + ("pure " if titrant_basis == "pure_reagent" else "")
        + spec["formula"],
    ))
    work.append(_line(
        "Pure reagent" if titrant_basis == "pure_reagent" else "Reagent as fed",
        f"{round(dose_mgl, 3):g} mg/L x {q.value:g} {q.unit} = {pure_kg_d:.2f} kg/d",
    ))
    if titrant_basis == "pure_reagent":
        work.append(_line(
            "Commercial product",
            f"{pure_kg_d:.2f} kg/d x 100 / {strength_pct:g} = "
            f"{product_kg_d:.2f} kg/d of {strength_pct:g}% product",
        ))
    else:
        work.append(_line(
            "Commercial product",
            "no correction (basis is as-delivered product)",
        ))
    if product_l_d is not None:
        work.append(_line(
            "Product volume",
            f"{product_kg_d:.2f} kg/d / {rho.canonical:g} kg/L = "
            f"{product_l_d:.1f} L/d",
        ))
        work.append(_line(
            "Feed rate",
            f"{product_l_h:.2f} L/h ({product_kg_h:.3f} kg/h) flow-paced "
            f"across 24 h",
        ))
    else:
        work.append(_line(
            "Product volume",
            "not calculated — solution density was not supplied. Mass only.",
        ))
        work.append(_line(
            "Feed rate",
            f"{product_kg_h:.3f} kg/h flow-paced across 24 h",
        ))
    for w in warnings:
        work.append(_line("WARNING", w))

    # 8. The caveat that fires on every single result, per section 2.5.
    caveats.extend(warnings)
    caveats.append(
        "This is a STARTING POINT, not a steady-state dose. A titration "
        "measures the buffer demand of one grab sample at one moment. Feed it "
        "flow-paced across the full 24 hours rather than as a batch, then "
        "trend pH and alkalinity at the dosing point and downstream, and "
        "adjust from what the plant actually does."
    )
    if spec["hazard"]:
        caveats.append(spec["hazard"])
    if reagent == "quicklime" and titrant_basis == "pure_reagent":
        caveats.append(
            "Quicklime (CaO) was priced on its own equivalent weight of "
            "28.04 mg/meq. If the plant actually feeds slaked lime, the "
            "hydrated-lime equivalent weight of 37.05 applies instead and this "
            "dose is about 25% low."
        )

    # 9. Conversions first, so the operator sees what was assumed.
    parsed = [x for x in (q, v_titrant, v_sample, m_before, m_after, m_used,
                          c_dose, rho) if x is not None]
    out = ["Inputs:", echo_all(*parsed), ""]
    out += work
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in parsed]
    conversions += [c for c in feed["conversions"] if "working units" in c]
    conversions += [
        f"NOTE: {n}" for n in dict.fromkeys(n for x in parsed for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            # Masses and volumes are named apart on purpose — the source this
            # is derived from labelled a mass as m3/d and carried it through.
            "dose_mg_per_l": round(dose_mgl, 3),
            "pure_reagent_kg_per_day": round(pure_kg_d, 2),
            "product_kg_per_day": round(product_kg_d, 2),
            "product_kg_per_hour": round(product_kg_h, 3),
            "product_l_per_day": round(product_l_d, 1) if product_l_d is not None else None,
            "product_l_per_hour": round(product_l_h, 2) if product_l_h is not None else None,
            "reagent": reagent,
            "reagent_formula": spec["formula"],
            "titrant_basis": titrant_basis,
            "input_mode": input_mode,
            "bench_method": bench_method if input_mode == "from_titration" else None,
            "product_strength_percent": (
                strength_pct if titrant_basis == "pure_reagent" else None
            ),
            "strength_correction_applied": titrant_basis == "pure_reagent",
            "warnings": warnings,
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # Caustic, volumetric, pure-reagent basis, through commercial strength.
    # 6.5 mL of 0.02 N NaOH into a 1000 mL sample is 5.2 mg/L.
    print(calc_ph_adjustment(
        reagent="caustic_soda", input_mode="from_titration",
        titrant_basis="pure_reagent", bench_method="volumetric",
        titrant_volume={"value": 6.5, "unit": "mL"},
        titrant_normality=0.02,
        sample_volume={"value": 1000, "unit": "mL"},
        plant_flow={"value": 7.5, "unit": "MGD"},
        product_strength_percent=25,
        solution_density={"value": 1.28, "unit": "kg/L"},
        endpoint_ph=7.2, target_ph=7.2, plant_nitrifies=False,
        sample_source="mixed liquor", dosing_point="mixed liquor",
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Lime, gravimetric, as-delivered basis. No strength correction, and no
    # volume, because no density was supplied.
    print(calc_ph_adjustment(
        reagent="hydrated_lime", input_mode="from_titration",
        titrant_basis="as_delivered_product", bench_method="gravimetric",
        mass_used={"value": 4.4, "unit": "mg"},
        sample_volume={"value": 1000, "unit": "mL"},
        plant_flow={"value": 7.5, "unit": "MGD"},
        endpoint_ph=7.0, target_ph=7.0, plant_nitrifies=True,
        sample_source="primary effluent", dosing_point="aeration basin influent",
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # The double-count: an as-delivered result with a purity correction asked
    # for on top of it.
    try:
        calc_ph_adjustment(
            reagent="hydrated_lime", input_mode="from_titration",
            titrant_basis="as_delivered_product", bench_method="gravimetric",
            mass_used={"value": 4.4, "unit": "mg"},
            sample_volume={"value": 1000, "unit": "mL"},
            plant_flow={"value": 7.5, "unit": "MGD"},
            product_strength_percent=93,
            endpoint_ph=7.0, target_ph=7.0, plant_nitrifies=False,
        )
        raise AssertionError("expected a double-count error")
    except ValueError as e:
        print(f"double-counted purity correctly rejected:\n  {e}")

    print("\n" + "=" * 68 + "\n")

    # A dose in the density slot cannot be caught dimensionally — both are
    # mass/volume — so it is caught on magnitude.
    try:
        calc_ph_adjustment(
            reagent="caustic_soda", input_mode="from_known_dose",
            titrant_basis="pure_reagent",
            dose={"value": 5.2, "unit": "mg/L"},
            plant_flow={"value": 7.5, "unit": "MGD"},
            product_strength_percent=25,
            solution_density={"value": 5.2, "unit": "mg/L"},
            target_ph=7.2, plant_nitrifies=False,
        )
        raise AssertionError("expected a density range error")
    except ValueError as e:
        print(f"dose in the density slot correctly rejected:\n  {e}")

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_ph_adjustment(
            reagent="caustic_soda", input_mode="from_known_dose",
            titrant_basis="pure_reagent",
            dose={"value": 5.2, "unit": "mg/L"},
            plant_flow={"value": 150, "unit": "m3"},      # volume as flow
            product_strength_percent=25,
            target_ph=7.2, plant_nitrifies=False,
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
