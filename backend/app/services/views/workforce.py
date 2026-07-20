"""Workforce view — vertical content seam (Module 18a).

The one place caregiver *compliance and capacity* meaning lives on the server: what
"expiring" means, how much a caregiver could work vs. how much they are booked, and
which credentials need attention today. Core code never imports this — the seam
router (`routers/workforce.py`), the schedule router's roster, and the caregiver
tool handlers (`services/tools/entities.py`, also seam) are the only readers.

The Roster rides the Caregivers surface (it is NOT a fifth sanctioned vertical
surface): this file is a re-templating-seam member alongside `views/caregivers.py`,
`views/matching.py`, `views/clients.py`, and the entity migration.

Three things live here that a different vertical would rewrite wholesale:

  * CREDENTIAL STATUS — `credential_status()`, computed at READ time from
    `expires_at` and the in-seam `EXPIRING_DAYS` constant (the M16 `evv_flag`
    precedent, user-locked). No stored status column, no detector loop, no LLM: a
    credential's standing is a function of its date and today, and deriving it on
    read means it can never go stale after a renewal is entered.

  * UTILIZATION MATH — `available_week_hours()` / `roster_rows()` /
    `roster_metrics()`. Deterministic SQL and arithmetic, no LLM near the numbers
    (CLAUDE.md). Available hours come from the same `resources.availability` jsonb
    the matcher reads (via `matching._hm`), so "available" means one thing across
    the board, the matcher, and this page. A caregiver who has declared NO
    availability gets `utilization = None`, never 0% — we don't know their
    capacity, and printing 0% would read as "idle" when it means "unknown".

  * THE ACTIVE/INACTIVE LINE — an inactive caregiver is excluded from matching
    candidates (`views/matching.rank_candidates`) and from the schedule board's
    roster (`routers/schedule._roster`). `roster_rows()` here is the ONE surface
    that lists everyone, because the Roster tab is where you go to bring someone
    back. Compliance counts in `roster_metrics()` deliberately ignore inactive
    caregivers' credentials — a lapsed CPR for someone who isn't working is not a
    compliance emergency.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from psycopg.rows import dict_row

from .matching import _hm, week_hours_map

# How far ahead a credential counts as "expiring". In-seam constant, not config:
# one client, and the number is a policy statement the office user should be able
# to read in the code (the EVV grace-window precedent).
EXPIRING_DAYS = 60

# Resource lifecycle. Not a delete — home-care staff leave and come back, and their
# visit history has to survive either way.
RESOURCE_STATUSES: list[dict] = [
    {"key": "active", "label": "Active"},
    {"key": "inactive", "label": "Inactive"},
]
RESOURCE_STATUS_KEYS: list[str] = [s["key"] for s in RESOURCE_STATUSES]
_RESOURCE_STATUS_LABELS: dict[str, str] = {s["key"]: s["label"] for s in RESOURCE_STATUSES}

# Credential states, in the order the UI stacks them (worst first).
CREDENTIAL_STATUSES: list[str] = ["expired", "expiring", "valid", "no_expiry"]


def is_valid_resource_status(status: str | None) -> bool:
    return status in _RESOURCE_STATUS_LABELS


def resource_status_label(status: str | None) -> str:
    """Plain label for a resource status. Falls back to the raw value so an
    unrecognized status never crashes a summary or a task title."""
    if status is None:
        return "—"
    return _RESOURCE_STATUS_LABELS.get(status, status)


# ---------------------------------------------------------------------------
# credential status — the read-time derivation
# ---------------------------------------------------------------------------
def credential_status(expires_at: date | None, today: date | None = None) -> str:
    """'no_expiry' | 'expired' | 'expiring' | 'valid' for one credential.

    Pure function of the expiry date and the day it is read on — nothing is stored.
    A null `expires_at` is a credential that does not renew (a one-time sign-off),
    which is a distinct state from "valid until a date", so the UI can say so.
    Today counts as expiring, not expired: it is valid through its last day.
    """
    if expires_at is None:
        return "no_expiry"
    ref = today or date.today()
    if isinstance(expires_at, datetime):
        expires_at = expires_at.date()
    if expires_at < ref:
        return "expired"
    if expires_at <= ref + timedelta(days=EXPIRING_DAYS):
        return "expiring"
    return "valid"


def days_until(expires_at: date | None, today: date | None = None) -> int | None:
    """Whole days until expiry (negative once past). None for a no-expiry row."""
    if expires_at is None:
        return None
    ref = today or date.today()
    if isinstance(expires_at, datetime):
        expires_at = expires_at.date()
    return (expires_at - ref).days


# ---------------------------------------------------------------------------
# capacity math
# ---------------------------------------------------------------------------
def available_week_hours(availability: dict | None) -> float | None:
    """Declared hours per week from the `resources.availability` jsonb
    ({"mon": ["08:00-16:00"], …}), summing every parseable window across every day.

    `None` — not 0.0 — when nothing is declared: an empty availability means we do
    not know this caregiver's capacity, and a 0 would make utilization look like a
    real number (and divide by zero). Malformed windows are skipped rather than
    raising; the matcher already treats the jsonb as best-effort.
    """
    if not availability:
        return None
    total = 0.0
    found = False
    for ranges in availability.values():
        for r in (ranges or []):
            parts = str(r).split("-")
            if len(parts) != 2:
                continue
            lo, hi = _hm(parts[0]), _hm(parts[1])
            if lo is None or hi is None or hi <= lo:
                continue
            total += (hi - lo) / 60.0
            found = True
    return round(total, 2) if found else None


def utilization(scheduled_hours: float, available_hours: float | None) -> float | None:
    """Scheduled ÷ available, as a percentage. `None` when capacity is unknown.
    Not capped — a caregiver booked past their declared availability is exactly
    what the Roster tab needs to show, so >100% is a real, displayable number."""
    if not available_hours:
        return None
    return round(100.0 * scheduled_hours / available_hours, 1)


# ---------------------------------------------------------------------------
# roster + metrics
# ---------------------------------------------------------------------------
# Every credential in the tenant, worst-dated first — one query for the whole
# roster (low tens of caregivers, so a per-row fetch would be pure overhead).
_CREDENTIALS_SQL = """
select rc.id, rc.resource_id, rc.qualification_id, q.name as qualification_name,
       rc.issued_at, rc.expires_at, rc.notes
  from public.resource_credentials rc
  join public.qualifications q on q.id = rc.qualification_id
 order by rc.expires_at nulls last, q.name
"""


def _credential_out(row: dict, today: date) -> dict:
    return {
        "id": str(row["id"]),
        "resource_id": str(row["resource_id"]),
        "qualification_id": str(row["qualification_id"]),
        "qualification_name": row["qualification_name"],
        "issued_at": row["issued_at"],
        "expires_at": row["expires_at"],
        "status": credential_status(row["expires_at"], today),
        "days_left": days_until(row["expires_at"], today),
        "notes": row["notes"],
    }


async def roster_rows(conn, ref: date | datetime | str | None = None) -> list[dict]:
    """Every caregiver — ACTIVE AND INACTIVE — with capacity and credentials.

    This is the one surface that lists inactive people: the Roster tab is where an
    office user goes to reactivate someone or check a departed caregiver's file.
    The board and the matcher both filter to active.

    `ref` picks the ISO week `hours_this_week` is measured over (defaults to today),
    reusing `matching.week_hours_map` so the number matches the board exactly.
    """
    ref = ref or date.today()
    hours = await week_hours_map(conn, ref)
    today = date.today()

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, name, phone, email, status, address, zip, languages,
                      traits, qualification_ids, region_ids, availability
                 from public.resources order by name"""
        )
        resources = await cur.fetchall()
        await cur.execute(_CREDENTIALS_SQL)
        cred_rows = await cur.fetchall()

    by_resource: dict[str, list[dict]] = {}
    for row in cred_rows:
        by_resource.setdefault(str(row["resource_id"]), []).append(
            _credential_out(row, today)
        )

    out: list[dict] = []
    for r in resources:
        rid = str(r["id"])
        scheduled = round(hours.get(rid, 0.0), 2)
        available = available_week_hours(r["availability"])
        out.append({
            "id": rid,
            "name": r["name"],
            "phone": r["phone"],
            "email": r["email"],
            "status": r["status"],
            # address/zip ride along so the shared CaregiverDrawer can be opened
            # from the Roster tab without a second fetch — and so saving from
            # there can't blank a field the drawer never received.
            "address": r["address"],
            "zip": r["zip"],
            "languages": list(r["languages"] or []),
            "traits": list(r["traits"] or []),
            "qualification_ids": [str(x) for x in (r["qualification_ids"] or [])],
            "region_ids": [str(x) for x in (r["region_ids"] or [])],
            "availability": r["availability"] or {},
            "hours_this_week": scheduled,
            "available_hours": available,
            "utilization": utilization(scheduled, available),
            "credentials": by_resource.get(rid, []),
        })
    return out


async def roster_metrics(
    conn, ref: date | datetime | str | None = None, rows: list[dict] | None = None
) -> dict:
    """Headline compliance + capacity numbers for the Roster tab's strip.

    Computed from `roster_rows` (one pass, one definition of every number the page
    shows). `avg_utilization` averages only ACTIVE caregivers who have declared
    availability — including unknowns as zeroes would drag the number toward a
    meaningless floor. Credential counts likewise cover active caregivers only.

    Pass `rows` when the caller already has them (the router serves both in one
    response) so the roster query runs once, not twice.
    """
    if rows is None:
        rows = await roster_rows(conn, ref)
    active = [r for r in rows if r["status"] == "active"]

    utils = [r["utilization"] for r in active if r["utilization"] is not None]
    creds = [c for r in active for c in r["credentials"]]

    return {
        "active_count": len(active),
        "inactive_count": len(rows) - len(active),
        "avg_utilization": round(sum(utils) / len(utils), 1) if utils else None,
        "expiring_count": sum(1 for c in creds if c["status"] == "expiring"),
        "expired_count": sum(1 for c in creds if c["status"] == "expired"),
        "credential_count": len(creds),
    }


async def expiring_credentials(conn, days_ahead: int = EXPIRING_DAYS) -> list[dict]:
    """Active caregivers' credentials expiring within `days_ahead` days, soonest
    first — ALREADY-EXPIRED rows included, because "your CPR lapsed last week" is
    the most urgent version of this question, not a separate one.

    Each row is plain-language ready (caregiver + credential names, no UUIDs in the
    text) so the safe `list_expiring_credentials` tool and the daily digest
    automation can render it without a second lookup.
    """
    days_ahead = max(1, min(int(days_ahead), 365))
    today = date.today()
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select rc.id, rc.resource_id, r.name as caregiver, r.phone,
                      q.name as credential, rc.issued_at, rc.expires_at, rc.notes
                 from public.resource_credentials rc
                 join public.resources r on r.id = rc.resource_id
                 join public.qualifications q on q.id = rc.qualification_id
                where r.status = 'active'
                  and rc.expires_at is not null
                  and rc.expires_at <= current_date + %(days)s::int
                order by rc.expires_at, r.name""",
            {"days": days_ahead},
        )
        rows = await cur.fetchall()

    return [
        {
            "id": str(r["id"]),
            "resource_id": str(r["resource_id"]),
            "caregiver": r["caregiver"],
            "phone": r["phone"],
            "credential": r["credential"],
            "issued_at": r["issued_at"],
            "expires_at": r["expires_at"],
            "days_left": days_until(r["expires_at"], today),
            "status": credential_status(r["expires_at"], today),
            "notes": r["notes"],
        }
        for r in rows
    ]


def describe_expiry(row: dict) -> str:
    """One plain-language clause for a digest line: "Maria Santos's CPR expires in
    30 days" / "…expired 10 days ago" / "…expires today"."""
    days = row.get("days_left")
    who = f"{row['caregiver']}'s {row['credential']}"
    if days is None:
        return f"{who} has no expiry on file"
    if days < 0:
        n = abs(days)
        return f"{who} expired {n} day{'s' if n != 1 else ''} ago"
    if days == 0:
        return f"{who} expires today"
    return f"{who} expires in {days} day{'s' if days != 1 else ''}"
