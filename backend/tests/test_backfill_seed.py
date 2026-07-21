"""Split history-seed (v1.1.0, Task 7): the backfill's three-pass message ingest.

`_ingest_communications` stores every message (structured pass, embed=False), then
embeds only the should_embed-selected ones (batched pass), then builds a comm
profile per touched entity (summary pass). Gated on NEXUS_APP_DB_URL + VOYAGE (the
profile pass additionally needs ANTHROPIC_API_KEY; it degrades gracefully without).
A full re-run must add no duplicate rows, chunks, or profiles.
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

_has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
LEAD = str(uuid.uuid4())

LONG = (
    "Care coordination call. The client wants a Polish-speaking caregiver and asked "
    "us to avoid Wednesday mornings for her physical therapy. She prefers the same two "
    "caregivers on rotation for continuity and requested written updates after visits. "
    * 3
)


async def _row(conn, external_id):
    """One communication + its chunk count, by external id."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, embedded from public.communications where external_id = %s",
            (external_id,),
        )
        comm = await cur.fetchone()
        if comm is None:
            return None
        await cur.execute(
            "select count(*) n from public.communication_chunks "
            "where communication_id = %s", (comm["id"],)
        )
        comm["chunks"] = (await cur.fetchone())["n"]
    return comm


async def _profile_count(conn):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select count(*) n from public.entity_summaries "
            "where entity_id = %s and kind = 'comm_profile'", (LEAD,)
        )
        return (await cur.fetchone())["n"]


async def _scenario():
    from app import db
    from app.scripts.backfill_welcomehome import _ingest_communications

    tag = uuid.uuid4().hex[:8]
    call_id = f"seed:{tag}:call"
    sms_id = f"seed:{tag}:sms"
    pending = [
        ("call", "inbound", "2026-07-01T10:00:00Z", LONG, LEAD, call_id),
        ("sms", "outbound", "2026-07-01T11:00:00Z", "See you at 10.", LEAD, sms_id),
    ]
    out: dict = {}
    await db.open_pool()
    try:
        out["stored"] = await _ingest_communications(conftest.DEMO_TENANT, pending)
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            out["call_row"] = await _row(conn, call_id)
            out["sms_row"] = await _row(conn, sms_id)
            out["profiles"] = await _profile_count(conn)

        # full re-run: idempotent — no new rows, chunks, or profiles.
        out["stored_replay"] = await _ingest_communications(conftest.DEMO_TENANT, pending)
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            out["call_row_replay"] = await _row(conn, call_id)
            out["profiles_replay"] = await _profile_count(conn)
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.communications where external_id like %s",
                    (f"seed:{tag}:%",),
                )
                out["comm_count_replay"] = (await cur.fetchone())[0]
            await conn.execute(
                "delete from public.communications where external_id like %s",
                (f"seed:{tag}:%",),
            )
            await conn.execute(
                "delete from public.entity_summaries where entity_id = %s", (LEAD,)
            )
        return out
    finally:
        await db.close_pool()


@pytest.fixture(scope="module")
def result():
    return asyncio.run(_scenario())


def test_structured_pass_stores_every_message(result):
    """Store-all: both the long call and the short SMS get a row."""
    assert result["stored"] == 2
    assert result["call_row"] is not None
    assert result["sms_row"] is not None


def test_short_message_is_stored_but_never_embedded(result):
    """The batched pass is selective — a short SMS is stored with no chunks and
    embedded=false, deterministically (the policy, not the network, decides)."""
    from app.services.communications import should_embed

    assert result["sms_row"]["embedded"] is False
    assert result["sms_row"]["chunks"] == 0
    # and the policy selects the long call, not the sms
    assert should_embed("call", LONG) is True
    assert should_embed("sms", "See you at 10.") is False


def test_full_rerun_is_idempotent(result):
    """A re-run adds no duplicate rows, chunks, or profiles."""
    assert result["comm_count_replay"] == 2  # still exactly our two rows
    assert result["stored_replay"] == 2
    a, b = result["call_row"], result["call_row_replay"]
    assert b["chunks"] == a["chunks"]        # chunk count stable across re-embed
    assert result["profiles_replay"] == result["profiles"]


@pytest.mark.skipif(not _has_key, reason="ANTHROPIC_API_KEY required")
def test_summary_pass_builds_one_profile_for_the_touched_lead(result):
    assert result["profiles"] == 1
