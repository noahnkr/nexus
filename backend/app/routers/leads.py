"""Leads view API (Module 9, vertical seam).

The human REST surface for the leads pipeline — directory list + facets, create,
detail, and partial edit (basic fields + stage moves), plus the on-demand smart
summary. JWT tenant-scoped like every `/api` route (RLS does all filtering, so no
query mentions tenant_id).

Writes are `source_system='user'` (the Tasks-page precedent): a coordinator
clicking their own UI is the approver, so there's no approval gate here — the gate
is for agent-initiated effects. Every stage move emits `lead.stage_changed` (the
event 9b's per-stage sequences trigger on); other field edits emit one
`lead.updated`. No-op PATCHes emit nothing. There is NO delete route — a lead ends
as converted or lost, keeping funnel history honest (user-locked).

Vertical seam: this router and `services/views/leads.py` are re-templating-seam
members. Core never imports them; M10 adds `routers/caregivers.py` alongside.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..deps import get_tenant_id
from ..schemas import (
    LeadCreate,
    LeadFacets,
    LeadMetrics,
    LeadOut,
    LeadPage,
    LeadPatch,
    LeadSummaryOut,
    RegionRef,
)
from ..services.events import log_event
from ..services.views.leads import (
    LEAD_SUMMARY_INTRO,
    LEAD_SUMMARY_SPAN,
    change_stage,
    funnel_metrics,
    is_valid_stage,
)
from ..services.views.summary import (
    SummaryUnavailable,
    get_or_generate_comm_profile,
    get_or_generate_entity_summary,
    regenerate_comm_profile,
    regenerate_entity_summary,
)

router = APIRouter(prefix="/api", tags=["leads"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

# Basic (non-status) fields a PATCH may write, all nullable text except region_id.
_BASIC_FIELDS = ("name", "phone", "email", "source", "region_id")


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


def _lead_out(row: dict) -> LeadOut:
    return LeadOut(
        id=str(row["id"]),
        name=row["name"],
        phone=row["phone"],
        email=row["email"],
        source=row["source"],
        status=row["status"],
        region_id=str(row["region_id"]) if row["region_id"] else None,
        region_name=row.get("region_name"),
        requirements=row["requirements"] or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _load_lead(conn, lead_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select l.*, r.name as region_name
                 from public.leads l
                 left join public.regions r on r.id = l.region_id
                where l.id = %s""",
            (lead_id,),
        )
        return await cur.fetchone()


async def _validate_region(conn, region_id: str | None) -> str | None:
    """Return a validated region id (RLS-scoped existence check) or raise 422. A
    missing/None region is allowed (leads need not have a region)."""
    if region_id is None:
        return None
    region_id = _valid_uuid(region_id, "region_id")
    async with conn.cursor() as cur:
        await cur.execute("select 1 from public.regions where id = %s", (region_id,))
        if await cur.fetchone() is None:
            raise HTTPException(status_code=422, detail="region not found")
    return region_id


# ---------------------------------------------------------------------------
# list + facets  (literal paths registered BEFORE /leads/{lead_id})
# ---------------------------------------------------------------------------
@router.get("/leads", response_model=LeadPage)
async def list_leads(
    conn=Depends(tenant_conn),
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
):
    limit = min(limit, _MAX_LIMIT)
    where: list[str] = []
    params: dict = {}
    if status:
        where.append("l.status = %(status)s")
        params["status"] = status
    if source:
        where.append("l.source = %(source)s")
        params["source"] = source
    if q and q.strip():
        where.append(
            "(l.name ilike %(q)s or l.phone ilike %(q)s or l.email ilike %(q)s)"
        )
        params["q"] = f"%{q.strip()}%"
    where_sql = (" where " + " and ".join(where)) if where else ""

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select count(*) as n from public.leads l{where_sql}", params
        )
        total = (await cur.fetchone())["n"]
        await cur.execute(
            f"""select l.*, r.name as region_name
                  from public.leads l
                  left join public.regions r on r.id = l.region_id
                {where_sql}
                 order by l.created_at desc
                 limit %(limit)s offset %(offset)s""",
            {**params, "limit": limit, "offset": offset},
        )
        rows = await cur.fetchall()
    return LeadPage(leads=[_lead_out(r) for r in rows], total=total)


@router.get("/leads/metrics", response_model=LeadMetrics)
async def lead_metrics(conn=Depends(tenant_conn)):
    """Funnel conversion metrics for the directory's dashboard widgets (9b)."""
    return LeadMetrics(**await funnel_metrics(conn))


@router.get("/leads/facets", response_model=LeadFacets)
async def lead_facets(conn=Depends(tenant_conn)):
    """Distinct non-null sources (source filter) + regions (create/edit selector).
    `select distinct` is fine at this scale and keeps the surface business-agnostic."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select distinct source from public.leads "
            "where source is not null and source <> '' order by source"
        )
        sources = [r["source"] for r in await cur.fetchall()]
        await cur.execute("select id, name from public.regions order by name")
        regions = [RegionRef(id=str(r["id"]), name=r["name"]) for r in await cur.fetchall()]
    return LeadFacets(sources=sources, regions=regions)


@router.post("/leads", response_model=LeadOut, status_code=201)
async def create_lead(
    body: LeadCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    region_id = await _validate_region(conn, body.region_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.leads (tenant_id, name, phone, email, source, region_id)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (tenant_id, name, body.phone, body.email, body.source, region_id),
        )
        lead_id = str((await cur.fetchone())["id"])

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="lead.created",
        entity_type="lead",
        entity_id=lead_id,
        payload={"summary": f"Lead '{name}' created manually"},
    )
    row = await _load_lead(conn, lead_id)
    assert row is not None
    return _lead_out(row)


# ---------------------------------------------------------------------------
# detail + partial edit + smart summary
# ---------------------------------------------------------------------------
@router.get("/leads/{lead_id}", response_model=LeadOut)
async def get_lead(lead_id: str, conn=Depends(tenant_conn)):
    lead_id = _valid_uuid(lead_id, "lead_id")
    row = await _load_lead(conn, lead_id)
    if row is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return _lead_out(row)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
async def patch_lead(
    lead_id: str,
    body: LeadPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    lead_id = _valid_uuid(lead_id, "lead_id")
    provided = body.model_fields_set  # only fields the client actually sent

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.leads where id = %s for update", (lead_id,))
        current = await cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="lead not found")

    # --- status change (stage move) ---
    status_change: tuple[str, str] | None = None
    if "status" in provided:
        new_status = body.status
        if not is_valid_stage(new_status):
            raise HTTPException(status_code=422, detail="invalid status")
        assert new_status is not None
        if new_status != current["status"]:
            status_change = (current["status"], new_status)

    # --- basic field changes ---
    basic_updates: dict = {}
    for field in _BASIC_FIELDS:
        if field not in provided:
            continue
        value = getattr(body, field)
        if field == "region_id":
            value = await _validate_region(conn, value)
        if value != current[field]:
            basic_updates[field] = value

    if not status_change and not basic_updates:
        row = await _load_lead(conn, lead_id)  # no-op: emit nothing
        assert row is not None
        return _lead_out(row)

    # Basic fields first, so a rename landing in the same PATCH is already visible
    # when change_stage reads the name for its event summary (and so the
    # stage_changed event still precedes lead.updated, as it always has).
    if basic_updates:
        set_parts: list[str] = []
        params: list = []
        for field, value in basic_updates.items():
            set_parts.append(f"{field} = %s")
            params.append(value)
        params.append(lead_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"update public.leads set {', '.join(set_parts)} where id = %s", params
            )

    name = basic_updates.get("name", current["name"])
    if status_change:
        # The one writer of leads.status (18a) — same event this route used to
        # emit inline, now shared with the tool handler and the CRM sync.
        await change_stage(conn, tenant_id, "user", lead_id, status_change[1])
    if basic_updates:
        changed = ", ".join(sorted(basic_updates.keys()))
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="user",
            event_type="lead.updated",
            entity_type="lead",
            entity_id=lead_id,
            payload={"summary": f"Lead '{name}' updated ({changed})", "fields": sorted(basic_updates.keys())},
        )

    row = await _load_lead(conn, lead_id)
    assert row is not None
    return _lead_out(row)


@router.get("/leads/{lead_id}/summary", response_model=LeadSummaryOut)
async def lead_summary(
    lead_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """AI smart summary for a lead — cached (WS7): the first open generates and
    persists it, later opens serve the cached row instantly. 503 (plain message)
    when nothing is cached and no Anthropic key is configured, so the profile still
    renders with a quiet notice."""
    lead_id = _valid_uuid(lead_id, "lead_id")
    row = await _load_lead(conn, lead_id)
    if row is None:
        raise HTTPException(status_code=404, detail="lead not found")
    try:
        result = await get_or_generate_entity_summary(
            conn,
            tenant_id,
            entity_row=_lead_out(row).model_dump(exclude={"id", "region_id"}),
            entity_type="lead",
            entity_id=lead_id,
            prompt_intro=LEAD_SUMMARY_INTRO,
            span_name=LEAD_SUMMARY_SPAN,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return LeadSummaryOut(**result)


@router.post("/leads/{lead_id}/summary/regenerate", response_model=LeadSummaryOut)
async def regenerate_lead_summary(
    lead_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Force a fresh summary and overwrite the cache (the manual Regenerate button).
    503 when no Anthropic key is configured."""
    lead_id = _valid_uuid(lead_id, "lead_id")
    row = await _load_lead(conn, lead_id)
    if row is None:
        raise HTTPException(status_code=404, detail="lead not found")
    try:
        result = await regenerate_entity_summary(
            conn,
            tenant_id,
            entity_row=_lead_out(row).model_dump(exclude={"id", "region_id"}),
            entity_type="lead",
            entity_id=lead_id,
            prompt_intro=LEAD_SUMMARY_INTRO,
            span_name=LEAD_SUMMARY_SPAN,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return LeadSummaryOut(**result)


@router.get("/leads/{lead_id}/comm-profile", response_model=LeadSummaryOut)
async def lead_comm_profile(
    lead_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """AI communication profile for a lead (tier-3 derived knowledge): tone,
    responsiveness, preferred channel, recurring topics, from their message
    history. Cached under the `comm_profile` kind. 503 when a profile would need
    generating but no Anthropic key is configured."""
    lead_id = _valid_uuid(lead_id, "lead_id")
    row = await _load_lead(conn, lead_id)
    if row is None:
        raise HTTPException(status_code=404, detail="lead not found")
    try:
        result = await get_or_generate_comm_profile(
            conn, tenant_id, entity_type="lead", entity_id=lead_id,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return LeadSummaryOut(**result)


@router.post("/leads/{lead_id}/comm-profile/regenerate", response_model=LeadSummaryOut)
async def regenerate_lead_comm_profile(
    lead_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Force a fresh communication profile and overwrite its cache row."""
    lead_id = _valid_uuid(lead_id, "lead_id")
    row = await _load_lead(conn, lead_id)
    if row is None:
        raise HTTPException(status_code=404, detail="lead not found")
    try:
        result = await regenerate_comm_profile(
            conn, tenant_id, entity_type="lead", entity_id=lead_id,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return LeadSummaryOut(**result)
