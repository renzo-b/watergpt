# WaterGPT

A personal project: an assistant for water and wastewater treatment plant
operators, built to find out what it actually takes to make an LLM trustworthy
in a domain where a fluent wrong answer is worse than no answer at all.

No UI, no database, no auth. A terminal agent loop, a document pipeline, and a
regression harness — because the interesting problems are all upstream of the
interface.

---

## Motivation

Ask an operator's question — _"our raw water is at 28 °C, which CT equation do
we use?"_ — and a general-purpose model will answer confidently, fluently, and
sometimes wrongly. It will do arithmetic in its head. It will state a design
figure it half-remembers. It will answer a question whose decisive input the
operator never supplied.

None of those failures announce themselves. That is the whole difficulty: the
wrong answer and the right one look identical, and the person reading it is
standing in a plant making a decision about drinking water.

Most of this repo is machinery for making those failures either impossible or
visible.

---

## Core Ideas

**The model never does arithmetic.** Twelve calculators handle CT, SRT, F:M,
RO normalisation, chemical feed and so on. They take `{"value", "unit"}` objects
rather than bare numbers, so a swapped argument raises instead of returning a
plausible figure, and the model never converts units in its head. Each returns a
full derivation trace — but the model only ever sees the summary string, so it
reports a computed result rather than paraphrasing derivation text.

**Structure is computed; only meaning is interpreted.** The document pipeline
splits along this line. Where a table sits, which page a figure is on, where one
section ends — docling and openpyxl already know that exactly, so no model is
asked. What each part _means_, in the words an operator would search by, is the
only question a model gets. Row counts, date ranges and extremes are computed by
pandas and never stated by the model: an approximated fact is worse than a
missing one, because nothing downstream can tell the difference.

**Index and source are kept separate.** Documents are catalogued into
descriptions and locators that go into context; the content itself is fetched on
demand. A description can route a question — _"UV disinfection, pages 93–103"_ —
but it can't answer one, so anything that would quote a number reads the source
first. A 660-page O&M manual that doesn't remotely fit in context becomes a few
thousand tokens of index that does.

**Durable knowledge and instance values are different things.** A spreadsheet's
formulas — the CT equations, the variable definitions, the decision rules — are
knowledge, still true next month. Today's clearwell temperature sitting in an
input cell is one run of the sheet. Indexing the second as if it were the first
is how a system starts confidently reporting last Tuesday as a plant fact.

**Spreadsheets that are really logbooks get converted, not described.** A model
writes a converter; code runs it once and writes parquet. No description of a
monthly record sheet, however good, can answer _"how many days did chlorine
residual sit below the limit in March"_ — that needs a dataframe.

---

## Roughly how it fits together

```
        documents (pdf, docx, xlsx, csv)
                    │
                    ▼
        ingest/     extract  →  interpret  →  catalogue
                    structural   one model     descriptions
                    and free     call          + locators
                    │                              │
                    │  a sheet of dated readings   │
                    ▼                              ▼
        logs/       converter → parquet     agent.py  ── tools ──┐
                                                 │              │
                                                 │         calculators
                                                 │         fetch source
                                                 ▼              │
                                            answer ◄────────────┘
```

`rag/` holds an embedding-and-vector-store path that is deliberately not wired
in. At this corpus size the model can route by reading an index, and retrieval
that has to guess which document a question belongs to is solving a problem this
collection doesn't have yet.

---

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
echo ANTHROPIC_API_KEY=sk-... > .env
```

| command                                                                 | what it does                                                 |
| ----------------------------------------------------------------------- | ------------------------------------------------------------ |
| `python agent.py "CP-1 is leaking at the drain port, what do I check?"` | one question, printing tool calls and working as it goes     |
| `python -m ingest.pipeline --input documents/test`                      | ingest a folder of documents                                 |
| `python -m ingest.pipeline --catalogue`                                 | dump exactly what the model sees as context                  |
| `python run_evals.py`                                                   | the operator-question eval set                               |
| `python scripts/eval_fullcontext.py`                                    | the retrieval eval, with cost reported                       |
| `python tests/test_calc_ct_steps.py`                                    | calculator traces and schema conformance — no API key needed |

The `tests/` files and `units.py` run offline and take seconds. Anything
touching `ingest/` or the evals costs API credits.
