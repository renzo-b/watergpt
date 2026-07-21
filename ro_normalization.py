"""
RO normalization calculator — drop into tools.py.

Normalizes reverse osmosis performance to reference conditions (25 C, baseline
net driving pressure) following the ASTM D4516 approach, then compares current
performance to a baseline against standard cleaning triggers.

Why this is a tool and not prompt text: operators routinely compare RAW
permeate flow readings and reach wrong conclusions, because a 10 C temperature
swing alone moves flow by roughly 25%. The correction is deterministic and
belongs in code.

KNOWN LIMITATIONS — keep these visible:
  1. dP normalization uses permeate flow as a proxy for feed flow (exponent
     1.5). Proper practice corrects on feed flow. Add feed_flow_m3h to the
     state dict when available; at constant recovery the proxy is a scale
     factor, but it drifts when recovery changes.
  2. Osmotic pressure uses the standard TDS approximation — adequate for
     brackish municipal feeds, poor for high-sulphate or seawater.
  3. The 10-15% / 15% triggers are industry rules of thumb. A plant's O&M
     manual or the membrane manufacturer's criteria override them.
"""

import math


def _tcf(temp_c: float) -> float:
    """Temperature correction factor to 25 C for polyamide membranes.

    Returns the multiplier that converts measured flow at temp_c to the
    equivalent flow at 25 C. Cold water permeates less, so TCF > 1 below 25 C.
    """
    coeff = 2640.0 if temp_c >= 25.0 else 3020.0
    return math.exp(coeff * (1.0 / (273.15 + temp_c) - 1.0 / 298.15))


def _osmotic_bar(tds_mgl: float, temp_c: float) -> float:
    """Osmotic pressure from TDS. Standard approximation; brackish-water range."""
    psi = 0.0385 * tds_mgl * (temp_c + 320.0) / (1000.0 - tds_mgl / 1000.0)
    return psi / 14.5038


def _state(s: dict, label: str) -> dict:
    """Derive dP, NDP, TCF and salt passage for one operating state."""
    recovery = s["recovery_pct"] / 100.0
    if not 0.0 < recovery < 1.0:
        raise ValueError(f"{label}: recovery must be between 0 and 100 percent")

    dp = s["feed_pressure_bar"] - s["concentrate_pressure_bar"]
    if dp < 0:
        raise ValueError(
            f"{label}: concentrate pressure exceeds feed pressure — check the readings"
        )

    pf_avg = (s["feed_pressure_bar"] + s["concentrate_pressure_bar"]) / 2.0

    # Log-mean concentration factor across the element train.
    conc_factor = math.log(1.0 / (1.0 - recovery)) / recovery
    c_avg = s["feed_tds_mgl"] * conc_factor

    ndp = (
        pf_avg
        - s["permeate_pressure_bar"]
        - (_osmotic_bar(c_avg, s["temp_c"]) - _osmotic_bar(s["permeate_tds_mgl"], s["temp_c"]))
    )
    if ndp <= 0:
        raise ValueError(
            f"{label}: net driving pressure is zero or negative — feed pressure "
            f"cannot overcome osmotic pressure at these readings. Verify feed "
            f"TDS, pressures, and recovery."
        )

    return {
        "dp": dp,
        "ndp": ndp,
        "tcf": _tcf(s["temp_c"]),
        "c_avg": c_avg,
        "sp": s["permeate_tds_mgl"] / c_avg * 100.0,
        "q": s["permeate_flow_m3h"],
    }


def calc_ro_normalization(current: dict, baseline: dict) -> str:
    """Compare normalized RO performance against baseline cleaning triggers.

    baseline is normally the readings taken immediately after the last
    successful cleaning, or at commissioning. Both dicts need all eight keys.
    """
    cur = _state(current, "current")
    base = _state(baseline, "baseline")

    # Normalize both states to 25 C and the baseline net driving pressure.
    qn_base = base["q"] * base["tcf"]
    qn_cur = cur["q"] * cur["tcf"] * (base["ndp"] / cur["ndp"])
    d_flow = (qn_cur - qn_base) / qn_base * 100.0

    # dP corrected for flow change; see limitation 1 above.
    dpn_cur = cur["dp"] * (
        baseline["permeate_flow_m3h"] / current["permeate_flow_m3h"]
    ) ** 1.5
    d_dp = (dpn_cur - base["dp"]) / base["dp"] * 100.0

    d_sp = (cur["sp"] - base["sp"]) / base["sp"] * 100.0

    flags = []
    if d_flow <= -15:
        flags.append(f"Normalized permeate flow down {abs(d_flow):.0f}% — EXCEEDS the 10-15% cleaning trigger.")
    elif d_flow <= -10:
        flags.append(f"Normalized permeate flow down {abs(d_flow):.0f}% — at the 10-15% cleaning trigger.")
    if d_dp >= 15:
        flags.append(f"Normalized dP up {d_dp:.0f}% — EXCEEDS the 15% cleaning trigger.")
    if d_sp >= 15:
        flags.append(f"Normalized salt passage up {d_sp:.0f}% — EXCEEDS the 10-15% trigger.")
    elif d_sp >= 10:
        flags.append(f"Normalized salt passage up {d_sp:.0f}% — at the 10-15% trigger.")

    # Pattern interpretation drives cleaning chemistry choice.
    if d_dp >= 15 and d_flow > -10:
        flags.append("dP-dominant pattern: suggests particulate or biological fouling rather than scaling.")
    if d_sp >= 15 and d_dp < 15:
        flags.append("Salt-passage-dominant pattern: suggests scaling or membrane damage rather than fouling.")

    body = (
        f"Normalized to 25 C and baseline NDP ({base['ndp']:.1f} bar).\n"
        f"  Permeate flow:  {qn_base:.1f} -> {qn_cur:.1f} m3/h  ({d_flow:+.1f}%)\n"
        f"  Differential P: {base['dp']:.2f} -> {dpn_cur:.2f} bar  ({d_dp:+.1f}%)\n"
        f"  Salt passage:   {base['sp']:.2f} -> {cur['sp']:.2f} %  ({d_sp:+.1f}%)\n"
        f"  TCF current {cur['tcf']:.3f}, baseline {base['tcf']:.3f}; "
        f"NDP current {cur['ndp']:.1f} bar\n"
    )
    verdict = (
        "\n".join("  ! " + f for f in flags)
        if flags
        else "  No cleaning trigger exceeded. Continue trending."
    )
    return (
        body
        + verdict
        + "\n  Defer to the membrane manufacturer's dP limit and this plant's cleaning SOP."
    )


# ---------------------------------------------------------------------------
# Schema — nested state objects force a complete baseline. Without this, the
# model will happily normalize against invented reference conditions.
# ---------------------------------------------------------------------------

_RO_STATE = {
    "type": "object",
    "properties": {
        "permeate_flow_m3h": {"type": "number"},
        "feed_pressure_bar": {"type": "number"},
        "concentrate_pressure_bar": {"type": "number"},
        "permeate_pressure_bar": {
            "type": "number",
            "description": "Often near 0. Ask the operator rather than assuming.",
        },
        "feed_tds_mgl": {
            "type": "number",
            "description": "Feed TDS, or conductivity converted to TDS.",
        },
        "permeate_tds_mgl": {"type": "number"},
        "temp_c": {"type": "number"},
        "recovery_pct": {
            "type": "number",
            "description": "Permeate flow divided by feed flow, as a percentage.",
        },
    },
    "required": [
        "permeate_flow_m3h", "feed_pressure_bar", "concentrate_pressure_bar",
        "permeate_pressure_bar", "feed_tds_mgl", "permeate_tds_mgl",
        "temp_c", "recovery_pct",
    ],
}

RO_TOOL_SCHEMA = {
    "name": "calc_ro_normalization",
    "description": (
        "Normalize RO performance to reference conditions (25 C, baseline net "
        "driving pressure) per the ASTM D4516 approach, and compare current "
        "performance against a baseline to determine whether cleaning triggers "
        "are met. Use for ANY question about whether RO membranes need "
        "cleaning, or whether performance has declined. "
        "Requires a COMPLETE baseline set — normally the readings taken "
        "immediately after the last successful cleaning, or at commissioning. "
        "If the operator has not provided baseline values, ASK for them; do not "
        "assume, estimate, or substitute design values. Raw uncorrected readings "
        "cannot answer this question, because temperature and pressure changes "
        "alone shift permeate flow substantially."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"current": _RO_STATE, "baseline": _RO_STATE},
        "required": ["current", "baseline"],
    },
}


# ---------------------------------------------------------------------------
# Tests — run: python ro_normalization.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    baseline = dict(
        permeate_flow_m3h=44, feed_pressure_bar=12.0, concentrate_pressure_bar=10.95,
        permeate_pressure_bar=0.3, feed_tds_mgl=800, permeate_tds_mgl=25,
        temp_c=15, recovery_pct=75,
    )
    current = dict(
        permeate_flow_m3h=37, feed_pressure_bar=13.5, concentrate_pressure_bar=12.1,
        permeate_pressure_bar=0.3, feed_tds_mgl=820, permeate_tds_mgl=31,
        temp_c=12, recovery_pct=75,
    )

    # Identity: a state compared to itself must show zero change. This is the
    # single most important test — it catches sign and direction errors.
    identity = calc_ro_normalization(baseline, baseline)
    assert "+0.0%" in identity, identity
    print("identity test passed\n")

    print(calc_ro_normalization(current, baseline))

    # Error paths should raise, not return a confident number.
    for bad, why in [
        ({**current, "recovery_pct": 0}, "zero recovery"),
        ({**current, "concentrate_pressure_bar": 20.0}, "concentrate above feed"),
        ({**current, "feed_pressure_bar": 1.0, "concentrate_pressure_bar": 0.9}, "NDP non-positive"),
    ]:
        try:
            calc_ro_normalization(bad, baseline)
            raise AssertionError(f"expected a raise for: {why}")
        except ValueError:
            pass
    print("\nerror-path tests passed")
