"""Automations REST API (Module 7a, Task 4) — CRUD over recipes + a manual
run-now trigger that makes the whole engine curl-testable before any background
machinery (7b) exists.

Every write goes through `validate_recipe` (the single recipe gate): a bad recipe
is a 422 whose detail is the plain-language `RecipeError` message (M8 renders it
inline). Reads/writes are tenant-scoped via the standard `tenant_conn` dependency
— RLS does all filtering, so no query mentions tenant_id.

`POST /{id}/run` executes synchronously in-request: `start_run` commits, then
`advance_run` drives the run (in its own per-step transactions) until it completes,
parks (`waiting`/`waiting_approval`), or fails — then the refreshed run is returned.
A no-delay recipe finishes in well under a second at this scale (approvals set the
synchronous-execution precedent).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..db import tenant_conn, tenant_tx
from ..deps import get_current_user, get_tenant_id
from ..schemas import (
    AutomationCreate,
    AutomationOut,
    AutomationPatch,
    RunNow,
    RunOut,
)
from ..services.automations import (
    RecipeError,
    advance_run,
    get_run,
    start_run,
    validate_recipe,
)
from ..services.automations.scheduler import next_fire

router = APIRouter(prefix="/api", tags=["automations"])

_ACTIVE_STATES = ("running", "waiting", "waiting_approval")
_MAX_RUN_LIMIT = 100


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


def _automation_out(row: dict, active_runs: int = 0) -> AutomationOut:
    return AutomationOut(
        id=str(row["id"]),
        name=row["name"],
        description=row["description"],
        status=row["status"],
        trigger=row["trigger"] or {},
        conditions=row["conditions"] or [],
        steps=row["steps"] or [],
        next_fire_at=row["next_fire_at"],
        created_by=row["created_by"],
        active_runs=active_runs,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_out(row: dict) -> RunOut:
    return RunOut(
        id=str(row["id"]),
        automation_id=str(row["automation_id"]),
        status=row["status"],
        trigger_event_id=str(row["trigger_event_id"]) if row["trigger_event_id"] else None,
        entity_type=row["entity_type"],
        entity_id=str(row["entity_id"]) if row["entity_id"] else None,
        context=row["context"] or {},
        step_index=row["step_index"],
        step_log=row["step_log"] or [],
        wake_at=row["wake_at"],
        error=row["error"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _active_run_counts(conn, automation_ids: list[str]) -> dict[str, int]:
    if not automation_ids:
        return {}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select automation_id, count(*) as n
                 from public.automation_runs
                where automation_id = any(%s) and status = any(%s)
                group by automation_id""",
            (automation_ids, list(_ACTIVE_STATES)),
        )
        return {str(r["automation_id"]): r["n"] for r in await cur.fetchall()}


async def _load_row(conn, automation_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.automations where id = %s", (automation_id,))
        return await cur.fetchone()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("/automations", response_model=list[AutomationOut])
async def list_automations(conn=Depends(tenant_conn), status: str | None = None):
    where, params = "", []
    if status:
        if status not in ("active", "paused"):
            raise HTTPException(status_code=422, detail="status must be 'active' or 'paused'")
        where, params = " where status = %s", [status]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select * from public.automations{where} order by created_at desc", params
        )
        rows = await cur.fetchall()
    counts = await _active_run_counts(conn, [str(r["id"]) for r in rows])
    return [_automation_out(r, counts.get(str(r["id"]), 0)) for r in rows]


@router.post("/automations", response_model=AutomationOut, status_code=201)
async def create_automation(
    body: AutomationCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
    user: dict = Depends(get_current_user),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    recipe = {"trigger": body.trigger, "conditions": body.conditions, "steps": body.steps}
    try:
        validate_recipe(recipe)
    except RecipeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.automations
                 (tenant_id, name, description, status, trigger, conditions, steps, created_by)
               values (%s, %s, %s, 'paused', %s, %s, %s, %s)
               returning *""",
            (tenant_id, name, body.description, Json(body.trigger),
             Json(body.conditions), Json(body.steps), user.get("email")),
        )
        row = await cur.fetchone()
    return _automation_out(row, 0)


@router.get("/automations/{automation_id}", response_model=AutomationOut)
async def get_automation(automation_id: str, conn=Depends(tenant_conn)):
    automation_id = _valid_uuid(automation_id, "automation_id")
    row = await _load_row(conn, automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="automation not found")
    counts = await _active_run_counts(conn, [automation_id])
    return _automation_out(row, counts.get(automation_id, 0))


@router.patch("/automations/{automation_id}", response_model=AutomationOut)
async def patch_automation(
    automation_id: str,
    body: AutomationPatch,
    conn=Depends(tenant_conn),
):
    automation_id = _valid_uuid(automation_id, "automation_id")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.automations where id = %s for update", (automation_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="automation not found")

    # Merge provided fields onto the current recipe, then revalidate if the recipe
    # shape changed (a bad edit is a 422 and leaves the row untouched).
    trigger = body.trigger if body.trigger is not None else row["trigger"]
    conditions = body.conditions if body.conditions is not None else row["conditions"]
    steps = body.steps if body.steps is not None else row["steps"]
    if body.trigger is not None or body.conditions is not None or body.steps is not None:
        try:
            validate_recipe({"trigger": trigger, "conditions": conditions, "steps": steps})
        except RecipeError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    status = row["status"]
    if body.status is not None:
        if body.status not in ("active", "paused"):
            raise HTTPException(status_code=422, detail="status must be 'active' or 'paused'")
        status = body.status
    name = body.name.strip() if body.name is not None else row["name"]
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    description = body.description if body.description is not None else row["description"]

    # Cron bookkeeping (7b): an active cron automation needs `next_fire_at` armed;
    # recompute on (re)activation or an expression change, and clear it otherwise so
    # a paused/non-cron automation never sits with a stale schedule.
    next_fire_at = row["next_fire_at"]
    if trigger.get("type") == "cron" and status == "active":
        reactivated = row["status"] != "active"
        expr_changed = body.trigger is not None
        if next_fire_at is None or reactivated or expr_changed:
            next_fire_at = next_fire(trigger["expression"])
    else:
        next_fire_at = None

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """update public.automations
                  set name=%s, description=%s, status=%s, trigger=%s,
                      conditions=%s, steps=%s, next_fire_at=%s
                where id=%s
                returning *""",
            (name, description, status, Json(trigger), Json(conditions), Json(steps),
             next_fire_at, automation_id),
        )
        updated = await cur.fetchone()
    counts = await _active_run_counts(conn, [automation_id])
    return _automation_out(updated, counts.get(automation_id, 0))


@router.delete("/automations/{automation_id}", status_code=204)
async def delete_automation(automation_id: str, conn=Depends(tenant_conn)):
    automation_id = _valid_uuid(automation_id, "automation_id")
    async with conn.cursor() as cur:
        await cur.execute(
            "delete from public.automations where id = %s returning id", (automation_id,)
        )
        deleted = await cur.fetchone()
    if deleted is None:
        raise HTTPException(status_code=404, detail="automation not found")
    return None


# ---------------------------------------------------------------------------
# manual run + run history
# ---------------------------------------------------------------------------
@router.post("/automations/{automation_id}/run", response_model=RunOut)
async def run_now(
    automation_id: str,
    body: RunNow | None = None,
    tenant_id: str = Depends(get_tenant_id),
):
    automation_id = _valid_uuid(automation_id, "automation_id")
    entity_type = body.entity_type if body else None
    entity_id = _valid_uuid(body.entity_id, "entity_id") if body and body.entity_id else None

    # Own transaction so start_run commits before advance_run (which opens its own
    # per-step transactions and must see the committed run row).
    async with tenant_tx(tenant_id) as conn:
        automation = await _load_row(conn, automation_id)
        if automation is None:
            raise HTTPException(status_code=404, detail="automation not found")
        # Manual run is an explicit override: force the run regardless of entry
        # conditions, so a None return means only the concurrency guard fired.
        run_id = await start_run(
            conn, tenant_id, automation,
            entity_type=entity_type, entity_id=entity_id, skip_conditions=True,
        )
    if run_id is None:
        raise HTTPException(
            status_code=409,
            detail="an active run already exists for this automation and record",
        )

    await advance_run(tenant_id, run_id)

    async with tenant_tx(tenant_id) as conn:
        run = await get_run(conn, run_id)
    assert run is not None
    return _run_out(run)


@router.get("/automations/{automation_id}/runs", response_model=list[RunOut])
async def list_runs(
    automation_id: str,
    conn=Depends(tenant_conn),
    limit: int = Query(50, ge=1),
):
    automation_id = _valid_uuid(automation_id, "automation_id")
    limit = min(limit, _MAX_RUN_LIMIT)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select * from public.automation_runs
                where automation_id = %s
                order by created_at desc
                limit %s""",
            (automation_id, limit),
        )
        rows = await cur.fetchall()
    return [_run_out(r) for r in rows]


@router.get("/automation-runs/{run_id}", response_model=RunOut)
async def get_run_detail(run_id: str, conn=Depends(tenant_conn)):
    run_id = _valid_uuid(run_id, "run_id")
    row = await get_run(conn, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_out(row)
