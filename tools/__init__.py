"""
Tool package — assembles every tool the agent can call.

There is no schema list and no dispatch table in this file anymore. Each tool
declares its own schema next to its implementation with the @tool decorator (see
tools/registry.py). Importing the sub-packages below runs those decorators, which
register every tool. TOOL_SCHEMAS and dispatch are then derived from the registry.

To add a tool: create a file with an @tool-decorated function and make sure its
sub-package imports it. Nothing in this file needs to change.
"""

from tools.registry import all_schemas, dispatch  # noqa: F401  (re-exported)

# Side-effect imports: loading these modules runs each @tool decorator, which is
# what populates the registry. Order here is the order tools appear to the model.
from . import documents, calculators  # noqa: F401,E402

# The list passed as `tools=` on every model call.
TOOL_SCHEMAS = all_schemas()

__all__ = ["TOOL_SCHEMAS", "dispatch"]
