"""Caregivers view API (Module 10, vertical seam).

The human REST surface for the caregiver-recruiting pipeline — directory list +
facets, create, detail, and partial edit (basic fields + quals/regions + stage
moves), plus the on-demand hiring smart summary. JWT tenant-scoped like every
`/api` route (RLS does all filtering, so no query mentions tenant_id).

Writes are `source_system='user'` (the leads/Tasks-page precedent): a coordinator
clicking their own UI is the approver, so there's no approval gate here — the gate
is for agent-initiated effects. Every stage move goes through the single
`views/caregivers.move_stage()` path (emits `applicant.stage_changed`, and on
`hired` the atomic caregiver promotion); other field edits emit one
`applicant.updated`. No-op PATCHes emit nothing. There is NO delete route — an
applicant ends as hired or rejected, keeping funnel history honest (leads precedent).

Vertical seam: this router and `services/views/caregivers.py` are re-templating-seam
members alongside `routers/leads.py`. Core never imports them.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..deps import get_tenant_id
from ..schemas import (
    ApplicantCreate,
    ApplicantFacets,
    ApplicantMetrics,
    ApplicantOut,
    ApplicantPage,
    ApplicantPatch,
    ApplicantSummaryOut,
    QualificationRef,
    RegionRef,
)
from ..services.events import log_event
from ..services.views.caregivers import (
    APPLICANT_SUMMARY_INTRO,
    APPLICANT_SUMMARY_SPAN,
    MoveStageError,
    hiring_metrics,
    move_stage,
    stage_label,
)
from ..services.views.summary import (
    SummaryUnavailable,
    get_or_generate_entity_summary,
    regenerate_entity_summary,
)

router = APIRouter(prefix="/api", tags=["applicants"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

# Basic (non-stage, non-array) fields a PATCH may write.
_BASIC_FIELDS = ("name", "phone", "email", "source", "notes")
_ARRAY_FIELDS = ("qualification_ids", "region_ids")
_ARRAY_TABLE = {"qualification_ids": "qualifications", "region_ids": "regions"}


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


async def _name_maps(conn) -> tuple[dict, dict]:
    """qualification id->name and region id->name maps (RLS-scoped), so applicant
    rows resolve their id arrays to plain names."""
    quals, regions = {}, {}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select id, name from public.qualifications")
        for r in await cur.fetchall():
            quals[str(r["id"])] = r["name"]
        await cur.execute("select id, name from public.regions")
        for r in await cur.fetchall():
            regions[str(r["id"])] = r["name"]
    return quals, regions


def _applicant_out(row: dict, quals: dict, regions: dict, *, promoted: dict | None = None) -> ApplicantOut:
    q_ids = [str(x) for x in (row["qualification_ids"] or [])]
    r_ids = [str(x) for x in (row["region_ids"] or [])]
    return ApplicantOut(
        id=str(row["id"]),
        name=row["name"],
        phone=row["phone"],
        email=row["email"],
        source=row["source"],
        stage=row["stage"],
        qualification_ids=q_ids,
        region_ids=r_ids,
        qualification_names=[quals[i] for i in q_ids if i in quals],
        region_names=[regions[i] for i in r_ids if i in regions],
        availability=row["availability"] or {},
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        promoted_resource_id=promoted["resource_id"] if promoted else None,
        promoted_resource_name=promoted["name"] if promoted else None,
    )


async def _load_applicant(conn, applicant_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.applicants where id = %s", (applicant_id,))
        return await cur.fetchone()


async def _validate_ref_ids(conn, ids: list[str] | None, table: str, what: str) -> list[str]:
    """Validate a list of qualification/region ids: each a well-formed uuid that
    exists for the tenant (RLS-scoped). Empty/None -> []. Raises 422 on any miss."""
    if not ids:
        return []
    validated = [_valid_uuid(i, what) for i in ids]
    async with conn.cursor() as cur:
        await cur.execute(f"select id from public.{table} where id = any(%s)", (validated,))
        found = {str(r[0]) for r in await cur.fetchall()}
    missing = [i for i in validated if i not in found]
    if missing:
        raise HTTPException(status_code=422, detail=f"{what}: not found")
    return validated


# ---------------------------------------------------------------------------
# list + facets  (literal paths registered BEFORE /applicants/{applicant_id})
# ---------------------------------------------------------------------------
@router.get("/applicants", response_model=ApplicantPage)
async def list_applicants(
    conn=Depends(tenant_conn),
    stage: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
):
    limit = min(limit, _MAX_LIMIT)
    where: list[str] = []
    params: dict = {}
    if stage:
        where.append("stage = %(stage)s")
        params["stage"] = stage
    if source:
        where.append("source = %(source)s")
        params["source"] = source
    if q and q.strip():
        where.append("(name ilike %(q)s or phone ilike %(q)s or email ilike %(q)s)")
        params["q"] = f"%{q.strip()}%"
    where_sql = (" where " + " and ".join(where)) if where else ""

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"select count(*) as n from public.applicants{where_sql}", params)
        total = (await cur.fetchone())["n"]
        await cur.execute(
            f"select * from public.applicants{where_sql} "
            "order by created_at desc limit %(limit)s offset %(offset)s",
            {**params, "limit": limit, "offset": offset},
        )
        rows = await cur.fetchall()
    quals, regions = await _name_maps(conn)
    return ApplicantPage(
        applicants=[_applicant_out(r, quals, regions) for r in rows], total=total
    )


@router.get("/applicants/metrics", response_model=ApplicantMetrics)
async def applicant_metrics(conn=Depends(tenant_conn)):
    """Hiring funnel metrics for the directory's dashboard widgets (10b)."""
    return ApplicantMetrics(**await hiring_metrics(conn))


@router.get("/applicants/facets", response_model=ApplicantFacets)
async def applicant_facets(conn=Depends(tenant_conn)):
    """Distinct non-null sources (source filter) + regions + qualifications (the
    create/edit multi-selects)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select distinct source from public.applicants "
            "where source is not null and source <> '' order by source"
        )
        sources = [r["source"] for r in await cur.fetchall()]
        await cur.execute("select id, name from public.regions order by name")
        regions = [RegionRef(id=str(r["id"]), name=r["name"]) for r in await cur.fetchall()]
        await cur.execute("select id, name from public.qualifications order by name")
        quals = [
            QualificationRef(id=str(r["id"]), name=r["name"]) for r in await cur.fetchall()
        ]
    return ApplicantFacets(sources=sources, regions=regions, qualifications=quals)


@router.post("/applicants", response_model=ApplicantOut, status_code=201)
async def create_applicant(
    body: ApplicantCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    qual_ids = await _validate_ref_ids(conn, body.qualification_ids, "qualifications", "qualification_ids")
    region_ids = await _validate_ref_ids(conn, body.region_ids, "regions", "region_ids")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.applicants
                 (tenant_id, name, phone, email, source, qualification_ids, region_ids)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (tenant_id, name, body.phone, body.email, body.source, qual_ids, region_ids),
        )
        applicant_id = str((await cur.fetchone())["id"])

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="applicant.created",
        entity_type="applicant",
        entity_id=applicant_id,
        payload={"summary": f"Applicant '{name}' created manually"},
    )
    row = await _load_applicant(conn, applicant_id)
    assert row is not None
    quals, regions = await _name_maps(conn)
    return _applicant_out(row, quals, regions)


# ---------------------------------------------------------------------------
# detail + partial edit + smart summary
# ---------------------------------------------------------------------------
@router.get("/applicants/{applicant_id}", response_model=ApplicantOut)
async def get_applicant(applicant_id: str, conn=Depends(tenant_conn)):
    applicant_id = _valid_uuid(applicant_id, "applicant_id")
    row = await _load_applicant(conn, applicant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="applicant not found")
    quals, regions = await _name_maps(conn)
    return _applicant_out(row, quals, regions)


@router.patch("/applicants/{applicant_id}", response_model=ApplicantOut)
async def patch_applicant(
    applicant_id: str,
    body: ApplicantPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    applicant_id = _valid_uuid(applicant_id, "applicant_id")
    provided = body.model_fields_set

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.applicants where id = %s for update", (applicant_id,)
        )
        current = await cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="applicant not found")

    # --- basic + array field changes (one applicant.updated) ---
    updates: dict = {}
    for field in _BASIC_FIELDS:
        if field in provided:
            value = getattr(body, field)
            if value != current[field]:
                updates[field] = value
    for field in _ARRAY_FIELDS:
        if field in provided:
            value = await _validate_ref_ids(conn, getattr(body, field), _ARRAY_TABLE[field], field)
            if value != [str(x) for x in (current[field] or [])]:
                updates[field] = value

    if updates:
        set_parts = [f"{f} = %s" for f in updates]
        params = list(updates.values()) + [applicant_id]
        async with conn.cursor() as cur:
            await cur.execute(
                f"update public.applicants set {', '.join(set_parts)} where id = %s", params
            )
        name = updates.get("name", current["name"])
        changed = ", ".join(sorted(updates.keys()))
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="user",
            event_type="applicant.updated",
            entity_type="applicant",
            entity_id=applicant_id,
            payload={
                "summary": f"Applicant '{name}' updated ({changed})",
                "fields": sorted(updates.keys()),
            },
        )

    # --- stage change (the single move_stage path: event + hired-promotion) ---
    promoted: dict | None = None
    if "stage" in provided:
        try:
            result = await move_stage(conn, tenant_id, "user", applicant_id, body.stage)
        except MoveStageError as exc:
            code = 404 if exc.not_found else 422
            raise HTTPException(status_code=code, detail=str(exc))
        promoted = result["promoted"]

    row = await _load_applicant(conn, applicant_id)
    assert row is not None
    quals, regions = await _name_maps(conn)
    return _applicant_out(row, quals, regions, promoted=promoted)


def _summary_entity_row(ao: ApplicantOut) -> dict:
    """Plain-language fact block for the summary prompt — resolved names + the
    label stage, no raw uuid arrays."""
    return {
        "name": ao.name,
        "phone": ao.phone,
        "email": ao.email,
        "source": ao.source,
        "stage": stage_label(ao.stage),
        "qualifications": ao.qualification_names,
        "regions": ao.region_names,
        "availability": ao.availability,
        "notes": ao.notes,
    }


@router.get("/applicants/{applicant_id}/summary", response_model=ApplicantSummaryOut)
async def applicant_summary(
    applicant_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """AI hiring summary for an applicant — cached: the first open generates and
    persists it, later opens serve the cached row. 503 (plain message) when nothing
    is cached and no Anthropic key is configured, so the profile still renders."""
    applicant_id = _valid_uuid(applicant_id, "applicant_id")
    row = await _load_applicant(conn, applicant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="applicant not found")
    quals, regions = await _name_maps(conn)
    try:
        result = await get_or_generate_entity_summary(
            conn,
            tenant_id,
            entity_row=_summary_entity_row(_applicant_out(row, quals, regions)),
            entity_type="applicant",
            entity_id=applicant_id,
            prompt_intro=APPLICANT_SUMMARY_INTRO,
            span_name=APPLICANT_SUMMARY_SPAN,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ApplicantSummaryOut(**result)


@router.post("/applicants/{applicant_id}/summary/regenerate", response_model=ApplicantSummaryOut)
async def regenerate_applicant_summary(
    applicant_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Force a fresh summary and overwrite the cache (the manual Regenerate button).
    503 when no Anthropic key is configured."""
    applicant_id = _valid_uuid(applicant_id, "applicant_id")
    row = await _load_applicant(conn, applicant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="applicant not found")
    quals, regions = await _name_maps(conn)
    try:
        result = await regenerate_entity_summary(
            conn,
            tenant_id,
            entity_row=_summary_entity_row(_applicant_out(row, quals, regions)),
            entity_type="applicant",
            entity_id=applicant_id,
            prompt_intro=APPLICANT_SUMMARY_INTRO,
            span_name=APPLICANT_SUMMARY_SPAN,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ApplicantSummaryOut(**result)
