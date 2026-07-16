"""Event Log API (Module 4, Tasks 1 & 3), gated on NEXUS_APP_DB_URL.

Events are immutable (no DELETE), so each run seeds under a unique throwaway
source_system and scopes its assertions to it rather than asserting global counts.
Drives the real router via the app-client pattern.
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"


def test_events_in_realtime_publication():
    """Task 1: the migration added public.events to the Realtime publication."""
    import psycopg

    from app.config import settings

    conn = psycopg.connect(settings.nexus_app_db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from pg_publication_tables where pubname='supabase_realtime' "
                "and schemaname='public' and tablename='events'"
            )
            assert cur.fetchone() is not None
    finally:
        conn.close()


async def _seed_and_query():
    from app import db
    from app.main import app
    from app.services.events import log_event

    token = uuid.uuid4().hex[:8]
    src_a = f"evtest_a_{token}"
    src_b = f"evtest_b_{token}"

    await db.open_pool()
    try:
        # Batch 1 (source_a) before the mid timestamp.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for i in range(8):
                await log_event(
                    conn, tenant_id=DEMO_TENANT, source_system=src_a,
                    event_type="evtest.alpha", payload={"summary": f"alpha {i}"},
                )
        # Boundary in its own tx via clock_timestamp() (real wall-clock), so it is
        # strictly after batch 1's committed rows — `now()` is the tx start time
        # and would tie with the rows created in the same transaction.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute("select clock_timestamp()")
                ts_mid = (await cur.fetchone())[0]
        # Batch 2 (source_b) after the mid timestamp: 5 plain, 1 entity-linked,
        # 1 summary-less document.uploaded to exercise the derived template.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for i in range(5):
                await log_event(
                    conn, tenant_id=DEMO_TENANT, source_system=src_b,
                    event_type="evtest.beta", payload={"summary": f"beta {i}"},
                )
            await log_event(
                conn, tenant_id=DEMO_TENANT, source_system=src_b, event_type="evtest.beta",
                entity_type="lead", entity_id=MARGARET_LEAD, payload={"summary": "beta linked"},
            )
            await log_event(
                conn, tenant_id=DEMO_TENANT, source_system=src_b,
                event_type="document.uploaded", payload={"filename": "seedcheck.pdf"},
            )
        # A probe-tenant event that must never surface under the demo tenant.
        async with db.tenant_tx(PROBE_TENANT) as conn:
            await log_event(
                conn, tenant_id=PROBE_TENANT, source_system=src_a,
                event_type="evtest.alpha", payload={"summary": "probe only"},
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            out = {}

            # Pagination over source_a (8 rows) at limit=5: two pages, no dup/miss.
            seen_ids, cursor, pages = [], None, 0
            while True:
                params = {"source_system": src_a, "limit": 5}
                if cursor:
                    params["cursor"] = cursor
                page = (await ac.get("/api/events", params=params)).json()
                seen_ids.extend(e["id"] for e in page["events"])
                pages += 1
                cursor = page["next_cursor"]
                if not cursor:
                    break
            out["page_ids"] = seen_ids
            out["pages"] = pages

            out["src_b"] = (await ac.get("/api/events", params={"source_system": src_b, "limit": 100})).json()
            out["doc"] = (await ac.get("/api/events", params={
                "source_system": src_b, "event_type": "document.uploaded"})).json()
            out["since_a"] = (await ac.get("/api/events", params={
                "source_system": src_a, "since": ts_mid.isoformat()})).json()
            out["until_a"] = (await ac.get("/api/events", params={
                "source_system": src_a, "until": ts_mid.isoformat(), "limit": 100})).json()
            out["entity"] = (await ac.get("/api/events", params={
                "source_system": src_b, "entity_type": "lead", "entity_id": MARGARET_LEAD})).json()
            out["facets"] = (await ac.get("/api/events/facets")).json()
            out["capped"] = (await ac.get("/api/events", params={"limit": 500})).json()
            out["src_a_count"] = (await ac.get("/api/events", params={
                "source_system": src_a, "limit": 100})).json()
        return out, src_a, src_b
    finally:
        await db.close_pool()


def test_events_api():
    out, src_a, src_b = asyncio.run(_seed_and_query())

    # Pagination: all 8 source_a rows exactly once across 2 pages.
    assert len(out["page_ids"]) == 8
    assert len(set(out["page_ids"])) == 8
    assert out["pages"] == 2

    # source filter + probe isolation: exactly the 8 demo rows, not the probe row.
    assert len(out["src_a_count"]["events"]) == 8

    # source_b filter: 7 rows.
    assert len(out["src_b"]["events"]) == 7

    # event_type filter + server-derived summary for a summary-less row.
    assert len(out["doc"]["events"]) == 1
    assert out["doc"]["events"][0]["summary"] == "Document 'seedcheck.pdf' uploaded"

    # since/until window around ts_mid.
    assert len(out["since_a"]["events"]) == 0   # all source_a is before ts_mid
    assert len(out["until_a"]["events"]) == 8    # ...and thus all <= ts_mid

    # entity drill-down: only the one lead-linked source_b event.
    assert len(out["entity"]["events"]) == 1
    assert out["entity"]["events"][0]["entity_id"] == MARGARET_LEAD

    # facets list both seeded source systems.
    assert src_a in out["facets"]["source_systems"]
    assert src_b in out["facets"]["source_systems"]
    assert "evtest.beta" in out["facets"]["event_types"]

    # limit cap: 500 is clamped to 100 (the tenant has >100 accumulated events).
    assert len(out["capped"]["events"]) <= 100
    assert out["capped"]["next_cursor"] is not None
