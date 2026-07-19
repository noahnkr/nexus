"""Automation functions — the pure computations a recipe can run (WS3, M15c).

Pure functions of (conn, args); none touch the DB, so these run offline (no
NEXUS_APP_DB_URL gate). Also proves a recipe using one validates.

`weighted_score` was retired in favour of `formula` (M15c) and its tests went with
it — no stored recipe referenced it.
"""
import asyncio

import pytest

from app.services.automations.functions import get_function
from app.services.automations.recipe import RecipeError, validate_recipe


def test_unknown_function_rejected():
    with pytest.raises(RecipeError):
        validate_recipe({
            "trigger": {"type": "manual"},
            "steps": [{"type": "function", "function": "no_such_fn", "args": {}}],
        })


# --- days_until (Module 11a) — mirror of days_since -----------------------------
def _days_until(args):
    fn = get_function("days_until")
    assert fn is not None
    return asyncio.run(fn.handler(None, args))


def test_days_until_future():
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    # whole-day floor: 9 or 10 depending on sub-day remainder (mirror days_since tolerance)
    assert _days_until({"date": future}) in (9, 10)


def test_days_until_past_is_negative():
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert _days_until({"date": past}) < 0


def test_days_until_unparseable_errors():
    with pytest.raises(ValueError):
        _days_until({"date": "not-a-date"})


def test_days_until_registered():
    from app.services.automations.functions import all_functions

    assert "days_until" in {f.name for f in all_functions()}


# ---------------------------------------------------------------------------
# Module 15c — the `formula` function (hand-rolled parser, never eval)
# ---------------------------------------------------------------------------
def _formula(expr):
    fn = get_function("formula")
    assert fn is not None
    return asyncio.run(fn.handler(None, {"formula": expr}))


def test_formula_registered():
    from app.services.automations.functions import all_functions

    names = {f.name for f in all_functions()}
    assert "formula" in names


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("2+3*4", 14),           # precedence, not left-to-right
        ("(2+3)*4", 20),         # parens override it
        ("10/4", 2.5),           # true division, not floor
        ("-3 + 5", 2),           # unary minus
        ("--3", 3),              # stacked unary
        ("2 * -4", -8),          # unary after an operator
        ("  7  ", 7),            # whitespace tolerance
        ("1.5 * 2", 3),          # decimals
        (".5 + 1", 1.5),         # leading-dot decimal
        ("round(10/3, 2)", 3.33),
        ("round(10/3)", 3),      # digits default to 0
        ("round(2.5) * 2", 4),   # banker's rounding, per Python
        ("(1 + 2) * (3 + 4)", 21),
    ],
)
def test_formula_grammar(expr, expected):
    assert _formula(expr) == expected


def test_formula_rounds_to_four_dp():
    # Mirrors the weighted_score precedent — a float that would otherwise carry
    # binary-representation noise into a condition comparison.
    assert _formula("1/3") == 0.3333


@pytest.mark.parametrize(
    "expr,fragment",
    [
        ("2 +", "ends unexpectedly"),
        ("(2 + 3", "Expected ')'"),
        ("2 + 3)", "Unexpected ')'"),
        ("2 3", "Unexpected '3'"),
        ("1/0", "Division by zero"),
        ("2 + pending", "'pending' is not a number"),
        ("nonsense(2)", "'nonsense' is not a number"),
        ("2 $ 3", "isn't something I can calculate"),
        ("x" * 501, "too long"),
    ],
)
def test_formula_errors_are_plain_language(expr, fragment):
    with pytest.raises(ValueError) as exc:
        _formula(expr)
    message = str(exc.value)
    assert fragment in message
    # The engine surfaces this verbatim to a non-technical user.
    assert message.startswith("Couldn't compute the formula:")


@pytest.mark.parametrize("expr", ["", "   ", None])
def test_formula_requires_an_expression(expr):
    fn = get_function("formula")
    with pytest.raises(ValueError) as exc:
        asyncio.run(fn.handler(None, {"formula": expr}))
    assert "needs a 'formula'" in str(exc.value)


def test_formula_evaluates_template_rendered_values():
    """The engine substitutes {{tokens}} BEFORE the handler runs, so what arrives
    here is already a plain numeric string."""
    from app.services.automations.templates import render

    rendered = render(
        {"formula": "({{trigger.record.hourly_rate}} + 2) * 1.5"},
        {"trigger": {"record": {"hourly_rate": 22}}},
    )
    assert _formula(rendered["formula"]) == 36


def test_formula_unsubstituted_token_is_a_plain_error():
    """A field that held text (or a typo'd path) leaves a bare word behind rather
    than a number — the user must see why, not a parser trace."""
    with pytest.raises(ValueError) as exc:
        _formula("pending * 2")
    assert "'pending' is not a number" in str(exc.value)


def test_formula_never_executes_python():
    """The whole reason this is a parser: expression text comes from a recipe a
    user typed. Nothing here may reach an interpreter."""
    for attack in [
        "__import__('os').system('echo pwned')",
        "().__class__",
        "1 if True else 2",
        "open('x')",
    ]:
        with pytest.raises(ValueError):
            _formula(attack)


def test_formula_step_validates_in_a_recipe():
    recipe = {
        "trigger": {"type": "manual"},
        "conditions": [],
        "steps": [
            {
                "type": "function",
                "function": "formula",
                "args": {"formula": "{{entity.years_experience}} * 3"},
                "save_as": "score",
            }
        ],
    }
    validate_recipe(recipe)  # raises RecipeError if the shape is wrong
