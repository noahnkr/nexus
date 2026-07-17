"""Tasks & approvals API (Module 5).

Read + manage tasks and resolve queued approvals. Tenant-scoped via the standard
`tenant_conn` dependency — RLS does all filtering, so no query mentions tenant_id.
Each task embeds its `pending_actions` (0..n); `tool_input` rides along for the
UI's expandable technical detail only. Approve/reject route through
`services/approvals.py`, the sole caller of `execute_tool`'s approval bypass.

Every mutation writes an `events` row with a plain-language `payload.summary`
(manual creation, status changes) — the gate lifecycle events are written by the
approvals engine, not here.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..deps import get_current_user, get_tenant_id
from ..schemas import (
    ActionResolution,
    PendingActionOut,
    RejectBody,
    TaskCreate,
    TaskOut,
    TaskPage,
    TaskPatch,
)
from ..services.approvals import (
    ActionAlreadyResolved,
    ActionNotFound,
    approve_action,
    reject_action,
)
from ..services.events import log_event

router = APIRouter(prefix="/api", tags=["tasks"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

# Status transitions: pending<->in_progress, and either open state -> done/cancelled.
# Terminal states (done, cancelled) accept no further change.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress", "done", "cancelled"},
    "in_progress": {"pending", "done", "cancelled"},
}
_CLOSING = {"done", "cancelled"}


def _encode_cursor(created_at: datetime, task_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{task_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at, task_id = raw.split("|", 1)
        str(uuid.UUID(task_id))
        return created_at, task_id
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid cursor")


def _action_out(r: dict) -> PendingActionOut:
    return PendingActionOut(
        id=str(r["id"]),
        tool_name=r["tool_name"],
        tool_input=r["tool_input"] or {},
        status=r["status"],
        source_system=r["source_system"],
        result=r["result"],
        created_at=r["created_at"],
        resolved_at=r["resolved_at"],
        resolved_by=r["resolved_by"],
    )


def _task_out(r: dict, actions: list[dict]) -> TaskOut:
    return TaskOut(
        id=str(r["id"]),
        title=r["title"],
        description=r["description"],
        status=r["status"],
        priority=r["priority"],
        originating_event_id=str(r["originating_event_id"]) if r["originating_event_id"] else None,
        assigned_to=r["assigned_to"],
        due_at=r["due_at"],
        resolved_at=r["resolved_at"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        pending_actions=[_action_out(a) for a in actions],
    )


async def _actions_for(conn, task_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch pending_actions for a set of tasks, grouped by task_id (oldest first)."""
    if not task_ids:
        return {}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, task_id, tool_name, tool_input, status, source_system,
                      result, created_at, resolved_at, resolved_by
                 from public.pending_actions
                where task_id = any(%s)
                order by created_at asc""",
            (task_ids,),
        )
        rows = await cur.fetchall()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(str(r["task_id"]), []).append(r)
    return grouped


async def _load_task(conn, task_id: str) -> TaskOut | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.tasks where id = %s", (task_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    actions = (await _actions_for(conn, [str(row["id"])])).get(str(row["id"]), [])
    return _task_out(row, actions)


@router.get("/tasks", response_model=TaskPage)
async def list_tasks(
    conn=Depends(tenant_conn),
    status: str | None = None,
    priority: str | None = None,
    cursor: str | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=1),
):
    limit = min(limit, _MAX_LIMIT)
    where: list[str] = []
    params: dict = {}
    if status:
        # comma-separated list so the UI's "Open" tab can request pending+in_progress.
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            where.append("status = any(%(statuses)s)")
            params["statuses"] = statuses
    if priority:
        where.append("priority = %(priority)s")
        params["priority"] = priority
    if cursor:
        created_at, task_id = _decode_cursor(cursor)
        where.append("(created_at, id) < (%(cursor_ca)s::timestamptz, %(cursor_id)s::uuid)")
        params["cursor_ca"] = created_at
        params["cursor_id"] = task_id

    where_sql = (" where " + " and ".join(where)) if where else ""
    params["limit"] = limit + 1

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""select * from public.tasks{where_sql}
                 order by created_at desc, id desc
                 limit %(limit)s""",
            params,
        )
        rows = await cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    actions = await _actions_for(conn, [str(r["id"]) for r in rows])
    tasks = [_task_out(r, actions.get(str(r["id"]), [])) for r in rows]
    next_cursor = (
        _encode_cursor(rows[-1]["created_at"], str(rows[-1]["id"])) if has_more and rows else None
    )
    return TaskPage(tasks=tasks, next_cursor=next_cursor)


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    if body.priority not in ("low", "normal", "high", "urgent"):
        raise HTTPException(status_code=422, detail="invalid priority")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.tasks (tenant_id, title, description, priority, due_at)
               values (%s, %s, %s, %s, %s) returning id""",
            (tenant_id, title, body.description, body.priority, body.due_at),
        )
        task_id = str((await cur.fetchone())["id"])

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="task.created",
        entity_type="task",
        entity_id=task_id,
        payload={"summary": f"Task created: {title}", "priority": body.priority},
    )
    task = await _load_task(conn, task_id)
    assert task is not None
    return task


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def patch_task(
    task_id: str,
    body: TaskPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    try:
        task_id = str(uuid.UUID(task_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="task_id must be a valid id")

    new_status = body.status
    if new_status not in ("pending", "in_progress", "done", "cancelled"):
        raise HTTPException(status_code=422, detail="invalid status")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.tasks where id = %s for update", (task_id,))
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        current = row["status"]

        if new_status == current:
            no_op = True
        else:
            no_op = False
            allowed = _ALLOWED_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                raise HTTPException(
                    status_code=409,
                    detail=f"cannot change a {current} task to {new_status}",
                )
            # Can't close a task out from under an unresolved approval (D6).
            if new_status in _CLOSING:
                await cur.execute(
                    "select 1 from public.pending_actions "
                    "where task_id = %s and status = 'pending' limit 1",
                    (task_id,),
                )
                if await cur.fetchone() is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="resolve the pending approval before closing this task",
                    )
                await cur.execute(
                    "update public.tasks set status = %s, resolved_at = now() where id = %s",
                    (new_status, task_id),
                )
            else:
                await cur.execute(
                    "update public.tasks set status = %s, resolved_at = null where id = %s",
                    (new_status, task_id),
                )

    if no_op:
        return await _load_task(conn, task_id)

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="task.status_changed",
        entity_type="task",
        entity_id=task_id,
        payload={
            "summary": f"Task '{row['title']}' moved from {current} to {new_status}",
            "from": current,
            "to": new_status,
        },
    )
    return await _load_task(conn, task_id)


async def _resolution_response(conn, action_id: str, task_id: str) -> ActionResolution:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, tool_name, tool_input, status, source_system, result,
                      created_at, resolved_at, resolved_by
                 from public.pending_actions where id = %s""",
            (action_id,),
        )
        action_row = await cur.fetchone()
    task = await _load_task(conn, task_id)
    assert action_row is not None and task is not None
    return ActionResolution(action=_action_out(action_row), task=task)


def _validate_action_id(action_id: str) -> str:
    try:
        return str(uuid.UUID(action_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="action id must be a valid id")


@router.post("/pending-actions/{action_id}/approve", response_model=ActionResolution)
async def approve(
    action_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
    user: dict = Depends(get_current_user),
):
    action_id = _validate_action_id(action_id)
    try:
        task_id = await approve_action(
            conn, tenant_id, action_id, resolved_by=user["email"]
        )
    except ActionNotFound:
        raise HTTPException(status_code=404, detail="pending action not found")
    except ActionAlreadyResolved:
        raise HTTPException(status_code=409, detail="pending action already resolved")
    return await _resolution_response(conn, action_id, task_id)


@router.post("/pending-actions/{action_id}/reject", response_model=ActionResolution)
async def reject(
    action_id: str,
    body: RejectBody | None = None,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
    user: dict = Depends(get_current_user),
):
    action_id = _validate_action_id(action_id)
    note = body.note if body else None
    try:
        task_id = await reject_action(
            conn, tenant_id, action_id, resolved_by=user["email"], note=note
        )
    except ActionNotFound:
        raise HTTPException(status_code=404, detail="pending action not found")
    except ActionAlreadyResolved:
        raise HTTPException(status_code=409, detail="pending action already resolved")
    return await _resolution_response(conn, action_id, task_id)
