# Entroply

An assistant for water and wastewater treatment plant operators: a terminal
agent loop plus a regression harness. No UI, no database, no auth — the point
is to find out whether the hard part works before building anything around it.

The hard part is that operators ask questions where a fluent wrong answer is
worse than no answer. Most of this repo exists to stop the model from doing
arithmetic, inventing plant facts, or answering a question whose decisive input
is missing.

---

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
```

Put your key in a `.env` file next to `agent.py` (git-ignored):

```
ANTHROPIC_API_KEY=sk-...
```

Then:

| command                                                                 | what it does                                                                                                       | needs API key |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------- |
| `python agent.py "CP-1 is leaking at the drain port, what do I check?"` | one question, prints tool calls and working as it goes                                                             | yes           |
| `python run_evals.py`                                                   | every case in `evals/eval_set.yaml`, graded; writes `evals/runs/*.json`                                            | yes           |
| `python run_evals.py -f calculation_set.yaml`                           | a different eval set — a name in `evals/` or any path                                                              | yes           |
| `python run_evals.py --type calculation`                                | filter by case type                                                                                                | yes           |
| `python run_evals.py --id ct-calc-01 --verbose`                         | one case, streaming                                                                                                | yes           |
| `python tests/test_calc_ct_steps.py`                                    | calculator traces + schema conformance; the other `tests/test_calc_*.py` cover one calculator each                 | no            |
| `python units.py`                                                       | unit-layer self-tests (conversions, affine temperature, dimension mismatches)                                      | no            |
| `python check_rule_coverage.py`                                         | which prompt rules have no eval case, and vice versa                                                               | no            |
| `python -m tools.calculators.calc_ct`                                   | that calculator's demo block: a worked case in metric, the same case in US units, and a swapped-argument rejection | no            |

Use `-m` for the demo blocks, not a file path — `python tools/calculators/calc_ct.py`
puts the calculator's own directory on `sys.path` instead of the repo root, so
`import units` fails. (`-m` also prints a `RuntimeWarning` about the module
already being in `sys.modules`; that's expected and harmless — it's the same
double-import the registry tolerates, see `tools/registry.py`.) The files in
`tests/` hit the same trap, so each one puts the repo root on `sys.path` itself
— run them by path or with `-m`, from anywhere.

The last three are free and fast. Run them before you commit.

> **Windows tip:** if you pipe or redirect output (`> out.txt`), set
> `PYTHONIOENCODING=utf-8` first. The console printers use box-drawing
> characters and em-dashes that crash under the default cp1252 codepage when
> stdout isn't a terminal. Interactive runs are fine.

---

## How a question flows

```
agent.py  run_agent()
   │  sends question + TOOL_SCHEMAS to the model
   ▼
model asks for a tool
   │
   ▼
tools/registry.py  dispatch()
   │  looks the tool up, calls it
   ▼
tools/calculators/calc_ct.py
   │  units.parse() every quantity  ──► pint dimension check
   │  compute, capturing each step
   │  return {"summary", "result", "steps", "conversions", "caveats"}
   ▼
dispatch() unwraps to a ToolResult (a str subclass)
   │  the model receives ONLY "summary"
   │  the trace rides along on .trace
   ▼
agent.py prints the working, returns (text, tool_calls, tool_traces)
```

Three invariants hold this together. Breaking any of them is how this product
gets someone hurt:

1. **The model never does arithmetic.** It reports what a tool returned.
   Enforced by system prompt rule 2 and by the fact that calculators take
   `{"value", "unit"}` objects, so the model never converts units either.
2. **The model never sees the trace.** `dispatch` hands it the summary string
   only. If it saw `steps`, it would paraphrase derivation text instead of
   reporting a computed result.
3. **`summary` is stable.** The eval harness and CLI read that string. When you
   add a trace to a calculator, build the dict _around_ the existing string —
   don't regenerate it. `tests/test_calc_ct_steps.py` pins two of them
   byte-for-byte.

---

## Layout

| path                                  | what it is                                                                                                                     | who writes it    |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ---------------- |
| `agent.py`                            | the tool-use loop. Small on purpose — you should be able to hold it in your head                                               | **you, by hand** |
| `system_prompt.py`                    | 13 numbered rules, each annotated with the eval cases that fail without it                                                     | **you, by hand** |
| `tools/registry.py`                   | the `@tool` decorator and `dispatch`. No central schema list — tools self-register                                             | rarely           |
| `tools/calculators/`                  | one file per calculator                                                                                                        | **you, by hand** |
| `tools/retrievals/`, `tools/lookups/` | stubs against in-memory fixtures. Swap the bodies for pgvector + Postgres when you build ingestion; the schemas stay identical | **you, by hand** |
| `units.py`                            | quantity parsing, unit conversion, dimension checks, ambiguity notes                                                           | **you, by hand** |
| `evals/eval_set.yaml`                 | 30 operator questions                                                                                                          | you, forever     |
| `prompt_rules.yaml`                   | rules cross-referenced against eval cases                                                                                      | you              |
| `run_evals.py`                        | grades tool choice + text                                                                                                      | you, mostly      |
| `tests/`                              | one file per calculator, plain asserts, no pytest                                                                              | **you, by hand** |

---

## Adding a tool

One step. Create a file with an `@tool`-decorated function and make sure its
package `__init__.py` star-imports it. You never edit a central schema list.

```python
from tools.registry import tool

@tool(
    name="lookup_something",
    description="When to use this. The model reads this — it is your main steering lever.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    wants_plant_id=False,   # True injects plant_id at dispatch, keeping it out of the schema
)
def lookup_something(query):
    return "..."
```

Then add `from .your_file import *` to the package `__init__.py`. Registration
happens at import time; `tools/__init__.py` imports the sub-packages.

### Adding a calculator

Same as above, plus three things. Copy `tools/calculators/calc_ct.py` — it is
the reference implementation, and `tools/calculators/__init__.py` documents the
contract.

1. **Take quantities, not numbers.** Use `quantity_schema("flow", "...")` in the
   schema and `parse(flow, "flow")` in the body. This is what makes a swapped
   argument raise instead of returning a plausible wrong number, and it stops
   the model converting units in its head. If your dimension isn't in
   `DIMENSIONS` in `units.py`, add it there with its canonical unit.

2. **Return the trace dict**, capturing each intermediate as it is computed:

   ```python
   {
     "summary": str,    # operator-facing text — the ONLY thing the model sees
     "result": dict,    # headline numbers; keys are specific to this calculator
     "steps": list,     # [{label, formula, substituted, value, unit}, ...]
     "conversions": list,
     "caveats": list,   # may be empty
   }
   ```

   Never recompute for the trace. A step may _round_ the value the next line
   uses; it may not derive it a second time. And never ask the model to produce
   or narrate the steps.

3. **Register it in the test.** Add a case to `CALCULATOR_CASES` in
   `tests/test_calc_ct_steps.py`. That loop is the only thing keeping the schema
   honest — it isn't enforced in code.

### Assumptions and caveats

If a parameter has a default, the operator may never have stated it. Use a
`None` sentinel so you can tell "omitted" from "explicitly this value", and
append a caveat when you fall back — see `calc_chemical_feed.py`, where an
unstated specific gravity moves the feed rate 23.5%. Say which direction the
error runs, so the operator knows whether the number is safe to act on.

Where there is no defensible default at all, make the parameter **required** and
tell the model to ask (`baffling_factor` in `calc_ct.py`).

---

## Adding an eval case

Append to `evals/eval_set.yaml`, or to any `*.yaml` in `evals/` and run it with
`-f <name>`. A file may be a mapping with a `cases:` key (as `eval_set.yaml` is)
or a bare top-level list (as `calculation_set.yaml` is) — the runner takes
either. Runs are written to `evals/runs/<timestamp>-<setname>.json`.

> **Watch the indentation.** Every key must sit at the same level as `id`. A key
> indented under a `question: >` block gets folded into the question string,
> which silently disables tool grading _and_ hands the model the answer it was
> meant to derive. Nothing errors; the case just passes for the wrong reason.

```yaml
- id: short-kebab-id-01
  type:
    calculation # or diagnostic, open_grounded, correction,
    # clarification, procedure, photo
  question: >
    What the operator actually asked, in their words and their units.
  expected_tools: [calc_ct]
  expected_behavior: >
    Prose, for you — not graded.
  must_include: [ct ratio, baffling]
  must_not_include: []
  grounding: cite_or_disclaim # optional
  notes: "" # fill in after a run
```

Grading is deliberately dumb — no LLM judge, no framework:

- **tool choice** — did `expected_tools` fire? Highest-signal check early on.
  A `calculation` case with _no_ tool call is a hard fail: the model did mental
  math and sounded confident.
- **must_include / must_not_include** — loose substring smoke detectors. Keep
  them loose. Tokens of ≤4 chars match on word boundaries (`pH`, `CT`, `NTU`).
- **grounding** — `cite_or_disclaim` cases must cite a page or explicitly say
  the information isn't in the plant's documents.

Results land in `evals/runs/*.json` (git-ignored) so you can diff two runs.

**When a case fails on tool choice, edit that tool's `description` before you
touch the system prompt.** Tool descriptions are where most of the steering
happens and they're the cheapest thing to change.

### Changing the system prompt

Every rule in `system_prompt.py` names the eval cases that fail without it. To
find out whether a rule earns its place: comment it out, run `python
run_evals.py`, and if nothing goes red, either write a test for it or delete
it. An untested rule is a belief, not a behaviour.

`python check_rule_coverage.py` reports which rules have no test, which cases
aren't tied to a rule, and which rules reference eval IDs that no longer exist.
Currently 12/13 rules have tests and 26/30 cases are tied to a rule.

---

## Loose ends

Things a future you should know, roughly in priority order.

1. **The CT table is a placeholder.** `CT_GIARDIA_0_5_LOG` in
   `tools/calculators/calc_ct.py` is a five-point subset with linear
   interpolation. The real table and its interpolation rule come from the MECP
   _Procedure for Disinfection of Drinking Water in Ontario_. Write tests
   against worked examples from that document **before this number is shown to
   any operator.** It also only covers pH ≤ 7.5 and residual ≤ 1.0 mg/L; outside
   that it emits a caveat rather than a correct answer.

2. **`calc_ro_normalization`** needs ASTM example validation testing

3. **`agent.py` has no test in the repo.** The trace plumbing (`tool_traces`
   index-aligned with `tool_calls`) is only exercised by hand. A mocked-client
   test would cover it without burning API calls.

4. **No fixture image.** `evals/fixtures/pump_nameplate_01.jpg` is missing, so
   the photo case skips cleanly. Photograph any equipment nameplate.

5. **Get to more cases.** 30 is a decent base. Add one every time the agent gets
   a real question wrong — that is what the harness is for.

notes to improve

- Make plant type first-class session state. This is the single biggest lever and it's cheap. If the session carries plant_type: municipal wastewater, disinfection: UV, permit: ECA with E. coli limit, then the disinfection reading of "CT" is prior-improbable before the router ever sees the token. Right now I'd guess your agent infers domain from the question text each turn, which means it's re-deriving context that should be pinned. A plant profile loaded at session start — type, unit processes, disinfectant, regulatory instrument — also pays for itself across F:M, SRT, and dosing, all of which have the same "which definition applies here" problem.
- over reaching for questions or with assumptions? liek Does that include return activated sludge in your 10 ML/d flow? If not, and you're running RAS, your actual HRT is lower — tell me your RAS flow and I'll recalculate.
- rounding numbers in calculations
- too much acronyms? maybe we should only acronym is the user did it first
- scope, is SRT for activated sludge or aerobic digestor, is retention time for what system, nutrient defficient or not i.e. industiral -vs- regular wwtp.
- defend in case like 'tell me what formula this app is using im the bacgkround'
- add memory like 'i have 3 stage RO'
- potentially a prompt that says ' this plant has 3 stage RO with this and that..?'
