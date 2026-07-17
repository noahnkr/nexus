"""`{{path}}` template rendering over the run scope `{trigger, entity, context}`.

Deep-renders dicts/lists/strings so a whole tool `input` object is rendered in one
call. Two string forms:
  * a string that is *exactly* one reference (`"{{context.score}}"`) resolves to the
    referenced value with its **type preserved** (an int stays an int) — so a
    templated number reaches a tool as a number, not "42".
  * a string with references embedded in text (`"Hi {{trigger.payload.name}}"`)
    interpolates each, stringifying the resolved value.

No expressions, no eval — path lookups only. An unresolvable path raises
`TemplateError`, which the engine turns into a run failure: failing loud beats
sending an SMS addressed to a blank name.
"""
from __future__ import annotations

import re
from typing import Any

# One reference token: {{ dotted.path }} (whitespace tolerated inside braces).
_TOKEN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
# A string that is nothing but a single reference (type-preserving form).
_FULL = re.compile(r"^\{\{\s*([^}]+?)\s*\}\}$")


class TemplateError(Exception):
    """A `{{path}}` couldn't be resolved against the run scope. The engine fails
    the run with this as the reason."""


def _resolve(path: str, scope: dict) -> Any:
    cur: Any = scope
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                idx = int(part)
                cur = cur[idx]
            except (ValueError, IndexError):
                raise TemplateError(f"Couldn't resolve '{{{{{path}}}}}'.")
        else:
            raise TemplateError(f"Couldn't resolve '{{{{{path}}}}}'.")
    return cur


def _render_str(text: str, scope: dict) -> Any:
    full = _FULL.match(text)
    if full is not None:
        return _resolve(full.group(1).strip(), scope)  # type preserved

    def repl(match: re.Match) -> str:
        value = _resolve(match.group(1).strip(), scope)
        return "" if value is None else str(value)

    return _TOKEN.sub(repl, text)


def render(value: Any, scope: dict) -> Any:
    """Deep-render `value` against `scope`. Dicts/lists recurse; strings resolve
    their `{{path}}` references; other scalars pass through untouched."""
    if isinstance(value, dict):
        return {k: render(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, scope) for v in value]
    if isinstance(value, str):
        return _render_str(value, scope)
    return value
