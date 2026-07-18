"""Agent-drafted recipes (Module 8b) — a natural-language description becomes a
validated, UNSAVED recipe for human review in the builder.

Structured output per the CLAUDE.md Pydantic rule: a forced tool-use call
(`emit_recipe`) whose input is parsed into `AutomationDraft`, then run through the
same `validate_recipe()` the create path uses. On a validation failure the error is
fed back for exactly one retry; still failing raises `DraftError` (the router turns
it into a plain 422 + technical detail).

This module NEVER writes the database (user-locked): the standard validated create
path is the only writer of `automations` rows. Traced as an `automation_draft` chain
span so a drafting call is visible in LangSmith alongside `automation_run`.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from ...config import settings
from ...llm import get_anthropic, traceable
from ...schemas import AutomationDraft, Vocabulary
from .recipe import RecipeError, validate_recipe

_EMIT_TOOL = "emit_recipe"
_MAX_TOKENS = 1500


class DraftError(Exception):
    """Drafting failed after the retry. `detail` carries the technical reason for
    the payload; the message stays plain for the user."""

    def __init__(self, message: str, *, detail: str | None = None):
        super().__init__(message)
        self.detail = detail


def _emit_tool_schema() -> dict:
    return {
        "name": _EMIT_TOOL,
        "description": "Return the drafted automation recipe for the user to review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short plain-language name."},
                "description": {"type": "string", "description": "One-line summary."},
                "trigger": {"type": "object", "description": "WHEN — the trigger object."},
                "conditions": {
                    "type": "array", "items": {"type": "object"},
                    "description": "IF — declarative field comparisons (may be empty).",
                },
                "steps": {
                    "type": "array", "items": {"type": "object"},
                    "description": "THEN — the ordered steps.",
                },
                "explanation": {
                    "type": "string",
                    "description": "Plain-language explanation of what this automation does.",
                },
            },
            "required": ["name", "trigger", "steps", "explanation"],
        },
    }


def _catalog_prompt(fc) -> str:
    """Compact, paths-only rendering of the field catalog (Module 11a) so the agent
    references paths that actually resolve — per-entity `entity.*` fields, which
    record each trigger is about, and observed `trigger.payload.*` keys per event
    type. No labels: the agent needs the paths, not the plain-language names."""
    lines: list[str] = []
    if fc.entities:
        lines.append("RECORD TYPES AND THEIR FIELDS (entity.*):")
        for ent in fc.entities.values():
            lines.append(f"- {ent.label}: {', '.join(f.path for f in ent.fields)}")
    if fc.event_entity:
        by_entity: dict[str, list[str]] = {}
        for ev, et in fc.event_entity.items():
            by_entity.setdefault(et, []).append(ev)
        lines.append("WHICH RECORD EACH TRIGGER IS ABOUT:")
        for et, evs in by_entity.items():
            label = fc.entities[et].label if et in fc.entities else et
            lines.append(f"- {', '.join(sorted(evs))} -> {label}")
        lines.append(
            "(cron and manual triggers are NOT about a record — do not use entity.* there)"
        )
    if fc.payload_by_event:
        lines.append("PAYLOAD FIELDS SEEN PER EVENT (trigger.payload.*):")
        for ev in sorted(fc.payload_by_event):
            paths = ", ".join(f.path for f in fc.payload_by_event[ev])
            lines.append(f"- {ev}: {paths}")
    return "\n".join(lines)


def _system_prompt(vocab: Vocabulary) -> str:
    tools = [
        {"name": t.name, "label": t.label, "description": t.description,
         "input_schema": t.input_schema, "requires_approval": not t.safe}
        for t in vocab.tools
    ]
    functions = [
        {"name": f.name, "description": f.description, "input_schema": f.input_schema}
        for f in vocab.functions
    ]
    catalog = _catalog_prompt(vocab.field_catalog)
    return (
        "You draft business automations as declarative WHEN/IF/THEN recipes. You "
        "will call the `emit_recipe` tool exactly once with a complete recipe.\n\n"
        "RECIPE SHAPE:\n"
        "- trigger (WHEN), one of:\n"
        '  {\"type\":\"event\",\"event_type\":\"<type>\",\"source_system\":\"<src>\"?}\n'
        '  {\"type\":\"cron\",\"expression\":\"<5-field cron>\"}\n'
        '  {\"type\":\"manual\"}\n'
        "- conditions (IF): a list (AND) of "
        '{\"field\":\"<path>\",\"op\":\"<op>\",\"value\":<v>}. '
        "Field paths root at trigger. / entity. / context. Operators: "
        f"{', '.join(vocab.operators)}. exists/not_exists take no value.\n"
        "- steps (THEN), in order, each one of:\n"
        '  {\"type\":\"tool\",\"tool\":\"<name>\",\"input\":{...},\"save_as\":\"<key>\"?}\n'
        '  {\"type\":\"delay\",\"minutes|hours|days\":<int>=1}  (exactly one unit)\n'
        '  {\"type\":\"condition\",\"conditions\":[...],\"on_false\":\"stop\"}\n'
        '  {\"type\":\"function\",\"function\":\"<name>\",\"args\":{...},\"save_as\":\"<key>\"?}\n'
        '  {\"type\":\"generate\",\"prompt\":\"<text>\",\"save_as\":\"<key>\",\"model\":\"default|fast\"?}\n\n'
        "TEMPLATING: any string in a tool input, function args, or generate prompt "
        "may contain {{path}} references resolved from trigger/entity/context, e.g. "
        "{{trigger.payload.name}}, {{entity.phone}}, {{context.<save_as>}}. Use ONLY "
        "paths that exist for the trigger you choose (see AVAILABLE FIELDS).\n\n"
        f"AVAILABLE FIELDS:\n{catalog}\n\n"
        "RULES:\n"
        "- Use ONLY these event types: "
        f"{', '.join(vocab.triggers.event_types) or '(none observed yet)'}.\n"
        "- Use ONLY these source systems (or omit): "
        f"{', '.join(vocab.triggers.source_systems) or '(none)'}.\n"
        "- Use ONLY these tools (requires_approval=true means it pauses for human "
        "approval — prefer these for any outbound message like SMS/email):\n"
        f"{json.dumps(tools)}\n"
        "- Use ONLY these functions:\n"
        f"{json.dumps(functions)}\n"
        "- generate models: default (higher quality) or fast (cheap, for short text).\n"
        "- Give the automation a clear plain-language name and a one-line description. "
        "The explanation field describes, in plain language a non-technical user "
        "understands, what the automation will do.\n"
        "- Conditions are declarative comparisons only — never put logic in a step "
        "other than a function computing a value into context.\n"
    )


def _tool_use_block(response):
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == _EMIT_TOOL:
            return block
    return None


@traceable(run_type="chain", name="automation_draft")
async def draft_recipe(description: str, vocabulary: Vocabulary) -> AutomationDraft:
    client = get_anthropic()
    system = _system_prompt(vocabulary)
    messages: list[dict] = [
        {"role": "user", "content": f"Automate this: {description.strip()}"}
    ]

    last_error: str | None = None
    for attempt in range(2):  # initial + one retry
        response = await client.messages.create(
            model=settings.chat_model,
            max_tokens=_MAX_TOKENS,
            system=system,
            tools=[_emit_tool_schema()],
            tool_choice={"type": "tool", "name": _EMIT_TOOL},
            messages=messages,
        )
        block = _tool_use_block(response)
        if block is not None:
            try:
                draft = AutomationDraft.model_validate(block.input)
                validate_recipe({
                    "trigger": draft.trigger,
                    "conditions": draft.conditions,
                    "steps": draft.steps,
                })
                return draft
            except (ValidationError, RecipeError) as exc:
                last_error = str(exc)
        else:
            last_error = "the assistant did not return a recipe"

        if attempt == 0:
            # Feed the error back for a single retry.
            if block is not None:
                messages.append({
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": block.id,
                                 "name": _EMIT_TOOL, "input": block.input}],
                })
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": f"That recipe was invalid: {last_error}. "
                                   "Fix it and call emit_recipe again.",
                    }],
                })
            else:
                messages.append({
                    "role": "user",
                    "content": "Please call emit_recipe with a valid recipe.",
                })

    raise DraftError(
        "Couldn't draft that automation — try describing it differently.",
        detail=last_error,
    )
