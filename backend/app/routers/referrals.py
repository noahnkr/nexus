"""Referrals dashboard API (Module 17, vertical seam).

The human REST surface for referral-source analytics: the metrics payload (per-source
conversion + hours-won, computed by `services/views/referrals.referral_metrics`) and
CRUD over tracked `referral_partners`. JWT tenant-scoped like every `/api` route (RLS
does all filtering, so no query mentions tenant_id).

Partner writes are `source_system='user'` (the leads/clients/schedule precedent): an
owner curating their own partner list is the approver, so there's no approval gate
here — the gate is for agent-initiated OUTBOUND effects, and this page has none. Each
write emits a plain-language `referral_partner.*` event; `updated` names the changed
fields and a no-op PATCH emits nothing.

The Referrals dashboard rides the Leads surface (not a fifth sanctioned surface): this
router and `services/views/referrals.py` are re-templating-seam members alongside
`routers/leads.py`, `routers/clients.py`, and the entity migration. Core never imports
them. Enrichment is exact-name join only — no FK, no backfill: deleting a partner only
un-enriches its source string, and the leads keep their `source` and funnel history.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..deps import get_tenant_id
from ..schemas import (
    PartnerCreate,
    PartnerOut,
    PartnerPatch,
    ReferralMetrics,
)
from ..services.events import log_event
from ..services.views.referrals import (
    CATEGORY_KEYS,
    category_label,
    is_valid_category,
    referral_metrics,
)

router = APIRouter(prefix="/api", tags=["referrals"])

# Fields a PATCH may write directly (all of them — a rename just re-joins by name).
_PARTNER_FIELDS = ("name", "category", "contact_name", "phone", "email", "notes")


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


def _check_category(category: str | None) -> None:
    if not is_valid_category(category):
        raise HTTPException(
            status_code=422, detail=f"category must be one of: {', '.join(CATEGORY_KEYS)}"
        )


def _partner_out(row: dict) -> PartnerOut:
    return PartnerOut(
        id=str(row["id"]),
        name=row["name"],
        category=row["category"],
        contact_name=row["contact_name"],
        phone=row["phone"],
        email=row["email"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _added_phrase(name: str, category: str | None) -> str:
    return f"Referral partner '{name}' ({category_label(category).lower()}) added"


# ---------------------------------------------------------------------------
# metrics + partner list
# (literal paths registered BEFORE /partners/{partner_id} — the applicants gotcha.)
# ---------------------------------------------------------------------------
@router.get("/referrals/metrics", response_model=ReferralMetrics)
async def referrals_metrics(
    conn=Depends(tenant_conn),
    months: int = Query(6),
):
    """Per-source conversion + hours-won snapshot. `?months=` sets the width of the
    monthly trend window; the seam clamps it to 1–24 (out-of-range is clamped, not
    rejected). Deterministic seam SQL; empty tenant -> zeroes."""
    return ReferralMetrics(**await referral_metrics(conn, months=months))


@router.get("/referrals/partners", response_model=list[PartnerOut])
async def list_partners(conn=Depends(tenant_conn)):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.referral_partners order by name")
        rows = await cur.fetchall()
    return [_partner_out(r) for r in rows]


@router.post("/referrals/partners", response_model=PartnerOut, status_code=201)
async def create_partner(
    body: PartnerCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    _check_category(body.category)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select 1 from public.referral_partners where name = %s", (name,)
        )
        if await cur.fetchone() is not None:
            raise HTTPException(status_code=409, detail="a partner with that name already exists")
        await cur.execute(
            """insert into public.referral_partners
                 (tenant_id, name, category, contact_name, phone, email, notes)
               values (%s, %s, %s, %s, %s, %s, %s) returning *""",
            (tenant_id, name, body.category, body.contact_name, body.phone,
             body.email, body.notes),
        )
        row = await cur.fetchone()

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="referral_partner.created",
        entity_type="referral_partner",
        entity_id=str(row["id"]),
        payload={"summary": _added_phrase(name, body.category)},
    )
    return _partner_out(row)


# ---------------------------------------------------------------------------
# partial edit + delete
# ---------------------------------------------------------------------------
@router.patch("/referrals/partners/{partner_id}", response_model=PartnerOut)
async def patch_partner(
    partner_id: str,
    body: PartnerPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    partner_id = _valid_uuid(partner_id, "partner_id")
    provided = body.model_fields_set

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.referral_partners where id = %s for update",
            (partner_id,),
        )
        current = await cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="partner not found")

    updates: dict = {}
    for field in _PARTNER_FIELDS:
        if field not in provided:
            continue
        value = getattr(body, field)
        if field == "name":
            value = (value or "").strip()
            if not value:
                raise HTTPException(status_code=422, detail="name cannot be empty")
        elif field == "category":
            _check_category(value)
        if value != current[field]:
            updates[field] = value

    if not updates:
        return _partner_out(current)

    # A rename that collides with another partner's name is a 409 (the join key is
    # unique per tenant).
    if "name" in updates:
        async with conn.cursor() as cur:
            await cur.execute(
                "select 1 from public.referral_partners where name = %s and id <> %s",
                (updates["name"], partner_id),
            )
            if await cur.fetchone() is not None:
                raise HTTPException(
                    status_code=409, detail="a partner with that name already exists"
                )

    set_parts = [f"{f} = %s" for f in updates]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"update public.referral_partners set {', '.join(set_parts)} "
            "where id = %s returning *",
            list(updates.values()) + [partner_id],
        )
        row = await cur.fetchone()

    name = updates.get("name", current["name"])
    changed = ", ".join(sorted(updates.keys()))
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="referral_partner.updated",
        entity_type="referral_partner",
        entity_id=partner_id,
        payload={
            "summary": f"Referral partner '{name}' updated ({changed})",
            "fields": sorted(updates.keys()),
        },
    )
    return _partner_out(row)


@router.delete("/referrals/partners/{partner_id}", status_code=204)
async def delete_partner(
    partner_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    partner_id = _valid_uuid(partner_id, "partner_id")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.referral_partners where id = %s returning *",
            (partner_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="partner not found")

    # Deleting a partner only stops tracking its source string — the leads keep
    # their `source` and funnel history, and the source reappears as untracked.
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="referral_partner.deleted",
        entity_type="referral_partner",
        entity_id=partner_id,
        payload={"summary": f"Referral partner '{row['name']}' removed (leads keep their source)"},
    )
    return None
