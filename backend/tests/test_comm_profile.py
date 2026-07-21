"""Per-entity communication profile (v1.1.0, tier-3 derived knowledge). Gated on
NEXUS_APP_DB_URL; the model call additionally needs ANTHROPIC_API_KEY.

The profile is cached under the `comm_profile` kind, so it must coexist with an
entity's `smart_summary` row rather than clobbering it. With no messages it returns
a plain placeholder without a model call; with messages but no key it raises
SummaryUnavailable (the router maps that to 503).
"""
import asyncio
import os
import uuid

import pytest

import conftest

pytestmark = pytest.mark.skipif(
    not conftest.NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

_has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
LEAD = str(uuid.uuid4())  # a synthetic entity id (entity_id is not an FK)


async def _seed_comm(conn, tenant, lead, tag):
    await conn.execute(
        """insert into public.communications
             (tenant_id, channel, direction, occurred_at, body, entity_type,
              entity_id, source, external_id)
           values (%s, 'call', 'inbound', '2026-07-01T10:00:00Z', %s, 'lead', %s,
                   'test-profile', %s)""",
        (tenant, "She prefers morning calls and always replies within the hour.",
         lead, f"prof:{tag}:1"),
    )


async def _scenario():
    from psycopg.rows import dict_row

    from app import db
    from app.services.views.summary import (
        SummaryUnavailable,
        generate_comm_profile,
        get_or_generate_comm_profile,
        regenerate_comm_profile,
    )

    tag = uuid.uuid4().hex[:8]
    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            # An empty entity -> graceful placeholder, no model call, no cache write.
            empty = await generate_comm_profile(
                conn, entity_type="lead", entity_id=LEAD
            )
            out["empty_summary"] = empty["summary"]

            # A pre-existing smart summary that must survive comm-profile writes.
            await conn.execute(
                """insert into public.entity_summaries
                     (tenant_id, entity_type, entity_id, kind, summary)
                   values (%s, 'lead', %s, 'smart_summary', %s)
                   on conflict do nothing""",
                (conftest.DEMO_TENANT, LEAD, "the smart summary"),
            )
            await _seed_comm(conn, conftest.DEMO_TENANT, LEAD, tag)

        if _has_key:
            async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
                first = await get_or_generate_comm_profile(
                    conn, conftest.DEMO_TENANT, entity_type="lead", entity_id=LEAD
                )
                out["profile"] = first["summary"]
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select kind, summary from public.entity_summaries "
                        "where entity_id = %s order by kind", (LEAD,)
                    )
                    out["rows"] = await cur.fetchall()
                # Regenerate overwrites only the comm_profile row.
                await regenerate_comm_profile(
                    conn, conftest.DEMO_TENANT, entity_type="lead", entity_id=LEAD
                )
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select count(*) n from public.entity_summaries "
                        "where entity_id = %s", (LEAD,)
                    )
                    out["row_count_after_regen"] = (await cur.fetchone())["n"]
        else:
            async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
                try:
                    await get_or_generate_comm_profile(
                        conn, conftest.DEMO_TENANT, entity_type="lead", entity_id=LEAD
                    )
                    out["raised"] = False
                except SummaryUnavailable:
                    out["raised"] = True

        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.entity_summaries where entity_id = %s", (LEAD,)
            )
            await conn.execute(
                "delete from public.communications where source = 'test-profile'"
            )
        return out
    finally:
        await db.close_pool()


@pytest.fixture(scope="module")
def result():
    return asyncio.run(_scenario())


def test_empty_entity_returns_a_plain_placeholder(result):
    assert "No communications" in result["empty_summary"]


@pytest.mark.skipif(not _has_key, reason="ANTHROPIC_API_KEY required")
def test_profile_coexists_with_the_smart_summary(result):
    kinds = {r["kind"] for r in result["rows"]}
    assert kinds == {"smart_summary", "comm_profile"}
    # the smart summary was not clobbered
    smart = [r for r in result["rows"] if r["kind"] == "smart_summary"][0]
    assert smart["summary"] == "the smart summary"


@pytest.mark.skipif(not _has_key, reason="ANTHROPIC_API_KEY required")
def test_regenerate_overwrites_only_the_comm_profile_row(result):
    assert result["row_count_after_regen"] == 2  # still smart_summary + comm_profile


@pytest.mark.skipif(_has_key, reason="runs only without an Anthropic key")
def test_missing_key_raises_summary_unavailable(result):
    assert result["raised"] is True
