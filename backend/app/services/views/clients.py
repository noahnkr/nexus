"""Clients view — vertical content seam (Module 16a).

The one place client oversight's *meaning* lives on the server: the status config,
the single status-writing path, the census math, and the read-time EVV rules. Core
code never imports this — `routers/clients.py` (itself seam) and the client tool
handlers (`services/tools/entities.py`, also seam) are the only readers. It is the
clients instance of the M9/M10 seam convention (`views/leads.py` and
`views/caregivers.py` are the 1:1 templates), and the fourth sanctioned vertical
surface alongside Leads, Caregivers, and the Schedule board.

Three things live here that a different vertical would rewrite wholesale:

  * STATUS CONFIG + `change_status()` — the ONE writer of `clients.status`. Both
    the REST PATCH and the gated `update_client_status` tool delegate here, on the
    caller's tenant-scoped connection *inside the caller's transaction* (it must
    NOT open its own tenant_tx — a tool handler already holds one via
    execute_tool). One event emitter, so a coordinator's UI click and a
    chat/MCP-approved change are indistinguishable in the timeline and can't
    diverge (the views/caregivers.move_stage and views/schedule precedent).

  * CENSUS MATH — `census_metrics()` / `client_week_hours()`. Deterministic SQL,
    no LLM anywhere near the numbers (CLAUDE.md). The number that matters is
    LEAKAGE: authorized hours the business is contracted (and paid) to deliver,
    minus hours actually delivered. Delivered is actuals-first — the EVV clock
    duration when both stamps exist, the scheduled window otherwise.

  * EVV FLAGS — `evv_flag()`, computed at READ time from the clock stamps and the
    in-seam grace constant. No stored flag column, no detector loop, no cron
    writer (user-locked): a visit's lateness is a function of the clock and the
    current time, and deriving it on read means it can never go stale. `no_show`
    stays the explicit, human-recorded terminal status — `missed` is just "nobody
    has clocked in and the window has passed".
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from psycopg.rows import dict_row

from ..events import log_event

# ---------------------------------------------------------------------------
# status + payer config (mirrored on the frontend in lib/clients.ts)
# ---------------------------------------------------------------------------
# Home care does not run on a generic active/paused/ended lifecycle. A client on
# HOSPITAL HOLD is still ours — their authorized hours are suspended, not gone,
# and they come back. DISCHARGED is the end of service. There is deliberately no
# delete: statuses end a client, history stays.
CLIENT_STATUSES: list[dict] = [
    {"key": "active", "label": "Active", "terminal": False},
    {"key": "hospital_hold", "label": "Hospital hold", "terminal": False},
    {"key": "discharged", "label": "Discharged", "terminal": True},
]

STATUS_KEYS: list[str] = [s["key"] for s in CLIENT_STATUSES]
_STATUS_LABELS: dict[str, str] = {s["key"]: s["label"] for s in CLIENT_STATUSES}

# Who pays. Nullable in the DB (an intake in progress has no payer yet), so the
# census buckets a null payer as "unknown" rather than dropping the client.
CLIENT_PAYERS: list[dict] = [
    {"key": "private_pay", "label": "Private pay"},
    {"key": "medicaid", "label": "Medicaid"},
    {"key": "ltc_insurance", "label": "LTC insurance"},
    {"key": "va", "label": "VA"},
    {"key": "other", "label": "Other"},
]
PAYER_KEYS: list[str] = [p["key"] for p in CLIENT_PAYERS]
_PAYER_LABELS: dict[str, str] = {p["key"]: p["label"] for p in CLIENT_PAYERS}


def is_valid_status(status: str | None) -> bool:
    return status in _STATUS_LABELS


def status_label(status: str | None) -> str:
    """Plain label for a status value. Falls back to the raw value so an
    unrecognized status never crashes a summary or a task title."""
    if status is None:
        return "—"
    return _STATUS_LABELS.get(status, status)


def payer_label(payer: str | None) -> str:
    if payer is None:
        return "Unknown"
    return _PAYER_LABELS.get(payer, payer)


# --- Smart summary (Task 5) — the only vertical content the generic helper needs.
CLIENT_SUMMARY_INTRO = (
    "You summarize a home-care client for the office staff coordinating their "
    "care. In 2-4 sentences say who the client is, their current status and who "
    "pays, what care they need and how many hours a week they are authorized for, "
    "who the family contact is, and anything in the recent activity a coordinator "
    "should act on (a hospital hold, missed visits, a gap between authorized and "
    "delivered hours)."
)
CLIENT_SUMMARY_SPAN = "client_summary"


class ClientError(Exception):
    """A rejected client write. `not_found` (unknown client) maps to a router 404;
    otherwise the router uses 422. The tool handlers surface either as a plain
    ToolInputError."""

    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


# ---------------------------------------------------------------------------
# change_status() — THE single writer of clients.status.
# ---------------------------------------------------------------------------
async def change_status(
    conn, tenant_id: str, source_system: str, client_id: str, status: str
) -> dict:
    """Move one client to `status`, emitting `client.status_changed` in the caller's
    transaction. Returns `{changed, from, to, name}`.

    A no-op (target == current) returns `changed=False` and emits nothing — a
    re-submitted form must not litter the timeline. Raises ClientError for an
    invalid status or an unknown client."""
    if not is_valid_status(status):
        raise ClientError(f"invalid status; must be one of: {', '.join(STATUS_KEYS)}")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select name, status from public.clients where id = %s for update",
            (client_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise ClientError("client not found", not_found=True)

    name, current = row["name"], row["status"]
    if status == current:
        return {"changed": False, "from": current, "to": current, "name": name}

    await conn.execute(
        "update public.clients set status = %s where id = %s", (status, client_id)
    )

    # Plain language for the timeline: "placed on hospital hold" reads like a care
    # coordinator wrote it; "status changed from hospital_hold to active" does not.
    if status == "hospital_hold":
        phrase = f"Client '{name}' placed on hospital hold"
    elif status == "discharged":
        phrase = f"Client '{name}' discharged"
    elif current == "hospital_hold":
        phrase = f"Client '{name}' returned to active care"
    else:
        phrase = f"Client '{name}' set to {status_label(status).lower()}"

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="client.status_changed",
        entity_type="client",
        entity_id=client_id,
        payload={"summary": phrase, "from": current, "to": status},
    )
    return {"changed": True, "from": current, "to": status, "name": name}


# ---------------------------------------------------------------------------
# EVV read-time flags
# ---------------------------------------------------------------------------
# A caregiver running a few minutes behind is not a compliance event. Past this
# grace, a visit with nobody clocked in is worth surfacing on the board.
EVV_GRACE_MINUTES = 15


def evv_flag(visit: dict, now: datetime | None = None) -> str | None:
    """Read-time EVV status for one visit row: 'late', 'missed', or None.

    Only a `scheduled` visit with no check-in can be flagged — once someone clocks
    in, or the visit reaches a terminal status (completed / no_show / cancelled /
    called_out), the record speaks for itself and no derived flag applies.

    Pure function of the row + the clock, so the board feed, the client profile,
    and any future caller all agree without a stored column to keep in sync."""
    if visit.get("status") != "scheduled":
        return None
    if visit.get("check_in_at") is not None:
        return None
    start, end = visit.get("start_time"), visit.get("end_time")
    if start is None or end is None:
        return None

    now = now or datetime.now(timezone.utc)
    if now > end:
        return "missed"
    if now > start + timedelta(minutes=EVV_GRACE_MINUTES):
        return "late"
    return None


# ---------------------------------------------------------------------------
# census math
# ---------------------------------------------------------------------------
# The board's week and the census week must be the same week, or the two surfaces
# disagree about the same visits. Monday-start, matching services/views/schedule.
def week_bounds(week_start: date | datetime | None = None) -> tuple[datetime, datetime]:
    """[Monday 00:00, next Monday 00:00) containing `week_start` (default: now).

    Accepts a plain `date` as well as a datetime, because `routers/schedule.py`
    resolves its `?week=` param to a date — both surfaces must land on the same
    Monday or the board and the census would disagree about the same visits."""
    anchor = week_start or datetime.now(timezone.utc)
    if not isinstance(anchor, datetime):
        anchor = datetime.combine(anchor, time.min)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    monday = (anchor - timedelta(days=anchor.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=7)


# Visits that represent hours the business committed to staffing this week. An
# `open` shift is deliberately NOT here — it is reported separately as unfilled
# hours, because counting it as "scheduled" would hide the staffing gap.
_SCHEDULED_STATUSES = ("scheduled", "completed", "no_show", "called_out")

# Delivered hours, actuals-first: the clocked duration when BOTH EVV stamps exist,
# the scheduled window otherwise (user-locked). Only completed visits count — a
# no-show delivered nothing, and that gap is exactly what leakage measures.
_DELIVERED_SQL = """
    coalesce(
      extract(epoch from (s.check_out_at - s.check_in_at)),
      extract(epoch from (s.end_time - s.start_time))
    ) / 3600.0
"""

_SCHEDULED_HOURS_SQL = "extract(epoch from (s.end_time - s.start_time)) / 3600.0"


def _h(value) -> float:
    """Hours, rounded to a tenth — the resolution a coordinator reads in."""
    return round(float(value or 0.0), 1)


async def census_metrics(conn, week_start: date | datetime | None = None) -> dict:
    """Active-census + hours snapshot for the clients dashboard.

    Counts are over ACTIVE clients only (a discharged client is not census, and a
    hospital hold is not currently consuming hours). Hours are over the Monday week
    containing `week_start`, with visits attributed by `start_time`.

    Returns authorized / scheduled / delivered / open hours, the leakage gap, and
    the delivery rate. Empty tenant -> zeroes and a null rate, never a 500."""
    start, end = week_bounds(week_start)
    window = {"start": start, "end": end}

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select count(*) as n, coalesce(sum(authorized_hours_per_week), 0) as hours "
            "from public.clients where status = 'active'"
        )
        active = await cur.fetchone()

        await cur.execute(
            """select c.region_id, r.name as region, count(*) as n
                 from public.clients c
                 left join public.regions r on r.id = c.region_id
                where c.status = 'active'
                group by c.region_id, r.name
                order by n desc, r.name nulls last"""
        )
        by_region = [
            {
                "region_id": str(r["region_id"]) if r["region_id"] else None,
                "region": r["region"] or "Unassigned",
                "count": r["n"],
            }
            for r in await cur.fetchall()
        ]

        await cur.execute(
            """select coalesce(payer, 'unknown') as payer, count(*) as n
                 from public.clients where status = 'active'
                group by coalesce(payer, 'unknown')
                order by n desc, payer"""
        )
        by_payer = [{"payer": r["payer"], "count": r["n"]} for r in await cur.fetchall()]

        await cur.execute(
            f"""select coalesce(sum({_SCHEDULED_HOURS_SQL}), 0) as hours
                  from public.schedules s
                 where s.status = any(%(statuses)s)
                   and s.start_time >= %(start)s and s.start_time < %(end)s""",
            {**window, "statuses": list(_SCHEDULED_STATUSES)},
        )
        scheduled_hours = (await cur.fetchone())["hours"]

        await cur.execute(
            f"""select coalesce(sum({_DELIVERED_SQL}), 0) as hours
                  from public.schedules s
                 where s.status = 'completed'
                   and s.start_time >= %(start)s and s.start_time < %(end)s""",
            window,
        )
        delivered_hours = (await cur.fetchone())["hours"]

        await cur.execute(
            f"""select coalesce(sum({_SCHEDULED_HOURS_SQL}), 0) as hours
                  from public.schedules s
                 where s.status = 'open'
                   and s.start_time >= %(start)s and s.start_time < %(end)s""",
            window,
        )
        open_hours = (await cur.fetchone())["hours"]

    authorized = _h(active["hours"])
    delivered = _h(delivered_hours)
    return {
        "week_start": start,
        "week_end": end,
        "active_clients": active["n"],
        "by_region": by_region,
        "by_payer": by_payer,
        "authorized_hours": authorized,
        "scheduled_hours": _h(scheduled_hours),
        "delivered_hours": delivered,
        "open_hours": _h(open_hours),
        # Clamped at zero: delivering MORE than authorized is an overtime/billing
        # question, not leakage, and a negative "leakage" number reads as nonsense.
        "leakage_hours": _h(max(authorized - delivered, 0.0)),
        "delivery_rate": (
            round(100.0 * delivered / authorized, 1) if authorized else None
        ),
    }


async def client_week_hours(
    conn, client_id: str, week_start: date | datetime | None = None
) -> dict:
    """The same hours math scoped to one client (the profile's hours card and the
    `get_client` tool). Same window, same actuals-first delivered rule, so the
    profile can never disagree with the census strip."""
    start, end = week_bounds(week_start)
    window = {"start": start, "end": end, "cid": client_id}

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select authorized_hours_per_week from public.clients where id = %s",
            (client_id,),
        )
        row = await cur.fetchone()
        authorized = _h(row["authorized_hours_per_week"]) if row else 0.0

        await cur.execute(
            f"""select
                  coalesce(sum({_SCHEDULED_HOURS_SQL})
                           filter (where s.status = any(%(statuses)s)), 0) as scheduled,
                  coalesce(sum({_DELIVERED_SQL})
                           filter (where s.status = 'completed'), 0) as delivered,
                  coalesce(sum({_SCHEDULED_HOURS_SQL})
                           filter (where s.status = 'open'), 0) as open_hours
                from public.schedules s
               where s.client_id = %(cid)s
                 and s.start_time >= %(start)s and s.start_time < %(end)s""",
            {**window, "statuses": list(_SCHEDULED_STATUSES)},
        )
        hours = await cur.fetchone()

    delivered = _h(hours["delivered"])
    return {
        "week_start": start,
        "week_end": end,
        "authorized_hours": authorized,
        "scheduled_hours": _h(hours["scheduled"]),
        "delivered_hours": delivered,
        "open_hours": _h(hours["open_hours"]),
        "leakage_hours": _h(max(authorized - delivered, 0.0)),
        "delivery_rate": (
            round(100.0 * delivered / authorized, 1) if authorized else None
        ),
    }
