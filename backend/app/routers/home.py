"""Home summary API (Module 6b) — one round-trip of at-a-glance counts for the
landing widgets.

Read-only and business-agnostic: four scalar aggregates over core tables only
(`tasks`, `pending_actions`, `documents`, `events`). Tenant-scoped via the standard
`tenant_conn` dependency, so RLS does all filtering — no query mentions tenant_id.
The Home page composes this with `GET /api/events?limit=6` for recent activity; no
duplicate feed endpoint here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from ..db import tenant_conn
from ..schemas import DocumentCounts, HomeSummary

router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("/summary", response_model=HomeSummary)
async def home_summary(conn=Depends(tenant_conn)):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select
              (select count(*) from public.tasks
                 where status in ('pending', 'in_progress'))            as open_tasks,
              (select count(*) from public.pending_actions
                 where status = 'pending')                              as pending_approvals,
              (select count(*) from public.documents
                 where status = 'ready')                                as docs_ready,
              (select count(*) from public.documents
                 where status in ('uploaded', 'processing'))            as docs_processing,
              (select count(*) from public.documents
                 where status = 'failed')                               as docs_failed,
              (select count(*) from public.events
                 where created_at >= date_trunc('day', now()))          as events_today
            """
        )
        r = await cur.fetchone()

    return HomeSummary(
        open_tasks=r["open_tasks"],
        pending_approvals=r["pending_approvals"],
        documents=DocumentCounts(
            ready=r["docs_ready"],
            processing=r["docs_processing"],
            failed=r["docs_failed"],
        ),
        events_today=r["events_today"],
    )
