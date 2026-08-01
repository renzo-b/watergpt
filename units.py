"""
Entroply unit layer.

Every calculator takes quantities as {"value": <number>, "unit": "<string>"}
rather than as bare numbers in an assumed unit. The model reports what the
operator said; this module converts.

Why this exists: system prompt rule 2 says the model never does arithmetic,
but a signature like calc_ct(flow_lps=...) forces it to convert "1 MGD" in its
head before filling the parameter. Unit conversion is arithmetic. Moving it
here turns ungradeable model behaviour into tested code.

pint does the dimensional machinery — passing a volume where a flow belongs
raises instead of silently computing nonsense. This module adds the water
conventions pint does not know, and, importantly, flags the ambiguous cases
rather than resolving them silently.

    from units import Q, parse, echo

    flow = parse({"value": 1.0, "unit": "MGD"}, "flow")
    flow.canonical            # 43.81 (L/s)
    flow.notes                # ['MGD assumed to be US gallons...']
    echo(flow)                # '1.0 MGD = 43.813 L/s'
"""

from dataclasses import dataclass, field
from typing import Optional

import pint

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

UREG = pint.UnitRegistry()

# Water-industry shorthand pint does not ship with.
UREG.define("MGD = 1e6 * US_liquid_gallon / day = mgd")
UREG.define("MIGD = 1e6 * imperial_gallon / day = migd")   # Imperial variant
UREG.define("MLD = megaliter / day = mld = ML/d")
UREG.define("gpm = US_liquid_gallon / minute")
UREG.define("gpd = US_liquid_gallon / day")
UREG.define("igpm = imperial_gallon / minute")
UREG.define("cfs = foot ** 3 / second")
UREG.define("MG = 1e6 * US_liquid_gallon")                 # million gallons
UREG.define("mgL = milligram / liter = mg_per_L")

# ---------------------------------------------------------------------------
# Ambiguity notes
#
# These are the conversions where a number alone is not enough information.
# Rather than guessing silently, the tool states the assumption in its output
# so the operator can catch it. Same principle as the concentration-basis rule.
# ---------------------------------------------------------------------------

AMBIGUITY_NOTES = {
    "MGD": (
        "MGD assumed to be US gallons (3.785 L). If this plant reports in "
        "Imperial gallons (4.546 L), use MIGD — the difference is 20%."
    ),
    "gpm": "gpm assumed to be US gallons per minute. Imperial is igpm.",
    "gpd": "gpd assumed to be US gallons per day.",
    "MG": "MG assumed to be million US gallons.",
    "gal": "gallon assumed to be US (3.785 L). Imperial is 4.546 L.",
    "ppm": (
        "ppm treated as mg/L, which holds for dilute aqueous solutions near "
        "1.0 g/mL. For brines or high-TDS streams the two differ by the "
        "density ratio — state TDS or density if precision matters."
    ),
    "ppb": "ppb treated as ug/L; same dilute-solution assumption as ppm.",
}

# ---------------------------------------------------------------------------
# Dimensions
#
# Each entry: canonical unit that calculators compute in, plus the units a
# tool schema will advertise. The accepted list is a hint for the model, not a
# hard allow-list — anything pint can parse with the right dimensionality is
# accepted, so an unusual but valid unit does not force the model to convert
# by hand (which would violate rule 2).
# ---------------------------------------------------------------------------

DIMENSIONS = {
    "flow": {
        "canonical": "L/s",
        "accepted": ["L/s", "m3/h", "m3/d", "MLD", "MGD", "MIGD", "gpm", "gpd", "cfs"],
    },
    "volume": {
        "canonical": "m3",
        "accepted": ["m3", "L", "ML", "MG", "gal", "ft3"],
    },
    "area": {
        "canonical": "m2",
        "accepted": ["m2", "ft2", "cm2", "ha", "acre"],
    },
    "concentration": {
        "canonical": "mg/L",
        "accepted": ["mg/L", "g/m3", "ppm", "ppb", "ug/L", "g/L", "kg/m3"],
    },
    "mass": {
        "canonical": "kg",
        "accepted": ["kg", "g", "lb", "tonne", "ton"],
    },
    "mass_rate": {
        "canonical": "kg/d",
        "accepted": ["kg/d", "lb/d", "kg/h", "tonne/d"],
    },
    "volume_rate_small": {   # chemical feed pumps
        "canonical": "L/h",
        "accepted": ["L/h", "L/min", "mL/min", "L/d", "gph", "gpm"],
    },
    "pressure": {
        "canonical": "bar",
        "accepted": ["bar", "kPa", "psi", "m_H2O", "ft_H2O"],
    },
    "temperature": {
        "canonical": "degC",
        "accepted": ["degC", "degF", "K"],
    },
    "time": {
        "canonical": "min",
        "accepted": ["s", "min", "h", "d"],
    },
    "length": {
        "canonical": "m",
        "accepted": ["m", "cm", "mm", "ft", "in"],
    },
}

# pint spells some units differently from how operators write them.
ALIASES = {
    "m3": "m**3", "m3/h": "m**3/hour", "m3/d": "m**3/day", "m3/s": "m**3/second",
    "ft3": "foot**3", "kg/m3": "kg/m**3", "g/m3": "g/m**3",
    "m2": "m**2", "ft2": "foot**2", "cm2": "cm**2",
    "sqft": "foot**2", "sq_ft": "foot**2", "sqm": "m**2", "ha": "hectare",
    "L/s": "liter/second", "L/h": "liter/hour", "L/min": "liter/minute",
    "L/d": "liter/day", "mL/min": "milliliter/minute",
    "mg/L": "milligram/liter", "ug/L": "microgram/liter",
    "g/L": "gram/liter", "kg/d": "kg/day", "lb/d": "pound/day",
    "kg/h": "kg/hour", "tonne/d": "tonne/day",
    "ppm": "milligram/liter", "ppb": "microgram/liter",
    "gph": "US_liquid_gallon/hour",
    "m_H2O": "meter_H2O", "ft_H2O": "foot_H2O",
    "C": "degC", "F": "degF", "degreeC": "degC", "degreeF": "degF",
    "ML": "megaliter", "L": "liter", "gal": "US_liquid_gallon",
    "d": "day", "h": "hour", "min": "minute", "s": "second",
}


@dataclass
class Q:
    """A quantity that knows what the operator said and what the tool needs."""
    value: float
    unit: str                       # as the operator stated it
    canonical: float                # value in DIMENSIONS[dim]['canonical']
    canonical_unit: str
    dimension: str
    notes: list = field(default_factory=list)

    def to(self, unit: str) -> float:
        """Convert to any compatible unit."""
        target = ALIASES.get(unit, unit)
        if self.dimension == "temperature":
            return UREG.Quantity(
                self.value, ALIASES.get(self.unit, self.unit)
            ).to(target).magnitude
        src = ALIASES.get(self.unit, self.unit)
        return (self.value * UREG(src)).to(target).magnitude


class UnitError(ValueError):
    """Raised with a message written for the model to act on, not a stack trace."""


def _pint_unit(unit: str):
    spelled = ALIASES.get(unit, unit)
    try:
        return UREG(spelled)
    except Exception:
        raise UnitError(
            f"Unrecognized unit {unit!r}. Ask the operator to restate the "
            f"value, or use one of: "
            f"{', '.join(sorted({u for d in DIMENSIONS.values() for u in d['accepted']}))}"
        )


def parse(q, dimension: str) -> Q:
    """Validate a {'value', 'unit'} object and convert to canonical units.

    Raises UnitError with an actionable message on anything wrong — the agent
    loop hands that back to the model, which can fix its arguments or ask the
    operator.
    """
    if dimension not in DIMENSIONS:
        raise UnitError(f"Unknown dimension {dimension!r}")
    spec = DIMENSIONS[dimension]

    if not isinstance(q, dict) or "value" not in q or "unit" not in q:
        raise UnitError(
            f"Expected an object like {{'value': 45, 'unit': 'L/s'}} for this "
            f"{dimension} parameter, got {q!r}. Do not convert units yourself — "
            f"pass the value exactly as the operator stated it."
        )

    value, unit = q["value"], str(q["unit"]).strip()
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise UnitError(f"Value {q['value']!r} is not a number.")

    src = _pint_unit(unit)
    dst = _pint_unit(spec["canonical"])

    # Dimensional check — this is what makes a swapped argument raise instead
    # of silently producing a plausible wrong number.
    if src.dimensionality != dst.dimensionality:
        raise UnitError(
            f"{unit!r} is not a {dimension} unit (it has dimensions "
            f"{src.dimensionality}, expected {dst.dimensionality}). "
            f"Accepted {dimension} units: {', '.join(spec['accepted'])}."
        )

    # Temperature is affine, so pint needs an explicit Quantity, not a scale.
    if dimension == "temperature":
        canonical = UREG.Quantity(value, ALIASES.get(unit, unit)).to(
            spec["canonical"]
        ).magnitude
    else:
        canonical = (value * src).to(ALIASES.get(spec["canonical"], spec["canonical"])).magnitude

    notes = [AMBIGUITY_NOTES[k] for k in (unit, unit.split("/")[0]) if k in AMBIGUITY_NOTES]

    return Q(
        value=value, unit=unit, canonical=canonical,
        canonical_unit=spec["canonical"], dimension=dimension,
        notes=list(dict.fromkeys(notes)),
    )


def echo(q: Q) -> str:
    """One line showing the conversion, for inclusion in every tool result.

    Makes the conversion visible to the operator, auditable to you, and
    assertable in an eval via must_include.
    """
    if abs(q.canonical - q.value) < 1e-9 and q.unit == q.canonical_unit:
        return f"{q.value:g} {q.unit}"
    return f"{q.value:g} {q.unit} = {q.canonical:.4g} {q.canonical_unit}"


def echo_all(*qs: Q) -> str:
    """Conversion block plus any ambiguity notes, for the top of a result."""
    lines = [f"  {echo(q)}" for q in qs]
    notes = list(dict.fromkeys(n for q in qs for n in q.notes))
    if notes:
        lines += [f"  NOTE: {n}" for n in notes]
    return "\n".join(lines)


def quantity_schema(dimension: str, description: str) -> dict:
    """JSON schema fragment for a quantity parameter.

    The unit is a free string rather than an enum on purpose: an enum that
    omits the operator's unit would force the model to convert by hand, which
    is the exact behaviour this module exists to prevent. The accepted list
    goes in the description as a hint, and _pint_unit's error message handles
    anything unusual.
    """
    spec = DIMENSIONS[dimension]
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": {"type": "number", "description": "Numeric value only."},
            "unit": {
                "type": "string",
                "description": (
                    f"Unit exactly as the operator stated it. Do NOT convert. "
                    f"Common {dimension} units: {', '.join(spec['accepted'])}."
                ),
            },
        },
        "required": ["value", "unit"],
    }


# ---------------------------------------------------------------------------
# Tests — python units.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    def close(a, b, tol=1e-3):
        assert abs(a - b) / max(abs(b), 1e-12) < tol, f"{a} != {b}"

    # Known values
    close(parse({"value": 1.0, "unit": "MGD"}, "flow").canonical, 43.8126)
    close(parse({"value": 1.0, "unit": "MIGD"}, "flow").canonical, 52.6169)
    close(parse({"value": 45, "unit": "L/s"}, "flow").canonical, 45.0)
    close(parse({"value": 100, "unit": "gpm"}, "flow").canonical, 6.309)
    close(parse({"value": 1, "unit": "MLD"}, "flow").canonical, 11.574)
    close(parse({"value": 150, "unit": "m3"}, "volume").canonical, 150.0)
    close(parse({"value": 1, "unit": "MG"}, "volume").canonical, 3785.41)
    close(parse({"value": 1, "unit": "m2"}, "area").canonical, 1.0)
    close(parse({"value": 1, "unit": "ft2"}, "area").canonical, 0.092903)
    close(parse({"value": 1, "unit": "sqft"}, "area").canonical, 0.092903)
    close(parse({"value": 1, "unit": "ha"}, "area").canonical, 10000.0)
    close(parse({"value": 1, "unit": "acre"}, "area").canonical, 4046.86)
    close(parse({"value": 1, "unit": "g/m3"}, "concentration").canonical, 1.0)
    close(parse({"value": 1, "unit": "g/L"}, "concentration").canonical, 1000.0)
    close(parse({"value": 1000, "unit": "lb/d"}, "mass_rate").canonical, 453.59)
    close(parse({"value": 14.5038, "unit": "psi"}, "pressure").canonical, 1.0)
    print("known-value tests passed")

    # Temperature is affine — a scale factor gets this wrong
    close(parse({"value": 32, "unit": "degF"}, "temperature").canonical, 0.0, tol=1e9)
    assert abs(parse({"value": 32, "unit": "degF"}, "temperature").canonical) < 1e-9
    close(parse({"value": 212, "unit": "degF"}, "temperature").canonical, 100.0)
    close(parse({"value": 5, "unit": "degC"}, "temperature").canonical, 5.0)
    print("affine temperature tests passed")

    # Round trip
    for unit in ("MGD", "gpm", "m3/h", "MLD", "cfs"):
        q = parse({"value": 7.0, "unit": unit}, "flow")
        close(q.to(unit), 7.0)
    print("round-trip tests passed")

    # Dimensional mismatch must raise, not compute
    for bad, dim in [({"value": 1, "unit": "m3"}, "flow"),
                     ({"value": 1, "unit": "L/s"}, "volume"),
                     ({"value": 1, "unit": "m3"}, "area"),
                     ({"value": 1, "unit": "m"}, "area"),
                     ({"value": 1, "unit": "kg"}, "concentration")]:
        try:
            parse(bad, dim)
            raise AssertionError(f"expected UnitError for {bad} as {dim}")
        except UnitError:
            pass
    print("dimension-mismatch tests passed")

    # Bare numbers and junk must raise with an actionable message
    for bad in (45, "45 L/s", {"value": 45}, {"value": "abc", "unit": "L/s"}):
        try:
            parse(bad, "flow")
            raise AssertionError(f"expected UnitError for {bad!r}")
        except UnitError:
            pass
    print("malformed-input tests passed")

    # Ambiguity notes fire where they should
    assert parse({"value": 1, "unit": "MGD"}, "flow").notes, "MGD should warn"
    assert parse({"value": 1, "unit": "ppm"}, "concentration").notes, "ppm should warn"
    assert not parse({"value": 1, "unit": "L/s"}, "flow").notes, "L/s is unambiguous"
    print("ambiguity-note tests passed")

    print()
    print(echo_all(
        parse({"value": 1.0, "unit": "MGD"}, "flow"),
        parse({"value": 150, "unit": "m3"}, "volume"),
        parse({"value": 41, "unit": "degF"}, "temperature"),
        parse({"value": 0.8, "unit": "ppm"}, "concentration"),
    ))
