"""Communications tier (v1.1.0): schema + the `ingest_communication` seam.

Schema assertions run over the direct `db` connection (rolls back). The service
scenario is gated on NEXUS_APP_DB_URL + VOYAGE_API_KEY — it exercises store-all
(a short SMS stored but not embedded), embed-selectively (a long call chunked +
embedded), idempotency by external id, and cross-source dedup by content hash.
"""
import asyncio
import os
import uuid

import psycopg
import pytest

import conftest
from conftest import set_tenant

MARGARET = "33333333-0000-0000-0000-000000000001"  # a demo-tenant lead


# --------------------------------------------------------------------------- #
# Schema (db fixture — rolls back, no embeddings)
# --------------------------------------------------------------------------- #

def test_communications_tables_exist(db):
    with db.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema='public' "
            "and table_name in ('communications','communication_chunks')"
        )
        names = {r[0] for r in cur.fetchall()}
    assert names == {"communications", "communication_chunks"}


def test_comm_chunks_hnsw_index_exists(db):
    with db.cursor() as cur:
        cur.execute(
            "select indexdef from pg_indexes where schemaname='public' "
            "and indexname='communication_chunks_embedding_idx'"
        )
        row = cur.fetchone()
    assert row is not None and "hnsw" in row[0].lower()


def _insert_comm(cur, tenant, *, source, external_id):
    cur.execute(
        """insert into public.communications
             (tenant_id, channel, direction, occurred_at, body, source, external_id)
           values (%s, 'call', 'inbound', '2026-07-03T10:00:00Z', 'hi', %s, %s)
           returning id""",
        (tenant, source, external_id),
    )
    return cur.fetchone()[0]


def test_source_external_id_is_unique(db, demo_tenant_id):
    set_tenant(db, demo_tenant_id)
    with db.cursor() as cur:
        _insert_comm(cur, demo_tenant_id, source="test", external_id="dup-1")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_comm(cur, demo_tenant_id, source="test", external_id="dup-1")


def test_null_external_id_rows_are_allowed(db, demo_tenant_id):
    """The partial unique index only covers non-null external ids, so two rows with
    no connector id (e.g. manually logged) coexist."""
    set_tenant(db, demo_tenant_id)
    with db.cursor() as cur:
        _insert_comm(cur, demo_tenant_id, source="test", external_id=None)
        _insert_comm(cur, demo_tenant_id, source="test", external_id=None)  # no raise


def test_entity_summaries_holds_two_kinds_per_entity(db, demo_tenant_id):
    """The PK includes `kind`, so two derived kinds can coexist for one entity.

    v1.1.4 merged the comm profile INTO the smart summary, so only `smart_summary`
    is written today — but the column and its place in the PK stay, and this proves
    the seam still admits a second kind without schema surgery."""
    set_tenant(db, demo_tenant_id)
    entity = str(uuid.uuid4())
    with db.cursor() as cur:
        for kind in ("smart_summary", "probe_kind"):
            cur.execute(
                """insert into public.entity_summaries
                     (tenant_id, entity_type, entity_id, summary, kind)
                   values (%s, 'lead', %s, %s, %s)""",
                (demo_tenant_id, entity, f"the {kind}", kind),
            )
        cur.execute(
            "select count(*) from public.entity_summaries where entity_id=%s", (entity,)
        )
        assert cur.fetchone()[0] == 2


# --------------------------------------------------------------------------- #
# Service — ingest_communication (gated on DB + Voyage)
# --------------------------------------------------------------------------- #

_service = pytest.mark.skipif(
    not (conftest.NEXUS_APP_DB_URL and os.getenv("VOYAGE_API_KEY")),
    reason="NEXUS_APP_DB_URL and VOYAGE_API_KEY required",
)

LONG_CALL = (
    "Intake call transcript. The caller is the daughter of a prospective client in "
    "Naperville. Her mother fell in the kitchen two weeks ago and has been unsteady "
    "since. The family wants four mornings a week of help with bathing, dressing, and "
    "breakfast, and asked specifically about caregiver consistency and whether "
    "long-term care insurance is accepted. She will confirm the assessment time after "
    "speaking with her brother this weekend. " * 2
)


async def _service_scenario():
    from psycopg.rows import dict_row

    from app import db
    from app.services.communications import ingest_communication

    tag = uuid.uuid4().hex[:8]
    # Unique per run so content_hash dedup can't reuse a stale row left by an
    # earlier (e.g. rate-limited) run; identical WITHIN the run so the idempotency
    # and cross-source-dedup assertions still hold.
    call_body = f"{LONG_CALL} [ref {tag}]"
    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.communications where source in "
                "('test-comms','other-source')"
            )
        # store-all, embed-selectively: a short SMS is stored, not embedded.
        out["sms_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="sms", direction="outbound",
            occurred_at="2026-07-03T09:00:00Z", body=f"On my way, see you at 10. [{tag}]",
            entity_type="lead", entity_id=MARGARET, source="test-comms",
            external_id=f"test:{tag}:sms",
        )
        # a long call is chunked + embedded.
        out["call_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="call", direction="inbound",
            occurred_at="2026-07-03T10:00:00Z", body=call_body,
            entity_type="lead", entity_id=MARGARET, source="test-comms",
            external_id=f"test:{tag}:call",
        )
        # idempotent: same (source, external_id) returns the same row.
        out["call_replay_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="call", direction="inbound",
            occurred_at="2026-07-03T10:00:00Z", body=call_body,
            entity_type="lead", entity_id=MARGARET, source="test-comms",
            external_id=f"test:{tag}:call",
        )
        # cross-source dedup: same content + entity from another source -> existing id.
        out["cross_source_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="call", direction="inbound",
            occurred_at="2026-07-03T10:00:00Z", body=call_body,
            entity_type="lead", entity_id=MARGARET, source="other-source",
            external_id=f"other:{tag}:call",
        )
        # blank body is a no-op.
        out["blank_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="note", direction=None,
            occurred_at="2026-07-03T11:00:00Z", body="   ",
            source="test-comms", external_id=f"test:{tag}:blank",
        )

        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select channel, embedded, entity_id from public.communications "
                    "where id = %s", (out["sms_id"],)
                )
                out["sms"] = await cur.fetchone()
                await cur.execute(
                    "select embedded from public.communications where id = %s",
                    (out["call_id"],),
                )
                out["call"] = await cur.fetchone()
                await cur.execute(
                    "select count(*) n from public.communication_chunks "
                    "where communication_id = %s", (out["sms_id"],)
                )
                out["sms_chunk_count"] = (await cur.fetchone())["n"]
                await cur.execute(
                    "select count(*) n, count(embedding) embedded "
                    "from public.communication_chunks where communication_id = %s",
                    (out["call_id"],),
                )
                out["call_chunks"] = await cur.fetchone()

            # cleanup (chunks cascade on comm delete)
            await conn.execute(
                "delete from public.communications where source in "
                "('test-comms','other-source')"
            )
        return out
    finally:
        await db.close_pool()


@pytest.fixture(scope="module")
def svc():
    return asyncio.run(_service_scenario())


@_service
def test_short_sms_is_stored_not_embedded(svc):
    assert svc["sms_id"] is not None
    assert svc["sms"]["channel"] == "sms"
    assert svc["sms"]["embedded"] is False
    assert str(svc["sms"]["entity_id"]) == MARGARET
    assert svc["sms_chunk_count"] == 0


@_service
def test_long_call_is_chunked_and_embedded(svc):
    assert svc["call"]["embedded"] is True
    assert svc["call_chunks"]["n"] >= 1
    assert svc["call_chunks"]["embedded"] == svc["call_chunks"]["n"]


@_service
def test_ingest_is_idempotent_by_external_id(svc):
    assert svc["call_replay_id"] == svc["call_id"]


@_service
def test_cross_source_dedup_by_content_hash(svc):
    assert svc["cross_source_id"] == svc["call_id"]


@_service
def test_blank_body_is_a_no_op(svc):
    assert svc["blank_id"] is None
