"""
Tool registry — the single source of truth the agent loop reads from.

There is no hand-maintained schema list and no hand-maintained name->function
dict anymore. Each tool declares its own schema with the @tool decorator, right
next to its implementation, so the two can never drift apart. Importing a tool
module runs its decorator, which registers it here.

Adding a tool is now one step: create a file with an @tool-decorated function and
make sure its package imports it (the sub-package __init__ files do this with a
star import). You never edit this file or a central schema list again.
"""

# name -> {"schema": {...}, "fn": callable, "wants_plant_id": bool}
_TOOLS = {}


class ToolResult(str):
    """A tool's summary string, with a machine-readable trace attached.

    Subclassing str is the point: every existing caller — agent.py's
    str(output), the verbose print, the eval harness — keeps seeing exactly
    the string the tool has always returned, with no call-site changes. The
    trace rides alongside on .trace for a UI that wants to render the working.

    The model only ever sees the string. The trace is never fed back to it as
    text, because the steps in it are Python's arithmetic, not the model's.
    """

    trace = None

    def __new__(cls, summary, trace=None):
        obj = super().__new__(cls, summary)
        obj.trace = trace
        return obj


def tool(name, description, input_schema, wants_plant_id=False):
    """Register the decorated function as an agent tool.

    Keep this decorator in the same file as the function it wraps. The schema
    the model sees and the code that runs are then one atomic unit — rename an
    argument and the schema is right there in front of you.

    wants_plant_id=True injects the caller's plant_id as a keyword argument at
    dispatch time (for plant-scoped retrievals), so it never appears in the
    schema the model fills in.
    """

    def register(fn):
        if name in _TOOLS:
            raise ValueError(f"duplicate tool name: {name!r}")
        _TOOLS[name] = {
            "schema": {
                "name": name,
                "description": description,
                "input_schema": input_schema,
            },
            "fn": fn,
            "wants_plant_id": wants_plant_id,
        }
        return fn

    return register


def all_schemas():
    """The list passed as `tools=` on every model call, in registration order."""
    return [t["schema"] for t in _TOOLS.values()]


def dispatch(name, tool_input, plant_id="demo"):
    """Look up a tool by name and call it with the model's arguments.

    A tool may return either a plain string or a dict with a "summary" key
    plus a structured trace. Either way this returns something that IS a
    string, so the agent loop is unchanged; when there is a trace, the return
    value is a ToolResult carrying it on .trace.
    """
    entry = _TOOLS.get(name)
    if entry is None:
        raise ValueError(f"unknown tool: {name}")
    if entry["wants_plant_id"]:
        out = entry["fn"](plant_id=plant_id, **tool_input)
    else:
        out = entry["fn"](**tool_input)

    # Unwrap the trace dict to its summary. The model must never be handed the
    # whole dict as text — it would read the steps as something to re-derive.
    if isinstance(out, dict) and "summary" in out:
        return ToolResult(out["summary"], trace=out)
    return out
