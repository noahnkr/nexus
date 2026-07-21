"""Communications retrieval (v1.1.0): `retrieve_communications` returns embedded
comms and never store-only ones. Gated on NEXUS_APP_DB_URL + VOYAGE_API_KEY.

The store != embed guarantee is the point: a short message is stored for the
timeline but is deliberately NOT retrievable via RAG.
"""
import asyncio
import os
import uuid

import pytest

import conftest

pytestmark = pytest.mark.skipif(
    not (conftest.NEXUS_APP_DB_URL and os.getenv("VOYAGE_API_KEY")),
    reason="NEXUS_APP_DB_URL and VOYAGE_API_KEY required",
)

MARGARET = "33333333-0000-0000-0000-000000000001"

EMBEDDED_CALL = (
    "Care coordination call. We confirmed the client prefers a female caregiver who "
    "speaks Polish, and that visits should avoid Wednesday mornings because of her "
    "physical therapy appointments. The daughter also asked us to keep the same two "
    "caregivers on rotation for continuity. " * 2
)


async def _scenario():
    from app import db
    from app.services.communications import ingest_communication
    from app.services.retrieval import retrieve_communications

    tag = uuid.uuid4().hex[:8]
    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.communications where source = 'test-retr'"
            )
        out["embedded_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="call", direction="inbound",
            occurred_at="2026-07-04T10:00:00Z", body=f"{EMBEDDED_CALL} [ref {tag}]",
            entity_type="lead", entity_id=MARGARET, source="test-retr",
            external_id=f"retr:{tag}:call",
        )
        # store-only: a short SMS that must never surface in retrieval.
        out["store_only_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="sms", direction="inbound",
            occurred_at="2026-07-04T11:00:00Z",
            body=f"Please call me back about Wednesday. [{tag}]",
            entity_type="lead", entity_id=MARGARET, source="test-retr",
            external_id=f"retr:{tag}:sms",
        )
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            out["hits"] = await retrieve_communications(
                conn, "does she prefer a Polish-speaking caregiver", limit=8
            )
            await conn.execute(
                "delete from public.communications where source = 'test-retr'"
            )
        return out
    finally:
        await db.close_pool()


@pytest.fixture(scope="module")
def result():
    return asyncio.run(_scenario())


def test_embedded_comm_is_retrievable(result):
    ids = {h["communication_id"] for h in result["hits"]}
    assert result["embedded_id"] in ids


def test_store_only_comm_is_never_retrieved(result):
    ids = {h["communication_id"] for h in result["hits"]}
    assert result["store_only_id"] not in ids


def test_hits_carry_channel_and_source_labels(result):
    mine = [h for h in result["hits"] if h["communication_id"] == result["embedded_id"]]
    assert mine, "the embedded comm should be among the hits"
    hit = mine[0]
    assert hit["channel"] == "call"
    assert hit["source"] == "test-retr"
    assert "chunk_text" in hit
