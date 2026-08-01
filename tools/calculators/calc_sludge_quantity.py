# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Primary sludge quantity from a solids mass balance across the tank:
#
#     dry solids removed  = flow x (influent SS - effluent SS)
#     wet sludge mass     = dry solids / dry-solids fraction
#     wet sludge volume   = wet sludge mass / sludge density
#
# One tool rather than two, deliberately. Wet sludge is derived from dry
# solids, so splitting them would make the model read a number out of one tool
# result and pass it into the next — arithmetic handling that system prompt
# rule 2 forbids and that the eval harness cannot see. Both figures come out of
# one call, from one mass balance.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool

# Assumed when the operator does not state it. Primary sludge is typically
# 1.02-1.03; water is 1.0, so this under-states density and over-states volume.
DEFAULT_SLUDGE_SG = 1.0


@tool(
    name="calc_sludge_quantity",
    description=(
        "Calculate sludge removed from a primary or settling tank by solids "
        "mass balance: dry solids removed (kg/day) and wet sludge as both mass "
        "per day and volume per day. Use for 'how much sludge am I making', "
        "sludge pumping rate, or solids loading questions. Needs influent and "
        "effluent suspended solids, flow, and the percent dry solids of the "
        "pumped sludge. Never compute it yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow": quantity_schema("flow", "Wastewater flow through the tank."),
            "influent_ss": quantity_schema(
                "concentration", "Influent (raw) suspended solids."
            ),
            "effluent_ss": quantity_schema(
                "concentration", "Effluent (settled) suspended solids."
            ),
            "sludge_dry_solids_pct": {
                "type": "number",
                "description": (
                    "Percent dry solids of the PUMPED sludge, w/w. Primary "
                    "sludge is typically 3-7%, waste activated sludge 0.5-1%. "
                    "There is no safe default — ask the operator for the lab "
                    "result or the sludge sampler reading rather than assuming."
                ),
            },
            "sludge_sg": {
                "type": "number",
                "description": (
                    "Specific gravity of the wet sludge. Primary sludge is "
                    "typically 1.02-1.03. If omitted this defaults to 1.0 "
                    "(water) and the result says so."
                ),
            },
        },
        "required": [
            "flow", "influent_ss", "effluent_ss", "sludge_dry_solids_pct",
        ],
    },
)
def calc_sludge_quantity(flow, influent_ss, effluent_ss,
                         sludge_dry_solids_pct, sludge_sg=None):
    """Dry solids removed and wet sludge produced, by solids mass balance.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.

    sludge_sg defaults to None rather than 1.0 so an omitted value can be told
    from an operator who actually said 1.0; only the former earns a caveat.
    sludge_dry_solids_pct has no default at all — it spans an order of
    magnitude between primary sludge and WAS, so assuming it is not defensible.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    q = parse(flow, "flow")
    ss_in = parse(influent_ss, "concentration")
    ss_out = parse(effluent_ss, "concentration")

    sg_assumed = sludge_sg is None
    if sg_assumed:
        sludge_sg = DEFAULT_SLUDGE_SG

    if q.canonical <= 0:
        raise ValueError("Flow must be positive.")
    if not 0 < sludge_dry_solids_pct <= 100:
        raise ValueError(
            "Percent dry solids must be between 0 and 100. Primary sludge is "
            "typically 3-7%, waste activated sludge 0.5-1%."
        )
    if sludge_sg <= 0:
        raise ValueError("Sludge specific gravity must be positive.")
    if ss_out.canonical > ss_in.canonical:
        raise ValueError(
            f"Effluent SS ({ss_out.canonical:g} mg/L) exceeds influent SS "
            f"({ss_in.canonical:g} mg/L), so the tank is producing solids "
            "rather than removing them. That is normally a swapped sample, a "
            "lab error, or solids washout — check which before calculating."
        )

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []

    ss_removed = ss_in.canonical - ss_out.canonical
    steps.append({
        "label": "Suspended solids removed",
        "formula": "influent SS - effluent SS",
        "substituted": f"{ss_in.canonical:g} mg/L - {ss_out.canonical:g} mg/L",
        "value": round(ss_removed, 1),
        "unit": "mg/L",
    })

    flow_mld = q.canonical * 86400 / 1e6            # L/s -> ML/d
    steps.append({
        "label": "Flow in working units",
        "formula": "Q x 86400 / 1e6",
        "substituted": f"{q.canonical:g} L/s x 86400 / 1e6",
        "value": round(flow_mld, 3),
        "unit": "ML/d",
    })

    dry_solids_kg_d = flow_mld * ss_removed          # ML/d * mg/L == kg/d
    steps.append({
        "label": "Dry solids removed",
        "formula": "Q x SS removed",
        "substituted": f"{flow_mld:.3g} ML/d x {ss_removed:g} mg/L",
        "value": round(dry_solids_kg_d, 1),
        "unit": "kg/day",
    })

    wet_sludge_kg_d = dry_solids_kg_d / (sludge_dry_solids_pct / 100)
    steps.append({
        "label": "Wet sludge mass",
        "formula": "dry solids / dry-solids fraction",
        "substituted": (
            f"{dry_solids_kg_d:.1f} kg/day / {sludge_dry_solids_pct / 100:g}"
        ),
        "value": round(wet_sludge_kg_d, 1),
        "unit": "kg/day",
    })

    wet_sludge_m3_d = wet_sludge_kg_d / (sludge_sg * 1000)
    steps.append({
        "label": "Wet sludge volume",
        "formula": "wet sludge mass / sludge density",
        "substituted": (
            f"{wet_sludge_kg_d:.1f} kg/day / ({sludge_sg} x 1000 kg/m3)"
        ),
        "value": round(wet_sludge_m3_d, 2),
        "unit": "m3/day",
    })

    removal_pct = ss_removed / ss_in.canonical * 100
    steps.append({
        "label": "SS removal efficiency",
        "formula": "SS removed / influent SS x 100",
        "substituted": f"{ss_removed:g} / {ss_in.canonical:g} x 100",
        "value": round(removal_pct, 1),
        "unit": "%",
    })

    caveats = []
    if sg_assumed:
        caveats.append(
            f"Sludge specific gravity was not given — assumed "
            f"{DEFAULT_SLUDGE_SG:g} (water). Primary sludge is typically "
            "1.02-1.03, which is denser and so needs LESS volume than shown. "
            "The mass per day is unaffected."
        )

    # 2. Conversions first, so the operator sees what was assumed.
    out = ["Inputs:", echo_all(q, ss_in, ss_out), ""]
    out += [
        f"SS removed: {ss_removed:.1f} mg/L ({removal_pct:.1f}% removal)",
        f"Dry solids removed: {dry_solids_kg_d:.1f} kg/day",
        f"Wet sludge ({sludge_dry_solids_pct}% DS, SG {sludge_sg}):",
        f"  {wet_sludge_kg_d:.1f} kg/day",
        f"  {wet_sludge_m3_d:.2f} m3/day ({wet_sludge_m3_d * 1000:.0f} L/day)",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in (q, ss_in, ss_out)]
    conversions.append(f"{q.value:g} {q.unit} = {flow_mld:.3g} ML/d (working units)")
    conversions += [
        f"NOTE: {n}"
        for n in dict.fromkeys(n for x in (q, ss_in, ss_out) for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "ss_removed_mgl": round(ss_removed, 1),
            "removal_pct": round(removal_pct, 1),
            "dry_solids_kg_per_day": round(dry_solids_kg_d, 1),
            "wet_sludge_kg_per_day": round(wet_sludge_kg_d, 1),
            "wet_sludge_m3_per_day": round(wet_sludge_m3_d, 2),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # 10 ML/d primary tank, 220 -> 90 mg/L SS, sludge at 4% DS.
    print(calc_sludge_quantity(
        flow={"value": 10, "unit": "MLD"},
        influent_ss={"value": 220, "unit": "mg/L"},
        effluent_ss={"value": 90, "unit": "mg/L"},
        sludge_dry_solids_pct=4.0,
        sludge_sg=1.02,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Identical physical case stated in US units — must give the same answer.
    print(calc_sludge_quantity(
        flow={"value": 2.6417, "unit": "MGD"},
        influent_ss={"value": 220, "unit": "ppm"},
        effluent_ss={"value": 90, "unit": "ppm"},
        sludge_dry_solids_pct=4.0,
        sludge_sg=1.02,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Omitting SG is allowed, but the result says so.
    print(calc_sludge_quantity(
        flow={"value": 10, "unit": "MLD"},
        influent_ss={"value": 220, "unit": "mg/L"},
        effluent_ss={"value": 90, "unit": "mg/L"},
        sludge_dry_solids_pct=4.0,
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_sludge_quantity(
            flow={"value": 220, "unit": "mg/L"},     # SS in the flow slot
            influent_ss={"value": 10, "unit": "MLD"},
            effluent_ss={"value": 90, "unit": "mg/L"},
            sludge_dry_solids_pct=4.0,
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")

    # Effluent above influent is a data error, not a negative sludge yield.
    try:
        calc_sludge_quantity(
            flow={"value": 10, "unit": "MLD"},
            influent_ss={"value": 90, "unit": "mg/L"},
            effluent_ss={"value": 220, "unit": "mg/L"},
            sludge_dry_solids_pct=4.0,
        )
        raise AssertionError("expected a ValueError")
    except ValueError as e:
        print(f"\ninverted SS correctly rejected:\n  {e}")
