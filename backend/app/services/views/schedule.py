"""Schedule transition seam — Module 12a vertical content seam.

The ONE writer of schedule state. Both the REST routes (routers/schedule.py) and the
gated tool handlers (services/tools/entities.py) delegate here, on the caller's
tenant-scoped connection *inside the caller's transaction* — it never opens its own
tenant_tx (a tool handler already holds one via execute_tool; a route holds its own).
One event emitter per transition, so a coordinator's board click and a chat/MCP-
approved action are indistinguishable in the timeline and can't diverge (the
views/caregivers.move_stage precedent).

Seven transitions, each with its first-class event:
  create_visits  -> schedule.created (per row)
  assign         -> schedule.assigned
  call_out       -> schedule.called_out (original, carries replacement id)
                    + schedule.created (replacement open shift)
  cancel         -> schedule.cancelled
  set_outcome    -> schedule.updated (past-visit bookkeeping to completed/no_show)
  check_in       -> schedule.checked_in  (EVV clock-in, M16a)
  check_out      -> schedule.checked_out (EVV clock-out; ALSO completes the visit)

Statuses live in schedules.status (12a migration CHECK) with coherence CHECKs tying
resource presence to status. This is a re-templating-seam member alongside
matching.py, the entity migration, tools/entities.py, and the connector writers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row

from ..events import log_event
from .matching import availability_covers, missing_qualification_names

# A repeat-weekly create expands to at most this many EXTRA occurrences beyond the
# first (13 rows total). Bounds one all-or-nothing transaction (user-locked).
MAX_REPEAT_WEEKS = 12


class ScheduleError(Exception):
    """A rejected schedule transition. `not_found` (unknown visit) maps to a router
    404; `conflict` (hard time-overlap) maps to 409; otherwise the router uses 422.
    The tool handlers surface any of them as a plain ToolInputError."""

    def __init__(self, message: str, *, not_found: bool = False, conflict: bool = False):
        super().__init__(message)
        self.not_found = not_found
        self.conflict = conflict


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _current_tenant(conn) -> str:
    async with conn.cursor() as cur:
        await cur.execute("select app.current_tenant_id()")
        return str((await cur.fetchone())[0])


async def _name(conn, table: str, entity_id: str) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute(f"select name from public.{table} where id = %s", (entity_id,))
        row = await cur.fetchone()
    return row[0] if row else None


def _fmt_window(start: datetime, end: datetime) -> str:
    """'Tue Jul 21 8:00–12:00' — plain, leading-zero-free hour, for event summaries."""
    day = f"{start.strftime('%a %b')} {start.day}"
    return f"{day} {start.hour}:{start.minute:02d}–{end.hour}:{end.minute:02d}"


async def _visit(conn, schedule_id: str, *, lock: bool = False) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select * from public.schedules where id = %s{' for update' if lock else ''}",
            (schedule_id,),
        )
        return await cur.fetchone()


async def _has_overlap(conn, resource_id: str, start, end, *, exclude_id: str | None) -> bool:
    """True iff the caregiver already holds a scheduled/called_out visit overlapping
    [start, end). This is the hard-conflict check every write path shares."""
    async with conn.cursor() as cur:
        await cur.execute(
            """select 1 from public.schedules
                where resource_id = %(rid)s
                  and status in ('scheduled','called_out')
                  and start_time < %(end)s and end_time > %(start)s
                  and (%(exclude)s::uuid is null or id <> %(exclude)s::uuid)
                limit 1""",
            {"rid": resource_id, "start": start, "end": end, "exclude": exclude_id},
        )
        return await cur.fetchone() is not None


# ---------------------------------------------------------------------------
# create_visits — one-off, assigned, or repeat-weekly. All-or-nothing in one tx.
# ---------------------------------------------------------------------------
async def create_visits(
    conn,
    *,
    client_id: str,
    resource_id: str | None = None,
    start: datetime,
    end: datetime,
    required_qualification_ids: list[str] | None = None,
    notes: str | None = None,
    repeat_weekly_until: datetime | None = None,
    source_system: str,
) -> list[dict]:
    """Create one visit, or a weekly series through `repeat_weekly_until` (capped at
    MAX_REPEAT_WEEKS extra rows). Status is derived: 'open' iff no caregiver, else
    'scheduled'. For an assigned caregiver, ANY occurrence overlapping their existing
    visits rejects the whole series. Emits schedule.created per row. Returns the
    created rows."""
    if end <= start:
        raise ScheduleError("end must be after start")
    if await _name(conn, "clients", client_id) is None:
        raise ScheduleError("no client found with that id")
    resource_name = None
    if resource_id is not None:
        resource_name = await _name(conn, "resources", resource_id)
        if resource_name is None:
            raise ScheduleError("no caregiver found with that id")

    quals = [str(x) for x in (required_qualification_ids or [])]
    status = "scheduled" if resource_id else "open"

    # Build the occurrence windows (first + weekly repeats).
    windows: list[tuple[datetime, datetime]] = [(start, end)]
    if repeat_weekly_until is not None:
        n = 1
        while True:
            nxt_start = start + timedelta(weeks=n)
            if nxt_start > repeat_weekly_until:
                break
            if n > MAX_REPEAT_WEEKS:
                raise ScheduleError(
                    f"a weekly series is capped at {MAX_REPEAT_WEEKS} extra visits; "
                    "shorten the repeat window"
                )
            windows.append((nxt_start, end + timedelta(weeks=n)))
            n += 1

    # For an assigned series, reject the WHOLE thing if any occurrence conflicts.
    if resource_id is not None:
        for w_start, w_end in windows:
            if await _has_overlap(conn, resource_id, w_start, w_end, exclude_id=None):
                raise ScheduleError(
                    f"{resource_name} already has a visit overlapping "
                    f"{_fmt_window(w_start, w_end)}",
                    conflict=True,
                )

    tenant_id = await _current_tenant(conn)
    client_name = await _name(conn, "clients", client_id)
    created: list[dict] = []
    async with conn.cursor(row_factory=dict_row) as cur:
        for w_start, w_end in windows:
            await cur.execute(
                """insert into public.schedules
                     (tenant_id, resource_id, client_id, start_time, end_time, status,
                      required_qualification_ids, notes)
                   values (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s)
                   returning *""",
                (resource_id, client_id, w_start, w_end, status, quals, notes),
            )
            row = await cur.fetchone()
            created.append(dict(row))
            if resource_name:
                summary = (
                    f"Visit for {client_name} with {resource_name}, "
                    f"{_fmt_window(w_start, w_end)}"
                )
            else:
                summary = f"Open shift for {client_name}, {_fmt_window(w_start, w_end)}"
            await log_event(
                conn,
                tenant_id=tenant_id,
                source_system=source_system,
                event_type="schedule.created",
                entity_type="schedule",
                entity_id=str(row["id"]),
                payload={"summary": summary},
            )
    return created


# ---------------------------------------------------------------------------
# assign — fill an open shift or reassign a scheduled visit.
# ---------------------------------------------------------------------------
async def assign(conn, schedule_id: str, resource_id: str, source_system: str) -> dict:
    """Assign a caregiver to an open or scheduled visit. Qualification gaps and
    availability mismatches are WARNINGS in the result, not blocks — the owner
    outranks the score. A hard time-overlap with the caregiver's other visits
    REJECTS. Sets status 'scheduled', emits schedule.assigned. Returns
    {schedule_id, resource_id, status, warnings}."""
    visit = await _visit(conn, schedule_id, lock=True)
    if visit is None:
        raise ScheduleError("no visit found with that id", not_found=True)
    if visit["status"] not in ("open", "scheduled"):
        raise ScheduleError(
            f"can only assign an open or scheduled visit (this one is {visit['status']})"
        )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, name, qualification_ids, availability from public.resources "
            "where id = %s",
            (resource_id,),
        )
        resource = await cur.fetchone()
    if resource is None:
        raise ScheduleError("no caregiver found with that id")

    if await _has_overlap(conn, resource_id, visit["start_time"], visit["end_time"],
                          exclude_id=schedule_id):
        raise ScheduleError(
            f"{resource['name']} already has a visit overlapping "
            f"{_fmt_window(visit['start_time'], visit['end_time'])}",
            conflict=True,
        )

    # Soft warnings (owner may override): qualification gaps + availability.
    warnings: list[str] = []
    missing = await missing_qualification_names(
        conn, visit["required_qualification_ids"], resource["qualification_ids"]
    )
    if missing:
        warnings.append("Missing qualification: " + ", ".join(sorted(missing)))
    if not availability_covers(resource["availability"], visit["start_time"], visit["end_time"]):
        warnings.append("Outside their declared availability for this shift")

    await conn.execute(
        "update public.schedules set resource_id = %s, status = 'scheduled' where id = %s",
        (resource_id, schedule_id),
    )

    tenant_id = await _current_tenant(conn)
    client_name = await _name(conn, "clients", str(visit["client_id"]))
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="schedule.assigned",
        entity_type="schedule",
        entity_id=schedule_id,
        payload={
            "summary": (
                f"Shift for {client_name} on "
                f"{_fmt_window(visit['start_time'], visit['end_time'])} "
                f"filled by {resource['name']}"
            ),
            "resource_id": resource_id,
        },
    )
    return {
        "schedule_id": schedule_id,
        "resource_id": resource_id,
        "status": "scheduled",
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# call_out — a scheduled caregiver drops; open a replacement.
# ---------------------------------------------------------------------------
async def call_out(conn, schedule_id: str, source_system: str) -> dict:
    """Record a call-out on a scheduled visit that hasn't ended. The original is
    retained as 'called_out' (who dropped stays queryable) and a linked open
    replacement is created for the same window/quals/notes. Emits schedule.called_out
    on the original (payload carries replacement_schedule_id — the automation trigger
    for call-out sequences) and schedule.created on the replacement. Returns
    {schedule_id, replacement_schedule_id}."""
    visit = await _visit(conn, schedule_id, lock=True)
    if visit is None:
        raise ScheduleError("no visit found with that id", not_found=True)
    if visit["status"] != "scheduled":
        raise ScheduleError(
            f"only a scheduled visit can be called out (this one is {visit['status']})"
        )

    tenant_id = await _current_tenant(conn)
    resource_name = await _name(conn, "resources", str(visit["resource_id"]))
    client_name = await _name(conn, "clients", str(visit["client_id"]))

    await conn.execute(
        "update public.schedules set status = 'called_out' where id = %s", (schedule_id,)
    )

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.schedules
                 (tenant_id, resource_id, client_id, start_time, end_time, status,
                  required_qualification_ids, notes, replaces_schedule_id)
               values (app.current_tenant_id(), null, %s, %s, %s, 'open', %s, %s, %s)
               returning id""",
            (
                visit["client_id"], visit["start_time"], visit["end_time"],
                visit["required_qualification_ids"], visit["notes"], schedule_id,
            ),
        )
        replacement_id = str((await cur.fetchone())["id"])

    window = _fmt_window(visit["start_time"], visit["end_time"])
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="schedule.called_out",
        entity_type="schedule",
        entity_id=schedule_id,
        payload={
            "summary": (
                f"{resource_name} called out of {window} with {client_name} — "
                "open shift created"
            ),
            "replacement_schedule_id": replacement_id,
        },
    )
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="schedule.created",
        entity_type="schedule",
        entity_id=replacement_id,
        payload={"summary": f"Open shift for {client_name}, {window}"},
    )
    return {"schedule_id": schedule_id, "replacement_schedule_id": replacement_id}


# ---------------------------------------------------------------------------
# cancel — terminal verb for a scheduled or open visit.
# ---------------------------------------------------------------------------
async def cancel(conn, schedule_id: str, source_system: str) -> dict:
    """Cancel a scheduled or open visit. Cancelling a replacement does NOT resurrect
    the original called-out visit (the original stays 'called_out' for history).
    Emits schedule.cancelled. Returns {schedule_id, status}."""
    visit = await _visit(conn, schedule_id, lock=True)
    if visit is None:
        raise ScheduleError("no visit found with that id", not_found=True)
    if visit["status"] not in ("scheduled", "open"):
        raise ScheduleError(
            f"only a scheduled or open visit can be cancelled (this one is {visit['status']})"
        )

    await conn.execute(
        "update public.schedules set status = 'cancelled' where id = %s", (schedule_id,)
    )
    tenant_id = await _current_tenant(conn)
    client_name = await _name(conn, "clients", str(visit["client_id"]))
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="schedule.cancelled",
        entity_type="schedule",
        entity_id=schedule_id,
        payload={
            "summary": (
                f"Visit for {client_name} on "
                f"{_fmt_window(visit['start_time'], visit['end_time'])} cancelled"
            )
        },
    )
    return {"schedule_id": schedule_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# set_outcome — past-visit bookkeeping to completed / no_show.
# ---------------------------------------------------------------------------
_OUTCOME_STATUSES = ("completed", "no_show")


async def set_outcome(conn, schedule_id: str, status: str, source_system: str) -> dict:
    """Record how a visit turned out (completed / no_show). Valid on a scheduled or
    called_out visit. Emits schedule.updated. Returns {schedule_id, status}."""
    if status not in _OUTCOME_STATUSES:
        raise ScheduleError(f"outcome must be one of: {', '.join(_OUTCOME_STATUSES)}")
    visit = await _visit(conn, schedule_id, lock=True)
    if visit is None:
        raise ScheduleError("no visit found with that id", not_found=True)
    if visit["status"] not in ("scheduled", "called_out"):
        raise ScheduleError(
            f"can only record an outcome on a scheduled or called-out visit "
            f"(this one is {visit['status']})"
        )
    if visit["resource_id"] is None:
        # A coherence CHECK forbids completed/no_show without a caregiver anyway.
        raise ScheduleError("this visit has no caregiver to record an outcome for")

    await conn.execute(
        "update public.schedules set status = %s where id = %s", (status, schedule_id)
    )
    tenant_id = await _current_tenant(conn)
    client_name = await _name(conn, "clients", str(visit["client_id"]))
    label = "completed" if status == "completed" else "marked no-show"
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="schedule.updated",
        entity_type="schedule",
        entity_id=schedule_id,
        payload={
            "summary": (
                f"Visit for {client_name} on "
                f"{_fmt_window(visit['start_time'], visit['end_time'])} {label}"
            ),
            "status": status,
        },
    )
    return {"schedule_id": schedule_id, "status": status}


# ---------------------------------------------------------------------------
# check_in / check_out — EVV clock stamps (Module 16a).
#
# Electronic Visit Verification is legally mandated for Medicaid-funded home care
# in most states: the record of when a caregiver actually arrived and left is a
# billing artifact, not a convenience. These are the ONLY writers of
# check_in_at/check_out_at — the board drawer, the gated agent tools, and (from
# Module 14) connector-fed telephony clock-ins all land here.
#
# Deliberately NOT symmetric with the other transitions: check_out also COMPLETES
# the visit. A caregiver clocking out *is* the visit finishing, and leaving the
# status at 'scheduled' would mean the delivered-hours math ignored the very visit
# whose actual duration we just recorded. `set_outcome` remains for manual
# bookkeeping when no clock data exists.
# ---------------------------------------------------------------------------
def _fmt_duration(delta: timedelta) -> str:
    """'4h 10m' — the plain form a coordinator reads in an event summary."""
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(max(minutes, 0), 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    return f"{hours}h" if hours else f"{minutes}m"


async def check_in(
    conn, schedule_id: str, source_system: str, at: datetime | None = None
) -> dict:
    """Record a caregiver clocking in to a scheduled visit. Valid only on a
    `scheduled` visit that has a caregiver and no existing check-in. `at` defaults
    to now. Emits schedule.checked_in. Returns {schedule_id, check_in_at}."""
    visit = await _visit(conn, schedule_id, lock=True)
    if visit is None:
        raise ScheduleError("no visit found with that id", not_found=True)
    if visit["status"] != "scheduled":
        raise ScheduleError(
            f"can only check in to a scheduled visit (this one is {visit['status']})"
        )
    if visit["resource_id"] is None:
        raise ScheduleError("this visit has no caregiver to check in")
    if visit["check_in_at"] is not None:
        raise ScheduleError("this visit is already checked in")

    stamp = at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    await conn.execute(
        "update public.schedules set check_in_at = %s where id = %s", (stamp, schedule_id)
    )

    tenant_id = await _current_tenant(conn)
    resource_name = await _name(conn, "resources", str(visit["resource_id"]))
    client_name = await _name(conn, "clients", str(visit["client_id"]))
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="schedule.checked_in",
        entity_type="schedule",
        entity_id=schedule_id,
        payload={
            "summary": (
                f"{resource_name} checked in to the visit with {client_name} — "
                f"{_fmt_window(visit['start_time'], visit['end_time'])}"
            ),
            "check_in_at": stamp.isoformat(),
        },
    )
    return {"schedule_id": schedule_id, "check_in_at": stamp}


async def check_out(
    conn, schedule_id: str, source_system: str, at: datetime | None = None
) -> dict:
    """Record a caregiver clocking out, which COMPLETES the visit in the same
    transition. Requires a prior check-in, no prior check-out, and a stamp after
    the check-in (the DB coherence CHECK enforces the last one too — this is the
    plain-language version of the same rule). Emits schedule.checked_out carrying
    the actual duration. Returns {schedule_id, check_out_at, status, actual_hours}."""
    visit = await _visit(conn, schedule_id, lock=True)
    if visit is None:
        raise ScheduleError("no visit found with that id", not_found=True)
    if visit["check_in_at"] is None:
        raise ScheduleError("this visit has not been checked in yet")
    if visit["check_out_at"] is not None:
        raise ScheduleError("this visit is already checked out")

    stamp = at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if stamp <= visit["check_in_at"]:
        raise ScheduleError("check-out must be after check-in")

    await conn.execute(
        "update public.schedules set check_out_at = %s, status = 'completed' where id = %s",
        (stamp, schedule_id),
    )

    worked = stamp - visit["check_in_at"]
    tenant_id = await _current_tenant(conn)
    resource_name = await _name(conn, "resources", str(visit["resource_id"]))
    client_name = await _name(conn, "clients", str(visit["client_id"]))
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="schedule.checked_out",
        entity_type="schedule",
        entity_id=schedule_id,
        payload={
            "summary": (
                f"{resource_name} checked out of the visit with {client_name} — "
                f"{_fmt_duration(worked)}"
            ),
            "check_out_at": stamp.isoformat(),
            "actual_hours": round(worked.total_seconds() / 3600.0, 2),
        },
    )
    return {
        "schedule_id": schedule_id,
        "check_out_at": stamp,
        "status": "completed",
        "actual_hours": round(worked.total_seconds() / 3600.0, 2),
    }
