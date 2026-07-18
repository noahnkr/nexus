"""Recipe vocabulary, templates, and function registry (Module 7a, Task 2).

Fully offline — no DB, no network. Validation composes the tool registry (bootstrapped
by importing app.services.tools) so `tool`/`function` existence checks are real.
"""
import asyncio

import pytest

from app.services.automations.functions import get_function
from app.services.automations.recipe import (
    MAX_STEPS,
    RecipeError,
    validate_recipe,
)
from app.services.automations.templates import TemplateError, render


# ---------------------------------------------------------------------------
# trigger + step shapes round-trip
# ---------------------------------------------------------------------------
def test_event_trigger_roundtrips():
    r = validate_recipe({
        "trigger": {"type": "event", "event_type": "lead.created", "source_system": "welcomehome"},
        "steps": [],
    })
    assert r.trigger.type == "event"
    assert r.trigger.event_type == "lead.created"
    assert r.trigger.source_system == "welcomehome"


def test_cron_trigger_roundtrips():
    r = validate_recipe({"trigger": {"type": "cron", "expression": "0 9 * * 1"}, "steps": []})
    assert r.trigger.type == "cron"
    assert r.trigger.expression == "0 9 * * 1"


def test_manual_trigger_roundtrips():
    r = validate_recipe({"trigger": {"type": "manual"}, "steps": []})
    assert r.trigger.type == "manual"


def test_all_step_types_roundtrip():
    r = validate_recipe({
        "trigger": {"type": "manual"},
        "conditions": [{"field": "entity.status", "op": "eq", "value": "new"}],
        "steps": [
            {"type": "function", "function": "now", "save_as": "ts"},
            {"type": "generate", "prompt": "Say hi to {{entity.name}}", "save_as": "msg", "model": "fast"},
            {"type": "condition", "conditions": [{"field": "context.msg", "op": "exists"}]},
            {"type": "delay", "hours": 2},
            {"type": "tool", "tool": "send_sms", "input": {"to": "{{entity.phone}}", "body": "{{context.msg}}"}, "save_as": "sent"},
        ],
    })
    assert [s.type for s in r.steps] == ["function", "generate", "condition", "delay", "tool"]
    assert r.steps[1].model == "fast"


# ---------------------------------------------------------------------------
# Caregivers stage-sequence convention (Module 10b) — the recipe the constrained
# builder assembles for a stage must validate, with the managed condition present.
# ---------------------------------------------------------------------------
def test_caregivers_rejected_sequence_convention():
    # `rejected` stage: applicant.stage_changed + managed to=rejected, then a
    # generate step feeding a gated send_email (the PRD's denied-email use case).
    r = validate_recipe({
        "trigger": {"type": "event", "event_type": "applicant.stage_changed"},
        "conditions": [{"field": "trigger.payload.to", "op": "eq", "value": "rejected"}],
        "steps": [
            {"type": "generate", "prompt": "Write a kind rejection note to {{entity.name}}.",
             "save_as": "msg", "model": "fast"},
            {"type": "tool", "tool": "send_email",
             "input": {"to": "{{entity.email}}", "subject": "Update on your application",
                       "body": "{{context.msg}}"}, "save_as": "sent"},
        ],
    })
    assert r.trigger.type == "event"
    assert r.trigger.event_type == "applicant.stage_changed"
    # the managed condition (payload.to = the stage) survives validation
    assert r.conditions[0].field == "trigger.payload.to"
    assert r.conditions[0].value == "rejected"
    assert [s.type for s in r.steps] == ["generate", "tool"]


def test_caregivers_applied_sequence_convention():
    # `applied` stage IS creation -> applicant.created, no managed condition.
    r = validate_recipe({
        "trigger": {"type": "event", "event_type": "applicant.created"},
        "steps": [{"type": "tool", "tool": "send_email",
                   "input": {"to": "{{entity.email}}", "subject": "Thanks for applying", "body": "Hi"},
                   "save_as": "sent"}],
    })
    assert r.trigger.event_type == "applicant.created"
    assert r.conditions == []


# ---------------------------------------------------------------------------
# bad shapes -> plain-language RecipeError
# ---------------------------------------------------------------------------
def test_unknown_tool_rejected():
    with pytest.raises(RecipeError) as e:
        validate_recipe({
            "trigger": {"type": "manual"},
            "steps": [{"type": "tool", "tool": "nonexistent_tool", "input": {}}],
        })
    assert "nonexistent_tool" in str(e.value)


def test_unknown_function_rejected():
    with pytest.raises(RecipeError) as e:
        validate_recipe({
            "trigger": {"type": "manual"},
            "steps": [{"type": "function", "function": "bogus_fn"}],
        })
    assert "bogus_fn" in str(e.value)


def test_zero_delay_rejected():
    with pytest.raises(RecipeError):
        validate_recipe({"trigger": {"type": "manual"},
                         "steps": [{"type": "delay", "minutes": 0}]})


def test_negative_delay_rejected():
    with pytest.raises(RecipeError):
        validate_recipe({"trigger": {"type": "manual"},
                         "steps": [{"type": "delay", "days": -1}]})


def test_delay_needs_exactly_one_unit():
    with pytest.raises(RecipeError):
        validate_recipe({"trigger": {"type": "manual"},
                         "steps": [{"type": "delay", "minutes": 5, "hours": 1}]})
    with pytest.raises(RecipeError):
        validate_recipe({"trigger": {"type": "manual"}, "steps": [{"type": "delay"}]})


def test_too_many_steps_rejected():
    steps = [{"type": "function", "function": "now", "save_as": f"s{i}"} for i in range(MAX_STEPS + 1)]
    with pytest.raises(RecipeError) as e:
        validate_recipe({"trigger": {"type": "manual"}, "steps": steps})
    assert str(MAX_STEPS) in str(e.value)


def test_bad_cron_rejected():
    with pytest.raises(RecipeError):
        validate_recipe({"trigger": {"type": "cron", "expression": "not a cron"}, "steps": []})
    with pytest.raises(RecipeError):
        validate_recipe({"trigger": {"type": "cron", "expression": "0 9 * *"}, "steps": []})  # 4 fields


def test_bad_operator_rejected():
    with pytest.raises(RecipeError) as e:
        validate_recipe({
            "trigger": {"type": "manual"},
            "conditions": [{"field": "entity.status", "op": "matches", "value": "x"}],
            "steps": [],
        })
    assert "matches" in str(e.value)


def test_unknown_trigger_type_rejected():
    with pytest.raises(RecipeError):
        validate_recipe({"trigger": {"type": "sometime"}, "steps": []})


def test_comparison_condition_needs_value():
    with pytest.raises(RecipeError):
        validate_recipe({
            "trigger": {"type": "manual"},
            "conditions": [{"field": "entity.score", "op": "gt"}],
            "steps": [],
        })


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def test_render_nested_path():
    scope = {"trigger": {"payload": {"lead": {"name": "Margaret"}}}, "entity": {}, "context": {}}
    assert render("Hi {{trigger.payload.lead.name}}!", scope) == "Hi Margaret!"


def test_render_full_value_preserves_type():
    scope = {"trigger": {}, "entity": {}, "context": {"score": 42, "flag": True, "obj": {"a": 1}}}
    assert render("{{context.score}}", scope) == 42
    assert render("{{context.flag}}", scope) is True
    assert render("{{context.obj}}", scope) == {"a": 1}


def test_render_deep_structure():
    scope = {"trigger": {"payload": {"name": "Meg", "phone": "+16195550100"}}, "entity": {}, "context": {}}
    out = render(
        {"to": "{{trigger.payload.phone}}", "lines": ["Hello {{trigger.payload.name}}", "Bye"]},
        scope,
    )
    assert out == {"to": "+16195550100", "lines": ["Hello Meg", "Bye"]}


def test_render_missing_path_raises():
    scope = {"trigger": {"payload": {}}, "entity": {}, "context": {}}
    with pytest.raises(TemplateError):
        render("Hi {{trigger.payload.name}}", scope)
    with pytest.raises(TemplateError):
        render("{{context.nope}}", scope)


# ---------------------------------------------------------------------------
# functions
# ---------------------------------------------------------------------------
def test_days_since_computes():
    fn = get_function("days_since")
    # 10 days ago -> 10 (allow the clock ticking during the test).
    from datetime import datetime, timedelta, timezone

    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    result = asyncio.run(fn.handler(None, {"date": ten_days_ago}))
    assert result in (9, 10)


def test_now_is_iso():
    fn = get_function("now")
    result = asyncio.run(fn.handler(None, {}))
    from datetime import datetime

    datetime.fromisoformat(result)  # parses without raising
