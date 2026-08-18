"""Interpretation. One model call per document, or per batch of a large one.

extract.py has already said what parts exist and where. This asks the only
question left, the one no parser answers: what is each part, in the words an
operator would use to look for it.

Everything is interpreted in the same call - prose, tables, figures, formulas,
spreadsheet grids. There is no per-kind module and no per-kind prompt, because
the kinds are not really different problems: each is "describe this so someone
can find it later", and a single prompt that says so handles a photograph of a
control panel and a page of chemical dosing prose equally well. Figures ride in
the same request as image blocks, so the model sees the picture rather than a
caption describing it.

Two things the model is never asked to do:

  - restate content. A description says a section covers backwash sequencing;
    it does not say the backwash runs for eight minutes. The number stays in
    the source and is fetched when a question needs it. A description that
    quotes figures becomes a second, staler copy of the document.
  - state a statistic. Row counts, page counts, date ranges and extremes are
    computed from the extraction or not stated at all. A model asked for them
    will approximate, and an approximated fact is worse than a missing one
    because nothing downstream can tell.

The routing decision lives here too. Whether a spreadsheet grid is an operating
log or a calculation sheet is genuinely ambiguous - a dated column and a lot of
numbers describes both - so the model is asked, and a sheet it calls a log gets
handed to logs/convert.py for real conversion to parquet.
"""

import json

# Characters of extracted text per request. A large document is split into
# several calls rather than truncated: truncation is silent to the model, which
# would then describe the fragment it was given with no way to know the rest
# existed. Sized so one batch plus its images stays well inside the request
# limit with room for the response.
BATCH_CHARS = 120_000

# Per-part text sent for interpretation. A description needs the shape of a
# section, not all of it; the whole thing is still in the source file and is
# what a fetch returns. Prevents one 40-page appendix from consuming a batch.
PART_CHARS = 4_000

# Above this, a document cannot be carried whole into context regardless of
# what the model would prefer, so verbatim is not offered as an option. Roughly
# 6k tokens - about a dozen pages of prose.
VERBATIM_CHARS = 24_000

MAX_OUTPUT_TOKENS = 16_000

SYSTEM = """\
You are cataloguing an uploaded document so that a water and wastewater \
treatment agent can later find what it needs inside it.

You are given the document's parts, already located for you: sections with \
page numbers, tables, figures, formulas, or the sheets of a spreadsheet. \
Figures are attached as images, each immediately after the line naming it.

For every part, write a description that answers one question: if an operator \
needed this, what would they be looking for? Name the equipment, the process \
step, the chemical, the parameter, the form - in plant vocabulary, not \
document vocabulary. "Chlorine contact tank sizing and CT credit calculation" \
is useful. "A table with numeric data" is not.

Rules that matter more than they look:

- Describe, do not restate. Say what a part covers, never what it says. No \
setpoints, no dose rates, no limits, no durations, no equipment counts, no \
dates. Those stay in the source and are read from it when a question needs \
them. You are writing an index entry, not a replacement.
- State no statistic. No row counts, page counts, date ranges, minima or \
maxima. They are computed from the extraction, and a number you estimate is \
worse than no number because nothing downstream can tell it was estimated.
- Look at the figures. Say what the image actually shows - a process flow \
diagram, a pump performance curve, a photograph of a valve gallery, a site \
plan, a logo - and what equipment or process it concerns. A figure you cannot \
make sense of should be described as unclear rather than guessed at.
- Say whether each part is worth indexing. A logo, a letterhead crest, a \
decorative photograph, a fragment of page furniture, a table-of-contents row \
or a blank form field is not: nobody will ever look for it, and it would be \
read again on every future question. Describe it briefly and mark it not \
indexed. Anything carrying process, equipment, regulatory or procedural \
content is worth indexing, and if you are unsure, index it.
- For a formula, say what it computes and what its variables mean.
- For a spreadsheet sheet, decide what it IS. A sheet of dated observations \
recorded over and over - a monthly record grid, a daily operating log - is a \
"log". A sheet of equations, design parameters, lookup values or a filled-in \
form is a "table". This decision routes the sheet to a converter, so make it \
on the sheet's structure rather than its title.

Then judge the document as a whole. If it is short enough that carrying its \
full text costs little - a one or two page SOP, a form, a notice - choose \
"verbatim", and its words go into context directly where nothing stands \
between a question and the text that answers it. Choose "interpreted" for \
anything longer, where descriptions and page locators do the routing and the \
content is fetched on demand. When in doubt for a short document, prefer \
verbatim: a summary can route a question, but only the source can answer one."""


def component_schema(allow_verbatim):
    mode = {
        "type": "string",
        "enum": ["verbatim", "interpreted"] if allow_verbatim else ["interpreted"],
        "description": (
            "verbatim only for a document short enough to carry whole."
            if allow_verbatim
            else "This document is too large to carry whole."
        ),
    }
    return {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "One paragraph: what this document is and what it is for, "
                    "in the words an operator would search by. This decides "
                    "whether the document gets opened at all."
                ),
            },
            "mode": mode,
            "notes": {
                "type": "string",
                "description": (
                    "Anything that forced a decision, any part you could not "
                    "make sense of, anything you were unsure of."
                ),
            },
            "components": {
                "type": "array",
                "description": "One entry per part you were given.",
                "items": {
                    "type": "object",
                    "properties": {
                        "component_id": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["section", "table", "image", "formula", "log"],
                            "description": (
                                "Usually the kind you were given. Change it "
                                "only for a spreadsheet sheet that is an "
                                "operating log rather than a table."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "What this part covers, in plant terms. No "
                                "values, no statistics."
                            ),
                        },
                        "indexed": {
                            "type": "boolean",
                            "description": (
                                "False for a part nobody would ever search "
                                "for: logos, letterheads, decorative images, "
                                "page furniture, table-of-contents rows. "
                                "True if you are unsure."
                            ),
                        },
                    },
                    "required": ["component_id", "kind", "description", "indexed"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "mode", "notes", "components"],
        "additionalProperties": False,
    }


def batches(parts):
    """Split parts into request-sized groups, never truncating the document."""
    out, current, size = [], [], 0
    for part in parts:
        cost = min(len(part.text), PART_CHARS) + 200
        if current and size + cost > BATCH_CHARS:
            out.append(current)
            current, size = [], 0
        current.append(part)
        size += cost
    if current:
        out.append(current)
    return out


def render_part(part):
    text = part.text[:PART_CHARS]
    truncated = " (truncated for interpretation)" if len(part.text) > PART_CHARS else ""
    head = f"--- {part.component_id} | kind: {part.kind} | at: {part.locator}"
    if part.title:
        head += f" | title: {part.title}"
    head += " ---"
    if part.kind == "image":
        return f"{head}\n(the image follows)"
    return f"{head}{truncated}\n{text}" if text else head


def build_blocks(path, parts, file_type, part_of=None):
    """The user turn: a text block per part, with images interleaved."""
    header = f"FILE: {path}\nTYPE: {file_type}\nPARTS IN THIS REQUEST: {len(parts)}"
    if part_of:
        header += (
            f"\nNOTE: this is batch {part_of[0]} of {part_of[1]} for one large "
            "document. Describe only the parts in this request."
        )
    blocks = [{"type": "text", "text": header}]
    for part in parts:
        blocks.append({"type": "text", "text": render_part(part)})
        if part.kind == "image" and part.image:
            from ingest.extract import encode_image

            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": encode_image(part.image),
                },
            })
    return blocks


def call_model(client, model, blocks, allow_verbatim):
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": blocks}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": component_schema(allow_verbatim),
            }
        },
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"the response hit MAX_OUTPUT_TOKENS ({MAX_OUTPUT_TOKENS}) after "
            f"{response.usage.output_tokens} tokens, so its JSON is cut off. "
            "Lower BATCH_CHARS so fewer parts are described per call."
        )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), response.usage


def interpret(path, parts, file_type, client, model, log=print):
    """Describe every part. Returns (doc-level result, {component_id: entry})."""
    groups = batches(parts)
    text_total = sum(len(p.text) for p in parts)

    # Verbatim is only coherent for a document that fits in one request: if it
    # took several, it is by definition too large to carry into context whole.
    allow_verbatim = len(groups) == 1 and text_total <= VERBATIM_CHARS

    described = {}
    head = None
    usage_in = usage_out = 0

    for i, group in enumerate(groups, 1):
        blocks = build_blocks(
            path, group, file_type,
            part_of=(i, len(groups)) if len(groups) > 1 else None,
        )
        result, usage = call_model(client, model, blocks, allow_verbatim)
        usage_in += usage.input_tokens
        usage_out += usage.output_tokens
        log(f"    batch {i}/{len(groups)}: {usage.input_tokens} in / "
            f"{usage.output_tokens} out, {len(result.get('components', []))} described")
        if head is None:
            head = result
        for entry in result.get("components", []):
            described[entry["component_id"]] = entry

    return head or {}, described, (usage_in, usage_out)
