"""WelcomeHome activity -> communications tier (v1.1.0). Gated on NEXUS_APP_DB_URL
+ VOYAGE_API_KEY.

The v1.0.0 transcript path routed long CRM narratives into the `documents` corpus.
It now routes EVERY message/interaction into the communications tier instead, linked
to its `lead.activity_logged` timeline event as the spine. This drives the runner's
real `after_commit` and asserts: a communication row tagged to the lead, its
source_event_id pointing at the activity event, no documents row created, retrievable
via search_communications and NOT via search_documents, and idempotent on replay.
"""
import asyncio
import os
import uuid

import pytest

import conftest
from app.services.connectors.ingest import SYNC_RECEIPT, ingest_payload

pytestmark = pytest.mark.skipif(
    not (conftest.NEXUS_APP_DB_URL and os.getenv("VOYAGE_API_KEY")),
    reason="NEXUS_APP_DB_URL and VOYAGE_API_KEY required",
)

TRANSCRIPT = (
    "Intake call transcript. Claire, the daughter, called about her mother Marguerite "
    "who fell in the kitchen and has been unsteady since. The family wants four mornings "
    "a week of help with bathing and breakfast, and asked whether the same caregiver "
    "comes each visit and whether long-term care insurance is accepted. " * 2
)


async def _scenario():
    from psycopg.rows import dict_row

    from app import db
    from app.services.connectors.wh_runner import WelcomeHomeRunner

    tag = uuid.uuid4().hex[:8]
    pid = f"wh:prospect:comm-{tag}"
    aid = f"wh:activity:comm-{tag}"
    out: dict = {}

    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            # 1. A lead for the activity to land on.
            await ingest_payload(
                "welcomehome",
                {"event": "prospect.synced", "prospect": {
                    "external_id": pid, "name": "Marguerite Test",
                    "source": "A Place For Mom", "status": "new",
                    "stage_name": "Inquiry", "contacts": [],
                }},
                tenant_id=conftest.DEMO_TENANT, receipt_event_type=SYNC_RECEIPT, conn=conn,
            )
            row = await (await conn.execute(
                "select entity_id from public.external_ids "
                "where entity_type='lead' and external_id=%s", (pid,)
            )).fetchone()
            lead_id = str(row[0])
            out["lead_id"] = lead_id

            # 2. A Call activity -> writes the lead.activity_logged spine event.
            result = await ingest_payload(
                "welcomehome",
                {"event": "activity.synced", "activity": {
                    "external_id": pid, "activity_id": aid, "activity_type": "Call",
                    "direction": "inbound", "notes": TRANSCRIPT,
                    "occurred_at": "2026-06-01T15:10:00.000Z",
                    "summary": "Call (inbound): Intake call transcript.",
                }},
                tenant_id=conftest.DEMO_TENANT, receipt_event_type=SYNC_RECEIPT, conn=conn,
            )
            out["activity_matched"] = result.get("matched")

        # 3. Drive the runner's real after_commit over the queued message.
        runner = WelcomeHomeRunner()
        runner._pending_communications = [
            ("call", "inbound", "2026-06-01T15:10:00.000Z", TRANSCRIPT, lead_id, aid)
        ]
        out["ingested"] = await runner.after_commit(conftest.DEMO_TENANT)
        # replay: a second after_commit over the same message must not duplicate it.
        runner._pending_communications = [
            ("call", "inbound", "2026-06-01T15:10:00.000Z", TRANSCRIPT, lead_id, aid)
        ]
        out["ingested_replay"] = await runner.after_commit(conftest.DEMO_TENANT)

        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select id, channel, embedded, entity_id, source_event_id "
                    "from public.communications where source='welcomehome' and external_id=%s",
                    (aid,),
                )
                out["comms"] = await cur.fetchall()
                # The spine event this comm should point at.
                await cur.execute(
                    "select id from public.events where entity_id=%s "
                    "and event_type='lead.activity_logged' "
                    "and payload->'detail'->>'wh_activity_id'=%s",
                    (lead_id, aid),
                )
                out["event_id"] = str((await cur.fetchone())["id"])
                # No documents row was created for this lead (files-only corpus).
                await cur.execute(
                    "select count(*) n from public.documents where entity_id=%s", (lead_id,)
                )
                out["doc_count"] = (await cur.fetchone())["n"]

            from app.services.retrieval import retrieve_chunks, retrieve_communications
            out["comm_hits"] = await retrieve_communications(
                conn, "does the same caregiver come each visit", limit=8
            )
            out["doc_hits"] = await retrieve_chunks(
                conn, "does the same caregiver come each visit", limit=8
            )

            # cleanup (chunks cascade; leave immutable events)
            await conn.execute(
                "delete from public.communications where source='welcomehome' and external_id=%s",
                (aid,),
            )
            await conn.execute("delete from public.leads where id=%s", (lead_id,))
            await conn.execute(
                "delete from public.external_ids where external_id in (%s)", (pid,)
            )
        return out
    finally:
        await db.close_pool()


@pytest.fixture(scope="module")
def result():
    return asyncio.run(_scenario())


def test_activity_creates_one_communication_linked_to_its_event(result):
    assert result["activity_matched"] == 1
    assert result["ingested"] == 1
    comms = result["comms"]
    assert len(comms) == 1
    comm = comms[0]
    assert comm["channel"] == "call"
    assert str(comm["entity_id"]) == result["lead_id"]
    # Event-as-spine: the comm points at the activity's timeline event.
    assert str(comm["source_event_id"]) == result["event_id"]


def test_long_transcript_is_embedded(result):
    assert result["comms"][0]["embedded"] is True


def test_no_documents_row_is_created(result):
    """The documents corpus is files-only now — a CRM narrative never lands there."""
    assert result["doc_count"] == 0


def test_transcript_is_retrievable_via_communications_not_documents(result):
    comm_id = str(result["comms"][0]["id"])
    comm_ids = {h["communication_id"] for h in result["comm_hits"]}
    assert comm_id in comm_ids
    # search_documents must not surface it — it isn't a document.
    doc_texts = " ".join(h.get("chunk_text", "") for h in result["doc_hits"])
    assert "Intake call transcript" not in doc_texts


def test_after_commit_is_idempotent(result):
    """A replayed sweep re-offers the same message; still exactly one row."""
    assert result["ingested_replay"] == 1
    assert len(result["comms"]) == 1
