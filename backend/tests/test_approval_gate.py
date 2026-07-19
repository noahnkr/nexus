"""Approval gate + approvals engine (Module 5a, Tasks 2 & 3), gated on
NEXUS_APP_DB_URL.

Exercises the gate path and the approve/reject state machine with throwaway tools
registered only inside the test (never in the app registry), so the behavior is
proven before the real write tools exist. Tasks/pending_actions created here are
cleaned up; events are immutable, so assertions scope to the unique tool name.
"""
import asyncio
import uuid

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


def _register_tool(name, handler, *, safe=False, describe=None):
    from app.services.tools import ToolDef
    from app.services.tools.registry import register

    register(ToolDef(
        name=name,
        description="throwaway test tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        safe=safe,
        gate_describe=describe,
    ))


def _unregister(*names):
    from app.services.tools.registry import _REGISTRY

    for n in names:
        _REGISTRY.pop(n, None)


async def _cleanup(conn, task_ids):
    for tid in task_ids:
        await conn.execute("delete from public.pending_actions where task_id=%s", (tid,))
        await conn.execute("delete from public.tasks where id=%s", (tid,))


async def _events_for(conn, tool_name):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select event_type, payload from public.events "
            "where payload->>'tool_name'=%s order by created_at",
            (tool_name,),
        )
        return await cur.fetchall()


async def _task(conn, task_id):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.tasks where id=%s", (task_id,))
        return await cur.fetchone()


async def _action(conn, action_id):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.pending_actions where id=%s", (action_id,))
        return await cur.fetchone()


# ---------------------------------------------------------------------------
# Task 2 — gate path: a gated call queues (no execution, non-error result)
# ---------------------------------------------------------------------------
async def _gate_scenario():
    from app import db
    from app.services.tools import ToolResult, execute_tool

    sfx = uuid.uuid4().hex[:8]
    name = f"t_gate_{sfx}"
    state = {"ran": 0}

    async def handler(conn, args):
        state["ran"] += 1
        return ToolResult("should-not-run", {})

    async def describe(conn, args):
        return "Do the important thing"

    _register_tool(name, handler, safe=False, describe=describe)
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            result = await execute_tool(conn, DEMO_TENANT, name, {"x": 1}, source_system="chat")
            task = await _task(conn, result.data["task_id"])
            action = await _action(conn, result.data["pending_action_id"])
            events = await _events_for(conn, name)
            await _cleanup(conn, [result.data["task_id"]])
        return result, task, action, events, state
    finally:
        _unregister(name)
        await db.close_pool()


def test_gated_call_queues():
    result, task, action, events, state = asyncio.run(_gate_scenario())

    # Non-error queued result carrying the task + action ids.
    assert result.is_error is False
    assert result.data["status"] == "queued"
    assert result.data["task_id"] and result.data["pending_action_id"]

    # Exactly one events row for this tool — the action.queued row, with a summary.
    assert len(events) == 1
    assert events[0]["event_type"] == "action.queued"
    assert "Do the important thing" in events[0]["payload"]["summary"]

    # Task: high priority, plain title from gate_describe, linked to the event.
    assert task["priority"] == "high"
    assert "Do the important thing" in task["title"]
    assert task["originating_event_id"] is not None
    assert task["status"] == "pending"

    # Action: pending, args preserved, source recorded.
    assert action["status"] == "pending"
    assert action["tool_input"] == {"x": 1}
    assert action["source_system"] == "chat"

    # The handler did NOT run.
    assert state["ran"] == 0


# ---------------------------------------------------------------------------
# Task 3 — approvals engine: approve / reject / double-resolve / failing handler
# ---------------------------------------------------------------------------
async def _approve_scenario():
    from app import db
    from app.services.approvals import (
        ActionAlreadyResolved,
        approve_action,
    )
    from app.services.tools import ToolResult, execute_tool

    sfx = uuid.uuid4().hex[:8]
    name = f"t_appr_{sfx}"
    state = {"ran": 0}

    async def handler(conn, args):
        state["ran"] += 1
        return ToolResult("did the thing", {"ok": True})

    _register_tool(name, handler, safe=False)
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            queued = await execute_tool(conn, DEMO_TENANT, name, {}, source_system="mcp")
            action_id = queued.data["pending_action_id"]
            task_id = queued.data["task_id"]

            await approve_action(conn, DEMO_TENANT, action_id, resolved_by="tester")
            action = await _action(conn, action_id)
            task = await _task(conn, task_id)
            events = await _events_for(conn, name)

            # Double-resolve is rejected and leaves the row unchanged.
            double = None
            try:
                await approve_action(conn, DEMO_TENANT, action_id)
            except ActionAlreadyResolved:
                double = "already"
            action_after = await _action(conn, action_id)

            await _cleanup(conn, [task_id])
        return state, action, task, events, double, action_after
    finally:
        _unregister(name)
        await db.close_pool()


def test_approve_executes():
    state, action, task, events, double, action_after = asyncio.run(_approve_scenario())

    assert state["ran"] == 1  # handler ran exactly once
    assert action["status"] == "executed"
    assert action["result"]["summary"] == "did the thing"
    assert action["resolved_at"] is not None
    assert action["resolved_by"] == "tester"
    assert task["status"] == "done"
    assert task["resolved_at"] is not None

    types = [e["event_type"] for e in events]
    assert "action.approved" in types
    tool_called = [e for e in events if e["event_type"] == "tool.called"]
    assert len(tool_called) == 1
    assert tool_called[0]["payload"]["pending_action_id"] == str(action["id"])

    # Double-resolve raised and did not change the executed row.
    assert double == "already"
    assert action_after["status"] == "executed"


async def _reject_scenario():
    from app import db
    from app.services.approvals import reject_action
    from app.services.tools import ToolResult, execute_tool

    sfx = uuid.uuid4().hex[:8]
    name = f"t_rej_{sfx}"
    state = {"ran": 0}

    async def handler(conn, args):
        state["ran"] += 1
        return ToolResult("nope", {})

    _register_tool(name, handler, safe=False)
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            queued = await execute_tool(conn, DEMO_TENANT, name, {}, source_system="chat")
            action_id = queued.data["pending_action_id"]
            task_id = queued.data["task_id"]
            await reject_action(conn, DEMO_TENANT, action_id, note="not appropriate")
            action = await _action(conn, action_id)
            task = await _task(conn, task_id)
            events = await _events_for(conn, name)
            await _cleanup(conn, [task_id])
        return state, action, task, events
    finally:
        _unregister(name)
        await db.close_pool()


def test_reject_cancels():
    state, action, task, events = asyncio.run(_reject_scenario())

    assert state["ran"] == 0  # never executed
    assert action["status"] == "rejected"
    assert action["result"]["summary"] == "not appropriate"
    assert task["status"] == "cancelled"
    assert task["resolved_at"] is not None
    types = [e["event_type"] for e in events]
    assert "action.rejected" in types
    assert "tool.called" not in types


async def _failing_scenario():
    from app import db
    from app.services.approvals import approve_action
    from app.services.tools import execute_tool

    sfx = uuid.uuid4().hex[:8]
    name = f"t_fail_{sfx}"

    async def handler(conn, args):
        raise RuntimeError("boom in handler")

    _register_tool(name, handler, safe=False)
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            queued = await execute_tool(conn, DEMO_TENANT, name, {}, source_system="chat")
            action_id = queued.data["pending_action_id"]
            task_id = queued.data["task_id"]
            await approve_action(conn, DEMO_TENANT, action_id)
            action = await _action(conn, action_id)
            task = await _task(conn, task_id)
            events = await _events_for(conn, name)
            await _cleanup(conn, [task_id])
        return action, task, events
    finally:
        _unregister(name)
        await db.close_pool()


def test_failing_handler_marks_failed():
    action, task, events = asyncio.run(_failing_scenario())

    assert action["status"] == "failed"
    assert "error" in action["result"]
    # Task stays pending so a human can decide (cancel or re-ask).
    assert task["status"] == "pending"
    # action.approved is still written, recording the failed outcome.
    approved = [e for e in events if e["event_type"] == "action.approved"]
    assert len(approved) == 1
    assert approved[0]["payload"]["outcome"] == "failed"


# ---------------------------------------------------------------------------
# Module 15a — approve with edits (editable_fields seam)
# ---------------------------------------------------------------------------
async def _edit_scenario():
    """Queue a send_sms-shaped gated tool, then approve with a reworded body."""
    from app import db
    from app.services.approvals import approve_action
    from app.services.tools import ToolResult, execute_tool

    sfx = uuid.uuid4().hex[:8]
    name = f"t_edit_{sfx}"
    seen = {}

    async def handler(conn, args):
        seen.update(args)
        return ToolResult(f"[placeholder] Would send SMS to {args['to']}: “{args['body']}”", {})

    from app.services.tools import ToolDef
    from app.services.tools.registry import register

    register(ToolDef(
        name=name,
        description="throwaway editable test tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        safe=False,
        editable_fields=["body"],
    ))

    draft = {"to": "+16195550101", "body": "Hi Margret, can you cover Tuesday?"}
    fixed = "Hi Margaret, can you cover Tuesday?"

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            queued = await execute_tool(conn, DEMO_TENANT, name, draft, source_system="chat")
            action_id = queued.data["pending_action_id"]
            task_id = queued.data["task_id"]

            await approve_action(
                conn, DEMO_TENANT, action_id,
                resolved_by="tester", edited_input={"body": fixed},
            )
            action = await _action(conn, action_id)
            events = await _events_for(conn, name)
            await _cleanup(conn, [task_id])
        return seen, action, events, draft, fixed
    finally:
        _unregister(name)
        await db.close_pool()


def test_approve_with_edits_executes_edited_input():
    seen, action, events, draft, fixed = asyncio.run(_edit_scenario())

    # The handler ran with the corrected text — and only the editable key changed.
    assert seen["body"] == fixed
    assert seen["to"] == draft["to"]

    # The stored input is the final text, so the row and the audit agree.
    assert action["status"] == "executed"
    assert action["tool_input"]["body"] == fixed
    assert action["result"]["edited"] is True
    assert action["result"]["edited_fields"] == ["body"]
    assert fixed in action["result"]["summary"]

    # action.approved records WHAT changed and what the agent originally drafted.
    approved = [e for e in events if e["event_type"] == "action.approved"]
    assert len(approved) == 1
    payload = approved[0]["payload"]
    assert payload["edited"] is True
    assert payload["edited_fields"] == ["body"]
    assert payload["original_input"]["body"] == draft["body"]
    assert "edits" in payload["summary"]

    # The tool.called audit row carries the executed (edited) input, not the draft.
    called = [e for e in events if e["event_type"] == "tool.called"]
    assert len(called) == 1
    assert called[0]["payload"]["input"]["body"] == fixed


async def _noop_edit_scenario():
    """Re-sending the unchanged input is a plain approval, not an 'edited' one."""
    from app import db
    from app.services.approvals import approve_action
    from app.services.tools import ToolResult, execute_tool

    sfx = uuid.uuid4().hex[:8]
    name = f"t_noedit_{sfx}"

    async def handler(conn, args):
        return ToolResult("sent", {})

    _register_tool(name, handler, safe=False)
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            queued = await execute_tool(
                conn, DEMO_TENANT, name, {"body": "unchanged"}, source_system="chat"
            )
            await approve_action(
                conn, DEMO_TENANT, queued.data["pending_action_id"],
                edited_input={"body": "unchanged"},
            )
            action = await _action(conn, queued.data["pending_action_id"])
            events = await _events_for(conn, name)
            await _cleanup(conn, [queued.data["task_id"]])
        return action, events
    finally:
        _unregister(name)
        await db.close_pool()


def test_unchanged_input_is_not_recorded_as_an_edit():
    action, events = asyncio.run(_noop_edit_scenario())

    assert action["status"] == "executed"
    assert "edited" not in action["result"]
    approved = [e for e in events if e["event_type"] == "action.approved"][0]
    assert "edited" not in approved["payload"]
    assert "original_input" not in approved["payload"]
