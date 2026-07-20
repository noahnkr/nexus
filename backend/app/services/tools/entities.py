"""VERTICAL SEAM — senior-care entity read tools + the reporting schema doc.

This file is to Module 2 what `entities_senior_care.sql` is to Module 0: the one
place a new vertical replaces. Core tools (`documents.py`, `reporting.py`) never
reference care concepts; those live here.

Seven read-only tools over the entity schema. Handlers receive the
already-tenant-scoped connection — RLS does all tenant filtering, so no tool
takes `tenant_id` and no SQL ever mentions it. All UUID inputs are validated
before touching SQL, so a bad id is a clean tool error, not a psycopg exception.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from psycopg.rows import dict_row

from ..events import log_event
from ..views.clients import STATUS_KEYS as CLIENT_STATUSES
from ..views.clients import status_label as client_status_label
from ..views.clients import census_metrics, client_week_hours, payer_label
from ..views.leads import stage_label
from ..views.matching import rank_candidates
from ..views.schedule import (
    ScheduleError,
    assign,
    call_out,
    cancel,
    check_in,
    check_out,
    create_visits,
)
from ..views.workforce import EXPIRING_DAYS, describe_expiry, expiring_credentials
from .core import ToolDef, ToolInputError, ToolResult, _jsonable, current_invocation
from .registry import register

LEAD_STATUSES = ["new", "contacted", "qualified", "converted", "lost"]
# Client statuses come from the clients seam (views/clients.CLIENT_STATUSES) — one
# source, so the tool enum can never drift from the DB CHECK or the REST surface.
SCHEDULE_STATUSES = ["open", "scheduled", "called_out", "completed", "cancelled", "no_show"]
# Caregiver-recruiting pipeline stages (Module 10); mirrors views/caregivers.py
# STAGE_KEYS and the applicants.stage CHECK.
APPLICANT_STAGES = ["applied", "screening", "interview", "offer", "hired", "rejected"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _require_uuid(args: dict, key: str) -> str:
    raw = args.get(key)
    if raw is None or str(raw).strip() == "":
        raise ToolInputError(f"'{key}' is required.")
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        raise ToolInputError(f"'{key}' must be a valid id.")


def _limit(args: dict, default: int, cap: int = 100) -> int:
    try:
        n = int(args.get("limit", default))
    except (ValueError, TypeError):
        n = default
    return max(1, min(n, cap))


def _like(term: str | None) -> str | None:
    if term is None or str(term).strip() == "":
        return None
    return f"%{term}%"


async def _fetch_all(conn, sql: str, params) -> list[dict]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return [_jsonable(dict(r)) for r in await cur.fetchall()]


async def _fetch_one(conn, sql: str, params) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        row = await cur.fetchone()
        return _jsonable(dict(row)) if row else None


async def _name_maps(conn) -> tuple[dict, dict]:
    """qualification id->name and region id->name maps for the tenant. Small
    reference tables, fetched whole so resource rows resolve id arrays to names."""
    quals = {
        r["id"]: r["name"]
        for r in await _fetch_all(conn, "select id, name from public.qualifications", ())
    }
    regions = {
        r["id"]: r["name"]
        for r in await _fetch_all(conn, "select id, name from public.regions", ())
    }
    return quals, regions


async def _resolve_name(conn, table: str, name: str) -> str | None:
    row = await _fetch_one(
        conn, f"select id from public.{table} where name ilike %s limit 1", (name,)
    )
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------
async def _list_leads(conn, args: dict) -> ToolResult:
    status = args.get("status")
    region = args.get("region")
    search = _like(args.get("search"))
    limit = _limit(args, 20)
    rows = await _fetch_all(
        conn,
        """select l.id, l.name, l.phone, l.email, l.source, l.status,
                  r.name as region, l.requirements, l.created_at
             from public.leads l
             left join public.regions r on r.id = l.region_id
            where (%(status)s::text is null or l.status = %(status)s)
              and (%(region)s::text is null or r.name ilike %(region)s)
              and (%(search)s::text is null
                   or l.name ilike %(search)s
                   or l.email ilike %(search)s
                   or l.phone ilike %(search)s)
            order by l.created_at desc
            limit %(limit)s""",
        {"status": status, "region": region, "search": search, "limit": limit},
    )
    filt = _describe({"status": status, "region": region, "search": args.get("search")})
    return ToolResult(f"Found {len(rows)} lead(s){filt}.", {"leads": rows, "count": len(rows)})


async def _get_lead(conn, args: dict) -> ToolResult:
    lead_id = _require_uuid(args, "lead_id")
    lead = await _fetch_one(
        conn,
        """select l.*, r.name as region
             from public.leads l
             left join public.regions r on r.id = l.region_id
            where l.id = %s""",
        (lead_id,),
    )
    if lead is None:
        return ToolResult("No lead found with that id.", {"lead": None})
    lead["external_ids"] = await _fetch_all(
        conn,
        """select source_system, external_id, last_synced_at
             from public.external_ids
            where entity_type = 'lead' and entity_id = %s""",
        (lead_id,),
    )
    return ToolResult(f"Lead: {lead['name']} (status {lead['status']}).", {"lead": lead})


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------
async def _list_clients(conn, args: dict) -> ToolResult:
    status = args.get("status")
    search = _like(args.get("search"))
    limit = _limit(args, 20)
    rows = await _fetch_all(
        conn,
        """select c.id, c.name, c.phone, c.email, c.status, c.lead_id,
                  c.requirements, c.created_at
             from public.clients c
            where (%(status)s::text is null or c.status = %(status)s)
              and (%(search)s::text is null
                   or c.name ilike %(search)s
                   or c.email ilike %(search)s
                   or c.phone ilike %(search)s)
            order by c.created_at desc
            limit %(limit)s""",
        {"status": status, "search": search, "limit": limit},
    )
    filt = _describe({"status": status, "search": args.get("search")})
    return ToolResult(
        f"Found {len(rows)} client(s){filt}.", {"clients": rows, "count": len(rows)}
    )


async def _get_client(conn, args: dict) -> ToolResult:
    client_id = _require_uuid(args, "client_id")
    client = await _fetch_one(
        conn, "select * from public.clients where id = %s", (client_id,)
    )
    if client is None:
        return ToolResult("No client found with that id.", {"client": None})
    client["external_ids"] = await _fetch_all(
        conn,
        """select source_system, external_id, last_synced_at
             from public.external_ids
            where entity_type = 'client' and entity_id = %s""",
        (client_id,),
    )
    client["upcoming_schedules"] = await _fetch_all(
        conn,
        """select s.id, s.start_time, s.end_time, s.status, s.check_in_at,
                  s.check_out_at, r.name as resource
             from public.schedules s
             left join public.resources r on r.id = s.resource_id
            where s.client_id = %s and s.start_time >= now()
            order by s.start_time
            limit 5""",
        (client_id,),
    )
    # 16a: the care picture the agent needs to answer "who is this client and are
    # we delivering their hours" without a second tool call.
    client["region"] = (
        await _lookup_name(conn, "regions", str(client["region_id"]))
        if client.get("region_id")
        else None
    )
    client["payer_label"] = payer_label(client.get("payer"))
    client["contacts"] = await _fetch_all(
        conn,
        """select name, relationship, phone, email, is_primary, notes
             from public.client_contacts
            where client_id = %s
            order by is_primary desc, name""",
        (client_id,),
    )
    client["hours_this_week"] = _jsonable(await client_week_hours(conn, client_id))

    return ToolResult(
        f"Client: {client['name']} "
        f"(status {client_status_label(client['status']).lower()}).",
        {"client": client},
    )


# ---------------------------------------------------------------------------
# resources (caregivers)
# ---------------------------------------------------------------------------
async def _list_resources(conn, args: dict) -> ToolResult:
    qual_name = args.get("qualification")
    region_name = args.get("region")
    search = _like(args.get("search"))
    limit = _limit(args, 20)

    qual_id = await _resolve_name(conn, "qualifications", qual_name) if qual_name else None
    region_id = await _resolve_name(conn, "regions", region_name) if region_name else None

    # A named filter that matches no reference row can never match a resource.
    if (qual_name and qual_id is None) or (region_name and region_id is None):
        missing = qual_name if qual_id is None and qual_name else region_name
        return ToolResult(
            f"No resources found (no match for '{missing}').",
            {"resources": [], "count": 0},
        )

    rows = await _fetch_all(
        conn,
        """select id, name, phone, email, qualification_ids, region_ids, availability
             from public.resources
            where (%(qual_id)s::uuid is null or %(qual_id)s::uuid = any(qualification_ids))
              and (%(region_id)s::uuid is null or %(region_id)s::uuid = any(region_ids))
              and (%(search)s::text is null or name ilike %(search)s or email ilike %(search)s)
            order by name
            limit %(limit)s""",
        {"qual_id": qual_id, "region_id": region_id, "search": search, "limit": limit},
    )

    quals, regions = await _name_maps(conn)
    for r in rows:
        r["qualifications"] = [quals[q] for q in r.pop("qualification_ids") if q in quals]
        r["regions"] = [regions[g] for g in r.pop("region_ids") if g in regions]

    filt = _describe({"qualification": qual_name, "region": region_name, "search": args.get("search")})
    return ToolResult(
        f"Found {len(rows)} caregiver(s){filt}.", {"resources": rows, "count": len(rows)}
    )


async def _get_resource_availability(conn, args: dict) -> ToolResult:
    resource_id = _require_uuid(args, "resource_id")
    resource = await _fetch_one(
        conn,
        "select id, name, availability from public.resources where id = %s",
        (resource_id,),
    )
    if resource is None:
        return ToolResult("No caregiver found with that id.", {"resource": None})
    upcoming = await _fetch_all(
        conn,
        """select s.id, s.start_time, s.end_time, s.status, c.name as client
             from public.schedules s
             join public.clients c on c.id = s.client_id
            where s.resource_id = %s and s.status = 'scheduled' and s.start_time >= now()
            order by s.start_time""",
        (resource_id,),
    )
    return ToolResult(
        f"{resource['name']}: {len(upcoming)} upcoming visit(s).",
        {
            "resource": {"id": resource["id"], "name": resource["name"]},
            "availability": resource["availability"],
            "upcoming_schedules": upcoming,
        },
    )


# ---------------------------------------------------------------------------
# applicants (caregiver-recruiting pipeline — Module 10)
# ---------------------------------------------------------------------------
async def _list_applicants(conn, args: dict) -> ToolResult:
    stage = args.get("stage")
    source = args.get("source")
    search = _like(args.get("search"))
    limit = _limit(args, 20)
    rows = await _fetch_all(
        conn,
        """select id, name, phone, email, source, stage,
                  qualification_ids, region_ids, created_at
             from public.applicants
            where (%(stage)s::text is null or stage = %(stage)s)
              and (%(source)s::text is null or source = %(source)s)
              and (%(search)s::text is null
                   or name ilike %(search)s
                   or email ilike %(search)s
                   or phone ilike %(search)s)
            order by created_at desc
            limit %(limit)s""",
        {"stage": stage, "source": source, "search": search, "limit": limit},
    )
    quals, regions = await _name_maps(conn)
    for r in rows:
        r["qualifications"] = [quals[q] for q in r.pop("qualification_ids") if q in quals]
        r["regions"] = [regions[g] for g in r.pop("region_ids") if g in regions]
    filt = _describe({"stage": stage, "source": source, "search": args.get("search")})
    return ToolResult(
        f"Found {len(rows)} applicant(s){filt}.", {"applicants": rows, "count": len(rows)}
    )


async def _get_applicant(conn, args: dict) -> ToolResult:
    applicant_id = _require_uuid(args, "applicant_id")
    applicant = await _fetch_one(
        conn, "select * from public.applicants where id = %s", (applicant_id,)
    )
    if applicant is None:
        return ToolResult("No applicant found with that id.", {"applicant": None})
    quals, regions = await _name_maps(conn)
    applicant["qualifications"] = [
        quals[q] for q in applicant.pop("qualification_ids") if q in quals
    ]
    applicant["regions"] = [
        regions[g] for g in applicant.pop("region_ids") if g in regions
    ]
    applicant["external_ids"] = await _fetch_all(
        conn,
        """select source_system, external_id, last_synced_at
             from public.external_ids
            where entity_type = 'applicant' and entity_id = %s""",
        (applicant_id,),
    )
    return ToolResult(
        f"Applicant: {applicant['name']} (stage {applicant['stage']}).",
        {"applicant": applicant},
    )


# ---------------------------------------------------------------------------
# workforce compliance (Module 18)
# ---------------------------------------------------------------------------
async def _list_expiring_credentials(conn, args: dict) -> ToolResult:
    """SAFE read: which active caregivers' credentials need attention. Nothing here
    changes state, so it needs no gate — the daily digest automation calls it and
    then creates a task, which is where the human decision lives."""
    raw = args.get("days_ahead", EXPIRING_DAYS)
    try:
        days_ahead = int(raw)
    except (ValueError, TypeError):
        days_ahead = EXPIRING_DAYS
    days_ahead = max(1, min(days_ahead, 365))

    rows = await expiring_credentials(conn, days_ahead)
    if not rows:
        return ToolResult(
            f"No credentials expire in the next {days_ahead} days.",
            {"credentials": [], "count": 0, "days_ahead": days_ahead},
        )

    # Plain-language content line — no UUIDs, no raw dates. The digest task's
    # description is built from exactly this text.
    clauses = "; ".join(describe_expiry(r) for r in rows)
    noun = "credential" if len(rows) == 1 else "credentials"
    return ToolResult(
        f"{len(rows)} {noun} need attention: {clauses}.",
        {
            "credentials": _jsonable(rows),
            "count": len(rows),
            "days_ahead": days_ahead,
            "summary": clauses,
        },
    )


# ---------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------
async def _list_schedules(conn, args: dict) -> ToolResult:
    client_id = _require_uuid(args, "client_id") if args.get("client_id") else None
    resource_id = _require_uuid(args, "resource_id") if args.get("resource_id") else None
    status = args.get("status")
    date_from = args.get("date_from")
    date_to = args.get("date_to")
    limit = _limit(args, 50, cap=200)
    rows = await _fetch_all(
        conn,
        """select s.id, s.start_time, s.end_time, s.status,
                  c.name as client, r.name as resource
             from public.schedules s
             join public.clients c on c.id = s.client_id
             left join public.resources r on r.id = s.resource_id
            where (%(client_id)s::uuid is null or s.client_id = %(client_id)s::uuid)
              and (%(resource_id)s::uuid is null or s.resource_id = %(resource_id)s::uuid)
              and (%(status)s::text is null or s.status = %(status)s)
              and (%(date_from)s::timestamptz is null or s.start_time >= %(date_from)s::timestamptz)
              and (%(date_to)s::timestamptz is null or s.start_time <= %(date_to)s::timestamptz)
            order by s.start_time
            limit %(limit)s""",
        {
            "client_id": client_id,
            "resource_id": resource_id,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
    )
    filt = _describe({"status": status})
    return ToolResult(
        f"Found {len(rows)} schedule(s){filt}.", {"schedules": rows, "count": len(rows)}
    )


def _describe(filters: dict) -> str:
    parts = [f"{k}={v}" for k, v in filters.items() if v not in (None, "")]
    return f" ({', '.join(parts)})" if parts else ""


# ---------------------------------------------------------------------------
# WRITE tools (gated) — state-changing entity operations. All safe=False, so
# execute_tool queues them for human approval instead of running. Each supplies a
# read-only `gate_describe` that names entities in plain language for the task
# title. Handlers validate inputs with the same helpers as the read tools, so a
# bad argument still fails cleanly *after* approval (a `failed` action, not a crash).
# ---------------------------------------------------------------------------
def _require_iso(args: dict, key: str) -> datetime:
    raw = args.get(key)
    if raw is None or str(raw).strip() == "":
        raise ToolInputError(f"'{key}' is required.")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ToolInputError(f"'{key}' must be an ISO-8601 date-time.")


async def _lookup_name(conn, table: str, entity_id: str) -> str | None:
    row = await _fetch_one(conn, f"select name from public.{table} where id=%s", (entity_id,))
    return row["name"] if row else None


def _fmt_time(start, end) -> str:
    """'Tue Jul 21 8:00–12:00' from either datetimes or _jsonable ISO strings — the
    gate_describe helpers read rows through _fetch_one, which coerces timestamps to
    strings."""
    s = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
    e = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
    return f"{s.strftime('%a %b')} {s.day} {s.hour}:{s.minute:02d}–{e.hour}:{e.minute:02d}"


# --- update_lead_status ---
async def _describe_update_lead_status(conn, args: dict) -> str:
    lead_id = _require_uuid(args, "lead_id")
    name = await _lookup_name(conn, "leads", lead_id) or f"id {lead_id[:8]}"
    return f"Update lead '{name}' to {args.get('status')}"


async def _update_lead_status(conn, args: dict) -> ToolResult:
    lead_id = _require_uuid(args, "lead_id")
    status = args.get("status")
    if status not in LEAD_STATUSES:
        raise ToolInputError(f"'status' must be one of: {', '.join(LEAD_STATUSES)}.")
    # Read the current status first so the stage_changed `from` is truthful.
    row = await _fetch_one(
        conn, "select name, status from public.leads where id=%s", (lead_id,)
    )
    if row is None:
        raise ToolInputError("No lead found with that id.")
    name, previous = row["name"], row["status"]
    await conn.execute("update public.leads set status=%s where id=%s", (status, lead_id))

    # Emit the first-class stage event (9a) so chat/MCP/automation stage moves
    # appear in the lead's timeline and (chat/MCP only — the M7 loop guard skips
    # automation-sourced events) fire 9b's per-stage sequences. Same payload shape
    # as the REST PATCH. Only on an actual change, so `from`/`to` differ.
    if status != previous:
        inv = current_invocation()
        if inv is not None:
            # Advancing the lead ends any in-flight sequence bound to the leads view
            # for it (same supersede the REST route applies). Lazy import avoids the
            # tools<->automations cycle (the engine imports execute_tool).
            from ..automations import supersede_sequence_runs

            await supersede_sequence_runs(
                conn, inv["tenant_id"], "lead", lead_id, view="leads"
            )
            await log_event(
                conn,
                tenant_id=inv["tenant_id"],
                source_system=inv["source_system"],
                event_type="lead.stage_changed",
                entity_type="lead",
                entity_id=lead_id,
                payload={
                    "summary": (
                        f"Lead '{name}' moved from {stage_label(previous)} "
                        f"to {stage_label(status)}"
                    ),
                    "from": previous,
                    "to": status,
                },
            )
    return ToolResult(
        f"Updated lead '{name}' to {status}.", {"lead_id": lead_id, "status": status}
    )


# --- update_client_status ---
async def _describe_update_client_status(conn, args: dict) -> str:
    client_id = _require_uuid(args, "client_id")
    name = await _lookup_name(conn, "clients", client_id) or f"id {client_id[:8]}"
    # The task title an office user reads should say "hospital hold", not the raw
    # `hospital_hold` column value (CLAUDE.md: plain language in user-facing views).
    return f"Update client '{name}' to {client_status_label(args.get('status')).lower()}"


async def _update_client_status(conn, args: dict) -> ToolResult:
    """Change a client's status. Delegates to the single `change_status()` path
    (never its own UPDATE) so a chat/MCP-approved change emits the same
    `client.status_changed` event as a coordinator's UI click — the
    views/caregivers.move_stage precedent."""
    client_id = _require_uuid(args, "client_id")
    status = args.get("status")
    if status not in CLIENT_STATUSES:
        raise ToolInputError(f"'status' must be one of: {', '.join(CLIENT_STATUSES)}.")

    # The caller's tenant + source_system ride the event (the M7 loop guard depends
    # on it). Lazy import avoids the tools<->views import cycle at module load.
    from ..views.clients import ClientError, change_status

    inv = current_invocation()
    tenant_id = inv["tenant_id"] if inv else None
    try:
        result = await change_status(
            conn, tenant_id, _source_system(), client_id, status
        )
    except ClientError as exc:
        raise ToolInputError(str(exc))

    name = result["name"]
    if not result["changed"]:
        return ToolResult(
            f"Client '{name}' is already {client_status_label(status).lower()}.",
            {"client_id": client_id, "status": status, "changed": False},
        )
    return ToolResult(
        f"Updated client '{name}' to {client_status_label(status).lower()}.",
        {"client_id": client_id, "status": status, "changed": True},
    )


def _source_system() -> str:
    """source_system for the in-flight tool call (chat/mcp/automation), for the seam's
    event attribution. Falls back to 'chat' outside execute_tool (never in practice)."""
    inv = current_invocation()
    return inv["source_system"] if inv else "chat"


def _opt_uuids(args: dict, key: str) -> list[str]:
    """Validate an optional list of uuids (e.g. required_qualification_ids). Absent
    -> []. Any malformed member is a clean tool error."""
    raw = args.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ToolInputError(f"'{key}' must be a list of ids.")
    out = []
    for item in raw:
        try:
            out.append(str(uuid.UUID(str(item))))
        except (ValueError, AttributeError, TypeError):
            raise ToolInputError(f"'{key}' must contain valid ids.")
    return out


# --- create_schedule (delegates to the create_visits seam) ---
async def _describe_create_schedule(conn, args: dict) -> str:
    client_id = _require_uuid(args, "client_id")
    client = await _lookup_name(conn, "clients", client_id) or "a client"
    when = str(args.get("start_time", "")).strip()
    tail = f" starting {when}" if when else ""
    resource_raw = args.get("resource_id")
    if resource_raw is None or str(resource_raw).strip() == "":
        return f"Create an open shift for {client}{tail}"
    resource_id = _require_uuid(args, "resource_id")
    resource = await _lookup_name(conn, "resources", resource_id) or "a caregiver"
    return f"Schedule a visit for {client} with {resource}{tail}"


async def _create_schedule(conn, args: dict) -> ToolResult:
    """Create a visit (assigned) or an open shift (no caregiver), optionally repeated
    weekly. The single writer is views/schedule.create_visits — no schedule INSERT
    lives here, so a chat/MCP-approved create emits the same schedule.created events
    a board create does."""
    client_id = _require_uuid(args, "client_id")
    start_time = _require_iso(args, "start_time")
    end_time = _require_iso(args, "end_time")
    resource_id = None
    if args.get("resource_id") not in (None, ""):
        resource_id = _require_uuid(args, "resource_id")
    quals = _opt_uuids(args, "required_qualification_ids")
    repeat_until = None
    if args.get("repeat_weekly_until") not in (None, ""):
        repeat_until = _require_iso(args, "repeat_weekly_until")
    try:
        rows = await create_visits(
            conn,
            client_id=client_id,
            resource_id=resource_id,
            start=start_time,
            end=end_time,
            required_qualification_ids=quals,
            notes=args.get("notes"),
            repeat_weekly_until=repeat_until,
            source_system=_source_system(),
        )
    except ScheduleError as exc:
        raise ToolInputError(str(exc))

    kind = "visit" if resource_id else "open shift"
    plural = "" if len(rows) == 1 else "s"
    return ToolResult(
        f"Created {len(rows)} {kind}{plural}.",
        {"schedule_ids": [str(r["id"]) for r in rows], "count": len(rows),
         "status": rows[0]["status"] if rows else None},
    )


# --- cancel_schedule (delegates to the cancel seam) ---
async def _describe_cancel_schedule(conn, args: dict) -> str:
    schedule_id = _require_uuid(args, "schedule_id")
    row = await _fetch_one(
        conn,
        """select s.start_time, c.name as client
             from public.schedules s
             join public.clients c on c.id = s.client_id
            where s.id = %s""",
        (schedule_id,),
    )
    if row is None:
        return f"Cancel visit id {schedule_id[:8]}"
    return f"Cancel the visit for {row['client']} on {row['start_time']}"


async def _cancel_schedule(conn, args: dict) -> ToolResult:
    schedule_id = _require_uuid(args, "schedule_id")
    client = await _fetch_one(
        conn,
        "select c.name from public.schedules s join public.clients c on c.id = s.client_id "
        "where s.id = %s",
        (schedule_id,),
    )
    try:
        await cancel(conn, schedule_id, _source_system())
    except ScheduleError as exc:
        raise ToolInputError(str(exc))
    who = client["name"] if client else "the client"
    return ToolResult(
        f"Cancelled the visit for {who}.",
        {"schedule_id": schedule_id, "status": "cancelled"},
    )


# --- find_available_caregivers (SAFE — ranks the roster for a shift) ---
async def _find_available_caregivers(conn, args: dict) -> ToolResult:
    """Rank caregivers for an open shift (by schedule_id) or an ad-hoc window (client
    + start/end [+ required quals]). Safe: it reads and ranks, changing nothing, so
    chat/MCP and automation steps can call it and drop the result into run context."""
    if args.get("schedule_id") not in (None, ""):
        schedule_id = _require_uuid(args, "schedule_id")
        row = await _fetch_one(
            conn, "select * from public.schedules where id = %s", (schedule_id,)
        )
        if row is None:
            raise ToolInputError("No visit found with that id.")
        schedule_row = row
    else:
        client_id = _require_uuid(args, "client_id")
        if await _lookup_name(conn, "clients", client_id) is None:
            raise ToolInputError("No client found with that id.")
        schedule_row = {
            "client_id": client_id,
            "start_time": _require_iso(args, "start_time"),
            "end_time": _require_iso(args, "end_time"),
            "required_qualification_ids": _opt_uuids(args, "required_qualification_ids"),
        }
        if schedule_row["end_time"] <= schedule_row["start_time"]:
            raise ToolInputError("'end_time' must be after 'start_time'.")

    candidates = await rank_candidates(conn, schedule_row)
    if not candidates:
        return ToolResult(
            "No eligible caregivers found for that shift.",
            {"candidates": [], "count": 0},
        )
    top = candidates[:3]
    parts = []
    for c in top:
        reason = c["reasons"][0] if c["reasons"] else "meets the basic requirements"
        parts.append(f"{c['name']} (score {c['score']}: {reason.lower()})")
    summary = f"Top {len(top)} of {len(candidates)} caregiver(s): " + "; ".join(parts) + "."
    return ToolResult(summary, {"candidates": candidates, "count": len(candidates)})


# --- record_call_out (gated — delegates to call_out) ---
async def _describe_record_call_out(conn, args: dict) -> str:
    schedule_id = _require_uuid(args, "schedule_id")
    row = await _fetch_one(
        conn,
        """select s.start_time, s.end_time, c.name as client, r.name as resource
             from public.schedules s
             join public.clients c on c.id = s.client_id
             left join public.resources r on r.id = s.resource_id
            where s.id = %s""",
        (schedule_id,),
    )
    if row is None:
        return f"Record a call-out for visit id {schedule_id[:8]}"
    who = row["resource"] or "the caregiver"
    return (
        f"Record a call-out for {who}'s {_fmt_time(row['start_time'], row['end_time'])} "
        f"visit with {row['client']} and open a replacement shift"
    )


async def _record_call_out(conn, args: dict) -> ToolResult:
    schedule_id = _require_uuid(args, "schedule_id")
    try:
        result = await call_out(conn, schedule_id, _source_system())
    except ScheduleError as exc:
        raise ToolInputError(str(exc))
    return ToolResult(
        "Recorded the call-out and opened a replacement shift.",
        {
            "schedule_id": schedule_id,
            "replacement_schedule_id": result["replacement_schedule_id"],
        },
    )


# --- assign_caregiver (gated — delegates to assign) ---
async def _describe_assign_caregiver(conn, args: dict) -> str:
    schedule_id = _require_uuid(args, "schedule_id")
    resource_id = _require_uuid(args, "resource_id")
    resource = await _lookup_name(conn, "resources", resource_id) or "a caregiver"
    row = await _fetch_one(
        conn,
        """select s.start_time, s.end_time, c.name as client
             from public.schedules s
             join public.clients c on c.id = s.client_id
            where s.id = %s""",
        (schedule_id,),
    )
    if row is None:
        return f"Assign {resource} to visit id {schedule_id[:8]}"
    return (
        f"Assign {resource} to {row['client']}'s "
        f"{_fmt_time(row['start_time'], row['end_time'])} visit"
    )


async def _assign_caregiver(conn, args: dict) -> ToolResult:
    schedule_id = _require_uuid(args, "schedule_id")
    resource_id = _require_uuid(args, "resource_id")
    try:
        result = await assign(conn, schedule_id, resource_id, _source_system())
    except ScheduleError as exc:
        raise ToolInputError(str(exc))
    resource = await _lookup_name(conn, "resources", resource_id) or "the caregiver"
    warnings = result["warnings"]
    tail = f" ({'; '.join(warnings)})" if warnings else ""
    return ToolResult(
        f"Assigned {resource} to the visit.{tail}",
        {"schedule_id": schedule_id, "resource_id": resource_id, "warnings": warnings},
    )


# --- EVV clock tools (Module 16a) ---
# Gated despite feeling like a small stamp: check-in/out times are the billing
# record for a Medicaid-funded visit, and check-out also completes the visit. An
# agent asserting when a caregiver arrived is exactly the kind of externally
# visible record change the M5 gate exists for.
async def _visit_gate_phrase(conn, schedule_id: str, verb: str) -> str:
    row = await _fetch_one(
        conn,
        """select c.name as client, s.start_time, s.end_time
             from public.schedules s
             join public.clients c on c.id = s.client_id
            where s.id = %s""",
        (schedule_id,),
    )
    if row is None:
        return f"Record {verb} for an unknown visit"
    when = _fmt_time(row["start_time"], row["end_time"])
    return f"Record {verb} for the visit with {row['client']} on {when}"


def _optional_time(args: dict, key: str) -> datetime | None:
    """Optional ISO-8601 stamp; absent/blank means 'now' (the seam's default)."""
    raw = args.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    return _require_iso(args, key)


async def _describe_record_visit_check_in(conn, args: dict) -> str:
    return await _visit_gate_phrase(conn, _require_uuid(args, "schedule_id"), "check-in")


async def _record_visit_check_in(conn, args: dict) -> ToolResult:
    schedule_id = _require_uuid(args, "schedule_id")
    try:
        result = await check_in(
            conn, schedule_id, _source_system(), at=_optional_time(args, "time")
        )
    except ScheduleError as exc:
        raise ToolInputError(str(exc))
    return ToolResult("Checked in to the visit.", _jsonable(result))


async def _describe_record_visit_check_out(conn, args: dict) -> str:
    return await _visit_gate_phrase(conn, _require_uuid(args, "schedule_id"), "check-out")


async def _record_visit_check_out(conn, args: dict) -> ToolResult:
    schedule_id = _require_uuid(args, "schedule_id")
    try:
        result = await check_out(
            conn, schedule_id, _source_system(), at=_optional_time(args, "time")
        )
    except ScheduleError as exc:
        raise ToolInputError(str(exc))
    return ToolResult(
        f"Checked out of the visit ({result['actual_hours']} hours worked); "
        "the visit is now complete.",
        _jsonable(result),
    )


# --- get_census (safe read over the seam's deterministic math) ---
async def _get_census(conn, args: dict) -> ToolResult:
    """Current-week census. Read-only and safe: it computes nothing the office
    can't see on the Clients page, and the numbers come from the same seam
    function that page renders, so chat and UI can never disagree."""
    metrics = await census_metrics(conn)
    rate = metrics["delivery_rate"]
    line = (
        f"{metrics['active_clients']} active client(s); "
        f"{metrics['authorized_hours']} authorized hrs/wk, "
        f"{metrics['delivered_hours']} delivered this week"
    )
    if rate is not None:
        line += f" ({rate}%)"
    if metrics["leakage_hours"]:
        line += f" — {metrics['leakage_hours']} hrs short of authorized"
    if metrics["open_hours"]:
        line += f"; {metrics['open_hours']} hrs still unfilled"
    return ToolResult(line + ".", _jsonable(metrics))


# --- update_applicant_stage (Module 10) ---
async def _describe_update_applicant_stage(conn, args: dict) -> str:
    applicant_id = _require_uuid(args, "applicant_id")
    name = await _lookup_name(conn, "applicants", applicant_id) or f"id {applicant_id[:8]}"
    return f"Move applicant '{name}' to {args.get('stage')}"


async def _update_applicant_stage(conn, args: dict) -> ToolResult:
    """Move an applicant along the hiring pipeline. Delegates to the single
    `move_stage()` path (never its own UPDATE) so a chat/MCP-approved move emits the
    same `applicant.stage_changed` event — and, on `hired`, the same atomic
    caregiver promotion — as a coordinator's UI move."""
    applicant_id = _require_uuid(args, "applicant_id")
    stage = args.get("stage")
    if stage not in APPLICANT_STAGES:
        raise ToolInputError(f"'stage' must be one of: {', '.join(APPLICANT_STAGES)}.")
    name = await _lookup_name(conn, "applicants", applicant_id)
    if name is None:
        raise ToolInputError("No applicant found with that id.")

    # The caller's tenant + source_system ride the event (the M7 loop guard depends
    # on it). Lazy import avoids the tools<->views/automations import cycle.
    from ..views.caregivers import MoveStageError, move_stage

    inv = current_invocation()
    tenant_id = inv["tenant_id"] if inv else None
    source_system = inv["source_system"] if inv else "chat"
    try:
        result = await move_stage(conn, tenant_id, source_system, applicant_id, stage)
    except MoveStageError as exc:
        raise ToolInputError(str(exc))

    data: dict = {"applicant_id": applicant_id, "stage": stage}
    promoted = result["promoted"]
    if promoted:
        data["resource_id"] = promoted["resource_id"]
        return ToolResult(
            f"Moved applicant '{name}' to {stage}; caregiver record created.", data
        )
    return ToolResult(f"Moved applicant '{name}' to {stage}.", data)


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def _obj(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


register(ToolDef(
    name="list_leads",
    description=(
        "List prospective clients (leads), optionally filtered by pipeline status, "
        "region name, or a text search over name/email/phone."
    ),
    input_schema=_obj({
        "status": {"type": "string", "enum": LEAD_STATUSES, "description": "Pipeline status filter."},
        "region": {"type": "string", "description": "Region name, e.g. 'North County'."},
        "search": {"type": "string", "description": "Case-insensitive match on name, email, or phone."},
        "limit": {"type": "integer", "default": 20, "maximum": 100},
    }),
    handler=_list_leads,
))

register(ToolDef(
    name="get_lead",
    description="Get one lead by id, including its region and cross-system external ids.",
    input_schema=_obj({"lead_id": {"type": "string", "description": "The lead's id."}}, ["lead_id"]),
    handler=_get_lead,
))

register(ToolDef(
    name="list_clients",
    description=(
        "List active care recipients (clients), optionally filtered by status or a "
        "text search over name/email/phone."
    ),
    input_schema=_obj({
        "status": {"type": "string", "enum": CLIENT_STATUSES, "description": "Client status filter."},
        "search": {"type": "string", "description": "Case-insensitive match on name, email, or phone."},
        "limit": {"type": "integer", "default": 20, "maximum": 100},
    }),
    handler=_list_clients,
))

register(ToolDef(
    name="get_client",
    description=(
        "Get one client by id, including cross-system external ids and their next "
        "few upcoming scheduled visits."
    ),
    input_schema=_obj({"client_id": {"type": "string", "description": "The client's id."}}, ["client_id"]),
    handler=_get_client,
))

register(ToolDef(
    name="list_resources",
    description=(
        "List caregivers (resources), optionally filtered by a qualification name "
        "(e.g. 'Dementia Care'), a region name, or a text search. Returns each "
        "caregiver's qualification and region names."
    ),
    input_schema=_obj({
        "qualification": {"type": "string", "description": "Qualification name, e.g. 'Dementia Care'."},
        "region": {"type": "string", "description": "Region name the caregiver serves."},
        "search": {"type": "string", "description": "Case-insensitive match on name or email."},
        "limit": {"type": "integer", "default": 20, "maximum": 100},
    }),
    handler=_list_resources,
))

register(ToolDef(
    name="get_resource_availability",
    description=(
        "Get one caregiver's weekly availability plus their upcoming scheduled "
        "visits, by caregiver id."
    ),
    input_schema=_obj({"resource_id": {"type": "string", "description": "The caregiver's id."}}, ["resource_id"]),
    handler=_get_resource_availability,
))

register(ToolDef(
    name="list_applicants",
    description=(
        "List caregiver job applicants (the hiring pipeline), optionally filtered by "
        "stage, source, or a text search over name/email/phone. Returns each "
        "applicant's qualification and region names."
    ),
    input_schema=_obj({
        "stage": {"type": "string", "enum": APPLICANT_STAGES, "description": "Hiring stage filter."},
        "source": {"type": "string", "description": "Where they applied from, e.g. 'indeed'."},
        "search": {"type": "string", "description": "Case-insensitive match on name, email, or phone."},
        "limit": {"type": "integer", "default": 20, "maximum": 100},
    }),
    handler=_list_applicants,
))

register(ToolDef(
    name="get_applicant",
    description=(
        "Get one caregiver applicant by id, including their qualification and region "
        "names, availability, notes, and cross-system external ids."
    ),
    input_schema=_obj({"applicant_id": {"type": "string", "description": "The applicant's id."}}, ["applicant_id"]),
    handler=_get_applicant,
))

register(ToolDef(
    name="list_schedules",
    description=(
        "List scheduled visits joining client and caregiver names, optionally "
        "filtered by client, caregiver, status, or a start-time date range "
        "(ISO-8601). Ordered by start time."
    ),
    input_schema=_obj({
        "client_id": {"type": "string", "description": "Filter to one client's visits."},
        "resource_id": {"type": "string", "description": "Filter to one caregiver's visits."},
        "status": {"type": "string", "enum": SCHEDULE_STATUSES, "description": "Visit status filter."},
        "date_from": {"type": "string", "description": "Only visits starting on/after this ISO-8601 time."},
        "date_to": {"type": "string", "description": "Only visits starting on/before this ISO-8601 time."},
        "limit": {"type": "integer", "default": 50, "maximum": 200},
    }),
    handler=_list_schedules,
))

register(ToolDef(
    name="list_expiring_credentials",
    description=(
        "List caregiver credentials (CPR, TB test, background check, licenses) that "
        "have expired or expire soon, for active caregivers only. Already-expired "
        "credentials are always included — an expired credential can mean a "
        "caregiver legally can't work a shift. Read-only."
    ),
    input_schema=_obj({
        "days_ahead": {
            "type": "integer",
            "default": EXPIRING_DAYS,
            "minimum": 1,
            "maximum": 365,
            "description": (
                "How far ahead to look, in days (default 60). Values outside 1–365 "
                "are clamped."
            ),
        },
    }),
    handler=_list_expiring_credentials,
))


# --- gated write tools (safe=False -> queued for human approval) --------------
register(ToolDef(
    name="update_lead_status",
    description=(
        "Change a lead's pipeline status (e.g. mark a lead as contacted or "
        "qualified). This changes a record and requires human approval before it "
        "takes effect."
    ),
    input_schema=_obj({
        "lead_id": {"type": "string", "description": "The lead's id."},
        "status": {"type": "string", "enum": LEAD_STATUSES, "description": "New pipeline status."},
    }, ["lead_id", "status"]),
    handler=_update_lead_status,
    safe=False,
    gate_describe=_describe_update_lead_status,
))

register(ToolDef(
    name="update_client_status",
    description=(
        "Change a client's status: 'active' (receiving care), 'hospital_hold' "
        "(temporarily admitted elsewhere; care resumes on discharge), or "
        "'discharged' (service ended). This changes a record and requires human "
        "approval before it takes effect."
    ),
    input_schema=_obj({
        "client_id": {"type": "string", "description": "The client's id."},
        "status": {"type": "string", "enum": CLIENT_STATUSES, "description": "New client status."},
    }, ["client_id", "status"]),
    handler=_update_client_status,
    safe=False,
    gate_describe=_describe_update_client_status,
))

register(ToolDef(
    name="record_visit_check_in",
    description=(
        "Record that the caregiver has clocked in to a scheduled visit (Electronic "
        "Visit Verification). Defaults to now; pass `time` for a back-dated stamp. "
        "This changes a billing-relevant care record and requires human approval "
        "before it takes effect."
    ),
    input_schema=_obj({
        "schedule_id": {"type": "string", "description": "The scheduled visit's id."},
        "time": {
            "type": "string",
            "description": "When they clocked in (ISO-8601). Omit for now.",
        },
    }, ["schedule_id"]),
    handler=_record_visit_check_in,
    safe=False,
    gate_describe=_describe_record_visit_check_in,
))

register(ToolDef(
    name="record_visit_check_out",
    description=(
        "Record that the caregiver has clocked out of a visit they checked in to "
        "(Electronic Visit Verification). This also marks the visit completed, and "
        "the clocked duration becomes the visit's delivered hours. Defaults to now; "
        "pass `time` for a back-dated stamp. This changes a billing-relevant care "
        "record and requires human approval before it takes effect."
    ),
    input_schema=_obj({
        "schedule_id": {"type": "string", "description": "The visit's id."},
        "time": {
            "type": "string",
            "description": "When they clocked out (ISO-8601). Omit for now.",
        },
    }, ["schedule_id"]),
    handler=_record_visit_check_out,
    safe=False,
    gate_describe=_describe_record_visit_check_out,
))

register(ToolDef(
    name="get_census",
    description=(
        "Get this week's client census and hours: how many active clients (by "
        "region and payer), how many hours a week they are authorized for, how many "
        "hours are scheduled, how many were actually delivered, how many shifts are "
        "still unfilled, and the gap between authorized and delivered hours. "
        "Read-only."
    ),
    input_schema=_obj({}),
    handler=_get_census,
    safe=True,
))

register(ToolDef(
    name="create_schedule",
    description=(
        "Schedule a visit over a time window (ISO-8601 start/end; end after start). "
        "Assign a caregiver by passing resource_id, or omit it to create an unfilled "
        "'open' shift to staff later. Optionally require qualifications, add notes, or "
        "repeat weekly through a date (up to 12 extra visits). This creates a record "
        "and requires human approval before it takes effect."
    ),
    input_schema=_obj({
        "client_id": {"type": "string", "description": "The client's id."},
        "resource_id": {"type": "string", "description": "The caregiver's id. Omit for an open (unfilled) shift."},
        "start_time": {"type": "string", "description": "Visit start (ISO-8601)."},
        "end_time": {"type": "string", "description": "Visit end (ISO-8601), after start."},
        "required_qualification_ids": {
            "type": "array", "items": {"type": "string"},
            "description": "Qualification ids the caregiver must hold for this visit.",
        },
        "notes": {"type": "string", "description": "Optional free-text note on the visit."},
        "repeat_weekly_until": {
            "type": "string",
            "description": "Repeat weekly through this date (ISO-8601); up to 12 extra visits.",
        },
    }, ["client_id", "start_time", "end_time"]),
    handler=_create_schedule,
    safe=False,
    gate_describe=_describe_create_schedule,
))

register(ToolDef(
    name="cancel_schedule",
    description=(
        "Cancel a scheduled or open visit by its id (not one already completed, "
        "called out, or cancelled). This changes a record and requires human "
        "approval before it takes effect."
    ),
    input_schema=_obj({
        "schedule_id": {"type": "string", "description": "The visit's id."},
    }, ["schedule_id"]),
    handler=_cancel_schedule,
    safe=False,
    gate_describe=_describe_cancel_schedule,
))

register(ToolDef(
    name="find_available_caregivers",
    description=(
        "Rank caregivers for a shift, with a plain-language reason and any warnings "
        "for each. Pass an open shift's schedule_id, or describe an ad-hoc window "
        "with client_id + start_time + end_time (ISO-8601) and optional "
        "required_qualification_ids. Read-only — it suggests, it does not assign."
    ),
    input_schema=_obj({
        "schedule_id": {"type": "string", "description": "An open shift to rank caregivers for."},
        "client_id": {"type": "string", "description": "Client for an ad-hoc window (with start/end)."},
        "start_time": {"type": "string", "description": "Window start (ISO-8601), for an ad-hoc rank."},
        "end_time": {"type": "string", "description": "Window end (ISO-8601), for an ad-hoc rank."},
        "required_qualification_ids": {
            "type": "array", "items": {"type": "string"},
            "description": "Qualification ids required for an ad-hoc window.",
        },
    }),
    handler=_find_available_caregivers,
    safe=True,
))

register(ToolDef(
    name="record_call_out",
    description=(
        "Record that the caregiver on a scheduled visit has called out. The visit is "
        "kept as 'called out' and a replacement open shift is created for the same "
        "window. This changes records and requires human approval before it takes effect."
    ),
    input_schema=_obj({
        "schedule_id": {"type": "string", "description": "The scheduled visit that was called out."},
    }, ["schedule_id"]),
    handler=_record_call_out,
    safe=False,
    gate_describe=_describe_record_call_out,
))

register(ToolDef(
    name="assign_caregiver",
    description=(
        "Assign a caregiver to an open or scheduled visit (filling or reassigning it). "
        "Qualification gaps and availability mismatches are returned as warnings, not "
        "blocks; a hard time conflict is refused. This changes a record and requires "
        "human approval before it takes effect."
    ),
    input_schema=_obj({
        "schedule_id": {"type": "string", "description": "The visit to assign."},
        "resource_id": {"type": "string", "description": "The caregiver to assign."},
    }, ["schedule_id", "resource_id"]),
    handler=_assign_caregiver,
    safe=False,
    gate_describe=_describe_assign_caregiver,
))

register(ToolDef(
    name="update_applicant_stage",
    description=(
        "Move a caregiver applicant to a new hiring stage (e.g. advance to interview "
        "or offer, or mark rejected). Moving an applicant to 'hired' also creates "
        "their caregiver record. This changes a record and requires human approval "
        "before it takes effect."
    ),
    input_schema=_obj({
        "applicant_id": {"type": "string", "description": "The applicant's id."},
        "stage": {"type": "string", "enum": APPLICANT_STAGES, "description": "New hiring stage."},
    }, ["applicant_id", "stage"]),
    handler=_update_applicant_stage,
    safe=False,
    gate_describe=_describe_update_applicant_stage,
))


# ---------------------------------------------------------------------------
# SQL_SCHEMA_DOC — injected into run_report's description (reporting.py). Concise
# table/column/enum reference for the allowlisted read surface.
# ---------------------------------------------------------------------------
SQL_SCHEMA_DOC = """\
Tables available for read-only reporting (rows are automatically filtered to the
current tenant — never add a tenant_id condition):

leads(id uuid, name text, phone text, email text, source text,
      status text {new|contacted|qualified|converted|lost},
      region_id uuid -> regions.id, requirements jsonb, created_at timestamptz)
clients(id uuid, lead_id uuid -> leads.id, name text, phone text, email text,
        status text {active|hospital_hold|discharged}, requirements jsonb,
        address text, zip text, languages text[], preferences text[],
        region_id uuid -> regions.id,
        payer text {private_pay|medicaid|ltc_insurance|va|other} (NULL = unknown),
        authorized_hours_per_week numeric (contracted hours/week — the census
          denominator; authorized minus delivered is revenue leakage),
        care_summary text, created_at timestamptz)
client_contacts(id uuid, client_id uuid -> clients.id, name text, relationship text,
                phone text, email text, is_primary boolean, notes text,
                created_at timestamptz)  -- family / POA contacts for a client
resources(id uuid, name text, phone text, email text, qualification_ids uuid[],
          region_ids uuid[], availability jsonb, address text, zip text,
          languages text[], traits text[], applicant_id uuid -> applicants.id,
          status text {active|inactive}, created_at timestamptz)
          -- caregivers (applicant_id set when hired from an applicant). An
          -- 'inactive' caregiver is excluded from matching and the schedule board;
          -- filter `status = 'active'` for anything about who can work today.
resource_credentials(id uuid, resource_id uuid -> resources.id,
                     qualification_id uuid -> qualifications.id,
                     issued_at date, expires_at date (NULL = does not expire),
                     notes text, created_at timestamptz)
                     -- DATED evidence over the qualification vocabulary, one row per
                     -- (caregiver, qualification). Expiry status is derived, not
                     -- stored: expires_at < current_date = expired; within 60 days =
                     -- expiring. resources.qualification_ids remains the "has this
                     -- skill" matching input.
applicants(id uuid, name text, phone text, email text, source text,
           stage text {applied|screening|interview|offer|hired|rejected},
           qualification_ids uuid[], region_ids uuid[], availability jsonb,
           notes text, created_at timestamptz)  -- caregiver-recruiting pipeline
schedules(id uuid, resource_id uuid -> resources.id (NULL for an unfilled 'open' shift),
          client_id uuid -> clients.id, start_time timestamptz, end_time timestamptz,
          status text {open|scheduled|called_out|completed|cancelled|no_show},
          required_qualification_ids uuid[], replaces_schedule_id uuid -> schedules.id,
          notes text, check_in_at timestamptz, check_out_at timestamptz,
          created_at timestamptz)
          -- check_in_at/check_out_at are the EVV clock stamps. Delivered hours for a
          -- completed visit = the clocked duration when both stamps exist, else the
          -- scheduled window (end_time - start_time).
referral_partners(id uuid, name text, category text
                  {hospital|senior_living|discharge_planner|home_health|community|other}
                  (NULL = untyped), contact_name text, phone text, email text,
                  notes text, created_at timestamptz)
                  -- tracked referral sources. Join to leads by EXACT name:
                  -- `join referral_partners p on p.name = l.source` — which partners
                  -- send leads that convert. leads.source is free text; an untracked
                  -- source simply has no matching partner row.
regions(id uuid, name text, zip_codes text[])
qualifications(id uuid, name text, description text)
events(id uuid, source_system text, event_type text, entity_type text,
       entity_id uuid, payload jsonb, created_at timestamptz)  -- immutable audit log
tasks(id uuid, title text, description text, status text, priority text,
      originating_event_id uuid -> events.id, assigned_to text, due_at timestamptz,
      created_at timestamptz, resolved_at timestamptz)
pending_actions(id uuid, task_id uuid -> tasks.id, tool_name text, tool_input jsonb,
                status text {pending|approved|rejected|executed|failed},
                source_system text, result jsonb, resolved_by text,
                created_at timestamptz, resolved_at timestamptz)
external_ids(id uuid, entity_type text, entity_id uuid, source_system text,
             external_id text, last_synced_at timestamptz)
documents(id uuid, filename text, mime_type text,
          status text {uploaded|processing|ready|failed},
          entity_type text, entity_id uuid,  -- optional canonical-entity tag
          created_at timestamptz)

resources.qualification_ids / region_ids are uuid arrays: filter with
`<id> = any(qualification_ids)`, or `join qualifications q on q.id = any(r.qualification_ids)`
to get qualification names.
"""
