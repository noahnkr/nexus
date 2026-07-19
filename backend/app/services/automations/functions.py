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

from .formula import evaluate


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


def _as_number(value: Any, what: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{what} must be a number.")


async def _formula(conn, args: dict) -> float:
    """Evaluate an arithmetic expression (M15c).

    By the time this runs, the engine's template pass has already substituted
    `{{field}}` references, so `"{{trigger.record.hourly_rate}} * 1.5"` arrives as
    `"22.5 * 1.5"`. Parsing (never `eval`) keeps a user-typed expression on the
    control path safe; see services/automations/formula.py."""
    raw = args.get("formula")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError("formula needs a 'formula' expression.")
    try:
        value = evaluate(str(raw))
    except ValueError as exc:
        # Prefix so the run's error line names which step failed and why.
        raise ValueError(f"Couldn't compute the formula: {exc}")
    return round(value, 4)


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


async def _days_until(conn, args: dict) -> int:
    """Whole days from now until a given date/date-time (negative if the date has
    already passed) — the mirror of days_since. Raises ValueError on an unparseable
    date. Powers credential-expiry / upcoming-date automations."""
    raw = args.get("date")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError("days_until needs a 'date'.")
    then = _parse_dt(raw)
    now = datetime.now(timezone.utc)
    return (then - now).days


register_function(FunctionDef(
    name="now",
    description="The current date and time as an ISO-8601 UTC timestamp.",
    input_schema={"type": "object", "properties": {}},
    handler=_now,
))

register_function(FunctionDef(
    name="formula",
    description=(
        "Calculate a number from an arithmetic expression, then branch on the "
        "result with a condition. Supports + - * /, parentheses, negative numbers, "
        "and round(value) or round(value, decimals). Reference record fields with "
        "{{templates}} — they are substituted before the calculation runs, e.g. "
        "'({{trigger.record.hourly_rate}} + 2) * 1.5' or "
        "'round({{entity.visits_last_month}} / 4, 1)'. Every referenced field must "
        "hold a number."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "formula": {
                "type": "string",
                "description": (
                    "The expression to calculate, e.g. "
                    "'{{entity.years_experience}} * 3 + 10'."
                ),
            },
        },
        "required": ["formula"],
    },
    handler=_formula,
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

register_function(FunctionDef(
    name="days_until",
    description=(
        "How many days from now until a date (ISO-8601) — negative if it's already "
        "past. Use it for upcoming-date automations, e.g. a credential expiry: "
        "'when a caregiver's certification is within 30 days of expiring…'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "An ISO-8601 date or date-time."},
        },
        "required": ["date"],
    },
    handler=_days_until,
))
