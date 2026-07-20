"""The shared ingest seam (Module 18a, Task 3), gated on NEXUS_APP_DB_URL.

`ingest_payload` is the extraction of what used to be the webhook route's body.
The refactor is only sound if the two callers are indistinguishable downstream,
so this drives the SAME signed fixture payload down both paths — over HTTP and by
direct call — and asserts they produce the same receipt + resolution shape.

The only intended difference is the receipt's label: a polled row is recorded as
`connector.received`, not `webhook.received`, because it was never a webhook.
"""
import asyncio
import json

import httpx
import pytest

import conftest
from app.config import settings
from app.services.connectors import register_adapter
from app.services.connectors.ingest import (
    SYNC_RECEIPT,
    WEBHOOK_RECEIPT,
    UnknownSource,
    ingest_payload,
)
from test_webhook_ingress import SECRET, _ThrowawayAdapter, _signed_headers

pytestmark = pytest.mark.skipif(
    not conftest.NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)


async def _events_since(conn, source: str, event_type: str, since) -> list:
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select event_type, source_system, payload, entity_type, entity_id "
            "from public.events where source_system=%s and event_type=%s "
            "and created_at >= %s order by created_at",
            (source, event_type, since),
        )
        return await cur.fetchall()


async def _scenario():
    from app import db
    from app.main import app

    settings.nexus_webhook_secret = SECRET
    settings.nexus_tenant_id = conftest.DEMO_TENANT
    register_adapter(_ThrowawayAdapter())

    out: dict = {}
    await db.open_pool()
    try:
        # Watermark from the DB clock, not the local one — `events.created_at`
        # defaults to the server's now(), and the two clocks drift.
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute("select now()")
                since = (await cur.fetchone())[0]

        # --- path A: over HTTP, through the route's verify + the seam ---
        body = json.dumps({"external_id": "SEAM-PARITY-1"}).encode()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/webhooks/throwaway_test", content=body, headers=_signed_headers(body)
            )
        out["http_counts"] = resp.json()

        # --- path B: direct call, exactly as a sync runner makes it ---
        out["direct_counts"] = await ingest_payload(
            "throwaway_test",
            {"external_id": "SEAM-PARITY-2"},
            tenant_id=conftest.DEMO_TENANT,
            receipt_event_type=SYNC_RECEIPT,
        )

        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            out["webhook_receipts"] = await _events_since(
                conn, "throwaway_test", WEBHOOK_RECEIPT, since
            )
            out["sync_receipts"] = await _events_since(
                conn, "throwaway_test", SYNC_RECEIPT, since
            )
            out["resolved"] = await _events_since(
                conn, "throwaway_test", "test.reference", since
            )

            # --- path C: direct call reusing the caller's transaction (the
            # runner's real shape — a whole page ingested in one tx).
            out["in_tx_counts"] = await ingest_payload(
                "throwaway_test",
                {"external_id": "SEAM-PARITY-3"},
                tenant_id=conftest.DEMO_TENANT,
                receipt_event_type=SYNC_RECEIPT,
                conn=conn,
            )
            out["in_tx_receipts"] = len(
                await _events_since(conn, "throwaway_test", SYNC_RECEIPT, since)
            )
        return out
    finally:
        await db.close_pool()


def test_ingest_payload_matches_the_http_path():
    r = asyncio.run(_scenario())

    # Same counts from both callers.
    assert r["http_counts"] == {"received": 1, "matched": 0, "created": 0, "tasks": 1}
    assert r["direct_counts"] == r["http_counts"]

    # Exactly one receipt each, labeled by how it arrived.
    assert len(r["webhook_receipts"]) == 1
    assert len(r["sync_receipts"]) == 1
    assert r["webhook_receipts"][0]["payload"]["body"]["external_id"] == "SEAM-PARITY-1"
    assert r["sync_receipts"][0]["payload"]["body"]["external_id"] == "SEAM-PARITY-2"

    # Both produced the same resolution event shape — the point of the refactor.
    resolved = r["resolved"]
    assert len(resolved) == 2
    assert {e["payload"]["external_id"] for e in resolved} == {
        "SEAM-PARITY-1",
        "SEAM-PARITY-2",
    }
    assert {e["payload"]["resolution"] for e in resolved} == {"task"}


def test_ingest_payload_can_join_the_callers_transaction():
    """A runner ingests a whole export page inside one transaction; passing `conn`
    must not open a second one or change any outcome."""
    r = asyncio.run(_scenario())
    assert r["in_tx_counts"] == {"received": 1, "matched": 0, "created": 0, "tasks": 1}
    assert r["in_tx_receipts"] == 2  # the earlier sync receipt + this one


def test_ingest_payload_rejects_an_unregistered_source():
    async def scenario():
        await ingest_payload("no_such_source", {}, tenant_id=conftest.DEMO_TENANT)

    with pytest.raises(UnknownSource):
        asyncio.run(scenario())


def test_connector_received_has_a_plain_summary():
    """The Event Log derives summaries at read time for types without one; a
    polled receipt must not read as a webhook."""
    from app.services.event_summaries import summarize_event

    assert summarize_event("connector.received", "welcomehome", {}) == (
        "Synced a record from welcomehome"
    )
