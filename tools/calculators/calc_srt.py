# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Solids retention time for an ACTIVATED SLUDGE system. Also answers to mean
# cell residence time (MCRT) and sludge age — the same quantity under three
# names, and this is the only tool that computes it.
#
#     SRT = solids inventory in the system / solids leaving the system per day
#
# Scope is activated sludge only. An aerobic digester has a different system
# boundary and a different denominator — solids destruction rather than
# physical removal — so a shared tool would let digester inputs be read with
# activated-sludge semantics and return a number that looks right. A digester
# SRT gets its own tool.
#
# THERE IS NO SHORT-CUT FORM HERE, deliberately. A calc_mean_cell_residence_time
# tool used to offer V / Q_waste for when no solids data was available. That
# identity holds only when wasting is from the aeration basin (so the waste
# concentration is MLSS and cancels) AND effluent solids are negligible. When
# either is false it overstates sludge age, and the RAS-line case overstates it
# by the thickening factor — the same 2-4x error trap 2 below exists to catch,
# arriving through a door marked "convenience". So the inventory needs MLSS,
# and if MLSS is unknown the answer is to ask for it, not to approximate. The
# unknown-count check below says so in as many words.
#
# WHY THIS IS A PINNED TOOL RATHER THAN GENERIC ALGEBRA
#
# The arithmetic is two multiplications and a divide. What is hard is knowing
# which terms belong in it, and the four ways operators get that wrong are all
# invisible in the answer — each returns a plausible number:
#
#   1. Aerobic vs total basis. Counting the clarifier blanket or not changes
#      the inventory, and it is not a rounding difference. `basis` is a
#      required enum with no default; this tool will not infer it.
#
#   2. RAS concentration vs MLSS in the waste term. Wasting off the RAS line
#      but entering MLSS as the waste concentration inflates SRT by roughly the
#      thickening factor — a 3x error that reads as a healthy sludge age.
#      `waste_location` is required, and the two are cross-checked.
#
#   3. Dropping effluent solids. Optional-and-omitted is how that happens, so
#      effluent solids are required here. An explicit zero is allowed and says
#      in the working what it costs. The error is largest exactly where it
#      matters most: short SRT, poor settling, solids washout.
#
#   4. Mixing TSS and VSS across the terms. `solids_basis` is required and
#      applies to every solids input. Nothing is converted between the two —
#      there is no universal volatile fraction.
#
# NO INTERPRETATION. This tool returns arithmetic and refuses to judge it. The
# minimum aerobic SRT for nitrification is temperature-dependent and carries a
# safety factor; declaring a number adequate without mixed liquor temperature
# is the kind of confident wrong answer the whole repo exists to prevent. If
# interpretive text is added later it takes temperature as an input.
#
# Follows the quantity, step and caveat patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

BASES = ("aerobic", "total")
WASTE_LOCATIONS = ("ras_line", "aeration_basin")
SOLIDS_BASES = ("TSS", "VSS")

# A RAS/underflow stream is thickened 2-4x above mixed liquor. If a caller says
# they waste off the RAS line but hands over a concentration this close to
# MLSS, one of the two is wrong — almost always the concentration.
RAS_MLSS_SIMILARITY = 0.20

# Column the working block aligns its values to. Matches the shape in the spec.
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
    name="calc_srt",
    description=(
        "Calculate solids retention time (SRT) for an ACTIVATED SLUDGE system "
        "by solids mass balance, or solve backwards for the waste rate or the "
        "MLSS needed to hit a target SRT. This is the ONLY tool for SRT, mean "
        "cell residence time (MCRT) and sludge age — they are three names for "
        "the same quantity, so use it for all of them. Not for aerobic "
        "digesters: different system boundary, different denominator. "
        "basis, waste_location and solids_basis are required and have no "
        "defaults: if the operator has not said whether they mean aerobic or "
        "total SRT, or where they waste from, ASK rather than calling this "
        "with a guess. Effluent solids are required too — pass an explicit "
        "zero only if the operator really means to exclude them. "
        "Leave exactly one of target_srt, waste_flow or mlss unspecified: omit "
        "target_srt to calculate SRT, or give target_srt and omit waste_flow "
        "or mlss to solve for it. "
        "If MLSS is unknown, ask the operator for it. Do not fall back on "
        "reactor volume divided by waste flow: that short-cut assumes wasting "
        "from the aeration basin with negligible effluent solids, and "
        "overstates sludge age by the thickening factor when wasting is "
        "actually off the RAS line. Never compute any of this yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "basis": {
                "type": "string",
                "enum": list(BASES),
                "description": (
                    "REQUIRED, no default. 'aerobic' counts only the aeration "
                    "basin inventory. 'total' adds the clarifier blanket "
                    "inventory. The two answers differ materially. If the "
                    "operator did not say which, ask — do not infer it."
                ),
            },
            "waste_location": {
                "type": "string",
                "enum": list(WASTE_LOCATIONS),
                "description": (
                    "REQUIRED, no default. Where the waste sludge is drawn "
                    "from. 'ras_line' means WAS off the RAS/underflow, so the "
                    "waste concentration is the RAS solids — supply "
                    "waste_solids. 'aeration_basin' means hydraulic wasting of "
                    "mixed liquor, so the waste concentration IS the MLSS and "
                    "waste_solids should be omitted."
                ),
            },
            "solids_basis": {
                "type": "string",
                "enum": list(SOLIDS_BASES),
                "description": (
                    "REQUIRED, no default. Whether every solids figure below "
                    "is total (TSS) or volatile (VSS) suspended solids. All of "
                    "them must be on the same basis. Nothing is converted "
                    "between TSS and VSS — there is no universal volatile "
                    "fraction. Ask which the operator's numbers are."
                ),
            },
            "aeration_volume": quantity_schema(
                "volume", "Liquid volume of the aeration basins in service."
            ),
            "mlss": quantity_schema(
                "concentration",
                "Mixed liquor suspended solids in the aeration basin, on the "
                "stated solids_basis. Omit this (with target_srt given) to "
                "solve for the MLSS a target SRT requires.",
            ),
            "waste_flow": quantity_schema(
                "flow",
                "Waste sludge flow (Q_WAS). Omit this (with target_srt given) "
                "to solve for the waste rate a target SRT requires.",
            ),
            "waste_solids": quantity_schema(
                "concentration",
                "Solids concentration of the waste stream, on the stated "
                "solids_basis. Required when waste_location is 'ras_line' — "
                "this is the RAS/underflow concentration, typically 2-4x MLSS. "
                "Omit when waste_location is 'aeration_basin', where it equals "
                "MLSS by definition.",
            ),
            "influent_flow": quantity_schema(
                "flow",
                "Plant influent flow. Effluent flow is taken as influent flow "
                "minus waste flow.",
            ),
            "effluent_solids": quantity_schema(
                "concentration",
                "REQUIRED. Final effluent suspended solids, on the stated "
                "solids_basis. Pass an explicit zero only to deliberately "
                "exclude effluent losses — the working will say that this "
                "overstates SRT.",
            ),
            "target_srt": quantity_schema(
                "time",
                "Target SRT, for a reverse solve. Give this and omit either "
                "waste_flow or mlss to solve for that value. Omit it to "
                "calculate SRT from the inputs.",
            ),
            "blanket_depth": quantity_schema(
                "length",
                "Sludge blanket depth in the secondary clarifiers. Required "
                "when basis is 'total'.",
            ),
            "clarifier_surface_area": quantity_schema(
                "area",
                "Surface area of ONE secondary clarifier. Required when basis "
                "is 'total'.",
            ),
            "blanket_solids": quantity_schema(
                "concentration",
                "Solids concentration within the clarifier blanket, on the "
                "stated solids_basis. Required when basis is 'total'.",
            ),
            "clarifiers_in_service": {
                "type": "integer",
                "description": (
                    "Number of secondary clarifiers in service, used to sum "
                    "the blanket inventory. Required when basis is 'total'."
                ),
            },
        },
        "required": [
            "basis", "waste_location", "solids_basis",
            "aeration_volume", "influent_flow", "effluent_solids",
        ],
    },
)
def calc_srt(
    basis=None, waste_location=None, solids_basis=None,
    aeration_volume=None, mlss=None, waste_flow=None, waste_solids=None,
    influent_flow=None, effluent_solids=None, target_srt=None,
    blanket_depth=None, clarifier_surface_area=None, blanket_solids=None,
    clarifiers_in_service=None,
):
    """Activated-sludge SRT by solids mass balance, in either direction.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.

    Exactly one of SRT, waste_flow or mlss is the unknown. Omitting target_srt
    makes SRT the unknown (the forward case); giving target_srt and omitting
    waste_flow or mlss solves for that one instead.
    """
    # 1. Enums first. These are the inputs that decide what the number MEANS,
    #    so a missing one is an error before any quantity is even parsed.
    basis = _enum(
        basis, BASES, "basis",
        "Aerobic SRT counts the aeration basin only; total SRT adds the "
        "clarifier blanket inventory.",
    )
    waste_location = _enum(
        waste_location, WASTE_LOCATIONS, "waste_location",
        "It sets which concentration the waste term uses — RAS solids, or "
        "MLSS.",
    )
    solids_basis = _enum(
        solids_basis, SOLIDS_BASES, "solids_basis",
        "Every solids figure must be on one basis and nothing is converted "
        "between them.",
    )

    # 2. Parse. Dimension errors surface here, before any math.
    if aeration_volume is None:
        raise ValueError("aeration_volume is required.")
    if influent_flow is None:
        raise ValueError("influent_flow is required.")
    if effluent_solids is None:
        raise ValueError(
            "effluent_solids is required — it is not optional in this tool. "
            "Omitting effluent losses overstates SRT, and the error is largest "
            "at short SRT and during poor settling. If the operator genuinely "
            "wants them excluded, pass an explicit zero."
        )

    v = parse(aeration_volume, "volume")
    q_inf = parse(influent_flow, "flow")
    c_eff = parse(effluent_solids, "concentration")
    c_mlss = parse(mlss, "concentration") if mlss is not None else None
    q_was = parse(waste_flow, "flow") if waste_flow is not None else None
    c_was_in = parse(waste_solids, "concentration") if waste_solids is not None else None
    t_target = parse(target_srt, "time") if target_srt is not None else None
    d_blanket = parse(blanket_depth, "length") if blanket_depth is not None else None
    a_clar = parse(clarifier_surface_area, "area") if clarifier_surface_area is not None else None
    c_blanket = parse(blanket_solids, "concentration") if blanket_solids is not None else None

    if v.canonical <= 0:
        raise ValueError("Aeration volume must be positive.")
    if q_inf.canonical <= 0:
        raise ValueError("Influent flow must be positive.")
    if c_eff.canonical < 0:
        raise ValueError("Effluent solids cannot be negative.")

    # 3. Exactly one unknown. Zero means the inputs are over-determined and the
    #    caller has not said what they want solved; more than one is unsolvable.
    unknowns = []
    if t_target is None:
        unknowns.append("srt")
    if q_was is None:
        unknowns.append("waste_flow")
    if c_mlss is None:
        unknowns.append("mlss")

    if len(unknowns) == 0:
        raise ValueError(
            "Nothing was left unknown. target_srt, waste_flow and mlss were "
            "all supplied, so there is nothing to solve for. Omit target_srt "
            "to calculate SRT from the plant's data, or omit waste_flow or "
            "mlss to solve for the value that hits the target SRT."
        )
    if unknowns == ["srt", "mlss"]:
        # The old short-cut request, arriving without its tool. Name what is
        # missing and why no approximation is offered in its place, rather than
        # reporting an unknown count the operator cannot act on.
        raise ValueError(
            "MLSS was not supplied, so the solids inventory cannot be formed. "
            "Ask the operator for the mixed liquor suspended solids. "
            "There is no short-cut in this tool: reactor volume divided by "
            "waste flow is only equal to SRT when wasting is from the aeration "
            "basin, so that MLSS cancels, AND effluent solids are negligible. "
            "Off the RAS line it overstates sludge age by the thickening "
            "factor, typically 2-4x, and the result looks entirely reasonable. "
            "If the intent was instead to solve for the MLSS that hits a "
            "target SRT, supply target_srt."
        )
    if len(unknowns) > 1:
        raise ValueError(
            f"{len(unknowns)} unknowns: {', '.join(unknowns)}. Exactly one of "
            f"SRT, waste_flow or mlss may be unknown. "
            + (
                "Supply target_srt as well, if the intent is a reverse solve."
                if "srt" in unknowns
                else "Supply the values that are known."
            )
        )
    solve_for = unknowns[0]

    if t_target is not None and t_target.to("d") <= 0:
        raise ValueError("Target SRT must be positive.")
    if q_was is not None and q_was.canonical <= 0:
        raise ValueError(
            "Waste flow must be positive — it is the denominator of SRT. A "
            "plant wasting nothing has no finite SRT."
        )
    if c_mlss is not None and c_mlss.canonical <= 0:
        raise ValueError("MLSS must be positive.")

    # Waste concentration: supplied for the RAS line, equal to MLSS in the
    # basin. This is the definition of the enum branch, not a hidden default.
    if waste_location == "ras_line" and c_was_in is None:
        raise ValueError(
            "waste_location is 'ras_line', so waste_solids is required — it is "
            "the RAS/underflow concentration, typically 2-4x MLSS. Ask the "
            "operator for the WAS or RAS solids figure. Do not substitute "
            "MLSS: that inflates SRT by the thickening factor."
        )
    if waste_location == "aeration_basin" and c_was_in is not None:
        if c_mlss is None:
            raise ValueError(
                "waste_location is 'aeration_basin', where the waste "
                "concentration equals MLSS by definition. Omit waste_solids."
            )
        drift = abs(c_was_in.canonical - c_mlss.canonical) / c_mlss.canonical
        if drift > RAS_MLSS_SIMILARITY:
            raise ValueError(
                f"waste_location is 'aeration_basin', where the waste "
                f"concentration equals MLSS, but waste_solids "
                f"({c_was_in.canonical:g} mg/L) differs from MLSS "
                f"({c_mlss.canonical:g} mg/L) by "
                f"{drift * 100:.0f}%. Either the wasting is actually off the "
                f"RAS line, or one of the two figures is wrong. Ask which."
            )

    if basis == "total":
        missing = [
            n for n, x in (
                ("blanket_depth", d_blanket),
                ("clarifier_surface_area", a_clar),
                ("blanket_solids", c_blanket),
                ("clarifiers_in_service", clarifiers_in_service),
            ) if x is None
        ]
        if missing:
            raise ValueError(
                f"basis is 'total', which adds the clarifier blanket "
                f"inventory, so these are required: {', '.join(missing)}. "
                f"Ask the operator, or use basis 'aerobic' if they mean "
                f"aeration basin solids only."
            )
        if not isinstance(clarifiers_in_service, int) or isinstance(clarifiers_in_service, bool):
            raise ValueError("clarifiers_in_service must be a whole number.")
        if clarifiers_in_service < 1:
            raise ValueError("clarifiers_in_service must be at least 1.")
        if d_blanket.canonical <= 0 or a_clar.canonical <= 0:
            raise ValueError(
                "Blanket depth and clarifier surface area must be positive."
            )
        if c_blanket.canonical <= 0:
            raise ValueError("Blanket solids concentration must be positive.")
    else:
        stray = [
            n for n, x in (
                ("blanket_depth", d_blanket),
                ("clarifier_surface_area", a_clar),
                ("blanket_solids", c_blanket),
                ("clarifiers_in_service", clarifiers_in_service),
            ) if x is not None
        ]
        if stray:
            raise ValueError(
                f"basis is 'aerobic', which excludes the clarifier blanket, "
                f"but blanket inputs were supplied: {', '.join(stray)}. Use "
                f"basis 'total' to count them, or drop them. Supplying them "
                f"under an aerobic basis means one of the two is a mistake."
            )

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []
    caveats = []
    work = []

    # 4. Working units: m3/d for flows, mg/L for solids, kg and kg/d for mass.
    inf_m3_d = q_inf.canonical * 86.4                  # L/s -> m3/d
    steps.append({
        "label": "Influent flow in working units",
        "formula": "Q_influent x 86.4",
        "substituted": f"{q_inf.canonical:g} L/s x 86.4",
        "value": round(inf_m3_d, 1),
        "unit": "m3/d",
    })

    blanket_kg = 0.0
    if basis == "total":
        blanket_kg = (
            clarifiers_in_service
            * d_blanket.canonical
            * a_clar.canonical
            * c_blanket.canonical
            / 1000
        )
        steps.append({
            "label": "Clarifier blanket inventory (estimated)",
            "formula": "n x blanket depth x clarifier area x blanket solids",
            "substituted": (
                f"{clarifiers_in_service} x {d_blanket.canonical:g} m x "
                f"{a_clar.canonical:g} m2 x {c_blanket.canonical:g} mg/L / 1000"
            ),
            "value": round(blanket_kg, 1),
            "unit": "kg",
        })

    # 5. Solve. One branch per unknown; each ends with mlss_mgl, was_m3_d and
    #    srt_d all populated, after which the working block is identical.
    target_d = t_target.to("d") if t_target is not None else None

    if solve_for == "srt":
        mlss_mgl = c_mlss.canonical
        was_m3_d = q_was.canonical * 86.4
        steps.append({
            "label": "Waste flow in working units",
            "formula": "Q_WAS x 86.4",
            "substituted": f"{q_was.canonical:g} L/s x 86.4",
            "value": round(was_m3_d, 1),
            "unit": "m3/d",
        })
    elif solve_for == "waste_flow":
        mlss_mgl = c_mlss.canonical
        # SRT = M / (Q_was.C_was + (Q_inf - Q_was).C_eff)  solved for Q_was.
        c_was_mgl = c_was_in.canonical if c_was_in is not None else mlss_mgl
        inventory_req = v.canonical * mlss_mgl / 1000 + blanket_kg
        removal_req = inventory_req / target_d
        effluent_at_zero_waste = inf_m3_d * c_eff.canonical / 1000
        if c_was_mgl <= c_eff.canonical:
            raise ValueError(
                "The waste stream is not more concentrated than the effluent, "
                "so no waste rate produces the target SRT. Check waste_solids "
                "and effluent_solids."
            )
        if removal_req <= effluent_at_zero_waste:
            raise ValueError(
                f"Target SRT of {target_d:g} d is not reachable by wasting. "
                f"Effluent losses alone remove "
                f"{effluent_at_zero_waste:.1f} kg/d, which is already at or "
                f"above the {removal_req:.1f} kg/d the target allows — so SRT "
                f"is below target even with zero wasting. Reduce effluent "
                f"solids or carry more inventory."
            )
        was_m3_d = (
            (removal_req - effluent_at_zero_waste) * 1000
            / (c_was_mgl - c_eff.canonical)
        )
        steps.append({
            "label": "Waste flow required for target SRT",
            "formula": (
                "(inventory / target SRT - Q_influent x effluent solids) "
                "/ (waste solids - effluent solids)"
            ),
            "substituted": (
                f"({inventory_req:.1f} kg / {target_d:g} d - "
                f"{effluent_at_zero_waste:.1f} kg/d) x 1000 / "
                f"({c_was_mgl:g} - {c_eff.canonical:g}) mg/L"
            ),
            "value": round(was_m3_d, 1),
            "unit": "m3/d",
        })
        if was_m3_d >= inf_m3_d:
            raise ValueError(
                f"The target SRT needs a waste rate of {was_m3_d:.1f} m3/d, "
                f"which is at or above the influent flow of {inf_m3_d:.1f} "
                f"m3/d. That is not physically possible — check the target and "
                f"the inventory."
            )
    else:  # solve_for == "mlss"
        was_m3_d = q_was.canonical * 86.4
        steps.append({
            "label": "Waste flow in working units",
            "formula": "Q_WAS x 86.4",
            "substituted": f"{q_was.canonical:g} L/s x 86.4",
            "value": round(was_m3_d, 1),
            "unit": "m3/d",
        })
        if was_m3_d >= inf_m3_d:
            raise ValueError(
                f"Waste flow ({was_m3_d:.1f} m3/d) is at or above influent "
                f"flow ({inf_m3_d:.1f} m3/d), which leaves no effluent. Check "
                f"the two flows."
            )
        eff_m3_d_pre = inf_m3_d - was_m3_d
        if waste_location == "ras_line":
            # Waste and effluent removal are both independent of MLSS, so the
            # required inventory follows directly from the target.
            removal = (
                was_m3_d * c_was_in.canonical / 1000
                + eff_m3_d_pre * c_eff.canonical / 1000
            )
            inventory_req = target_d * removal
            if inventory_req <= blanket_kg:
                raise ValueError(
                    f"Target SRT of {target_d:g} d needs a total inventory of "
                    f"{inventory_req:.1f} kg, but the clarifier blanket alone "
                    f"holds {blanket_kg:.1f} kg. No positive MLSS satisfies "
                    f"this. Check the target, the blanket inputs, or waste "
                    f"less."
                )
            mlss_mgl = (inventory_req - blanket_kg) * 1000 / v.canonical
            steps.append({
                "label": "MLSS required for target SRT",
                "formula": (
                    "(target SRT x total removal - blanket inventory) "
                    "x 1000 / aeration volume"
                ),
                "substituted": (
                    f"({target_d:g} d x {removal:.1f} kg/d - "
                    f"{blanket_kg:.1f} kg) x 1000 / {v.canonical:g} m3"
                ),
                "value": round(mlss_mgl, 1),
                "unit": "mg/L",
            })
        else:
            # Hydraulic wasting: MLSS is in the numerator AND the waste term.
            # With an aerobic basis and zero effluent solids it cancels out
            # entirely — SRT is V/Q_WAS at every MLSS, so there is nothing to
            # solve.
            denom = v.canonical - target_d * was_m3_d
            if blanket_kg == 0 and c_eff.canonical == 0:
                raise ValueError(
                    "MLSS cancels out of this case and cannot be solved for. "
                    "Wasting mixed liquor from the aeration basin on an "
                    "aerobic basis with zero effluent solids gives "
                    "SRT = V / Q_WAS at any MLSS "
                    f"({v.canonical:g} m3 / {was_m3_d:.1f} m3/d = "
                    f"{v.canonical / was_m3_d:.2f} d). Supply the real "
                    "effluent solids, or use basis 'total', to make MLSS "
                    "matter."
                )
            if denom <= 0:
                raise ValueError(
                    f"Target SRT of {target_d:g} d is not reachable at any "
                    f"MLSS. Wasting mixed liquor at {was_m3_d:.1f} m3/d from "
                    f"{v.canonical:g} m3 caps SRT at "
                    f"{v.canonical / was_m3_d:.2f} d, and effluent losses put "
                    f"it below that. Waste less."
                )
            mlss_mgl = (
                target_d * eff_m3_d_pre * c_eff.canonical - 1000 * blanket_kg
            ) / denom
            if mlss_mgl <= 0:
                raise ValueError(
                    f"Solving for MLSS gave {mlss_mgl:.1f} mg/L, which is not "
                    f"physical. The target SRT is above what this waste rate "
                    f"allows — wasting mixed liquor at {was_m3_d:.1f} m3/d "
                    f"from {v.canonical:g} m3 caps SRT at "
                    f"{v.canonical / was_m3_d:.2f} d before effluent losses."
                )
            steps.append({
                "label": "MLSS required for target SRT",
                "formula": (
                    "(target SRT x Q_effluent x effluent solids "
                    "- 1000 x blanket inventory) "
                    "/ (aeration volume - target SRT x Q_WAS)"
                ),
                "substituted": (
                    f"({target_d:g} d x {eff_m3_d_pre:.1f} m3/d x "
                    f"{c_eff.canonical:g} mg/L - 1000 x {blanket_kg:.1f} kg) "
                    f"/ ({v.canonical:g} m3 - {target_d:g} d x "
                    f"{was_m3_d:.1f} m3/d)"
                ),
                "value": round(mlss_mgl, 1),
                "unit": "mg/L",
            })

    # The waste concentration is only knowable once MLSS is, in the basin case.
    c_was_mgl = c_was_in.canonical if c_was_in is not None else mlss_mgl
    was_source = (
        "from RAS line" if waste_location == "ras_line"
        else "from aeration basin, = MLSS"
    )

    # 6. Both sides of the balance, from the now-complete set of values.
    aeration_kg = v.canonical * mlss_mgl / 1000
    steps.append({
        "label": "Aeration basin inventory",
        "formula": "V_aeration x MLSS",
        "substituted": f"{v.canonical:g} m3 x {mlss_mgl:g} mg/L / 1000",
        "value": round(aeration_kg, 1),
        "unit": "kg",
    })

    inventory_kg = aeration_kg + blanket_kg
    if basis == "total":
        steps.append({
            "label": "Total solids inventory",
            "formula": "aeration inventory + blanket inventory",
            "substituted": f"{aeration_kg:.1f} kg + {blanket_kg:.1f} kg",
            "value": round(inventory_kg, 1),
            "unit": "kg",
        })

    eff_m3_d = inf_m3_d - was_m3_d
    steps.append({
        "label": "Effluent flow",
        "formula": "Q_influent - Q_WAS",
        "substituted": f"{inf_m3_d:.1f} m3/d - {was_m3_d:.1f} m3/d",
        "value": round(eff_m3_d, 1),
        "unit": "m3/d",
    })

    was_kg_d = was_m3_d * c_was_mgl / 1000
    steps.append({
        "label": "WAS removal",
        "formula": "Q_WAS x waste solids",
        "substituted": f"{was_m3_d:.1f} m3/d x {c_was_mgl:g} mg/L / 1000",
        "value": round(was_kg_d, 1),
        "unit": "kg/d",
    })

    eff_kg_d = eff_m3_d * c_eff.canonical / 1000
    steps.append({
        "label": "Effluent removal",
        "formula": "Q_effluent x effluent solids",
        "substituted": f"{eff_m3_d:.1f} m3/d x {c_eff.canonical:g} mg/L / 1000",
        "value": round(eff_kg_d, 1),
        "unit": "kg/d",
    })

    removal_kg_d = was_kg_d + eff_kg_d
    steps.append({
        "label": "Total removal",
        "formula": "WAS removal + effluent removal",
        "substituted": f"{was_kg_d:.1f} kg/d + {eff_kg_d:.1f} kg/d",
        "value": round(removal_kg_d, 1),
        "unit": "kg/d",
    })

    srt_d = inventory_kg / removal_kg_d
    steps.append({
        "label": "SRT",
        "formula": "solids inventory / solids removed per day",
        "substituted": f"{inventory_kg:.1f} kg / {removal_kg_d:.1f} kg/d",
        "value": round(srt_d, 2),
        "unit": "d",
    })

    # 7. Warnings. Each is a line in the working, not prose appended to the end,
    #    because the operator reads the working to audit the number.
    warnings = []

    if waste_location == "ras_line":
        drift = abs(c_was_mgl - mlss_mgl) / mlss_mgl
        if drift <= RAS_MLSS_SIMILARITY:
            warnings.append(
                f"waste solids ({c_was_mgl:g} mg/L) is within "
                f"{drift * 100:.0f}% of MLSS ({mlss_mgl:g} mg/L), but wasting "
                f"is from the RAS line, where solids are normally 2-4x MLSS. "
                f"If MLSS was entered as the waste concentration by mistake, "
                f"this SRT is OVERSTATED by roughly the thickening factor. "
                f"Confirm the WAS/RAS solids figure."
            )

    if c_eff.canonical == 0:
        warnings.append(
            "effluent solids were given as zero, so effluent losses are "
            "EXCLUDED from the denominator. This OVERSTATES SRT. The error is "
            "largest exactly where SRT matters most — short SRT, poor "
            "settling, solids washout. Enter the measured effluent TSS."
        )

    if solids_basis == "VSS":
        warnings.append(
            "solids basis is VSS, so MLSS, waste and effluent figures must ALL "
            "be volatile solids. Plants normally report effluent as TSS — if "
            "the effluent figure is TSS, the bases are mixed and this SRT is "
            "wrong. Nothing was converted between TSS and VSS."
        )

    if basis == "total":
        warnings.append(
            "blanket inventory is an ESTIMATE. Blanket depth is a point "
            "measurement on a surface that varies with load, and blanket "
            "solids are rarely measured directly. The total-basis SRT is no "
            "more precise than the aerobic-basis one despite having more "
            "terms in it."
        )

    # 8. The working block. Every assumption gets a named line, including the
    #    ones that resolved to nothing — an operator auditing this must be able
    #    to see that the blanket was excluded, not just fail to find it.
    basis_note = (
        "aerobic (aeration basin only; clarifier blanket excluded)"
        if basis == "aerobic"
        else "total (aeration basin + clarifier blanket inventory)"
    )
    work.append(_line("Basis", basis_note))
    work.append(_line("Solids basis", solids_basis))
    work.append(_line(
        "Aeration inventory",
        f"{v.canonical:g} m3 x {mlss_mgl:g} mg/L = {aeration_kg:.1f} kg",
    ))
    if basis == "total":
        work.append(_line(
            "Blanket inventory",
            f"{clarifiers_in_service} x {d_blanket.canonical:g} m x "
            f"{a_clar.canonical:g} m2 x {c_blanket.canonical:g} mg/L = "
            f"{blanket_kg:.1f} kg (ESTIMATED)",
        ))
        work.append(_line(
            "Total inventory",
            f"{aeration_kg:.1f} kg + {blanket_kg:.1f} kg = "
            f"{inventory_kg:.1f} kg",
        ))
    else:
        work.append(_line("Blanket inventory", "excluded (aerobic basis)"))
    work.append(_line(
        "WAS removal",
        f"{was_m3_d:.1f} m3/d x {c_was_mgl:g} mg/L ({was_source}) = "
        f"{was_kg_d:.1f} kg/d",
    ))
    work.append(_line(
        "Effluent removal",
        f"{eff_m3_d:.1f} m3/d x {c_eff.canonical:g} mg/L = {eff_kg_d:.1f} kg/d"
        + ("  [excluded: zero entered]" if c_eff.canonical == 0 else ""),
    ))
    work.append(_line(
        "Total removal",
        f"{was_kg_d:.1f} kg/d + {eff_kg_d:.1f} kg/d = {removal_kg_d:.1f} kg/d",
    ))
    work.append(_line(
        "SRT",
        f"{inventory_kg:.1f} kg / {removal_kg_d:.1f} kg/d = {srt_d:.2f} d",
    ))
    if solve_for == "waste_flow":
        work.append(_line(
            "SOLVED FOR",
            f"waste flow = {was_m3_d:.1f} m3/d, to hit the target SRT of "
            f"{target_d:g} d",
        ))
    elif solve_for == "mlss":
        work.append(_line(
            "SOLVED FOR",
            f"MLSS = {mlss_mgl:.1f} mg/L, to hit the target SRT of "
            f"{target_d:g} d",
        ))
    for w in warnings:
        work.append(_line("WARNING", w))

    # No interpretation. See the header — adequacy for nitrification is
    # temperature-dependent and is not this tool's to declare.
    caveats.extend(warnings)
    caveats.append(
        "No judgment is offered on whether this SRT is adequate. The minimum "
        "aerobic SRT for nitrification depends on mixed liquor temperature and "
        "the safety factor applied, neither of which is an input here. Compare "
        "against this plant's own target."
    )

    # 9. Conversions first, so the operator sees what was assumed.
    parsed = [x for x in (v, q_inf, c_eff, c_mlss, q_was, c_was_in, t_target,
                          d_blanket, a_clar, c_blanket) if x is not None]
    out = ["Inputs:", echo_all(*parsed), ""]
    out += work
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in parsed]
    conversions.append(
        f"{q_inf.value:g} {q_inf.unit} = {inf_m3_d:.1f} m3/d (working units)"
    )
    conversions += [
        f"NOTE: {n}" for n in dict.fromkeys(n for x in parsed for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "srt_days": round(srt_d, 2),
            "basis": basis,
            "solids_basis": solids_basis,
            "waste_location": waste_location,
            "solved_for": solve_for,
            "solids_inventory_kg": round(inventory_kg, 1),
            "aeration_inventory_kg": round(aeration_kg, 1),
            "blanket_inventory_kg": round(blanket_kg, 1) if basis == "total" else None,
            "total_removal_kg_per_day": round(removal_kg_d, 1),
            "total_removal_lb_per_day": round(removal_kg_d * 2.20462, 1),
            "was_removal_kg_per_day": round(was_kg_d, 1),
            "effluent_removal_kg_per_day": round(eff_kg_d, 1),
            "waste_flow_m3_per_day": round(was_m3_d, 1),
            "mlss_mg_per_l": round(mlss_mgl, 1),
            "warnings": warnings,
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # Forward, aerobic basis, wasting from the RAS line, effluent counted.
    print(calc_srt(
        basis="aerobic", waste_location="ras_line", solids_basis="TSS",
        aeration_volume={"value": 5000, "unit": "m3"},
        mlss={"value": 3000, "unit": "mg/L"},
        waste_flow={"value": 200, "unit": "m3/d"},
        waste_solids={"value": 8000, "unit": "mg/L"},
        influent_flow={"value": 10, "unit": "MLD"},
        effluent_solids={"value": 15, "unit": "mg/L"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Total basis: the same plant with the clarifier blanket counted.
    print(calc_srt(
        basis="total", waste_location="ras_line", solids_basis="TSS",
        aeration_volume={"value": 5000, "unit": "m3"},
        mlss={"value": 3000, "unit": "mg/L"},
        waste_flow={"value": 200, "unit": "m3/d"},
        waste_solids={"value": 8000, "unit": "mg/L"},
        influent_flow={"value": 10, "unit": "MLD"},
        effluent_solids={"value": 15, "unit": "mg/L"},
        blanket_depth={"value": 0.6, "unit": "m"},
        clarifier_surface_area={"value": 450, "unit": "m2"},
        blanket_solids={"value": 6000, "unit": "mg/L"},
        clarifiers_in_service=2,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Reverse: what waste rate holds a 10-day SRT?
    print(calc_srt(
        basis="aerobic", waste_location="ras_line", solids_basis="TSS",
        aeration_volume={"value": 5000, "unit": "m3"},
        mlss={"value": 3000, "unit": "mg/L"},
        waste_solids={"value": 8000, "unit": "mg/L"},
        influent_flow={"value": 10, "unit": "MLD"},
        effluent_solids={"value": 15, "unit": "mg/L"},
        target_srt={"value": 10, "unit": "d"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # The trap: RAS-line wasting with an MLSS-level waste concentration.
    print(calc_srt(
        basis="aerobic", waste_location="ras_line", solids_basis="TSS",
        aeration_volume={"value": 5000, "unit": "m3"},
        mlss={"value": 3000, "unit": "mg/L"},
        waste_flow={"value": 200, "unit": "m3/d"},
        waste_solids={"value": 3000, "unit": "mg/L"},
        influent_flow={"value": 10, "unit": "MLD"},
        effluent_solids={"value": 15, "unit": "mg/L"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_srt(
            basis="aerobic", waste_location="ras_line", solids_basis="TSS",
            aeration_volume={"value": 200, "unit": "m3/d"},   # flow as volume
            mlss={"value": 3000, "unit": "mg/L"},
            waste_flow={"value": 5000, "unit": "m3"},
            waste_solids={"value": 8000, "unit": "mg/L"},
            influent_flow={"value": 10, "unit": "MLD"},
            effluent_solids={"value": 15, "unit": "mg/L"},
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
