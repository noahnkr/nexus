"""Tool layer public surface + registry bootstrap.

Importing this package runs each tool module's module-level `register()` calls.
The import order here fixes the tools-array order — `search_documents`, then the
seven entity tools, then `run_report` last — so the single `cache_control`
breakpoint always lands on `run_report` and the array caches stably.

Two entrypoints are exported: `execute_tool` (the audited execution seam) and
`anthropic_tool_defs` (the API-ready tools array).
"""
from .core import ToolDef, ToolInputError, ToolResult, execute_tool
from .registry import all_tools, anthropic_tool_defs, get_tool

# Bootstrap: side-effecting imports register the tools in a fixed order.
from . import documents  # noqa: E402,F401
from . import entities  # noqa: E402,F401
from . import reporting  # noqa: E402,F401

__all__ = [
    "ToolDef",
    "ToolResult",
    "ToolInputError",
    "execute_tool",
    "anthropic_tool_defs",
    "all_tools",
    "get_tool",
]
