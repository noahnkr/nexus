"""One merged summary per entity (v1.1.4), gated on NEXUS_APP_DB_URL.

Replaces test_comm_profile.py. The comm profile is no longer a second cached
artifact — correspondence is a section of the one summary — so what needs proving
moved: that the generator actually PUTS the messages in the prompt, that an entity
with no messages says so plainly instead of dropping the section, and that the
cache still holds exactly one row per entity.

Offline throughout: the Anthropic client is faked so prompt assembly is asserted
without a network call (the test_applicant_summary pattern).
"""
import asyncio
import uuid

import pytest

import conftest

pytestmark = pytest.mark.skipif(
    not conftest.NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

# Synthetic entity ids — entity_id is not an FK, so no row needs to exist.
WITH_COMMS = str(uuid.uuid4())
WITHOUT_COMMS = str(uuid.uuid4())

BODY_ONE = "She prefers morning calls and always replies within the hour."
BODY_TWO = "Following up by email as promised — please send the care plan when ready."


class _TextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_TextBlock(text)]


class _Messages:
    def __init__(self, captured):
        self.captured = captured

    async def create(self, **kwargs):
        self.captured.append(kwargs)
        return _Resp("A merged summary mentioning the record, activity and messages.")


class _Client:
    def __init__(self, captured):
        self.messages = _Messages(captured)


async def _seed_comms(conn, tenant, entity_id, tag):
    for i, (channel, body, when) in enumerate(
        [("call", BODY_ONE, "2026-07-01T10:00:00Z"),
         ("email", BODY_TWO, "2026-07-02T09:30:00Z")],
        start=1,
    ):
        await conn.execute(
            """insert into public.communications
                 (tenant_id, channel, direction, occurred_at, body, entity_type,
                  entity_id, source, external_id)
               values (%s, %s, 'inbound', %s, %s, 'lead', %s, 'test-merge', %s)""",
            (tenant, channel, when, body, entity_id, f"merge:{tag}:{i}"),
        )


async def _scenario():
    from psycopg.rows import dict_row

    from app import db
    from app.config import settings
    from app.services.views import summary as summary_mod
    from app.services.views.summary import (
        generate_entity_summary,
        get_or_generate_entity_summary,
        regenerate_entity_summary,
    )

    tag = uuid.uuid4().hex[:8]
    captured: list = []
    out: dict = {}

    original_key = settings.anthropic_api_key
    original_client = summary_mod.get_anthropic
    settings.anthropic_api_key = "sk-test-key"
    summary_mod.get_anthropic = lambda: _Client(captured)

    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await _seed_comms(conn, conftest.DEMO_TENANT, WITH_COMMS, tag)

            # --- an entity WITH correspondence ---
            await generate_entity_summary(
                conn,
                entity_row={"name": "Margaret Ellison", "status": "contacted"},
                entity_type="lead",
                entity_id=WITH_COMMS,
                prompt_intro="You summarize a prospective home-care client.",
                span_name="lead_summary",
            )
            out["with_comms"] = captured[-1]

            # --- an entity with NONE ---
            await generate_entity_summary(
                conn,
                entity_row={"name": "Quiet Lead", "status": "new"},
                entity_type="lead",
                entity_id=WITHOUT_COMMS,
                prompt_intro="You summarize a prospective home-care client.",
                span_name="lead_summary",
            )
            out["without_comms"] = captured[-1]

            # --- cache: one row, and regenerate overwrites rather than adds ---
            await get_or_generate_entity_summary(
                conn, conftest.DEMO_TENANT,
                entity_row={"name": "Margaret Ellison"},
                entity_type="lead", entity_id=WITH_COMMS,
                prompt_intro="intro", span_name="lead_summary",
            )
            calls_after_first = len(captured)
            # A second get must be served from cache — no model call.
            await get_or_generate_entity_summary(
                conn, conftest.DEMO_TENANT,
                entity_row={"name": "Margaret Ellison"},
                entity_type="lead", entity_id=WITH_COMMS,
                prompt_intro="intro", span_name="lead_summary",
            )
            out["cache_hit_added_no_call"] = len(captured) == calls_after_first

            await regenerate_entity_summary(
                conn, conftest.DEMO_TENANT,
                entity_row={"name": "Margaret Ellison"},
                entity_type="lead", entity_id=WITH_COMMS,
                prompt_intro="intro", span_name="lead_summary",
            )
            out["regen_made_a_call"] = len(captured) > calls_after_first

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select kind, count(*) n from public.entity_summaries "
                    "where entity_id = %s group by kind", (WITH_COMMS,),
                )
                out["cache_rows"] = [dict(r) for r in await cur.fetchall()]

            # cleanup
            await conn.execute(
                "delete from public.entity_summaries where entity_id = any(%s)",
                ([WITH_COMMS, WITHOUT_COMMS],),
            )
            await conn.execute(
                "delete from public.communications where source = 'test-merge'"
            )
        return out
    finally:
        settings.anthropic_api_key = original_key
        summary_mod.get_anthropic = original_client
        await db.close_pool()


@pytest.fixture(scope="module")
def result():
    return asyncio.run(_scenario())


def test_correspondence_reaches_the_prompt(result):
    """The whole point of the merge: the messages are IN the one summary's prompt."""
    user_content = result["with_comms"]["messages"][0]["content"]

    assert "Recent correspondence (oldest first):" in user_content
    assert BODY_ONE in user_content
    assert BODY_TWO in user_content
    # Oldest first, matching the section's promise.
    assert user_content.index(BODY_ONE) < user_content.index(BODY_TWO)
    # Channel is labeled so the model can say "prefers email".
    assert "[call inbound]" in user_content
    assert "[email inbound]" in user_content
    # The other two sections still stand.
    assert "Record:" in user_content
    assert "Recent activity (oldest first):" in user_content


def test_system_prompt_carries_the_vertical_intro_and_comms_guidance(result):
    system = result["with_comms"]["system"]

    assert "You summarize a prospective home-care client." in system
    assert "how they communicate" in system
    assert "3-5 short sentences" in system


def test_an_entity_with_no_messages_says_so_plainly(result):
    """Absence is stated, not silently dropped — the model should know the lack of
    correspondence is real rather than missing context."""
    user_content = result["without_comms"]["messages"][0]["content"]

    assert "(no correspondence on record yet)" in user_content
    assert "Recent correspondence (oldest first):" in user_content


def test_one_cache_row_per_entity(result):
    """One summary, one cache row, one Regenerate — the comm_profile kind is gone."""
    rows = result["cache_rows"]

    assert len(rows) == 1
    assert rows[0]["kind"] == "smart_summary"
    assert rows[0]["n"] == 1
    assert result["cache_hit_added_no_call"]
    assert result["regen_made_a_call"]
