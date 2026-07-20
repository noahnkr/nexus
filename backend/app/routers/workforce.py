"""Workforce & compliance API (Module 18a, vertical seam).

The human REST surface for the Caregivers page's Roster tab: the roster feed
(compliance/capacity metrics + one row per caregiver, computed by
`services/views/workforce.py`) and CRUD over dated `resource_credentials`. JWT
tenant-scoped like every `/api` route (RLS does all filtering, so no query mentions
tenant_id).

Credential writes are `source_system='user'` (the leads/clients/schedule/referrals
precedent): an office user recording a caregiver's renewal date is the approver, so
there is no approval gate here — the gate is for agent-initiated OUTBOUND effects,
and this page has none. Each write emits a plain-language `credential.*` event
carrying `entity_type='resource'` so it lands on the CAREGIVER's timeline; a
credential is dated evidence about a person, not an entity with a timeline of its
own.

Caregiver rows themselves are NOT written here — `routers/schedule.py::patch_roster`
remains the single writer of `resources` (including the active/inactive flag, which
emits `resource.status_changed`). One writer, one audit trail.

The Roster rides the Caregivers surface (not a fifth sanctioned surface): this router
and `services/views/workforce.py` are re-templating-seam members alongside
`routers/applicants.py`, `routers/schedule.py`, and the entity migration. Core never
imports them.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..deps import get_tenant_id
from ..schemas import (
    CredentialCreate,
    CredentialOut,
    CredentialPatch,
    RosterCaregiverOut,
    RosterMetrics,
    WorkforceRoster,
)
from ..services.events import log_event
from ..services.views.workforce import (
    credential_status,
    days_until,
    roster_metrics,
    roster_rows,
)

router = APIRouter(prefix="/api", tags=["workforce"])

_CREDENTIAL_FIELDS = ("issued_at", "expires_at", "notes")


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


def _week_ref(week: str | None):
    """Monday of the requested ISO week (the board's `?week=` convention), or None
    for the current week. Hours-this-week is measured over this window."""
    if not week:
        return None
    try:
        d = date.fromisoformat(week)
    except ValueError:
        raise HTTPException(status_code=422, detail="week must be YYYY-MM-DD")
    return d - timedelta(days=d.weekday())


def _fmt_date(value) -> str:
    """'Sep 12' — the date form used inside plain-language event summaries. Built
    by hand rather than with strftime('%-d'), which is not portable to Windows."""
    if not hasattr(value, "strftime"):
        return str(value)
    return f"{value.strftime('%b')} {value.day}"


async def _credential_row(conn, credential_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select rc.*, q.name as qualification_name, r.name as caregiver_name
                 from public.resource_credentials rc
                 join public.qualifications q on q.id = rc.qualification_id
                 join public.resources r on r.id = rc.resource_id
                where rc.id = %s""",
            (credential_id,),
        )
        return await cur.fetchone()


def _credential_out(row: dict) -> CredentialOut:
    today = date.today()
    return CredentialOut(
        id=str(row["id"]),
        resource_id=str(row["resource_id"]),
        qualification_id=str(row["qualification_id"]),
        qualification_name=row["qualification_name"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        status=credential_status(row["expires_at"], today),
        days_left=days_until(row["expires_at"], today),
        notes=row["notes"],
    )


def _expiry_phrase(expires_at) -> str:
    return f"expires {_fmt_date(expires_at)}" if expires_at else "no expiry"


# ---------------------------------------------------------------------------
# roster feed
# ---------------------------------------------------------------------------
@router.get("/workforce/roster", response_model=WorkforceRoster)
async def get_workforce_roster(conn=Depends(tenant_conn), week: str | None = None):
    """Compliance/capacity metrics plus one row per caregiver — ACTIVE AND INACTIVE
    (this is the one surface that lists everyone; the board and the matcher filter
    to active). `?week=YYYY-MM-DD` picks the ISO week `hours_this_week` and
    utilization are measured over. Deterministic seam math; empty tenant -> zeroes,
    never a 500."""
    ref = _week_ref(week)
    rows = await roster_rows(conn, ref)
    return WorkforceRoster(
        metrics=RosterMetrics(**await roster_metrics(conn, ref, rows=rows)),
        caregivers=[RosterCaregiverOut(**r) for r in rows],
    )


# ---------------------------------------------------------------------------
# credentials CRUD
# ---------------------------------------------------------------------------
@router.post("/workforce/credentials", response_model=CredentialOut, status_code=201)
async def create_credential(
    body: CredentialCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Record a dated credential on a caregiver. One row per (caregiver,
    qualification) — a second add for the same pair is a 409, not a duplicate; edit
    the existing row instead. Both ids are validated against RLS-scoped rows, so a
    cross-tenant id reads as "not found"."""
    resource_id = _valid_uuid(body.resource_id, "resource_id")
    qualification_id = _valid_uuid(body.qualification_id, "qualification_id")
    if body.issued_at and body.expires_at and body.expires_at < body.issued_at:
        raise HTTPException(status_code=422, detail="expires_at must be on/after issued_at")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select name from public.resources where id = %s", (resource_id,))
        resource = await cur.fetchone()
        if resource is None:
            raise HTTPException(status_code=404, detail="caregiver not found")
        await cur.execute(
            "select name from public.qualifications where id = %s", (qualification_id,)
        )
        qualification = await cur.fetchone()
        if qualification is None:
            raise HTTPException(status_code=404, detail="qualification not found")

        await cur.execute(
            """select 1 from public.resource_credentials
                where resource_id = %s and qualification_id = %s""",
            (resource_id, qualification_id),
        )
        if await cur.fetchone() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{resource['name']} already has a {qualification['name']} credential",
            )

        await cur.execute(
            """insert into public.resource_credentials
                 (tenant_id, resource_id, qualification_id, issued_at, expires_at, notes)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (tenant_id, resource_id, qualification_id, body.issued_at,
             body.expires_at, body.notes),
        )
        credential_id = str((await cur.fetchone())["id"])

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="credential.added",
        entity_type="resource",
        entity_id=resource_id,
        payload={
            "summary": (
                f"{qualification['name']} credential added for {resource['name']} "
                f"({_expiry_phrase(body.expires_at)})"
            ),
            "credential_id": credential_id,
            "qualification": qualification["name"],
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        },
    )
    row = await _credential_row(conn, credential_id)
    assert row is not None
    return _credential_out(row)


@router.patch("/workforce/credentials/{credential_id}", response_model=CredentialOut)
async def patch_credential(
    credential_id: str,
    body: CredentialPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Update a credential's dates or notes. Emits one `credential.updated` naming
    the changed fields; a no-op emits nothing (the referrals PATCH precedent)."""
    credential_id = _valid_uuid(credential_id, "credential_id")
    provided = body.model_fields_set

    current = await _credential_row(conn, credential_id)
    if current is None:
        raise HTTPException(status_code=404, detail="credential not found")

    updates: dict = {}
    for field in _CREDENTIAL_FIELDS:
        if field not in provided:
            continue
        value = getattr(body, field)
        if value != current[field]:
            updates[field] = value

    if not updates:
        return _credential_out(current)

    issued = updates.get("issued_at", current["issued_at"])
    expires = updates.get("expires_at", current["expires_at"])
    if issued and expires and expires < issued:
        raise HTTPException(status_code=422, detail="expires_at must be on/after issued_at")

    set_parts = [f"{f} = %s" for f in updates]
    async with conn.cursor() as cur:
        await cur.execute(
            f"update public.resource_credentials set {', '.join(set_parts)} where id = %s",
            list(updates.values()) + [credential_id],
        )

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="credential.updated",
        entity_type="resource",
        entity_id=str(current["resource_id"]),
        payload={
            "summary": (
                f"{current['qualification_name']} credential for "
                f"{current['caregiver_name']} updated ({_expiry_phrase(expires)})"
            ),
            "credential_id": credential_id,
            "qualification": current["qualification_name"],
            "fields": sorted(updates.keys()),
        },
    )
    row = await _credential_row(conn, credential_id)
    assert row is not None
    return _credential_out(row)


@router.delete("/workforce/credentials/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Remove a credential row. The caregiver KEEPS the underlying qualification —
    `resources.qualification_ids` is the matching input and is untouched here; only
    the dated evidence goes away."""
    credential_id = _valid_uuid(credential_id, "credential_id")
    current = await _credential_row(conn, credential_id)
    if current is None:
        raise HTTPException(status_code=404, detail="credential not found")

    async with conn.cursor() as cur:
        await cur.execute(
            "delete from public.resource_credentials where id = %s", (credential_id,)
        )

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="credential.removed",
        entity_type="resource",
        entity_id=str(current["resource_id"]),
        payload={
            "summary": (
                f"{current['qualification_name']} credential removed for "
                f"{current['caregiver_name']} (the qualification itself is kept)"
            ),
            "credential_id": credential_id,
            "qualification": current["qualification_name"],
        },
    )
    return None
