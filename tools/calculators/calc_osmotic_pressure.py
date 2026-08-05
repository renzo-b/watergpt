# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Osmotic pressure from TDS and temperature, over the whole range a membrane
# plant works in — brackish groundwater through seawater and SWRO brine.
#
# Why this is its own module: osmotic pressure is the term that decides whether
# a feed pressure can produce permeate at all, and calc_ro_normalization needs
# it on both sides of a comparison. It was previously a four-line TDS
# approximation private to that file, valid only for brackish water. A seawater
# train normalized with it was being handed a number roughly 30% too high.
#
# TWO CORRELATIONS, because no single one covers the range honestly:
#
#   brackish_tds  The TDS shortcut used throughout the RO industry:
#                   pi (psi) = 0.0385 x TDS x (T + 320) / (1000 - TDS/1000)
#                 Composition-independent, deliberately CONSERVATIVE — it runs
#                 high, which is the safe direction for sizing a feed pump. It
#                 is quoted for feeds below about 10,000 mg/L.
#
#   seawater      Sharqawy, Lienhard & Zubair (2010), Eq. (49) — the osmotic
#                 coefficient of seawater fitted to Bromley's data, converted
#                 to a pressure through the van 't Hoff relation:
#                   pi = phi x m x R x T x rho_w
#                 Valid 10-120 g/kg and 0-200 C, +/-1.4%. Assumes standard
#                 seawater ION COMPOSITION, which is what makes it right for
#                 SWRO and wrong for, say, a sulphate-dominant groundwater.
#
# The shortcut runs about 30% above the seawater correlation across the WHOLE
# range, not just at seawater strength — the offset is close to constant from
# 500 mg/L to 70,000. That is not a bug in either: the shortcut is a
# design-margin formula and the correlation is a physical property fit. Two
# consequences worth holding on to:
#
#   - A COMPARISON between two states largely cancels the offset, which is why
#     brackish normalization was giving sane answers on a high NDP.
#   - An ABSOLUTE number — minimum feed pressure, how much NDP is left — does
#     not cancel anything, and there the choice is the whole answer.
#
# So which correlation a number came from is reported with the number, never
# silently chosen and forgotten.
#
# SELECTION is therefore about COMPOSITION, not about which is more accurate.
# Below BRACKISH_TDS_LIMIT_MG_L the water is some unknown groundwater, Eq. (49)
# would be extrapolating below its fitted floor, and the composition-blind
# shortcut is the safer default. Above it the water is seawater or a seawater
# brine, which is exactly what Eq. (49) is fitted to. The boundary is one
# number because the shortcut's usual upper bound and Eq. (49)'s stated lower
# bound are the same 10 g/kg.
#
# Pass an explicit model to override; a caller comparing two states MUST pin
# one model for both, or the crossover appears in the answer as a performance
# change that never happened.
#
# Sources travel with the numbers rather than living only up here: REFERENCES
# below is attached to each model, quoted in the caveats the operator reads,
# and repeated in the docstring of every function that implements one. An
# osmotic pressure with no stated provenance is not checkable.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo_all, parse, quantity_schema
from tools.registry import tool

REFERENCES = {
    "sharqawy": (
        "Sharqawy, Lienhard & Zubair (2010), 'Thermophysical properties of "
        "seawater: a review of existing correlations and data', Desalination "
        "and Water Treatment 16(1-3) 354-380 — Eq. (49) osmotic coefficient "
        "(fitted to Bromley's data, 10-120 g/kg, 0-200 C, +/-1.4%) and Eq. (8) "
        "density (0-0.16 kg/kg, 0-180 C, +/-0.1%). http://web.mit.edu/seawater"
    ),
    "millero": (
        "Millero, Feistel, Wright & McDougall (2008), 'The composition of "
        "Standard Seawater and the definition of the Reference-Composition "
        "Salinity Scale', Deep-Sea Research I 55(1) 50-72 — mean molar mass of "
        "Reference-Composition sea salt, 31.4038 g/mol per mole of ions."
    ),
    # Deliberately not attributed. This formula is everywhere in RO practice
    # and nothing found so far establishes where it came from; naming a plant
    # manual that merely repeats it would be a fabricated citation. What CAN be
    # said about it is said.
    "tds_shortcut": (
        "Industry TDS shortcut, pi(psi) = 0.0385 x TDS x (T + 320) / "
        "(1000 - TDS/1000). In wide use, primary source not established — this "
        "is the (T + 320) variant, which runs above the more commonly "
        "published van 't Hoff form pi(psi) = 0.0385 x TDS x (T + 273) / 1000. "
        "Retained here unchanged so brackish trending stays comparable with "
        "figures produced before this module existed. For a defensible "
        "brackish number from a real ion analysis, use FilmTec's molality form "
        "pi(psi) = 1.12 x (273 + T) x sum(m_j) instead (DuPont FilmTec Reverse "
        "Osmosis Technical Manual, form 45-D01504-en)."
    ),
}

# Mole-weighted molar mass of Reference-Composition sea salt. This is mass per
# mole of DISSOLVED IONS, not per mole of salt molecules, which is what a
# colligative property counts — 1 g of sea salt is fewer particles than 1 g of
# NaCl (58.44/2 = 29.2 g per mole of ions), and that difference is most of why
# the seawater number lands below the composition-blind shortcut.
SEA_SALT_MOLAR_MASS_G_MOL = 31.4038

GAS_CONSTANT_J_PER_MOL_K = 8.3144598
PA_PER_BAR = 1.0e5
PSI_PER_BAR = 14.5038

# Where the shortcut stops and the seawater correlation starts. One number,
# because the shortcut's usual upper bound and Eq. (49)'s stated lower bound
# are the same 10 g/kg.
BRACKISH_TDS_LIMIT_MG_L = 10000.0

# Eq. (49) is fitted to 120 g/kg. Past that there is no correlation here, and
# an SWRO brine at 70-75% recovery is already near it.
SEAWATER_TDS_LIMIT_MG_L = 120000.0

# Outside this band neither correlation is fitted. Eq. (8) stops at 180 C;
# a membrane plant is nowhere near either end.
TEMP_MIN_C = 0.0
TEMP_MAX_C = 180.0

# Within this band around the crossover, both models are defensible and they
# disagree materially, so both numbers are reported rather than one chosen.
OVERLAP_LO_MG_L = 5000.0
OVERLAP_HI_MG_L = 20000.0

# Sharqawy Eq. (8), seawater density. Salinity as a mass fraction (kg/kg),
# temperature in C, result in kg/m3. +/-0.1% over 0-180 C and 0-0.16 kg/kg.
# The first bracket alone (S = 0) is pure water, and is used as such.
DENSITY_WATER_COEFFS = (9.999e2, 2.034e-2, -6.162e-3, 2.261e-5, -4.657e-8)
DENSITY_SALT_COEFFS = (8.020e2, -2.001, 1.677e-2, -3.060e-5, -1.613e-5)

# Sharqawy Eq. (49), seawater osmotic coefficient. Same units as above.
# Note the temperature series skips t^3 and the salinity series skips S^3 —
# that is the published form, not a transcription slip.
PHI_COEFFS = (
    8.9453e-1,    # a1
    4.1561e-4,    # a2   t
    -4.6262e-6,   # a3   t^2
    2.2211e-11,   # a4   t^4
    -1.1445e-1,   # a5   S
    -1.4783e-3,   # a6   S t
    -1.3526e-8,   # a7   S t^3
    7.0132,       # a8   S^2
    5.696e-2,     # a9   S^2 t
    -2.8624e-4,   # a10  S^2 t^2
)


def seawater_density_kg_m3(salinity_kg_kg: float, temp_c: float) -> float:
    """Seawater density in kg/m3. Pass salinity 0 for pure water.

    Source: Sharqawy, Lienhard & Zubair (2010) Eq. (8), Desalination and Water
    Treatment 16(1-3) 354-380. Valid 0-0.16 kg/kg and 0-180 C to +/-0.1%.
    """
    a, b, t, s = DENSITY_WATER_COEFFS, DENSITY_SALT_COEFFS, temp_c, salinity_kg_kg
    water = a[0] + a[1] * t + a[2] * t**2 + a[3] * t**3 + a[4] * t**4
    salt = (b[0] * s + b[1] * s * t + b[2] * s * t**2 + b[3] * s * t**3
            + b[4] * s**2 * t**2)
    return water + salt


def osmotic_coefficient(salinity_kg_kg: float, temp_c: float) -> float:
    """Osmotic coefficient of seawater. About 0.907 for standard seawater.

    Source: Sharqawy, Lienhard & Zubair (2010) Eq. (49), Desalination and Water
    Treatment 16(1-3) 354-380, fitted to the data of Bromley et al. (1974).
    Valid 10-120 g/kg and 0-200 C to +/-1.4%.
    """
    a, t, s = PHI_COEFFS, temp_c, salinity_kg_kg
    return (
        a[0] + a[1] * t + a[2] * t**2 + a[3] * t**4
        + a[4] * s + a[5] * s * t + a[6] * s * t**3
        + a[7] * s**2 + a[8] * s**2 * t + a[9] * s**2 * t**2
    )


def salinity_kg_kg(tds_mgl: float, temp_c: float) -> float:
    """TDS as mass per VOLUME (mg/L) to salinity as a mass FRACTION (kg/kg).

    The correlations are written in mass fraction; an operator's TDS reading is
    mass per litre. Converting between them needs the solution density, which
    itself depends on the salinity — so iterate. It converges in two passes,
    and the correction is worth having: at seawater strength 35,000 mg/L is
    34.2 g/kg, a 2.3% difference that would otherwise bias every SWRO answer
    the same way.
    """
    s = tds_mgl / 1.0e6                       # first guess: 1 kg/L
    for _ in range(8):
        # mg/L is g/m3; dividing by kg/m3 gives g/kg; /1000 gives kg/kg.
        nxt = tds_mgl / seawater_density_kg_m3(s, temp_c) / 1000.0
        if abs(nxt - s) < 1e-14:
            return nxt
        s = nxt
    return s


def _osmotic_brackish_bar(tds_mgl: float, temp_c: float) -> float:
    """The industry TDS shortcut, unchanged from what calc_ro_normalization
    used before this module existed — so brackish answers do not move.

    Source: REFERENCES['tds_shortcut'] — in wide use, primary source not
    established. Composition-blind and conservative; it runs roughly 30% above
    the seawater correlation across the whole range.
    """
    psi = 0.0385 * tds_mgl * (temp_c + 320.0) / (1000.0 - tds_mgl / 1000.0)
    return psi / PSI_PER_BAR


def _osmotic_seawater_bar(tds_mgl: float, temp_c: float) -> float:
    """Osmotic coefficient through van 't Hoff: pi = phi x m x R x T x rho_w.

    Sources: REFERENCES['sharqawy'] for the osmotic coefficient and density,
    REFERENCES['millero'] for the mean molar mass that turns a salinity into a
    count of dissolved ions.
    """
    s = salinity_kg_kg(tds_mgl, temp_c)
    s_g_kg = s * 1000.0
    phi = osmotic_coefficient(s, temp_c)
    # Molality of dissolved ions: mol per kg of WATER, not per kg of solution.
    molality = 1000.0 * s_g_kg / ((1000.0 - s_g_kg) * SEA_SALT_MOLAR_MASS_G_MOL)
    rho_w = seawater_density_kg_m3(0.0, temp_c)
    pascals = (phi * molality * GAS_CONSTANT_J_PER_MOL_K
               * (temp_c + 273.15) * rho_w)
    return pascals / PA_PER_BAR


OSMOTIC_MODELS = {
    "brackish_tds": {
        "fn": _osmotic_brackish_bar,
        "label": "brackish TDS approximation",
        "source": "industry TDS shortcut, 0.0385 x TDS x (T + 320) / (1000 - TDS/1000) psi",
        "range": f"feeds below about {BRACKISH_TDS_LIMIT_MG_L:g} mg/L",
        "references": [REFERENCES["tds_shortcut"]],
    },
    "seawater": {
        "fn": _osmotic_seawater_bar,
        "label": "seawater osmotic coefficient",
        "source": "Sharqawy, Lienhard & Zubair (2010) Eq. (49), +/-1.4%",
        "range": f"{BRACKISH_TDS_LIMIT_MG_L:g}-{SEAWATER_TDS_LIMIT_MG_L:g} mg/L, 0-200 C",
        "references": [REFERENCES["sharqawy"], REFERENCES["millero"]],
    },
}


def select_osmotic_model(tds_mgl: float) -> str:
    """Which correlation a water of this strength should be evaluated with.

    Deterministic and reported, never silent. Callers comparing two states must
    select once from the stronger of the two and pin it for both.
    """
    return "seawater" if tds_mgl >= BRACKISH_TDS_LIMIT_MG_L else "brackish_tds"


def osmotic_pressure_bar(tds_mgl, temp_c, model=None):
    """Osmotic pressure in bar, plus notes on anything the caller should see.

    Returns (bar, model_name, notes). Raises on readings no correlation covers;
    a reading merely outside the CHOSEN model's comfort zone gets a note, not an
    exception, because the caller may have pinned that model deliberately.
    """
    if tds_mgl < 0:
        raise ValueError(
            f"TDS must not be negative (got {tds_mgl:g} mg/L)."
        )
    if not TEMP_MIN_C <= temp_c <= TEMP_MAX_C:
        raise ValueError(
            f"Temperature {temp_c:g} C is outside the {TEMP_MIN_C:g}-"
            f"{TEMP_MAX_C:g} C range these correlations are fitted over."
        )
    if tds_mgl > SEAWATER_TDS_LIMIT_MG_L:
        raise ValueError(
            f"TDS of {tds_mgl:g} mg/L is above the "
            f"{SEAWATER_TDS_LIMIT_MG_L:g} mg/L ceiling of the seawater "
            "correlation. Past that there is no fit here — use a full "
            "ion-speciation model (OLI, PHREEQC) rather than a TDS shortcut."
        )

    if model is None or model == "auto":
        model = select_osmotic_model(tds_mgl)
    if model not in OSMOTIC_MODELS:
        raise ValueError(
            f"Unknown osmotic model {model!r}. Use one of: "
            f"{', '.join(sorted(OSMOTIC_MODELS))}, or 'auto'."
        )

    notes = []
    if model == "brackish_tds" and tds_mgl > BRACKISH_TDS_LIMIT_MG_L:
        notes.append(
            f"{tds_mgl:g} mg/L is above the {BRACKISH_TDS_LIMIT_MG_L:g} mg/L "
            "range of the brackish TDS approximation, which runs high there. "
            "The seawater correlation is the better fit above that."
        )
    if model == "seawater" and tds_mgl < BRACKISH_TDS_LIMIT_MG_L:
        notes.append(
            f"{tds_mgl:g} mg/L is below the {BRACKISH_TDS_LIMIT_MG_L:g} mg/L "
            "lower bound of the seawater correlation's fitted range, so this "
            "is an extrapolation — and it assumes seawater ion composition, "
            "which a brackish groundwater rarely has."
        )

    return OSMOTIC_MODELS[model]["fn"](tds_mgl, temp_c), model, notes


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

@tool(
    name="calc_osmotic_pressure",
    description=(
        "Calculate the osmotic pressure of a water from its TDS and "
        "temperature, for brackish water through seawater and SWRO brine. Use "
        "for ANY question about osmotic pressure, the minimum feed pressure an "
        "RO train needs, why a membrane produces no permeate, or how much net "
        "driving pressure is left. Never compute it yourself — the correlation "
        "depends on which range the water is in. "
        "Osmotic pressure is what the feed pressure must overcome before a "
        "single litre of permeate is produced, so it is the floor under any "
        "feed-pressure answer, not the answer itself. "
        "Pass the TDS and temperature with the units the operator used — do "
        "not convert. Leave the model unset unless the plant has told you "
        "which correlation it standardises on."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "concentration": quantity_schema(
                "concentration",
                "TDS of the stream. For a whole RO train the membrane sees the "
                "average concentrate-side TDS, which is higher than the feed "
                "TDS — use calc_ro_normalization for that.",
            ),
            "temperature": quantity_schema("temperature", "Water temperature."),
            "model": {
                "type": "string",
                "enum": ["auto", "brackish_tds", "seawater"],
                "description": (
                    "Which correlation to use. Omit (or 'auto') to select by "
                    "TDS, which is almost always right: the brackish TDS "
                    "approximation below 10000 mg/L, the seawater osmotic "
                    "coefficient above it. Set explicitly only when the plant "
                    "standardises on one, or when comparing two waters that "
                    "would otherwise land on different correlations."
                ),
            },
        },
        "required": ["concentration", "temperature"],
    },
)
def calc_osmotic_pressure(concentration, temperature, model=None):
    """Osmotic pressure of a water at a temperature, over the full RO range.

    Sources, also emitted as caveats so they reach the operator with the
    number rather than sitting in a docstring nobody opens:

      seawater      Sharqawy, Lienhard & Zubair (2010), Desalination and Water
                    Treatment 16(1-3) 354-380 — Eq. (49) osmotic coefficient,
                    Eq. (8) density. http://web.mit.edu/seawater
                    Millero, Feistel, Wright & McDougall (2008), Deep-Sea
                    Research I 55(1) 50-72 — mean molar mass of sea salt.
      brackish_tds  Industry TDS shortcut; primary source not established.
                    See REFERENCES['tds_shortcut'] for what can be said.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    tds_q = parse(concentration, "concentration")
    temp_q = parse(temperature, "temperature")
    tds = tds_q.canonical
    temp_c = temp_q.canonical

    steps = []
    caveats = []

    requested = model
    pressure, used, notes = osmotic_pressure_bar(tds, temp_c, model)
    spec = OSMOTIC_MODELS[used]

    if used == "seawater":
        s = salinity_kg_kg(tds, temp_c)
        s_g_kg = s * 1000.0
        rho_sw = seawater_density_kg_m3(s, temp_c)
        steps.append({
            "label": "Salinity as a mass fraction",
            "formula": "TDS / seawater density",
            "substituted": f"{tds:g} mg/L / {rho_sw:.1f} kg/m3",
            "value": round(s_g_kg, 3),
            "unit": "g/kg",
        })

        phi = osmotic_coefficient(s, temp_c)
        steps.append({
            "label": "Osmotic coefficient",
            "formula": "Sharqawy Eq. (49), polynomial in salinity and temperature",
            "substituted": f"phi(S = {s_g_kg:.3f} g/kg, t = {temp_c:g} C)",
            "value": round(phi, 4),
            "unit": "",                            # dimensionless
        })

        molality = 1000.0 * s_g_kg / (
            (1000.0 - s_g_kg) * SEA_SALT_MOLAR_MASS_G_MOL
        )
        steps.append({
            "label": "Total ion molality",
            "formula": "salinity / ((1 - salinity) x mean molar mass of sea salt)",
            "substituted": (
                f"{s_g_kg:.3f} g/kg / ((1000 - {s_g_kg:.3f}) / 1000 x "
                f"{SEA_SALT_MOLAR_MASS_G_MOL:g} g/mol)"
            ),
            "value": round(molality, 4),
            "unit": "mol/kg",
        })

        rho_w = seawater_density_kg_m3(0.0, temp_c)
        steps.append({
            "label": "Osmotic pressure",
            "formula": "osmotic coefficient x molality x R x T x water density",
            "substituted": (
                f"{phi:.4f} x {molality:.4f} mol/kg x "
                f"{GAS_CONSTANT_J_PER_MOL_K:g} J/mol/K x "
                f"{temp_c + 273.15:.2f} K x {rho_w:.1f} kg/m3"
            ),
            "value": round(pressure, 3),
            "unit": "bar",
        })
    else:
        psi = pressure * PSI_PER_BAR
        steps.append({
            "label": "Osmotic pressure",
            "formula": "0.0385 x TDS x (T + 320) / (1000 - TDS / 1000)",
            "substituted": (
                f"0.0385 x {tds:g} x ({temp_c:g} + 320) / "
                f"(1000 - {tds:g} / 1000)"
            ),
            "value": round(psi, 2),
            "unit": "psi",
        })
        steps.append({
            "label": "Osmotic pressure in bar",
            "formula": "psi / 14.5038",
            "substituted": f"{psi:.2f} psi / {PSI_PER_BAR:g}",
            "value": round(pressure, 3),
            "unit": "bar",
        })

    # The model is stated with the number, always — it is the single largest
    # source of spread in the answer.
    caveats.append(
        f"Computed with the {spec['label']} ({spec['source']}), which is the "
        f"correlation for {spec['range']}."
        + ("" if requested in (None, "auto")
           else " Model was set explicitly by the caller, not selected by TDS.")
    )
    caveats += [f"Source: {ref}" for ref in spec["references"]]
    caveats += notes

    # Where both models are defensible, the disagreement IS the uncertainty, so
    # the operator gets both numbers rather than a false precision.
    other = None
    if OVERLAP_LO_MG_L <= tds <= OVERLAP_HI_MG_L:
        other_name = "seawater" if used == "brackish_tds" else "brackish_tds"
        other, _, _ = osmotic_pressure_bar(tds, temp_c, other_name)
        caveats.append(
            f"At {tds:g} mg/L both correlations are arguable and they "
            f"disagree: {OSMOTIC_MODELS[other_name]['label']} gives "
            f"{other:.2f} bar against {pressure:.2f} bar here, a "
            f"{abs(other - pressure) / pressure * 100:.0f}% spread. The "
            "brackish approximation runs high by design, which is the safe "
            "direction for sizing and the wrong one for judging performance."
        )

    caveats.append(
        "Osmotic pressure is calculated from TDS alone, so it cannot see ion "
        "composition. A sulphate- or silica-dominant brackish water and a "
        "sodium-chloride one at the same TDS do not have the same osmotic "
        "pressure. Where the number drives a capital decision, get an ion "
        "analysis and a speciation model."
    )

    out = [
        "Inputs:",
        echo_all(tds_q, temp_q),
        "",
        f"Osmotic pressure: {pressure:.2f} bar "
        f"({pressure * PSI_PER_BAR:.1f} psi) at {tds:g} mg/L, {temp_c:g} C",
        f"  model: {spec['label']}",
    ]
    if other is not None:
        out.append(f"  cross-check, other correlation: {other:.2f} bar")
    out += [f"CAVEAT: {c} " for c in caveats]

    return {
        "summary": "\n".join(line.rstrip() for line in out),
        "result": {
            "osmotic_pressure_bar": round(pressure, 3),
            "osmotic_pressure_psi": round(pressure * PSI_PER_BAR, 2),
            "tds_mg_per_l": round(tds, 1),
            "temperature_c": round(temp_c, 2),
            "model": used,
            "model_auto_selected": requested in (None, "auto"),
        },
        "steps": steps,
        "conversions": [f"{tds_q.value:g} {tds_q.unit} = {tds:.4g} mg/L",
                        f"{temp_q.value:g} {temp_q.unit} = {temp_c:.4g} degC"]
        + [f"NOTE: {n}" for n in
           dict.fromkeys(tds_q.notes + temp_q.notes)],
        "caveats": caveats,
    }


if __name__ == "__main__":
    for tds, t in [(800, 15), (2000, 25), (10000, 25), (35000, 25), (70000, 25)]:
        r = calc_osmotic_pressure(
            concentration={"value": tds, "unit": "mg/L"},
            temperature={"value": t, "unit": "degC"},
        )["result"]
        print(f"{tds:>6} mg/L @ {t:>2} C -> {r['osmotic_pressure_bar']:>6.2f} bar "
              f"({r['model']})")

    print()
    print(calc_osmotic_pressure(
        concentration={"value": 35000, "unit": "ppm"},
        temperature={"value": 25, "unit": "degC"},
    )["summary"])
