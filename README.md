# Entroply — agent loop + eval harness (day 1)

No UI, no database, no auth. A terminal loop and a regression harness, which is
everything you need to find out whether the hard part works.

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...        # or put it in a .env file

python agent.py "CP-1 is leaking at the drain port, what do I check?"
python run_evals.py
python run_evals.py --id ct-calc-01 --verbose
```

## Files

| file | what it is | who writes it |
|---|---|---|
| `agent.py` | the tool-use loop | **you, by hand** |
| `system_prompt.py` | the system prompt | **you, by hand** |
| `tools/` | one file per tool, each declaring its own schema via the `@tool` decorator in `tools/registry.py` | **you, by hand** |
| `units.py` | quantity parsing and unit conversion, so calculators take `{"value", "unit"}` instead of bare numbers | **you, by hand** |
| `run_evals.py` | grades cases on tool choice + text | you, mostly |
| `evals/eval_set.yaml` | the questions | you, forever |

The first two are the product. Everything you add later — React, FastAPI,
Postgres, auth — is scaffolding around them, and is fair game for Claude to
generate.

## Before you go further

1. **Replace the CT table.** `CT_GIARDIA_0_5_LOG` in
   `tools/calculators/calc_ct.py` is a placeholder with linear interpolation.
   The real table (and the real interpolation rule) comes from the MECP
   *Procedure for Disinfection of Drinking Water in Ontario*. Write unit tests
   against worked examples from the procedure document before this number is
   shown to any operator.
2. **Add a fixture image** at `evals/fixtures/pump_nameplate_01.jpg` — photograph
   any equipment nameplate. The photo case skips cleanly until you do.
3. **Get to 12-15 cases.** Three proves the harness; a dozen catches
   regressions. Add one every time the agent gets a real question wrong.

## Where the stubs are

`search_plant_docs` and `lookup_equipment` run against in-memory fixture lists
in `tools/retrievals/` and `tools/lookups/`. Swap their bodies for pgvector +
Postgres queries when you build ingestion — the tool schemas stay identical, so
nothing else changes. That's the point of putting the seam at the tool
boundary.

## Reading the results

`run_evals.py` grades three things and writes each run to `evals/runs/*.json`:

- **tool choice** — did the expected tools fire? Highest-signal check early.
  A calculation case with no tool call at all is a hard fail: it means the
  model did mental math and sounded confident doing it.
- **must_include / must_not_include** — loose substring smoke detectors.
- **grounding** — for `cite_or_disclaim` cases, the answer must either cite a
  page or explicitly say the information isn't in the plant's documents.

When a case fails on tool choice, **edit that tool's description in `tools/`
before you touch the system prompt.** Tool descriptions are where most of the
steering happens, and they're the cheapest thing to change.
