"""Clients view API (Module 16a, vertical seam).

The human REST surface for client & care oversight — directory list + facets, the
census metrics strip, create, the care-overview detail (contacts, assigned
caregivers, this week's hours, tagged documents), partial edit including status
moves, family-contact CRUD, and the cached care smart summary. JWT tenant-scoped
like every `/api` route (RLS does all filtering, so no query mentions tenant_id).

Writes are `source_system='user'` (the leads/caregivers/schedule precedent): a
coordinator clicking their own UI is the approver, so there's no approval gate
here — the gate is for agent-initiated effects. Every status change goes through
the single `views/clients.change_status()` path (emits `client.status_changed`);
other field edits emit one `client.updated`. No-op PATCHes emit nothing. There is
NO delete route — a client ends as discharged, and the care history stays.

Vertical seam: this router and `services/views/clients.py` are re-templating-seam
members alongside `routers/leads.py`, `routers/applicants.py`, and
`routers/schedule.py`. Core never imports them.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..deps import get_tenant_id
from ..schemas import (
    CensusMetrics,
    ClientCaregiverRef,
    ClientContactCreate,
    ClientContactOut,
    ClientContactPatch,
    ClientCreate,
    ClientDetail,
    ClientDocumentRef,
    ClientFacets,
    ClientHours,
    ClientOut,
    ClientPage,
    ClientPatch,
    ClientSummaryOut,
    ClientVisits,
    RegionRef,
)
from ..services.events import log_event
from ..services.views.clients import (
    CLIENT_SUMMARY_INTRO,
    CLIENT_SUMMARY_SPAN,
    PAYER_KEYS,
    ClientError,
    census_metrics,
    change_status,
    client_week_hours,
    payer_label,
    status_label,
)
from ..services.views.summary import (
    SummaryUnavailable,
    get_or_generate_entity_summary,
    regenerate_entity_summary,
)
# Reuse the Schedule board's visit shaping so the profile's visit rows are byte-for-
# byte the same as the board's (same resolved names, same server-computed EVV flag).
# Both routers are vertical-seam members; core never imports either.
from .schedule import _VISIT_JOIN_SQL, _qual_names, _visit_out

router = APIRouter(prefix="/api", tags=["clients"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

# Fields a PATCH may write directly. `status` is deliberately absent — it routes
# through the seam's change_status() so the event can't be skipped.
_BASIC_FIELDS = (
    "name", "phone", "email", "address", "zip", "care_summary", "payer",
    "authorized_hours_per_week", "region_id",
)
_ARRAY_FIELDS = ("languages", "preferences")

_CONTACT_FIELDS = ("name", "relationship", "phone", "email", "is_primary", "notes")

# A caregiver counts as "assigned" to a client if they hold a live visit within
# this window — recent enough to be current, wide enough to survive a quiet week.
_CAREGIVER_WINDOW_DAYS = 30


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


def _http(exc: ClientError) -> HTTPException:
    """Unknown client -> 404; a rejected value -> 422."""
    return HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc))


def _check_payer(payer: str | None) -> None:
    if payer is not None and payer not in PAYER_KEYS:
        raise HTTPException(
            status_code=422, detail=f"payer must be one of: {', '.join(PAYER_KEYS)}"
        )


async def _check_region(conn, region_id: str | None) -> str | None:
    if region_id is None:
        return None
    rid = _valid_uuid(region_id, "region_id")
    async with conn.cursor() as cur:
        await cur.execute("select 1 from public.regions where id = %s", (rid,))
        if await cur.fetchone() is None:
            raise HTTPException(status_code=422, detail="region_id: not found")
    return rid


def _client_out(row: dict) -> ClientOut:
    hours = row.get("authorized_hours_per_week")
    return ClientOut(
        id=str(row["id"]),
        name=row["name"],
        phone=row["phone"],
        email=row["email"],
        status=row["status"],
        lead_id=str(row["lead_id"]) if row.get("lead_id") else None,
        address=row.get("address"),
        zip=row.get("zip"),
        languages=list(row.get("languages") or []),
        preferences=list(row.get("preferences") or []),
        region_id=str(row["region_id"]) if row.get("region_id") else None,
        region_name=row.get("region_name"),
        payer=row.get("payer"),
        authorized_hours_per_week=float(hours) if hours is not None else None,
        care_summary=row.get("care_summary"),
        requirements=row.get("requirements") or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _contact_out(row: dict) -> ClientContactOut:
    return ClientContactOut(
        id=str(row["id"]),
        client_id=str(row["client_id"]),
        name=row["name"],
        relationship=row["relationship"],
        phone=row["phone"],
        email=row["email"],
        is_primary=row["is_primary"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_CLIENT_JOIN_SQL = """
select c.*, r.name as region_name
  from public.clients c
  left join public.regions r on r.id = c.region_id
"""


async def _load_client(conn, client_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_CLIENT_JOIN_SQL + " where c.id = %s", (client_id,))
        return await cur.fetchone()


async def _require_client(conn, client_id: str) -> dict:
    row = await _load_client(conn, client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="client not found")
    return row


# ---------------------------------------------------------------------------
# list + metrics + facets
# (literal paths registered BEFORE /clients/{client_id} — the applicants gotcha:
#  otherwise "metrics" is parsed as a client id and 400s on the uuid check.)
# ---------------------------------------------------------------------------
@router.get("/clients", response_model=ClientPage)
async def list_clients(
    conn=Depends(tenant_conn),
    status: str | None = None,
    payer: str | None = None,
    region_id: str | None = None,
    q: str | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
):
    limit = min(limit, _MAX_LIMIT)
    where: list[str] = []
    params: dict = {}
    if status:
        where.append("c.status = %(status)s")
        params["status"] = status
    if payer:
        where.append("c.payer = %(payer)s")
        params["payer"] = payer
    if region_id:
        where.append("c.region_id = %(region_id)s")
        params["region_id"] = _valid_uuid(region_id, "region_id")
    if q and q.strip():
        where.append("(c.name ilike %(q)s or c.phone ilike %(q)s or c.email ilike %(q)s)")
        params["q"] = f"%{q.strip()}%"
    where_sql = (" where " + " and ".join(where)) if where else ""

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select count(*) as n from public.clients c{where_sql}", params
        )
        total = (await cur.fetchone())["n"]
        await cur.execute(
            _CLIENT_JOIN_SQL + where_sql
            + " order by c.name limit %(limit)s offset %(offset)s",
            {**params, "limit": limit, "offset": offset},
        )
        rows = await cur.fetchall()
    return ClientPage(clients=[_client_out(r) for r in rows], total=total)


@router.get("/clients/metrics", response_model=CensusMetrics)
async def client_metrics(conn=Depends(tenant_conn), week: str | None = None):
    """The census strip: active headcount by region/payer plus this week's
    authorized / scheduled / delivered / open hours and the leakage gap. `?week=`
    takes any YYYY-MM-DD; the seam snaps it to that week's Monday, matching the
    Schedule board's window."""
    ref: date | None = None
    if week:
        try:
            ref = date.fromisoformat(week)
        except ValueError:
            raise HTTPException(status_code=422, detail="week must be YYYY-MM-DD")
    return CensusMetrics(**await census_metrics(conn, ref))


@router.get("/clients/facets", response_model=ClientFacets)
async def client_facets(conn=Depends(tenant_conn)):
    """Observed statuses/payers (the directory's filter chips) + all regions (the
    create/edit selector)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select distinct status from public.clients order by status"
        )
        statuses = [r["status"] for r in await cur.fetchall()]
        await cur.execute(
            "select distinct payer from public.clients "
            "where payer is not null order by payer"
        )
        payers = [r["payer"] for r in await cur.fetchall()]
        await cur.execute("select id, name from public.regions order by name")
        regions = [RegionRef(id=str(r["id"]), name=r["name"]) for r in await cur.fetchall()]
    return ClientFacets(statuses=statuses, payers=payers, regions=regions)


@router.post("/clients", response_model=ClientOut, status_code=201)
async def create_client(
    body: ClientCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    _check_payer(body.payer)
    region_id = await _check_region(conn, body.region_id)
    if body.authorized_hours_per_week is not None and body.authorized_hours_per_week < 0:
        raise HTTPException(
            status_code=422, detail="authorized_hours_per_week must be 0 or more"
        )

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.clients
                 (tenant_id, name, phone, email, address, zip, region_id, payer,
                  authorized_hours_per_week, care_summary, languages, preferences)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               returning id""",
            (tenant_id, name, body.phone, body.email, body.address, body.zip,
             region_id, body.payer, body.authorized_hours_per_week, body.care_summary,
             body.languages, body.preferences),
        )
        client_id = str((await cur.fetchone())["id"])

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="client.created",
        entity_type="client",
        entity_id=client_id,
        payload={"summary": f"Client '{name}' created manually"},
    )
    return _client_out(await _require_client(conn, client_id))


# ---------------------------------------------------------------------------
# detail + partial edit
# ---------------------------------------------------------------------------
async def _contacts(conn, client_id: str) -> list[ClientContactOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.client_contacts where client_id = %s "
            "order by is_primary desc, name",
            (client_id,),
        )
        return [_contact_out(r) for r in await cur.fetchall()]


async def _caregivers(conn, client_id: str) -> list[ClientCaregiverRef]:
    """Distinct caregivers on this client's live visits in a ±30-day window, each
    with their next upcoming visit (null if they only have past ones)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""select s.resource_id, r.name,
                       min(s.start_time) filter (where s.start_time >= now()) as next_visit
                  from public.schedules s
                  join public.resources r on r.id = s.resource_id
                 where s.client_id = %s
                   and s.status in ('scheduled','completed')
                   and s.start_time >= now() - interval '{_CAREGIVER_WINDOW_DAYS} days'
                   and s.start_time <= now() + interval '{_CAREGIVER_WINDOW_DAYS} days'
                 group by s.resource_id, r.name
                 order by next_visit nulls last, r.name""",
            (client_id,),
        )
        return [
            ClientCaregiverRef(
                resource_id=str(r["resource_id"]), name=r["name"], next_visit=r["next_visit"]
            )
            for r in await cur.fetchall()
        ]


async def _documents(conn, client_id: str) -> list[ClientDocumentRef]:
    """Documents tagged to this client — care plans, assessments. One query, which
    is the entire reason `documents` gained entity_type/entity_id (M16a core
    migration); before it, this meant scanning chunks."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, filename, status, created_at from public.documents "
            "where entity_type = 'client' and entity_id = %s order by created_at desc",
            (client_id,),
        )
        return [
            ClientDocumentRef(
                id=str(r["id"]), filename=r["filename"], status=r["status"],
                created_at=r["created_at"],
            )
            for r in await cur.fetchall()
        ]


@router.get("/clients/{client_id}", response_model=ClientDetail)
async def get_client(client_id: str, conn=Depends(tenant_conn)):
    """The care-overview payload: the client record plus everything the profile
    page renders around it — contacts, who is serving them, this week's hours, and
    their documents."""
    client_id = _valid_uuid(client_id, "client_id")
    row = await _require_client(conn, client_id)
    return ClientDetail(
        **_client_out(row).model_dump(),
        contacts=await _contacts(conn, client_id),
        caregivers=await _caregivers(conn, client_id),
        hours_this_week=ClientHours(**await client_week_hours(conn, client_id)),
        documents=await _documents(conn, client_id),
    )


@router.get("/clients/{client_id}/visits", response_model=ClientVisits)
async def client_visits(
    client_id: str,
    conn=Depends(tenant_conn),
    upcoming: int = Query(5, ge=0, le=50),
    past: int = Query(5, ge=0, le=50),
):
    """This client's visits for the profile's visits card: the next `upcoming`
    starting from now (soonest first) and the last `past` before now (most recent
    first). Cancelled visits are omitted. Rows carry the board's resolved names and
    the read-time EVV flag, so the profile and the board never disagree."""
    client_id = _valid_uuid(client_id, "client_id")
    await _require_client(conn, client_id)
    qmap = await _qual_names(conn)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            _VISIT_JOIN_SQL
            + """ where s.client_id = %s and s.start_time >= now()
                   and s.status <> 'cancelled'
                 order by s.start_time asc limit %s""",
            (client_id, upcoming),
        )
        up = [_visit_out(r, qmap) for r in await cur.fetchall()]
        await cur.execute(
            _VISIT_JOIN_SQL
            + """ where s.client_id = %s and s.start_time < now()
                   and s.status <> 'cancelled'
                 order by s.start_time desc limit %s""",
            (client_id, past),
        )
        pst = [_visit_out(r, qmap) for r in await cur.fetchall()]
    return ClientVisits(upcoming=up, past=pst)


@router.patch("/clients/{client_id}", response_model=ClientDetail)
async def patch_client(
    client_id: str,
    body: ClientPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    client_id = _valid_uuid(client_id, "client_id")
    provided = body.model_fields_set

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.clients where id = %s for update", (client_id,)
        )
        current = await cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="client not found")

    # --- basic + array field changes (one client.updated) ---
    updates: dict = {}
    for field in _BASIC_FIELDS:
        if field not in provided:
            continue
        value = getattr(body, field)
        if field == "payer":
            _check_payer(value)
        elif field == "region_id":
            value = await _check_region(conn, value)
        elif field == "authorized_hours_per_week" and value is not None and value < 0:
            raise HTTPException(
                status_code=422, detail="authorized_hours_per_week must be 0 or more"
            )
        existing = current[field]
        if field == "region_id":
            existing = str(existing) if existing else None
        elif field == "authorized_hours_per_week" and existing is not None:
            existing = float(existing)
        if value != existing:
            updates[field] = value
    for field in _ARRAY_FIELDS:
        if field in provided:
            value = list(getattr(body, field) or [])
            if value != list(current[field] or []):
                updates[field] = value

    if updates:
        set_parts = [f"{f} = %s" for f in updates]
        params = list(updates.values()) + [client_id]
        async with conn.cursor() as cur:
            await cur.execute(
                f"update public.clients set {', '.join(set_parts)} where id = %s", params
            )
        name = updates.get("name", current["name"])
        changed = ", ".join(sorted(updates.keys()))
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="user",
            event_type="client.updated",
            entity_type="client",
            entity_id=client_id,
            payload={
                "summary": f"Client '{name}' updated ({changed})",
                "fields": sorted(updates.keys()),
            },
        )

    # --- status change (the single change_status path) ---
    if "status" in provided and body.status is not None:
        try:
            await change_status(conn, tenant_id, "user", client_id, body.status)
        except ClientError as exc:
            raise _http(exc)

    return await get_client(client_id, conn)


# ---------------------------------------------------------------------------
# family contacts
#
# Each write emits `client.updated` on the CLIENT, not on the contact: the
# timeline a coordinator reads is the client's, and a contact has no timeline of
# its own. Summaries name the person in plain language.
# ---------------------------------------------------------------------------
async def _clear_other_primaries(conn, client_id: str, keep_id: str | None) -> None:
    """At most one primary contact per client. Runs in the caller's transaction, so
    the swap is atomic — there is never a moment with two primaries."""
    await conn.execute(
        "update public.client_contacts set is_primary = false "
        "where client_id = %s and is_primary = true "
        "and (%s::uuid is null or id <> %s::uuid)",
        (client_id, keep_id, keep_id),
    )


def _contact_phrase(row: dict, client_name: str, verb: str) -> str:
    who = row["name"]
    if row.get("relationship"):
        who = f"{who} ({row['relationship']})"
    return f"Family contact '{who}' {verb} for {client_name}"


@router.post("/clients/{client_id}/contacts", response_model=ClientContactOut, status_code=201)
async def create_contact(
    client_id: str,
    body: ClientContactCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    client_id = _valid_uuid(client_id, "client_id")
    client = await _require_client(conn, client_id)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.client_contacts
                 (tenant_id, client_id, name, relationship, phone, email, is_primary, notes)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning *""",
            (tenant_id, client_id, name, body.relationship, body.phone, body.email,
             body.is_primary, body.notes),
        )
        row = await cur.fetchone()
    if body.is_primary:
        await _clear_other_primaries(conn, client_id, str(row["id"]))

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="client.updated",
        entity_type="client",
        entity_id=client_id,
        payload={"summary": _contact_phrase(row, client["name"], "added")},
    )
    return _contact_out(row)


@router.patch("/clients/{client_id}/contacts/{contact_id}", response_model=ClientContactOut)
async def patch_contact(
    client_id: str,
    contact_id: str,
    body: ClientContactPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    client_id = _valid_uuid(client_id, "client_id")
    contact_id = _valid_uuid(contact_id, "contact_id")
    client = await _require_client(conn, client_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.client_contacts where id = %s and client_id = %s "
            "for update",
            (contact_id, client_id),
        )
        current = await cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="contact not found")

    provided = body.model_fields_set
    updates = {
        f: getattr(body, f)
        for f in _CONTACT_FIELDS
        if f in provided and getattr(body, f) != current[f]
    }
    if not updates:
        return _contact_out(current)

    set_parts = [f"{f} = %s" for f in updates]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"update public.client_contacts set {', '.join(set_parts)} "
            "where id = %s returning *",
            list(updates.values()) + [contact_id],
        )
        row = await cur.fetchone()
    if updates.get("is_primary"):
        await _clear_other_primaries(conn, client_id, contact_id)

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="client.updated",
        entity_type="client",
        entity_id=client_id,
        payload={
            "summary": _contact_phrase(row, client["name"], "updated"),
            "fields": sorted(updates.keys()),
        },
    )
    return _contact_out(row)


@router.delete("/clients/{client_id}/contacts/{contact_id}", status_code=204)
async def delete_contact(
    client_id: str,
    contact_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    client_id = _valid_uuid(client_id, "client_id")
    contact_id = _valid_uuid(contact_id, "contact_id")
    client = await _require_client(conn, client_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.client_contacts where id = %s and client_id = %s "
            "returning *",
            (contact_id, client_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="contact not found")

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="user",
        event_type="client.updated",
        entity_type="client",
        entity_id=client_id,
        payload={"summary": _contact_phrase(row, client["name"], "removed")},
    )
    return None


# ---------------------------------------------------------------------------
# smart summary (cached — applicants precedent)
# ---------------------------------------------------------------------------
async def _summary_entity_row(conn, row: dict) -> dict:
    """Plain-language fact block for the summary prompt — labels not raw enum
    values, resolved names not uuids, and the hours the coordinator cares about."""
    client_id = str(row["id"])
    contacts = await _contacts(conn, client_id)
    hours = await client_week_hours(conn, client_id)
    return {
        "name": row["name"],
        "status": status_label(row["status"]),
        "payer": payer_label(row.get("payer")),
        "authorized_hours_per_week": row.get("authorized_hours_per_week"),
        "region": row.get("region_name"),
        "languages": list(row.get("languages") or []),
        "preferences": list(row.get("preferences") or []),
        "care_summary": row.get("care_summary"),
        "contacts": [
            f"{c.name} ({c.relationship or 'contact'})"
            + (" — primary" if c.is_primary else "")
            for c in contacts
        ],
        "hours_this_week": (
            f"{hours['delivered_hours']} delivered of "
            f"{hours['authorized_hours']} authorized"
        ),
    }


@router.get("/clients/{client_id}/summary", response_model=ClientSummaryOut)
async def client_summary(
    client_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """AI care summary for a client — cached: the first open generates and persists
    it, later opens serve the cached row. 503 (plain message) when nothing is
    cached and no Anthropic key is configured, so the profile still renders."""
    client_id = _valid_uuid(client_id, "client_id")
    row = await _require_client(conn, client_id)
    try:
        result = await get_or_generate_entity_summary(
            conn,
            tenant_id,
            entity_row=await _summary_entity_row(conn, row),
            entity_type="client",
            entity_id=client_id,
            prompt_intro=CLIENT_SUMMARY_INTRO,
            span_name=CLIENT_SUMMARY_SPAN,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ClientSummaryOut(**result)


@router.post("/clients/{client_id}/summary/regenerate", response_model=ClientSummaryOut)
async def regenerate_client_summary(
    client_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    """Force a fresh summary and overwrite the cache (the Regenerate button).
    503 when no Anthropic key is configured."""
    client_id = _valid_uuid(client_id, "client_id")
    row = await _require_client(conn, client_id)
    try:
        result = await regenerate_entity_summary(
            conn,
            tenant_id,
            entity_row=await _summary_entity_row(conn, row),
            entity_type="client",
            entity_id=client_id,
            prompt_intro=CLIENT_SUMMARY_INTRO,
            span_name=CLIENT_SUMMARY_SPAN,
        )
    except SummaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ClientSummaryOut(**result)
