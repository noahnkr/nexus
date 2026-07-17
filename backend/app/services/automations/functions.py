"""Function registry — safe-by-definition pure computations a recipe can run to
put a value into `context` (which a later declarative condition then compares).

This is the seam that lets scoring/derivation logic exist without an LLM in the
control path: a `function` step computes a number, a `condition` step branches on
it. Functions have **no external effect** — anything that reaches outside the
system must be a *tool* (gated), never a function. Vertical scoring functions
(M10) `register_function` here without touching core.

`input_schema` is JSON Schema like `ToolDef`'s, so M8's builder renders a
function-step form the same way it renders a tool form. Handlers receive the
already-tenant-scoped connection (RLS filters) and return a JSON-serializable value.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable


@dataclass
class FunctionDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[Any, dict], Awaitable[Any]]


_FUNCTIONS: dict[str, FunctionDef] = {}


def register_function(fn: FunctionDef) -> None:
    _FUNCTIONS[fn.name] = fn


def get_function(name: str) -> FunctionDef | None:
    return _FUNCTIONS.get(name)


def all_functions() -> list[FunctionDef]:
    return list(_FUNCTIONS.values())


# --- core functions (deliberately minimal) -----------------------------------
def _parse_dt(raw: Any) -> datetime:
    """Parse an ISO date or date-time into an aware UTC datetime."""
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, date):
        dt = datetime(raw.year, raw.month, raw.day)
    else:
        text = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _now(conn, args: dict) -> str:
    """Current instant as an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


async def _days_since(conn, args: dict) -> int:
    """Whole days between a given date/date-time and now (negative if in the
    future). Raises ValueError on an unparseable date — the engine fails the run
    with a plain message."""
    raw = args.get("date")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError("days_since needs a 'date'.")
    then = _parse_dt(raw)
    now = datetime.now(timezone.utc)
    return (now - then).days


register_function(FunctionDef(
    name="now",
    description="The current date and time as an ISO-8601 UTC timestamp.",
    input_schema={"type": "object", "properties": {}},
    handler=_now,
))

register_function(FunctionDef(
    name="days_since",
    description=(
        "Whole number of days between a given date (ISO-8601) and now. Useful for "
        "'has it been N days since…' conditions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "An ISO-8601 date or date-time."},
        },
        "required": ["date"],
    },
    handler=_days_since,
))
