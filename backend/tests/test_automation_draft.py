"""Agent drafting (Module 8b, Task 2). The offline cases monkeypatch the Anthropic
client (the test_chat_tools pattern) so the structured-output + retry logic is
proven without a network call. One gated live case exercises the real model.

Drafting NEVER writes the DB — `draft_recipe` is a pure function of (description,
vocabulary), so the offline cases need no database at all.
"""
import asyncio
import os

import pytest

from app.schemas import Vocabulary, VocabTriggers
from app.services.automations import draft as draft_mod
from app.services.automations.draft import DraftError, draft_recipe
from app.services.automations.recipe import OPERATORS

# A valid recipe the fake model "emits" — uses a real tool/function so validate_recipe
# (which checks the live registry) passes.
_VALID = {
    "name": "Welcome new lead",
    "description": "Text a welcome to new leads",
    "trigger": {"type": "event", "event_type": "lead.created"},
    "conditions": [],
    "steps": [{"type": "function", "function": "now", "save_as": "ts"}],
    "explanation": "When a lead is created, record the time.",
}
_INVALID = {
    "name": "Bad",
    "trigger": {"type": "event", "event_type": "lead.created"},
    "conditions": [],
    "steps": [{"type": "tool", "tool": "no_such_tool_xyz", "input": {}}],
    "explanation": "references a tool that doesn't exist",
}


class _Block:
    def __init__(self, payload):
        self.type = "tool_use"
        self.name = "emit_recipe"
        self.id = "tu1"
        self.input = payload


class _Resp:
    def __init__(self, block):
        self.content = [block]


class _Messages:
    def __init__(self, script, captured):
        self.script = script
        self.captured = captured
        self.i = 0

    async def create(self, **kwargs):
        self.captured.append(kwargs)
        block = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return _Resp(_Block(block))


class _Client:
    def __init__(self, script, captured):
        self.messages = _Messages(script, captured)


def _vocab() -> Vocabulary:
    return Vocabulary(
        triggers=VocabTriggers(event_types=["lead.created"], source_systems=["welcomehome"]),
        tools=[], functions=[], operators=list(OPERATORS),
        generate_models=["default", "fast"], field_roots=["trigger", "entity", "context"],
    )


def _install(monkeypatch, script, captured):
    monkeypatch.setattr(draft_mod, "get_anthropic", lambda: _Client(script, captured))


# ---------------------------------------------------------------------------
def test_valid_draft_returned(monkeypatch):
    captured: list = []
    _install(monkeypatch, [_VALID], captured)
    draft = asyncio.run(draft_recipe("welcome new leads", _vocab()))
    assert draft.name == "Welcome new lead"
    assert draft.trigger["type"] == "event"
    assert len(captured) == 1  # no retry needed


def test_single_retry_on_invalid(monkeypatch):
    captured: list = []
    _install(monkeypatch, [_INVALID, _VALID], captured)
    draft = asyncio.run(draft_recipe("welcome new leads", _vocab()))
    assert draft.name == "Welcome new lead"
    # exactly two calls; the second carries the validation error back to the model
    assert len(captured) == 2
    second_msgs = captured[1]["messages"]
    blob = str(second_msgs)
    assert "no_such_tool_xyz" in blob or "invalid" in blob.lower()


def test_twice_invalid_raises(monkeypatch):
    captured: list = []
    _install(monkeypatch, [_INVALID, _INVALID], captured)
    with pytest.raises(DraftError) as e:
        asyncio.run(draft_recipe("do something impossible", _vocab()))
    assert e.value.detail is not None  # technical reason preserved
    assert len(captured) == 2  # initial + one retry, then give up


# ---------------------------------------------------------------------------
# gated live case — real model drafts a valid recipe
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_live_draft_validates():
    from app.services.tools import all_tools
    from app.services.automations.functions import all_functions
    from app.schemas import VocabFunction, VocabTool
    from app.services.tools.labels import tool_label

    vocab = Vocabulary(
        triggers=VocabTriggers(event_types=["lead.created"], source_systems=["welcomehome"]),
        tools=[VocabTool(name=t.name, label=tool_label(t.name), description=t.description,
                         input_schema=t.input_schema, safe=t.safe) for t in all_tools()],
        functions=[VocabFunction(name=f.name, description=f.description,
                                 input_schema=f.input_schema) for f in all_functions()],
        operators=list(OPERATORS), generate_models=["default", "fast"],
        field_roots=["trigger", "entity", "context"],
    )
    draft = asyncio.run(draft_recipe(
        "When a new lead comes in from WelcomeHome, wait a day, then text them a "
        "personalized welcome message.",
        vocab,
    ))
    # draft_recipe already ran validate_recipe; a returned draft is valid by construction.
    assert draft.trigger["type"] == "event"
    assert len(draft.steps) >= 1
    assert draft.explanation
