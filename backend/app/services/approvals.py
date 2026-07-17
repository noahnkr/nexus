"""Approval engine — resolve a queued `pending_action` (approve or reject).

This is the ONLY module allowed to pass `execute_tool`'s `approved_action_id`
bypass (CLAUDE.md seam rule): approving runs the queued tool through the same
audited `execute_tool` seam, so an approved run writes the identical `tool.called`
audit row a direct call would, additionally tagged with the `pending_action_id`.

State machine (locked, D5/D6):
  * The action row is locked `for update`; a missing id raises `ActionNotFound`,
    an already-resolved one raises `ActionAlreadyResolved` (router -> 404 / 409).
  * approve: status -> approved, execute; on success -> executed + task done; on a
    handler error -> failed + task STAYS pending (a human decides: cancel or re-ask).
  * reject: status -> rejected, task -> cancelled. No execution.
  * Resolving an action drives its task one way only; the task never drives the
    action (a task PATCH is 409'd while an action is still pending — the router).

Execution is synchronous in-request: at this scale a tool handler is fast, so no
background queue. Every resolution writes an `action.approved` / `action.rejected`
event linked to the task, with a plain-language `payload.summary`.
"""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Json

from .automations import cancel_after_rejection, resume_after_approval
from .events import log_event
from .tools import execute_tool


class ActionNotFound(Exception):
    """No pending_action with that id is visible to this tenant."""


class ActionAlreadyResolved(Exception):
    """The action is no longer `pending` (already approved/rejected/executed/failed)."""


async def _lock_action(conn, action_id: str) -> dict:
    """Lock the action row and load it with its task title. RLS scopes visibility;
    the `for update` serializes concurrent approve/reject on the same action."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select pa.id, pa.task_id, pa.tool_name, pa.tool_input, pa.status,
                      pa.source_system, pa.automation_run_id, t.title as task_title
                 from public.pending_actions pa
                 join public.tasks t on t.id = pa.task_id
                where pa.id = %s
                for update of pa""",
            (action_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise ActionNotFound(action_id)
    if row["status"] != "pending":
        raise ActionAlreadyResolved(action_id)
    return row


async def approve_action(
    conn, tenant_id: str, action_id: str, *, resolved_by: str | None = None
) -> str:
    """Approve and execute a queued action. Returns the task id (the router
    refetches the refreshed action + task for its response)."""
    action = await _lock_action(conn, action_id)
    task_id = str(action["task_id"])

    # Mark approved before running, then execute through the seam with the bypass.
    await conn.execute(
        "update public.pending_actions set status='approved' where id=%s", (action_id,)
    )
    result = await execute_tool(
        conn,
        tenant_id,
        action["tool_name"],
        action["tool_input"],
        source_system=action["source_system"],
        approved_action_id=action_id,
    )

    outcome = {"summary": result.summary}
    if result.is_error:
        final_status = "failed"
        if isinstance(result.data, dict) and "error" in result.data:
            outcome["error"] = result.data["error"]
    else:
        final_status = "executed"

    await conn.execute(
        """update public.pending_actions
              set status=%s, result=%s, resolved_at=now(), resolved_by=%s
            where id=%s""",
        (final_status, Json(outcome), resolved_by, action_id),
    )
    # On success the task is done; on failure it stays pending (visible for a human).
    if final_status == "executed":
        await conn.execute(
            "update public.tasks set status='done', resolved_at=now() where id=%s",
            (task_id,),
        )

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=action["source_system"],
        event_type="action.approved",
        entity_type="task",
        entity_id=task_id,
        payload={
            "summary": f"Approved: {action['task_title']} — {result.summary}",
            "pending_action_id": action_id,
            "tool_name": action["tool_name"],
            "outcome": final_status,
        },
    )

    # If this action gated an automation step (Module 7b), resume the paused run in
    # the same request: on success the run continues from the gated step; on a
    # post-approval handler failure the run fails with NO second review task (the
    # failed action's task already stays pending — one human surface per failure).
    if action.get("automation_run_id") is not None:
        error = None
        if result.is_error and isinstance(result.data, dict):
            error = result.data.get("error")
        await resume_after_approval(
            conn, tenant_id, str(action["automation_run_id"]),
            tool_result=result.data if isinstance(result.data, dict) else {},
            is_error=result.is_error, error=error,
        )

    return task_id


async def reject_action(
    conn,
    tenant_id: str,
    action_id: str,
    *,
    resolved_by: str | None = None,
    note: str | None = None,
) -> str:
    """Reject a queued action (no execution) and cancel its task. Returns task id."""
    action = await _lock_action(conn, action_id)
    task_id = str(action["task_id"])
    summary = note.strip() if isinstance(note, str) and note.strip() else "Rejected"

    await conn.execute(
        """update public.pending_actions
              set status='rejected', result=%s, resolved_at=now(), resolved_by=%s
            where id=%s""",
        (Json({"summary": summary}), resolved_by, action_id),
    )
    await conn.execute(
        "update public.tasks set status='cancelled', resolved_at=now() where id=%s",
        (task_id,),
    )
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=action["source_system"],
        event_type="action.rejected",
        entity_type="task",
        entity_id=task_id,
        payload={
            "summary": f"Rejected: {action['task_title']} — {summary}",
            "pending_action_id": action_id,
            "tool_name": action["tool_name"],
        },
    )

    # A rejected gated automation step cancels its run (Module 7b).
    if action.get("automation_run_id") is not None:
        await cancel_after_rejection(
            conn, tenant_id, str(action["automation_run_id"]), resolved_by=resolved_by
        )

    return task_id
