# ---------------------------------------------------------------------------
# Calculators — deterministic, testable, no LLM involved
# ---------------------------------------------------------------------------
#
# Mean cell residence time (MCRT), a.k.a. solids retention time or sludge age:
# how many days a microorganism stays in the system before being wasted out.
#
#     MCRT = solids mass in the system / solids mass leaving per day
#
# Two forms, and this tool implements one equation that covers both:
#
#   Mass balance   inventory = V x MLSS (+ clarifier solids, if given)
#                  leaving   = Q_waste x waste SS + Q_eff x effluent SS
#
#   Short-cut      When mixed liquor is wasted from the reactor rather than the
#                  RAS line, the waste concentration IS the reactor MLSS, so
#                      MCRT = (V x MLSS) / (Q_w x MLSS) = V / Q_w
#                  MLSS cancels. The short-cut is not a different method, it is
#                  this method with waste SS = MLSS and effluent solids taken as
#                  negligible — so it is available without MLSS, and every
#                  assumption it rests on is emitted as a caveat.
#
# Two judgement calls the source text is explicit about, both surfaced as
# caveats rather than decided silently:
#   - Clarifier solids are excluded unless given. Acceptable when they are small
#     relative to reactor solids and RAS is steady; NOT acceptable when returned
#     solids swing reactor MLSS.
#   - Effluent solids are excluded unless given.
#
# Follows the quantity and trace patterns from calc_ct.py — see
# tools/calculators/__init__.py for the return shape.

from units import echo, echo_all, parse, quantity_schema
from tools.registry import tool


@tool(
    name="calc_mean_cell_residence_time",
    description=(
        "Calculate mean cell residence time (MCRT), also called solids "
        "retention time or sludge age, for an activated-sludge process. Use "
        "for MCRT/SRT/sludge age questions, or 'how long are my bugs staying "
        "in the system'. Works two ways: give reactor volume and waste flow "
        "alone for the short-cut form (valid when mixed liquor is wasted from "
        "the reactor), or add MLSS and waste/effluent solids for the full mass "
        "balance. Never compute it yourself. "
        "Pass every quantity with the unit the operator used — do not convert."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "reactor_volume": quantity_schema(
                "volume",
                "Liquid volume of the biological reactors in service.",
            ),
            "waste_flow": quantity_schema(
                "flow",
                "Waste sludge flow — WAS from the RAS line, or waste mixed "
                "liquor from the reactor. Ask which, it changes the solids "
                "concentration of the waste stream substantially.",
            ),
            "mlss": quantity_schema(
                "concentration",
                "Reactor mixed liquor suspended solids. Needed for the full "
                "mass balance. Omit it, along with waste_ss, to use the "
                "short-cut form (MLSS cancels there).",
            ),
            "waste_ss": quantity_schema(
                "concentration",
                "Suspended solids in the waste stream. If wasting mixed liquor "
                "from the reactor this equals MLSS and can be omitted. If "
                "wasting from the RAS line it is much higher — ask for it.",
            ),
            "effluent_flow": quantity_schema(
                "flow", "Final effluent flow. Omit to exclude effluent solids."
            ),
            "effluent_ss": quantity_schema(
                "concentration",
                "Final effluent suspended solids. Needs effluent_flow too.",
            ),
            "clarifier_solids": quantity_schema(
                "mass",
                "Mass of solids held in the clarifier blankets, if it is being "
                "counted in the inventory. Omit to exclude it — the result "
                "will say so.",
            ),
        },
        "required": ["reactor_volume", "waste_flow"],
    },
)
def calc_mean_cell_residence_time(
    reactor_volume, waste_flow, mlss=None, waste_ss=None,
    effluent_flow=None, effluent_ss=None, clarifier_solids=None,
):
    """Mean cell residence time by solids mass balance, or its short-cut form.

    Returns {"summary", "result", "steps", "conversions", "caveats"} — the
    calculator shape described in tools/calculators/__init__.py.
    """
    # 1. Parse first. Dimension errors surface here, before any math.
    v = parse(reactor_volume, "volume")
    q_w = parse(waste_flow, "flow")
    c_mlss = parse(mlss, "concentration") if mlss is not None else None
    c_waste = parse(waste_ss, "concentration") if waste_ss is not None else None
    q_eff = parse(effluent_flow, "flow") if effluent_flow is not None else None
    c_eff = parse(effluent_ss, "concentration") if effluent_ss is not None else None
    m_clar = parse(clarifier_solids, "mass") if clarifier_solids is not None else None

    if v.canonical <= 0:
        raise ValueError("Reactor volume must be positive.")
    if q_w.canonical <= 0:
        raise ValueError(
            "Waste flow must be positive — it is the denominator of MCRT. A "
            "plant wasting nothing has no finite MCRT."
        )
    if c_waste is not None and c_mlss is None:
        raise ValueError(
            "Waste SS was given without MLSS. The solids inventory needs the "
            "reactor MLSS. Either supply MLSS, or omit waste SS as well to use "
            "the short-cut form."
        )
    if (q_eff is None) != (c_eff is None):
        raise ValueError(
            "Effluent flow and effluent SS must be given together — one "
            "without the other cannot produce a solids mass."
        )

    # Each intermediate is captured as a step immediately after it is computed.
    # Nothing here is recalculated for the trace — the step entries only round,
    # for display, the value the next line goes on to use.
    steps = []
    caveats = []

    waste_m3_d = q_w.canonical * 86.4               # L/s -> m3/d
    steps.append({
        "label": "Waste flow in working units",
        "formula": "Q_waste x 86.4",
        "substituted": f"{q_w.canonical:g} L/s x 86.4",
        "value": round(waste_m3_d, 1),
        "unit": "m3/d",
    })

    # Short-cut form: no solids data at all, so MLSS cancels out of V/Q.
    shortcut = c_mlss is None

    if shortcut:
        method = "short-cut (V / Q_waste)"
        inventory_kg = None
        leaving_kg_d = None
        mcrt_d = v.canonical / waste_m3_d
        steps.append({
            "label": "MCRT (short-cut)",
            "formula": "V / Q_waste",
            "substituted": f"{v.canonical:g} m3 / {waste_m3_d:.1f} m3/d",
            "value": round(mcrt_d, 2),
            "unit": "d",
        })
        caveats.append(
            "Short-cut form used: reactor volume divided by waste flow. This "
            "holds only when mixed liquor is wasted from the reactor, so the "
            "waste concentration equals reactor MLSS. If wasting from the RAS "
            "line the waste is far more concentrated and the true MCRT is "
            "SHORTER than shown — supply MLSS and waste SS for the full "
            "mass balance."
        )
        caveats.append(
            "Effluent solids are excluded. Including them adds to the solids "
            "leaving and SHORTENS MCRT; the short-cut assumes they are small."
        )
    else:
        method = "mass balance"
        reactor_solids_kg = v.canonical * c_mlss.canonical / 1000
        steps.append({
            "label": "Reactor solids inventory",
            "formula": "V x MLSS",
            "substituted": (
                f"{v.canonical:g} m3 x {c_mlss.canonical:g} mg/L / 1000"
            ),
            "value": round(reactor_solids_kg, 1),
            "unit": "kg",
        })

        inventory_kg = reactor_solids_kg
        if m_clar is not None:
            inventory_kg = reactor_solids_kg + m_clar.canonical
            steps.append({
                "label": "Total solids inventory",
                "formula": "reactor solids + clarifier solids",
                "substituted": (
                    f"{reactor_solids_kg:.1f} kg + {m_clar.canonical:g} kg"
                ),
                "value": round(inventory_kg, 1),
                "unit": "kg",
            })
        else:
            caveats.append(
                "Clarifier solids are excluded from the inventory. That is "
                "acceptable when they are small next to reactor solids and the "
                "return rate is steady. If returned solids swing reactor MLSS, "
                "they must be included — supply clarifier_solids. Excluding "
                "them UNDERSTATES the inventory and so shortens MCRT."
            )

        # Wasting mixed liquor from the reactor means waste SS == MLSS.
        waste_conc = c_waste.canonical if c_waste is not None else c_mlss.canonical
        if c_waste is None:
            caveats.append(
                "Waste stream solids were not given — assumed equal to reactor "
                "MLSS, i.e. mixed liquor wasted from the reactor. If wasting "
                "from the RAS line the waste is more concentrated, more solids "
                "leave per day, and the true MCRT is SHORTER than shown."
            )

        wasted_kg_d = waste_m3_d * waste_conc / 1000
        steps.append({
            "label": "Solids wasted",
            "formula": "Q_waste x waste SS",
            "substituted": f"{waste_m3_d:.1f} m3/d x {waste_conc:g} mg/L / 1000",
            "value": round(wasted_kg_d, 1),
            "unit": "kg/d",
        })

        effluent_kg_d = 0.0
        if q_eff is not None:
            eff_m3_d = q_eff.canonical * 86.4
            effluent_kg_d = eff_m3_d * c_eff.canonical / 1000
            steps.append({
                "label": "Solids in effluent",
                "formula": "Q_effluent x effluent SS",
                "substituted": (
                    f"{eff_m3_d:.1f} m3/d x {c_eff.canonical:g} mg/L / 1000"
                ),
                "value": round(effluent_kg_d, 1),
                "unit": "kg/d",
            })
        else:
            caveats.append(
                "Effluent solids are excluded — assumed negligible. Including "
                "them adds to the solids leaving and SHORTENS MCRT."
            )

        leaving_kg_d = wasted_kg_d + effluent_kg_d
        steps.append({
            "label": "Total solids leaving",
            "formula": "wasted + effluent",
            "substituted": f"{wasted_kg_d:.1f} kg/d + {effluent_kg_d:.1f} kg/d",
            "value": round(leaving_kg_d, 1),
            "unit": "kg/d",
        })

        mcrt_d = inventory_kg / leaving_kg_d
        steps.append({
            "label": "MCRT",
            "formula": "inventory / solids leaving per day",
            "substituted": f"{inventory_kg:.1f} kg / {leaving_kg_d:.1f} kg/d",
            "value": round(mcrt_d, 2),
            "unit": "d",
        })

    # 2. Conversions first, so the operator sees what was assumed.
    parsed = [x for x in (v, q_w, c_mlss, c_waste, q_eff, c_eff, m_clar)
              if x is not None]
    out = ["Inputs:", echo_all(*parsed), ""]
    out += [f"Method: {method}"]
    if inventory_kg is not None:
        out += [
            f"Solids inventory: {inventory_kg:.1f} kg",
            f"Solids leaving: {leaving_kg_d:.1f} kg/day",
        ]
    out += [
        f"MCRT: {mcrt_d:.2f} days",
        "",
        "Reference ranges: 1-3 days for BOD removal only, 4-10 days for a "
        "nitrifying process, 20 days or more for extended aeration. These are "
        "typical literature values, NOT a target — set the target MCRT for "
        "this plant individually.",
    ]
    out += [f"CAVEAT: {c_}" for c_ in caveats]

    conversions = [echo(x) for x in parsed]
    conversions.append(
        f"{q_w.value:g} {q_w.unit} = {waste_m3_d:.1f} m3/d (working units)"
    )
    conversions += [
        f"NOTE: {n}" for n in dict.fromkeys(n for x in parsed for n in x.notes)
    ]

    return {
        "summary": "\n".join(out),
        "result": {
            "mcrt_days": round(mcrt_d, 2),
            "method": method,
            "solids_inventory_kg": (
                round(inventory_kg, 1) if inventory_kg is not None else None
            ),
            "solids_leaving_kg_per_day": (
                round(leaving_kg_d, 1) if leaving_kg_d is not None else None
            ),
        },
        "steps": steps,
        "conversions": conversions,
        "caveats": caveats,
    }


if __name__ == "__main__":
    from units import UnitError

    # The worked example from the source: 10 000 m3/d of mixed liquor wasted
    # from a 35 000-m3 reactor gives an MCRT of 3.5 days.
    print(calc_mean_cell_residence_time(
        reactor_volume={"value": 35000, "unit": "m3"},
        waste_flow={"value": 10000, "unit": "m3/d"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # The same example in US units — 9.24 mil. gal and 2.64 mgd.
    print(calc_mean_cell_residence_time(
        reactor_volume={"value": 9.24, "unit": "MG"},
        waste_flow={"value": 2.64, "unit": "MGD"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # Full mass balance: wasting from the RAS line, effluent solids counted.
    print(calc_mean_cell_residence_time(
        reactor_volume={"value": 5000, "unit": "m3"},
        waste_flow={"value": 200, "unit": "m3/d"},
        mlss={"value": 3000, "unit": "mg/L"},
        waste_ss={"value": 8000, "unit": "mg/L"},
        effluent_flow={"value": 10, "unit": "MLD"},
        effluent_ss={"value": 15, "unit": "mg/L"},
    )["summary"])

    print("\n" + "=" * 68 + "\n")

    # A swapped argument raises instead of returning a plausible wrong number.
    try:
        calc_mean_cell_residence_time(
            reactor_volume={"value": 10000, "unit": "m3/d"},   # flow as volume
            waste_flow={"value": 35000, "unit": "m3"},
        )
        raise AssertionError("expected a UnitError")
    except UnitError as e:
        print(f"swapped arguments correctly rejected:\n  {e}")
