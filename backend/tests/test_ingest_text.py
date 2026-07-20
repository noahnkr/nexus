"""Text ingestion seam (Module 18a, Task 6), gated on NEXUS_APP_DB_URL + VOYAGE_API_KEY.

`ingest_text` is how a call transcript on a CRM activity becomes retrievable in
chat. It reuses the M15 document entity tag rather than inventing a second way to
associate a document with a record, so the assertions here are mostly about that:
the document row and every chunk carry the lead tag, and `storage_path` is null
because there is no original file to keep.
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

TRANSCRIPT = (
    "Intake call transcript. The caller is the daughter of a prospective client in "
    "Naperville. She explained that her mother fell in the kitchen two weeks ago and "
    "has been unsteady on her feet since. Her mother lives alone in a two-story home "
    "and has been missing doses of her blood pressure medication. The family is "
    "looking for four mornings a week of help with bathing, dressing, and breakfast. "
    "They asked specifically about caregiver consistency — whether the same person "
    "would come to every visit — and about whether long-term care insurance is "
    "accepted. The daughter will confirm a time for the in-home assessment after "
    "speaking with her brother this weekend."
)


async def _scenario():
    from psycopg.rows import dict_row

    from app import db
    from app.services.ingestion import ingest_text

    external_id = f"wh:activity:test-{uuid.uuid4().hex[:8]}"
    out: dict = {}
    await db.open_pool()
    try:
        out["document_id"] = await ingest_text(
            conftest.DEMO_TENANT,
            "Call — WelcomeHome activity",
            TRANSCRIPT,
            entity_type="lead",
            entity_id=MARGARET,
            source="welcomehome",
            external_id=external_id,
        )
        # Re-offering the same transcript must not duplicate it.
        out["replay_id"] = await ingest_text(
            conftest.DEMO_TENANT,
            "Call — WelcomeHome activity",
            TRANSCRIPT,
            entity_type="lead",
            entity_id=MARGARET,
            source="welcomehome",
            external_id=external_id,
        )
        out["empty_id"] = await ingest_text(
            conftest.DEMO_TENANT, "Empty", "   ", source="welcomehome"
        )

        document_id = out["document_id"]
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select filename, status, storage_path, entity_type, entity_id, "
                    "mime_type from public.documents where id = %s", (document_id,)
                )
                out["document"] = await cur.fetchone()

                await cur.execute(
                    "select count(*) as n, count(embedding) as embedded, "
                    "count(*) filter (where entity_id = %s) as tagged "
                    "from public.document_chunks where document_id = %s",
                    (MARGARET, document_id),
                )
                out["chunks"] = await cur.fetchone()

            # Retrievable through the normal search path.
            from app.services.retrieval import retrieve_chunks

            out["hits"] = await retrieve_chunks(
                conn, "caregiver consistency long-term care insurance", limit=5
            )

            await conn.execute(
                "delete from public.document_chunks where document_id = %s", (document_id,)
            )
            await conn.execute("delete from public.documents where id = %s", (document_id,))
            await conn.execute(
                "delete from public.external_ids where external_id = %s", (external_id,)
            )
        return out
    finally:
        await db.close_pool()


@pytest.fixture(scope="module")
def result():
    """One run for the whole module. Every assertion below reads the same sweep:
    the embeddings API is rate-limited on the free tier, and re-running the
    scenario per test buys nothing but 429s."""
    return asyncio.run(_scenario())


def test_ingest_text_writes_a_tagged_document_with_no_stored_file(result):
    r = result

    doc = r["document"]
    assert doc["status"] == "ready"
    assert doc["filename"] == "Call — WelcomeHome activity"
    # No original file exists — the text lives in the chunks.
    assert doc["storage_path"] is None
    # The M15 entity tag, not a second association mechanism.
    assert doc["entity_type"] == "lead"
    assert str(doc["entity_id"]) == MARGARET


def test_every_chunk_inherits_the_entity_tag_and_is_embedded(result):
    r = result
    chunks = r["chunks"]
    assert chunks["n"] >= 1
    assert chunks["embedded"] == chunks["n"]
    assert chunks["tagged"] == chunks["n"]


def test_ingest_text_is_idempotent_by_external_id(result):
    """A re-runnable backfill re-offers the same transcript; it must find the
    existing document rather than duplicate the chunks."""
    r = result
    assert r["replay_id"] == r["document_id"]


def test_empty_text_is_a_no_op_not_a_failed_document(result):
    r = result
    assert r["empty_id"] is None


def test_the_transcript_is_retrievable(result):
    r = result
    texts = " ".join(h.get("chunk_text", "") for h in r["hits"])
    assert "caregiver consistency" in texts
