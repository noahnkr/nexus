"""Phone-domain entity resolution for GoTo events (v1.2.0, Task 4).

The four scenarios the plan names, each driven through the REAL ingress
(`POST /api/webhooks/goto` → `ingest_payload` → `route_normalized_event`) rather
than by calling the resolver directly, because the thing worth testing is that a
phone-keyed event survives the whole seam:

  1. an already-mapped number short-circuits on `external_ids`;
  2. an unmapped number matching exactly one record resolves, REGISTERS the
     mapping, and the next event from that number is a plain `matched`;
  3. a number shared by two different entities becomes a review task that NAMES
     both candidates — never a guess;
  4. an unknown number becomes the standard review task.

Scenario 2 also covers the type question that motivated the whole change: the
adapter says `entity_type="lead"` as a fallback, but the number belongs to a
CAREGIVER, and resolution must follow the seam's answer rather than the
adapter's guess.

Cleanup is narrowed to this module's own fixture ids (the v1.1.2 lesson: a
`like 'wh:%'` cleanup once deleted 90 live-synced leads).
"""
import asyncio
import json
import uuid

import httpx
import pytest

import conftest
from app.config import settings
from app.services.connectors import sign
from app.services.connectors.base import SIGNATURE_HEADER

SECRET = "test-webhook-secret"
BUSINESS = "+16303602784"

# Numbers verified absent from every people table and from `external_ids` before
# being chosen. The obvious 619-555-01xx range was NOT free — the seed's caregiver
# Brian Okafor already owns +16195550202, and using it made this suite report a
# false ambiguity. Any future number added here should be checked the same way.
MAPPED = "+12025550101"         # scenario 1 — pre-mapped in external_ids
CAREGIVER_NUM = "+12025550202"  # scenario 2 — on a resource row only
SHARED = "+12025550303"         # scenario 3 — on both a lead and a resource
UNKNOWN = "+12025550404"        # scenario 4 — on nothing

# Fixture ids, so teardown can name exactly what it created.
LEAD_A = str(uuid.uuid5(uuid.NAMESPACE_DNS, "nexus-goto-res-lead-a"))
LEAD_SHARED = str(uuid.uuid5(uuid.NAMESPACE_DNS, "nexus-goto-res-lead-shared"))
RES_CAREGIVER = str(uuid.uuid5(uuid.NAMESPACE_DNS, "nexus-goto-res-resource-cg"))
RES_SHARED = str(uuid.uuid5(uuid.NAMESPACE_DNS, "nexus-goto-res-resource-shared"))
ALL_FIXTURE_IDS = (LEAD_A, LEAD_SHARED, RES_CAREGIVER, RES_SHARED)

MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"


def _call_frame(counterpart: str, name: str = "Caller") -> dict:
    """A real Call Events Report notification, as the WebSocket bridge sees it."""
    return {
        "data": {
            "source": "call-events-report",
            "type": "REPORT_SUMMARY",
            "content": {
                "conversationSpaceId": f"conv-{counterpart[-4:]}",
                "direction": "INBOUND",
                "ownerPhoneNumber": BUSINESS,
                "startTime": "2026-07-21T18:42:43.633Z",
                "duration": 45000,
                "caller": {"name": name, "number": counterpart},
                "callee": {"name": "Office", "number": "1000"},
            },
        }
    }


async def _post(client, payload):
    body = json.dumps(payload).encode()
    settings.nexus_webhook_secret = SECRET
    return await client.post(
        "/api/webhooks/goto",
        content=body,
        headers={"content-type": "application/json", SIGNATURE_HEADER: sign(body)},
    )


async def _scenario():
    from app import db
    from app.main import app

    settings.nexus_webhook_secret = SECRET
    settings.nexus_tenant_id = conftest.DEMO_TENANT
    settings.goto_business_number = BUSINESS
    settings.goto_ignored_numbers = ""
    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute("select now()")
                start_ts = (await cur.fetchone())[0]

            # Scenario 1: a number already mapped to a known lead.
            await conn.execute(
                """insert into public.external_ids
                     (tenant_id, entity_type, entity_id, source_system, external_id)
                   values (%s,'lead',%s,'phone',%s)""",
                (conftest.DEMO_TENANT, MARGARET_LEAD, MAPPED),
            )
            # Scenario 2: the number lives on a CAREGIVER, not a lead.
            await conn.execute(
                """insert into public.resources
                     (id, tenant_id, name, phone, status)
                   values (%s,%s,'Test Caregiver Nina',%s,'active')""",
                (RES_CAREGIVER, conftest.DEMO_TENANT, "(202) 555-0202"),
            )
            # Scenario 3: the same number on two different entities. Stored in two
            # different written forms, which is exactly how this happens in life.
            await conn.execute(
                """insert into public.leads (id, tenant_id, name, phone, status)
                   values (%s,%s,'Test Lead Shared',%s,'new')""",
                (LEAD_SHARED, conftest.DEMO_TENANT, "202-555-0303"),
            )
            await conn.execute(
                """insert into public.resources
                     (id, tenant_id, name, phone, status)
                   values (%s,%s,'Test Caregiver Shared',%s,'active')""",
                (RES_SHARED, conftest.DEMO_TENANT, "+1 (202) 555-0303"),
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            out = {
                "mapped": await _post(c, _call_frame(MAPPED, "Margaret")),
                "caregiver_first": await _post(c, _call_frame(CAREGIVER_NUM, "Nina")),
                "caregiver_second": await _post(c, _call_frame(CAREGIVER_NUM, "Nina")),
                "shared": await _post(c, _call_frame(SHARED, "Whoever")),
                "unknown": await _post(c, _call_frame(UNKNOWN, "Stranger")),
            }
        results = {k: (r.status_code, r.json()) for k, r in out.items()}

        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                # Did the caregiver number get its mapping registered, and with
                # the type the SEAM decided rather than the adapter's fallback?
                await cur.execute(
                    "select entity_type, entity_id from public.external_ids "
                    "where external_id = %s",
                    (CAREGIVER_NUM,),
                )
                registered = await cur.fetchone()

                # Which entity did the matched call events land on?
                await cur.execute(
                    "select entity_type, entity_id from public.events "
                    "where event_type='call.completed' and created_at >= %s "
                    "and entity_id is not null",
                    (start_ts,),
                )
                matched_events = await cur.fetchall()

                await cur.execute(
                    "select title, description from public.tasks "
                    "where created_at >= %s order by created_at",
                    (start_ts,),
                )
                tasks = await cur.fetchall()
        return results, registered, matched_events, tasks
    finally:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.external_ids where external_id in (%s,%s,%s,%s)",
                (MAPPED, CAREGIVER_NUM, SHARED, UNKNOWN),
            )
            await conn.execute(
                "delete from public.resources where id in (%s,%s)",
                (RES_CAREGIVER, RES_SHARED),
            )
            await conn.execute("delete from public.leads where id = %s", (LEAD_SHARED,))
            await conn.execute(
                "delete from public.tasks where title like 'Review: Call from%'"
            )
        await db.close_pool()


@pytest.fixture(scope="module")
def outcome():
    conftest._require("NEXUS_APP_DB_URL")
    return asyncio.run(_scenario())


def test_an_already_mapped_number_short_circuits(outcome):
    results, _, _, _ = outcome
    assert results["mapped"][0] == 200
    assert results["mapped"][1]["matched"] == 1


def test_an_unmapped_number_resolves_through_the_vertical_seam(outcome):
    results, _, _, _ = outcome
    assert results["caregiver_first"][1]["matched"] == 1
    assert results["caregiver_first"][1]["tasks"] == 0


def test_resolution_follows_the_seam_not_the_adapters_fallback_type(outcome):
    """The adapter says 'lead' because a phone number carries no type. This
    number is a caregiver's, and the mapping must record that — otherwise every
    later call from this caregiver resolves against the wrong table."""
    _, registered, _, _ = outcome
    assert registered is not None, "the resolved number should have been mapped"
    entity_type, entity_id = registered
    assert entity_type == "resource"
    assert str(entity_id) == RES_CAREGIVER


def test_the_registered_mapping_makes_the_next_call_a_plain_match(outcome):
    """The point of registering: the second call costs one indexed lookup, not a
    scan across every people table."""
    results, _, _, _ = outcome
    assert results["caregiver_second"][1]["matched"] == 1
    assert results["caregiver_second"][1]["tasks"] == 0


def test_matched_calls_land_on_the_right_entities(outcome):
    _, _, matched_events, _ = outcome
    landed = {(t, str(i)) for t, i in matched_events}
    assert ("resource", RES_CAREGIVER) in landed
    assert ("lead", MARGARET_LEAD) in landed


def test_a_shared_number_becomes_a_task_naming_both_candidates(outcome):
    """Ambiguity is reported, never resolved by coin-flip: attaching a call to
    the wrong record is invisible damage, whereas a task takes seconds to clear."""
    results, _, _, tasks = outcome
    assert results["shared"][1] == {
        "received": 1, "matched": 0, "created": 0, "tasks": 1
    }
    shared_tasks = [t for t in tasks if "shared number" in t[0]]
    assert len(shared_tasks) == 1
    description = shared_tasks[0][1]
    assert "Test Lead Shared" in description
    assert "Test Caregiver Shared" in description


def test_an_unknown_number_becomes_the_standard_review_task(outcome):
    results, _, _, _ = outcome
    assert results["unknown"][1] == {
        "received": 1, "matched": 0, "created": 0, "tasks": 1
    }


def test_every_task_is_plain_language(outcome):
    """CLAUDE.md: no raw JSON or tool payloads in user-facing surfaces."""
    _, _, _, tasks = outcome
    for title, description in tasks:
        assert "{" not in title
        assert "{" not in (description or "")
