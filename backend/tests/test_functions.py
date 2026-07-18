"""Automation functions (WS3) — the weighted_score computation.

Pure functions of (conn, args); weighted_score never touches the DB, so these run
offline (no NEXUS_APP_DB_URL gate). Also proves a recipe using it validates.
"""
import asyncio

import pytest

from app.services.automations.functions import get_function
from app.services.automations.recipe import RecipeError, validate_recipe


def _score(args):
    fn = get_function("weighted_score")
    assert fn is not None
    return asyncio.run(fn.handler(None, args))


def test_weighted_score_basic():
    # 3*10 + (-1)*4 = 26
    out = _score({"weights": {"experience": 3, "distance": -1},
                  "inputs": {"experience": 10, "distance": 4}})
    assert out == 26


def test_weighted_score_coerces_and_skips_missing():
    # numeric strings coerce; a missing input contributes nothing (only experience counts)
    out = _score({"weights": {"experience": 2, "tenure": 5},
                  "inputs": {"experience": "7"}})
    assert out == 14


def test_weighted_score_non_numeric_input_errors():
    with pytest.raises(ValueError):
        _score({"weights": {"x": 1}, "inputs": {"x": "not-a-number"}})


def test_weighted_score_validates_in_recipe():
    recipe = validate_recipe({
        "trigger": {"type": "manual"},
        "steps": [
            {"type": "function", "function": "weighted_score",
             "args": {"weights": {"exp": 3}, "inputs": {"exp": "{{entity.years}}"}},
             "save_as": "score"},
            {"type": "condition",
             "conditions": [{"field": "context.score", "op": "gte", "value": 70}]},
        ],
    })
    assert recipe.steps[0].function == "weighted_score"


def test_unknown_function_rejected():
    with pytest.raises(RecipeError):
        validate_recipe({
            "trigger": {"type": "manual"},
            "steps": [{"type": "function", "function": "no_such_fn", "args": {}}],
        })
