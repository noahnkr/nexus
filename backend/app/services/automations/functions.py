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


def _as_number(value: Any, what: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{what} must be a number.")


async def _weighted_score(conn, args: dict) -> float:
    """A builder-configurable weighted sum: `sum(weights[k] * inputs[k])`. The
    formula lives in the recipe as data (weights + input references), so scoring a
    lead's value or an applicant's fit is tunable in the builder without code. A
    missing input contributes nothing; a non-numeric input is a plain error."""
    weights = args.get("weights") or {}
    inputs = args.get("inputs") or {}
    if not isinstance(weights, dict) or not isinstance(inputs, dict):
        raise ValueError("weighted_score needs 'weights' and 'inputs' objects.")
    total = 0.0
    for key, weight in weights.items():
        raw = inputs.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue  # missing/blank input contributes nothing
        total += _as_number(weight, f"weight for '{key}'") * _as_number(raw, f"input '{key}'")
    return round(total, 4)


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
    name="weighted_score",
    description=(
        "Compute a weighted score: the sum of each factor's weight times its value. "
        "Configure it in the builder — 'weights' maps factor names to numbers, and "
        "'inputs' maps the same names to values (often templated, e.g. "
        "{{entity.years_experience}}). Use it to score a lead's value or an "
        "applicant's fit, then branch on the result with a condition."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "weights": {
                "type": "object",
                "description": "Map of factor name to numeric weight (e.g. {\"experience\": 3}).",
            },
            "inputs": {
                "type": "object",
                "description": (
                    "Map of the same factor names to values to score (numbers or "
                    "{{templated}} references)."
                ),
            },
        },
        "required": ["weights", "inputs"],
    },
    handler=_weighted_score,
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
