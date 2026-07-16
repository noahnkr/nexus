"""Tool registry: register/lookup + the Anthropic `tools` array builder.

Insertion order is the tools-array order. The package bootstrap (`__init__`)
imports the tool modules in a fixed sequence so the single `cache_control`
breakpoint always lands on the same (last) tool and the whole array caches
stably across turns.

This module imports nothing from the rest of the tool package, so it can be
imported first without a cycle.
"""
from __future__ import annotations

from typing import Any

_REGISTRY: dict[str, Any] = {}


def register(tool: Any) -> None:
    _REGISTRY[tool.name] = tool


def get_tool(name: str) -> Any | None:
    return _REGISTRY.get(name)


def all_tools() -> list[Any]:
    return list(_REGISTRY.values())


def anthropic_tool_defs() -> list[dict]:
    """API-ready tool list. `cache_control` on the last entry caches the whole
    tools array (Anthropic caches the prefix up to and including the breakpoint)."""
    defs = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in _REGISTRY.values()
    ]
    if defs:
        defs[-1] = {**defs[-1], "cache_control": {"type": "ephemeral"}}
    return defs
