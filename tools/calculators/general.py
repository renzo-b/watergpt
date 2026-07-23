# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------

# CT required (mg·min/L) for 0.5-log Giardia inactivation by free chlorine,
# pH 6.0-7.5, residual <= 1.0 mg/L.
# PLACEHOLDER SUBSET — replace with the full table from the MECP Procedure for
# Disinfection of Drinking Water in Ontario before this is used for anything
# real. Interpolation below is linear in temperature, which is conservative-ish
# but not what the regulation specifies.
CT_GIARDIA_0_5_LOG = {0.5: 27.0, 5.0: 19.0, 10.0: 14.0, 15.0: 9.0, 20.0: 7.0}


def _interp_ct(temp_c: float) -> float:
    temps = sorted(CT_GIARDIA_0_5_LOG)
    if temp_c <= temps[0]:
        return CT_GIARDIA_0_5_LOG[temps[0]]
    if temp_c >= temps[-1]:
        return CT_GIARDIA_0_5_LOG[temps[-1]]
    for lo, hi in zip(temps, temps[1:]):
        if lo <= temp_c <= hi:
            f = (temp_c - lo) / (hi - lo)
            return CT_GIARDIA_0_5_LOG[lo] + f * (
                CT_GIARDIA_0_5_LOG[hi] - CT_GIARDIA_0_5_LOG[lo]
            )
    raise ValueError("temperature out of range")


def calc_ct(volume_m3, flow_lps, residual_mgl, temp_c, ph, baffling_factor):
    """CT achieved vs CT required for 0.5-log Giardia inactivation."""
    if flow_lps <= 0:
        raise ValueError("flow must be positive")
    if not 0 < baffling_factor <= 1:
        raise ValueError("baffling factor must be between 0 and 1")

    flow_m3_min = flow_lps * 60 / 1000
    theoretical_min = volume_m3 / flow_m3_min
    t10_min = theoretical_min * baffling_factor
    ct_actual = residual_mgl * t10_min
    ct_required = _interp_ct(temp_c)
    ratio = ct_actual / ct_required

    caveat = ""
    if ph > 7.5:
        caveat = (
            " NOTE: pH is above 7.5, which requires a different CT table than "
            "the one used here — this result understates the requirement."
        )
    if residual_mgl > 1.0:
        caveat += " NOTE: residual exceeds 1.0 mg/L, outside this table's range."

    return (
        f"Theoretical detention time: {theoretical_min:.1f} min\n"
        f"T10 (x baffling factor {baffling_factor}): {t10_min:.1f} min\n"
        f"CT achieved: {ct_actual:.1f} mg·min/L\n"
        f"CT required (0.5-log Giardia, {temp_c} C, pH {ph}): "
        f"{ct_required:.1f} mg·min/L\n"
        f"CT ratio: {ratio:.2f} — {'PASS' if ratio >= 1.0 else 'FAIL'}"
        f"{caveat}"
    )


def calc_chemical_feed(
    flow_mld, dose_mgl, solution_strength_pct=100.0, solution_sg=1.0
):
    """Chemical feed rate from flow and target dose."""
    if not 0 < solution_strength_pct <= 100:
        raise ValueError("solution strength must be between 0 and 100 percent")
    kg_per_day = flow_mld * dose_mgl  # ML/d * mg/L == kg/d
    neat_lpd = kg_per_day / (solution_sg * 1000) * 1000
    solution_lpd = neat_lpd / (solution_strength_pct / 100)
    return (
        f"Neat chemical required: {kg_per_day:.2f} kg/day\n"
        f"Solution feed rate ({solution_strength_pct}% w/w, SG {solution_sg}): "
        f"{solution_lpd:.1f} L/day ({solution_lpd / 1440:.2f} L/min)"
    )
