"""Tool execution core: the ToolDef/ToolResult contract and the single
`execute_tool` seam.

`execute_tool` is the ONLY way a tool runs — chat now, the MCP server in Module
3, n8n nodes in Module 7 all go through it. It:

  * GATES unsafe (state-changing) tools: instead of running, it queues the call
    to `pending_actions` behind a review `task` (Module 5's approval gate). A
    queued call is a SUCCESS — the model reports it plainly — not an error.
  * runs safe handlers (and approved gated calls) inside a savepoint so a handler
    error can't poison the outer transaction that still has to write the audit row,
  * catches handler errors into a structured `is_error` result, and
  * writes exactly one immutable `events` row per call (CLAUDE.md audit rule):
    an `action.queued` row on the gate path, a `tool.called` row when the handler
    actually runs (directly or post-approval).

Approval resolution re-enters this seam via the `approved_action_id` bypass
(services/approvals.py is the only allowed caller), so every real execution — direct
or approved — writes the same `tool.called` audit row.

The plain-language `summary` is what reaches user-facing surfaces and the audit
line; raw args/data stay in the `events` payload and LangSmith only.
"""
from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

from psycopg.types.json import Json

from ...llm import traceable
from ..events import log_event
from .registry import get_tool

# Invocation context for the running handler: the tenant + how the call arrived
# (chat / mcp / automation). Set by execute_tool around the handler call so a
# handler that needs to write an ADDITIONAL audit/entity event (e.g.
# update_lead_status emitting lead.stage_changed) can attribute it to the real
# caller — which the M7 loop guard depends on (an automation-sourced stage change
# must carry source_system='automation' so it is never re-dispatched). A
# contextvar keeps the handler signature (conn, args) untouched.
_invocation: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "tool_invocation", default=None
)


def current_invocation() -> dict | None:
    """`{"tenant_id", "source_system"}` for the in-flight tool call, or None when
    no tool is executing (a handler called outside execute_tool)."""
    return _invocation.get()


class ToolInputError(Exception):
    """Raised by a handler when the model supplied invalid arguments. Its message
    is surfaced as the tool's plain-language summary (a clean refusal, not a
    stack trace)."""


@dataclass
class ToolResult:
    summary: str  # plain-language, user-facing + audit line
    data: Any  # JSON-serializable; becomes the tool_result block the model reads
    is_error: bool = False


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[Any, dict], Awaitable[ToolResult]]
    safe: bool = True
    # Gated tools supply an async, read-only describer that names entities in plain
    # language ("Update lead 'Margaret Ellison' to contacted", not a UUID) for the
    # task title + action.queued summary. Runs before queueing, in the same tx.
    gate_describe: Callable[[Any, dict], Awaitable[str]] | None = None
    # Argument names a human may edit at approval time (M15a): the office user can
    # fix a typo in a drafted message instead of rejecting and re-asking. Only these
    # keys are accepted from the approve request, and only as non-empty strings —
    # editing an identifier or a recipient would change WHAT was approved, not how
    # it reads. Empty/None means approve-verbatim-or-reject, the default.
    editable_fields: list[str] | None = None


def _jsonable(value: Any) -> Any:
    """Coerce psycopg row values (uuid, datetime, Decimal, arrays, jsonb) into
    JSON-serializable form. Shared by every tool that returns DB rows."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


async def _audit(
    conn,
    tenant_id: str,
    source_system: str,
    name: str,
    args: Any,
    result: ToolResult,
    *,
    pending_action_id: str | None = None,
) -> ToolResult:
    payload: dict = {"tool_name": name, "summary": result.summary, "input": args}
    if pending_action_id is not None:
        payload["pending_action_id"] = pending_action_id
    if result.is_error and isinstance(result.data, dict) and "error" in result.data:
        payload["error"] = result.data["error"]
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="tool.called",
        payload=payload,
    )
    return result


async def _describe_gate(conn, tool: ToolDef, args: dict) -> str:
    """Plain-language description of a gated call for the task title / queued
    summary. Falls back to a generic phrase if the tool has no describer or it
    raises (a describer is read-only convenience, never load-bearing)."""
    if tool.gate_describe is not None:
        try:
            text = await tool.gate_describe(conn, args)
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception:  # noqa: BLE001 — a broken describer must not block queueing
            pass
    return f"Approve: {tool.name}"


async def _queue_gated_call(
    conn, tenant_id: str, source_system: str, tool: ToolDef, args: dict
) -> ToolResult:
    """Queue a state-changing call for human approval instead of running it.

    Write order (all in the caller's transaction): event -> task -> action. The
    action.queued event is written first so the task can point back at it via
    originating_event_id (same trick as connectors/resolution.py). A queued call
    is a SUCCESS: the model tells the user a task was created; is_error stays False.
    """
    describe = await _describe_gate(conn, tool, args)
    event_id = await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="action.queued",
        payload={
            "summary": f"Queued for approval: {describe}",
            "tool_name": tool.name,
            "input": args,
        },
    )

    async with conn.cursor() as cur:
        await cur.execute(
            """insert into public.tasks
                 (tenant_id, title, description, priority, originating_event_id)
               values (%s, %s, %s, 'high', %s)
               returning id""",
            (
                tenant_id,
                f"Approve: {describe}",
                (
                    f"The {source_system} assistant requested an action that needs "
                    f"your approval before it runs: {describe}."
                ),
                event_id,
            ),
        )
        task_id = str((await cur.fetchone())[0])
        await cur.execute(
            """insert into public.pending_actions
                 (tenant_id, task_id, tool_name, tool_input, source_system)
               values (%s, %s, %s, %s, %s)
               returning id""",
            (tenant_id, task_id, tool.name, Json(args), source_system),
        )
        action_id = str((await cur.fetchone())[0])

    return ToolResult(
        f"Queued for approval: {describe} (task created).",
        {"status": "queued", "task_id": task_id, "pending_action_id": action_id},
        is_error=False,
    )


@traceable(run_type="tool")
async def execute_tool(
    conn,
    tenant_id: str,
    name: str,
    args: Any,
    *,
    source_system: str = "chat",
    approved_action_id: str | None = None,
) -> ToolResult:
    """Validate, gate, run, and audit a single tool call. Never raises for a tool
    problem — every outcome (unknown tool, bad args, queued, handler error,
    success) resolves to a ToolResult and writes one `events` row.

    `approved_action_id` is the approval bypass: when set (only services/approvals.py
    passes it), the gate is skipped and the tool.called audit payload carries the
    pending_action_id, so an approved run audits identically to a direct one.
    """
    tool = get_tool(name)
    if tool is None:
        return await _audit(
            conn,
            tenant_id,
            source_system,
            name,
            args if isinstance(args, dict) else {},
            ToolResult(f"Unknown tool '{name}'.", {"error": f"unknown tool: {name}"}, True),
        )
    if not isinstance(args, dict):
        return await _audit(
            conn,
            tenant_id,
            source_system,
            name,
            {},
            ToolResult(
                f"'{name}' received invalid arguments.",
                {"error": "arguments must be a JSON object"},
                True,
            ),
        )
    if not tool.safe and approved_action_id is None:
        # Gate path: queue for approval, write exactly the action.queued event
        # (no tool.called — the handler has not run), return a non-error result.
        return await _queue_gated_call(conn, tenant_id, source_system, tool, args)
    token = _invocation.set({"tenant_id": tenant_id, "source_system": source_system})
    try:
        # Savepoint: a handler SQL error rolls back to here, leaving the outer
        # transaction alive to write the audit row below.
        async with conn.transaction():
            result = await tool.handler(conn, args)
    except ToolInputError as exc:
        result = ToolResult(str(exc), {"error": str(exc)}, True)
    except Exception as exc:  # noqa: BLE001 — any handler failure becomes an audited error
        result = ToolResult(
            f"'{name}' could not be completed.", {"error": str(exc)}, True
        )
    finally:
        _invocation.reset(token)
    return await _audit(
        conn, tenant_id, source_system, name, args, result,
        pending_action_id=approved_action_id,
    )
