"""Schedule board API (Module 12a, vertical seam).

The human REST surface for the Schedule board — the week feed (visits + roster),
create/expand, field edits + outcomes, the three transition verbs (call-out,
assign, cancel via the visit's own delete-less lifecycle), open-shift candidate
ranking, the minimal roster editor, and the gated notify-by-SMS. JWT tenant-scoped
like every `/api` route (RLS does all filtering, so no query mentions tenant_id).

Entity writes are `source_system='user'` (the leads/caregivers precedent): a
coordinator clicking their own board is the approver, so there's no approval gate on
create/assign/call-out/cancel/outcome/roster edits. Every status transition delegates
to the single `services/views/schedule.py` seam, so a board click and a chat/MCP-
approved action leave the same events. The ONE exception is `notify`: outbound SMS is
a system-executed external effect, so it runs through `execute_tool` and its gate
even from a human click (one seam, one audit trail).

Vertical seam: this router, `services/views/schedule.py`, and
`services/views/matching.py` are re-templating-seam members alongside
`routers/leads.py`/`routers/applicants.py`. Core never imports them.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..deps import get_tenant_id
from ..schemas import (
    AssignBody,
    AssignResult,
    CallOutResult,
    CandidateOut,
    CandidatesOut,
    CaregiverRosterOut,
    ClientRef,
    NotifyBody,
    NotifyResult,
    RosterPatch,
    ScheduleBoard,
    ScheduleCreate,
    SchedulePatch,
    SchedulesCreated,
    ScheduleVisitOut,
)
from ..services.events import log_event
from ..services.tools import execute_tool
from ..services.views.matching import rank_candidates, week_hours_map
from ..services.views.schedule import (
    ScheduleError,
    assign,
    call_out,
    cancel,
    create_visits,
    set_outcome,
)

router = APIRouter(prefix="/api", tags=["schedule"])

_ROSTER_FIELDS = ("name", "phone", "email", "address", "zip", "languages", "traits",
                  "availability")


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


def _http(exc: ScheduleError) -> HTTPException:
    """Map a seam error to a status: unknown visit -> 404, hard conflict -> 409,
    otherwise a rejected request -> 422."""
    if exc.not_found:
        return HTTPException(status_code=404, detail=str(exc))
    if exc.conflict:
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _week_start(week: str | None) -> date:
    """Monday of the ISO week containing `week` (YYYY-MM-DD), or of today."""
    if week:
        try:
            d = date.fromisoformat(week)
        except ValueError:
            raise HTTPException(status_code=422, detail="week must be YYYY-MM-DD")
    else:
        d = date.today()
    return d - timedelta(days=d.weekday())


async def _qual_names(conn) -> dict[str, str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select id, name from public.qualifications")
        return {str(r["id"]): r["name"] for r in await cur.fetchall()}


def _visit_out(row: dict, qmap: dict[str, str]) -> ScheduleVisitOut:
    q_ids = [str(x) for x in (row["required_qualification_ids"] or [])]
    return ScheduleVisitOut(
        id=str(row["id"]),
        client_id=str(row["client_id"]),
        client_name=row["client_name"],
        resource_id=str(row["resource_id"]) if row["resource_id"] else None,
        resource_name=row.get("resource_name"),
        start_time=row["start_time"],
        end_time=row["end_time"],
        status=row["status"],
        required_qualification_ids=q_ids,
        required_qualification_names=[qmap[i] for i in q_ids if i in qmap],
        replaces_schedule_id=str(row["replaces_schedule_id"]) if row["replaces_schedule_id"] else None,
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_VISIT_JOIN_SQL = """
select s.*, c.name as client_name, r.name as resource_name
  from public.schedules s
  join public.clients c on c.id = s.client_id
  left join public.resources r on r.id = s.resource_id
"""


async def _load_visit(conn, schedule_id: str, qmap: dict[str, str]) -> ScheduleVisitOut | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_VISIT_JOIN_SQL + " where s.id = %s", (schedule_id,))
        row = await cur.fetchone()
    return _visit_out(row, qmap) if row else None


async def _load_visits(conn, ids: list[str], qmap: dict[str, str]) -> list[ScheduleVisitOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_VISIT_JOIN_SQL + " where s.id = any(%s) order by s.start_time", (ids,))
        rows = await cur.fetchall()
    return [_visit_out(r, qmap) for r in rows]


async def _roster(conn, ref: date) -> list[CaregiverRosterOut]:
    hours = await week_hours_map(conn, ref)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, name, phone, email, address, zip, languages, traits,
                      qualification_ids, region_ids, availability
                 from public.resources order by name"""
        )
        rows = await cur.fetchall()
    return [
        CaregiverRosterOut(
            id=str(r["id"]),
            name=r["name"],
            phone=r["phone"],
            email=r["email"],
            address=r["address"],
            zip=r["zip"],
            languages=list(r["languages"] or []),
            traits=list(r["traits"] or []),
            qualification_ids=[str(x) for x in (r["qualification_ids"] or [])],
            region_ids=[str(x) for x in (r["region_ids"] or [])],
            availability=r["availability"] or {},
            hours_this_week=round(hours.get(str(r["id"]), 0.0), 2),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# board + roster
# ---------------------------------------------------------------------------
@router.get("/schedule", response_model=ScheduleBoard)
async def get_board(conn=Depends(tenant_conn), week: str | None = None):
    """One board payload for the Mon–Sun window: visits (client/resource names +
    resolved qualification names) and the full caregiver roster with hours_this_week
    (so empty caregiver rows still render). Cancelled visits are omitted."""
    ws = _week_start(week)
    we = ws + timedelta(days=7)
    qmap = await _qual_names(conn)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            _VISIT_JOIN_SQL
            + """ where s.start_time >= %s::timestamptz and s.start_time < %s::timestamptz
                   and s.status <> 'cancelled'
                 order by s.start_time""",
            (ws, we),
        )
        visit_rows = await cur.fetchall()
        await cur.execute("select id, name from public.clients order by name")
        clients = [ClientRef(id=str(r["id"]), name=r["name"]) for r in await cur.fetchall()]
    return ScheduleBoard(
        week_start=ws,
        visits=[_visit_out(r, qmap) for r in visit_rows],
        caregivers=await _roster(conn, ws),
        clients=clients,
    )


@router.get("/roster", response_model=list[CaregiverRosterOut])
async def get_roster(conn=Depends(tenant_conn), week: str | None = None):
    """The caregiver roster with hours for the requested (or current) week — the
    12b edit drawer's source."""
    return await _roster(conn, _week_start(week))


@router.patch("/roster/{resource_id}", response_model=CaregiverRosterOut)
async def patch_roster(
    resource_id: str,
    body: RosterPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Edit a caregiver's contact/address/zip/languages/traits/availability. Emits
    one resource.updated naming the changed fields; a no-op emits nothing."""
    resource_id = _valid_uuid(resource_id, "resource_id")
    provided = body.model_fields_set
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.resources where id = %s for update", (resource_id,)
        )
        current = await cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="caregiver not found")

    updates: dict = {}
    for field in _ROSTER_FIELDS:
        if field not in provided:
            continue
        value = getattr(body, field)
        cur_value = current[field]
        if field in ("languages", "traits"):
            if list(value or []) != list(cur_value or []):
                updates[field] = list(value or [])
        elif field == "availability":
            if (value or {}) != (cur_value or {}):
                updates[field] = value or {}
        elif value != cur_value:
            updates[field] = value

    if updates:
        from psycopg.types.json import Json

        set_parts, params = [], []
        for f, v in updates.items():
            set_parts.append(f"{f} = %s")
            params.append(Json(v) if f == "availability" else v)
        params.append(resource_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"update public.resources set {', '.join(set_parts)} where id = %s", params
            )
        name = updates.get("name", current["name"])
        changed = ", ".join(sorted(updates.keys()))
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="user",
            event_type="resource.updated",
            entity_type="resource",
            entity_id=resource_id,
            payload={
                "summary": f"Caregiver '{name}' updated ({changed})",
                "fields": sorted(updates.keys()),
            },
        )

    roster = await _roster(conn, _week_start(None))
    match = next((c for c in roster if c.id == resource_id), None)
    assert match is not None
    return match


# ---------------------------------------------------------------------------
# create / edit / transitions
# ---------------------------------------------------------------------------
@router.post("/schedules", response_model=SchedulesCreated, status_code=201)
async def create_schedules(
    body: ScheduleCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Create a visit (assigned) or an open shift (no caregiver), optionally repeated
    weekly through a date (up to 12 extra rows). Returns every created row."""
    client_id = _valid_uuid(body.client_id, "client_id")
    resource_id = _valid_uuid(body.resource_id, "resource_id") if body.resource_id else None
    quals = [_valid_uuid(q, "required_qualification_ids") for q in body.required_qualification_ids]
    repeat_dt: datetime | None = None
    if body.repeat_weekly_until is not None:
        repeat_dt = datetime.combine(
            body.repeat_weekly_until, time(23, 59, 59), tzinfo=body.start_time.tzinfo
        )
    try:
        rows = await create_visits(
            conn,
            client_id=client_id,
            resource_id=resource_id,
            start=body.start_time,
            end=body.end_time,
            required_qualification_ids=quals,
            notes=body.notes,
            repeat_weekly_until=repeat_dt,
            source_system="user",
        )
    except ScheduleError as exc:
        raise _http(exc)
    qmap = await _qual_names(conn)
    return SchedulesCreated(visits=await _load_visits(conn, [str(r["id"]) for r in rows], qmap))


@router.patch("/schedules/{schedule_id}", response_model=ScheduleVisitOut)
async def patch_schedule(
    schedule_id: str,
    body: SchedulePatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Edit an open/scheduled visit's window/notes/required quals, or record an
    outcome (completed|no_show via the set_outcome seam). Any other status is refused
    — the transitions (call-out/assign/cancel) have their own verbs."""
    schedule_id = _valid_uuid(schedule_id, "schedule_id")
    provided = body.model_fields_set
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.schedules where id = %s for update", (schedule_id,)
        )
        current = await cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="visit not found")

    # --- field edits (window / notes / required quals) — direct write on open/scheduled ---
    field_updates: dict = {}
    if "notes" in provided:
        field_updates["notes"] = body.notes
    if "start_time" in provided:
        field_updates["start_time"] = body.start_time
    if "end_time" in provided:
        field_updates["end_time"] = body.end_time
    if "required_qualification_ids" in provided:
        field_updates["required_qualification_ids"] = [
            _valid_uuid(q, "required_qualification_ids") for q in (body.required_qualification_ids or [])
        ]

    if field_updates:
        if current["status"] not in ("open", "scheduled"):
            raise HTTPException(
                status_code=422,
                detail=f"a {current['status']} visit can't be edited",
            )
        eff_start = field_updates.get("start_time", current["start_time"])
        eff_end = field_updates.get("end_time", current["end_time"])
        if eff_end <= eff_start:
            raise HTTPException(status_code=422, detail="end_time must be after start_time")
        set_parts, params = [], []
        for f, v in field_updates.items():
            set_parts.append(f"{f} = %s")
            params.append(v)
        params.append(schedule_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"update public.schedules set {', '.join(set_parts)} where id = %s", params
            )
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="user",
            event_type="schedule.updated",
            entity_type="schedule",
            entity_id=schedule_id,
            payload={
                "summary": f"Visit for {await _client_name(conn, current['client_id'])} edited "
                           f"({', '.join(sorted(field_updates.keys()))})",
                "fields": sorted(field_updates.keys()),
            },
        )

    # --- outcome status (completed / no_show) via the seam ---
    if "status" in provided:
        st = body.status
        if st not in ("completed", "no_show"):
            raise HTTPException(
                status_code=422,
                detail="use the call-out, assign, or cancel action to change a visit's status",
            )
        try:
            await set_outcome(conn, schedule_id, st, "user")
        except ScheduleError as exc:
            raise _http(exc)

    qmap = await _qual_names(conn)
    out = await _load_visit(conn, schedule_id, qmap)
    assert out is not None
    return out


async def _client_name(conn, client_id) -> str:
    async with conn.cursor() as cur:
        await cur.execute("select name from public.clients where id = %s", (client_id,))
        row = await cur.fetchone()
    return row[0] if row else "the client"


@router.post("/schedules/{schedule_id}/call-out", response_model=CallOutResult)
async def call_out_visit(
    schedule_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Record a call-out on a scheduled visit; opens a linked replacement open shift."""
    schedule_id = _valid_uuid(schedule_id, "schedule_id")
    try:
        result = await call_out(conn, schedule_id, "user")
    except ScheduleError as exc:
        raise _http(exc)
    return CallOutResult(**result)


@router.post("/schedules/{schedule_id}/assign", response_model=AssignResult)
async def assign_visit(
    schedule_id: str,
    body: AssignBody,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Fill an open shift or reassign a scheduled visit. Qualification/availability
    gaps come back as warnings; a hard time conflict is a 409."""
    schedule_id = _valid_uuid(schedule_id, "schedule_id")
    resource_id = _valid_uuid(body.resource_id, "resource_id")
    try:
        result = await assign(conn, schedule_id, resource_id, "user")
    except ScheduleError as exc:
        raise _http(exc)
    return AssignResult(**result)


@router.post("/schedules/{schedule_id}/cancel", response_model=ScheduleVisitOut)
async def cancel_visit(
    schedule_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Cancel a scheduled or open visit (the terminal verb — there is no DELETE)."""
    schedule_id = _valid_uuid(schedule_id, "schedule_id")
    try:
        await cancel(conn, schedule_id, "user")
    except ScheduleError as exc:
        raise _http(exc)
    qmap = await _qual_names(conn)
    out = await _load_visit(conn, schedule_id, qmap)
    assert out is not None
    return out


@router.get("/schedules/{schedule_id}/candidates", response_model=CandidatesOut)
async def visit_candidates(schedule_id: str, conn=Depends(tenant_conn)):
    """Rank caregivers for a fillable shift — an `open` shift (to fill) or a
    `scheduled` visit (to reassign; the ranker excludes the current holder's own
    time so they still appear, and the board filters them out). 409 once a visit has
    reached a terminal/called-out state, where there is nothing to rank."""
    schedule_id = _valid_uuid(schedule_id, "schedule_id")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.schedules where id = %s", (schedule_id,))
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="visit not found")
    if row["status"] not in ("open", "scheduled"):
        raise HTTPException(
            status_code=409, detail="candidates are ranked for open or scheduled visits only"
        )
    candidates = await rank_candidates(conn, row)
    return CandidatesOut(candidates=[CandidateOut(**c) for c in candidates])


@router.post("/schedules/{schedule_id}/notify", response_model=NotifyResult)
async def notify_caregiver(
    schedule_id: str,
    body: NotifyBody,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Text a caregiver about a shift. Even from a human click this runs through
    execute_tool's gate — send_sms is a system-executed external effect, and one seam
    means one audit trail. Returns the queued action id."""
    schedule_id = _valid_uuid(schedule_id, "schedule_id")
    resource_id = _valid_uuid(body.resource_id, "resource_id")
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is required")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select 1 from public.schedules where id = %s", (schedule_id,))
        if await cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="visit not found")
        await cur.execute("select phone from public.resources where id = %s", (resource_id,))
        resource = await cur.fetchone()
    if resource is None:
        raise HTTPException(status_code=404, detail="caregiver not found")
    if not resource["phone"]:
        raise HTTPException(status_code=422, detail="that caregiver has no phone number on file")

    result = await execute_tool(
        conn, tenant_id, "send_sms",
        {"to": resource["phone"], "body": message},
        source_system="user",
    )
    data = result.data if isinstance(result.data, dict) else {}
    return NotifyResult(
        status=data.get("status", "queued"),
        task_id=data.get("task_id"),
        pending_action_id=data.get("pending_action_id"),
        summary=result.summary,
    )
