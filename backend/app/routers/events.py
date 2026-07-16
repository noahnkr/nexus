"""Event Log API — the user-facing read surface over the immutable `events`
audit trail (PRD interface #5, the business-facing counterpart to LangSmith).

Read-only: no writers here. Tenant-scoped via the standard `tenant_conn`
dependency, so RLS does all tenant filtering. Summaries are derived at read time
(events are immutable); raw payload jsonb rides along as the sanctioned technical
detail for the row expander.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..schemas import EventFacets, EventOut, EventPage
from ..services.event_summaries import summarize_event

router = APIRouter(prefix="/api/events", tags=["events"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100


def _encode_cursor(created_at: datetime, event_id: str) -> str:
    return base64.urlsafe_b64encode(
        f"{created_at.isoformat()}|{event_id}".encode()
    ).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at, event_id = raw.split("|", 1)
        str(uuid.UUID(event_id))  # validate the id half
        return created_at, event_id
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid cursor")


@router.get("", response_model=EventPage)
async def list_events(
    conn=Depends(tenant_conn),
    source_system: str | None = None,
    event_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=1),
):
    limit = min(limit, _MAX_LIMIT)

    where: list[str] = []
    params: dict = {}
    if source_system:
        where.append("source_system = %(source_system)s")
        params["source_system"] = source_system
    if event_type:
        where.append("event_type = %(event_type)s")
        params["event_type"] = event_type
    if entity_type:
        where.append("entity_type = %(entity_type)s")
        params["entity_type"] = entity_type
    if entity_id:
        try:
            params["entity_id"] = str(uuid.UUID(entity_id))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=400, detail="entity_id must be a valid id")
        where.append("entity_id = %(entity_id)s")
    if since:
        where.append("created_at >= %(since)s")
        params["since"] = since
    if until:
        where.append("created_at <= %(until)s")
        params["until"] = until
    if cursor:
        created_at, event_id = _decode_cursor(cursor)
        # Keyset pagination: strictly older than the cursor row, tie-broken by id.
        where.append("(created_at, id) < (%(cursor_ca)s::timestamptz, %(cursor_id)s::uuid)")
        params["cursor_ca"] = created_at
        params["cursor_id"] = event_id

    where_sql = (" where " + " and ".join(where)) if where else ""
    params["limit"] = limit + 1  # one extra row tells us whether a next page exists

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""select id, created_at, source_system, event_type,
                       entity_type, entity_id, payload
                  from public.events{where_sql}
                 order by created_at desc, id desc
                 limit %(limit)s""",
            params,
        )
        rows = await cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]

    events = [
        EventOut(
            id=str(r["id"]),
            created_at=r["created_at"],
            source_system=r["source_system"],
            event_type=r["event_type"],
            entity_type=r["entity_type"],
            entity_id=str(r["entity_id"]) if r["entity_id"] else None,
            summary=summarize_event(r["event_type"], r["source_system"], r["payload"]),
            payload=r["payload"] or {},
        )
        for r in rows
    ]
    next_cursor = (
        _encode_cursor(rows[-1]["created_at"], str(rows[-1]["id"]))
        if has_more and rows
        else None
    )
    return EventPage(events=events, next_cursor=next_cursor)


@router.get("/facets", response_model=EventFacets)
async def event_facets(conn=Depends(tenant_conn)):
    """Distinct source systems and event types for the filter dropdowns. `select
    distinct` is fine at this scale and keeps the filters business-agnostic."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select distinct source_system from public.events order by source_system"
        )
        sources = [r[0] for r in await cur.fetchall()]
        await cur.execute(
            "select distinct event_type from public.events order by event_type"
        )
        types = [r[0] for r in await cur.fetchall()]
    return EventFacets(source_systems=sources, event_types=types)
