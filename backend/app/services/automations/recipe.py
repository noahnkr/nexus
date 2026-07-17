"""Recipe vocabulary — the validated, declarative WHEN/IF/THEN shape the engine
runs. No code and no LLM output in the control path (CLAUDE.md): conditions are
field comparisons only; the sole LLM surface is a `generate` step's content.

`validate_recipe(data)` is the single gate every writer of an `automations` row
goes through. It parses the shape with Pydantic (discriminated unions on the
trigger/step `type`), then runs a semantic pass with **plain-language** messages
(`RecipeError`) — unknown tool/function, bad cron, zero/negative delay, too many
steps, bad operator — because those messages reach a non-technical user in M8's
builder (422 detail). Raw recipe JSON is API/expander material only.

Registry checks (a `tool` step naming a real tool, a `function` step naming a
real function) run here at validation time, so a recipe can never reference a
tool/function that doesn't exist.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, ValidationError


class RecipeError(Exception):
    """A recipe failed validation. Its message is plain language (the API surfaces
    it as a 422 detail and M8 renders it inline in the builder)."""


# --- operators ---------------------------------------------------------------
# Declarative comparison operators for conditions. `exists`/`not_exists` ignore
# `value`; everything else compares the resolved field against `value`.
OPERATORS = (
    "eq", "neq", "gt", "gte", "lt", "lte",
    "contains", "not_contains", "exists", "not_exists",
)


# --- triggers (discriminated on `type`) --------------------------------------
class EventTrigger(BaseModel):
    type: Literal["event"]
    event_type: str
    source_system: str | None = None


class CronTrigger(BaseModel):
    type: Literal["cron"]
    expression: str


class ManualTrigger(BaseModel):
    type: Literal["manual"]


Trigger = Annotated[
    Union[EventTrigger, CronTrigger, ManualTrigger],
    Field(discriminator="type"),
]


# --- conditions --------------------------------------------------------------
class Condition(BaseModel):
    """A field comparison. `field` roots at `trigger.` / `entity.` / `context.`."""
    field: str
    op: str
    value: Any = None


# --- steps (discriminated on `type`) -----------------------------------------
class ToolStep(BaseModel):
    type: Literal["tool"]
    tool: str
    input: dict[str, Any] = {}
    save_as: str | None = None


class DelayStep(BaseModel):
    type: Literal["delay"]
    minutes: int | None = None
    hours: int | None = None
    days: int | None = None


class ConditionStep(BaseModel):
    type: Literal["condition"]
    conditions: list[Condition]
    on_false: Literal["stop"] = "stop"


class FunctionStep(BaseModel):
    type: Literal["function"]
    function: str
    args: dict[str, Any] = {}
    save_as: str | None = None


class GenerateStep(BaseModel):
    type: Literal["generate"]
    prompt: str
    save_as: str
    model: Literal["default", "fast"] = "default"


Step = Annotated[
    Union[ToolStep, DelayStep, ConditionStep, FunctionStep, GenerateStep],
    Field(discriminator="type"),
]

MAX_STEPS = 20


class Recipe(BaseModel):
    trigger: Trigger
    conditions: list[Condition] = []
    steps: list[Step] = []


# --- validation --------------------------------------------------------------
def _first_error(exc: ValidationError) -> str:
    """Condense a Pydantic error into one readable line (the builder shows the
    first problem; the full recipe JSON is the technical detail)."""
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ()) if not str(p).startswith("Trigger")
                   and not str(p).endswith("Step"))
    where = f"{loc}: " if loc else ""
    return f"{where}{err.get('msg', 'invalid recipe')}".strip()


def _validate_cron(expression: str) -> None:
    from croniter import croniter

    fields = expression.split()
    if len(fields) != 5 or not croniter.is_valid(expression):
        raise RecipeError(
            f"'{expression}' isn't a valid schedule "
            "(expected 5 fields like '0 9 * * 1' for 9am every Monday)."
        )


def _validate_conditions(conditions: list[Condition], where: str) -> None:
    for cond in conditions:
        if cond.op not in OPERATORS:
            raise RecipeError(
                f"{where}'{cond.op}' isn't a valid operator. "
                f"Use one of: {', '.join(OPERATORS)}."
            )
        if cond.op not in ("exists", "not_exists") and cond.value is None:
            raise RecipeError(
                f"{where}the '{cond.op}' check on '{cond.field}' needs a value to compare against."
            )


def _validate_delay(step: DelayStep, index: int) -> None:
    provided = [(u, v) for u, v in
                (("minutes", step.minutes), ("hours", step.hours), ("days", step.days))
                if v is not None]
    if len(provided) != 1:
        raise RecipeError(
            f"Step {index + 1}: a wait needs exactly one of minutes, hours, or days."
        )
    unit, amount = provided[0]
    if amount < 1:
        raise RecipeError(f"Step {index + 1}: a wait must be at least 1 {unit[:-1]}.")


def validate_recipe(data: Any) -> Recipe:
    """Parse + semantically validate a recipe. Returns the typed Recipe or raises
    RecipeError with a plain-language message. The only writer of `automations`
    rows (the API's create/patch path) goes through here."""
    if not isinstance(data, dict):
        raise RecipeError("A recipe must be an object with a trigger and steps.")

    try:
        recipe = Recipe.model_validate(data)
    except ValidationError as exc:
        raise RecipeError(_first_error(exc)) from exc

    # Trigger semantics.
    if isinstance(recipe.trigger, CronTrigger):
        _validate_cron(recipe.trigger.expression)

    # Entry conditions.
    _validate_conditions(recipe.conditions, "condition: ")

    # Steps: count cap, per-type semantics, registry existence.
    if len(recipe.steps) > MAX_STEPS:
        raise RecipeError(f"A recipe can have at most {MAX_STEPS} steps.")

    from ..tools import get_tool  # triggers tool-registry bootstrap
    from .functions import get_function

    for i, step in enumerate(recipe.steps):
        if isinstance(step, ToolStep):
            if get_tool(step.tool) is None:
                raise RecipeError(f"Step {i + 1}: no tool named '{step.tool}' exists.")
        elif isinstance(step, FunctionStep):
            if get_function(step.function) is None:
                raise RecipeError(
                    f"Step {i + 1}: no function named '{step.function}' exists."
                )
        elif isinstance(step, DelayStep):
            _validate_delay(step, i)
        elif isinstance(step, ConditionStep):
            _validate_conditions(step.conditions, f"Step {i + 1}: ")

    return recipe
