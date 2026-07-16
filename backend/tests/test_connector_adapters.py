"""Placeholder connector adapters, end-to-end (Module 3b, Task 5).

One signed POST per source through the real ingress, using the fixture payloads,
against the live DB. Covers each delivery shape: full-payload (welcomehome/goto/
wellsky), Pub/Sub base64 (gmail), and watch-channel header states (gcal sync vs
exists). Matched cases are pre-seeded in external_ids; unknown references fall to
review tasks. Seeded rows + this run's tasks are cleaned up; immutable events
remain.
"""
import asyncio
import json
import pathlib

import httpx

import conftest
from app.config import settings
from app.services.connectors import sign
from app.services.connectors.base import SIGNATURE_HEADER

SECRET = "test-webhook-secret"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "webhooks"

MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"
WALTER_CLIENT = "44444444-0000-0000-0000-000000000001"
SEED_SCHEDULE = "66666666-0000-0000-0000-000000000001"

GOTO_CALLER = "+16195559100"          # normalized from the fixture's "+1 (619) 555-9100"
WELLSKY_PATIENT = "WS-PATIENT-TEST-1"
GCAL_EVENT = "cal-evt-test-1"
WELCOMEHOME_PROSPECT = "WH-TEST-PROSPECT-9001"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _headers(body: bytes, extra: dict | None = None) -> dict:
    settings.nexus_webhook_secret = SECRET
    h = {"content-type": "application/json", SIGNATURE_HEADER: sign(body)}
    if extra:
        h.update(extra)
    return h


async def _post(client, source, payload, extra_headers=None):
    body = json.dumps(payload).encode()
    return await client.post(
        f"/api/webhooks/{source}", content=body, headers=_headers(body, extra_headers)
    )


async def _scenario():
    from app import db
    from app.main import app

    settings.nexus_webhook_secret = SECRET
    settings.nexus_tenant_id = conftest.DEMO_TENANT
    await db.open_pool()
    try:
        # Marker so per-run receipt/task counts are rerun-safe.
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute("select now()")
                start_ts = (await cur.fetchone())[0]
            # Pre-seed the matched mappings.
            await conn.execute(
                """insert into public.external_ids
                     (tenant_id, entity_type, entity_id, source_system, external_id)
                   values
                     (%s,'lead',%s,'phone',%s),
                     (%s,'client',%s,'ehr',%s),
                     (%s,'schedule',%s,'calendar',%s)""",
                (conftest.DEMO_TENANT, MARGARET_LEAD, GOTO_CALLER,
                 conftest.DEMO_TENANT, WALTER_CLIENT, WELLSKY_PATIENT,
                 conftest.DEMO_TENANT, SEED_SCHEDULE, GCAL_EVENT),
            )

        goto = _fixture("goto")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            results = {
                "welcomehome": (await _post(c, "welcomehome", _fixture("welcomehome"))),
                "goto_call": (await _post(c, "goto", goto["call_completed"])),
                "goto_sms": (await _post(c, "goto", goto["sms_unknown"])),
                "wellsky": (await _post(c, "wellsky", _fixture("wellsky"))),
                "gmail": (await _post(c, "gmail", _fixture("gmail"))),
                "gcal_exists": (
                    await _post(c, "gcal", _fixture("gcal"), {"x-goog-resource-state": "exists"})
                ),
                "gcal_sync": (
                    await _post(c, "gcal", {}, {"x-goog-resource-state": "sync"})
                ),
            }
        out = {k: (r.status_code, r.json()) for k, r in results.items()}

        # DB cross-checks scoped to this run.
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                # welcomehome created a lead + mapping.
                await cur.execute(
                    "select entity_id from public.external_ids where external_id=%s",
                    (WELCOMEHOME_PROSPECT,),
                )
                wh_map = await cur.fetchone()
                await cur.execute(
                    "select name from public.leads where id=%s",
                    (str(wh_map[0]),) if wh_map else (MARGARET_LEAD,),
                )
                wh_lead_name = (await cur.fetchone())[0] if wh_map else None

                # Exactly one webhook.received per POST this run (7 POSTs).
                await cur.execute(
                    "select count(*) from public.events "
                    "where event_type='webhook.received' and created_at >= %s",
                    (start_ts,),
                )
                receipts = (await cur.fetchone())[0]

                # Every event this run uses the connector name as source_system.
                await cur.execute(
                    "select count(*) from public.events where created_at >= %s "
                    "and source_system not in "
                    "('welcomehome','goto','wellsky','gmail','gcal')",
                    (start_ts,),
                )
                foreign_sources = (await cur.fetchone())[0]

                # Tasks from unknown goto sms + gmail sender — plain language.
                await cur.execute(
                    "select title, description from public.tasks where created_at >= %s",
                    (start_ts,),
                )
                task_rows = await cur.fetchall()
        return out, wh_lead_name, receipts, foreign_sources, task_rows
    finally:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.external_ids where external_id in (%s,%s,%s,%s)",
                (GOTO_CALLER, WELLSKY_PATIENT, GCAL_EVENT, WELCOMEHOME_PROSPECT),
            )
            await conn.execute("delete from public.leads where name='Beatrice Coleman'")
            await conn.execute(
                "delete from public.tasks where title like 'Review: SMS received from%' "
                "or title like 'Review: Email from%'"
            )
        await db.close_pool()


def test_all_adapters_end_to_end():
    conftest._require("NEXUS_APP_DB_URL")
    out, wh_lead_name, receipts, foreign_sources, task_rows = asyncio.run(_scenario())

    # welcomehome lead.created → auto-create.
    assert out["welcomehome"][0] == 200
    assert out["welcomehome"][1] == {"received": 1, "matched": 0, "created": 1, "tasks": 0}
    assert wh_lead_name == "Beatrice Coleman"

    # goto call.completed from a seeded phone number → matched.
    assert out["goto_call"][1]["matched"] == 1
    # goto sms.received from an unknown number → review task, no business write.
    assert out["goto_sms"][1] == {"received": 1, "matched": 0, "created": 0, "tasks": 1}

    # wellsky Patient for a seeded EHR id → matched to the client.
    assert out["wellsky"][1]["matched"] == 1

    # gmail Pub/Sub envelope (base64 round-trip) → email.received; unknown → task.
    assert out["gmail"][1] == {"received": 1, "matched": 0, "created": 0, "tasks": 1}

    # gcal exists → matched to the seeded schedule; sync → ack-only.
    assert out["gcal_exists"][1]["matched"] == 1
    assert out["gcal_sync"][1] == {"status": "ack"}

    # Cross-cutting.
    assert receipts == 7  # one webhook.received per POST
    assert foreign_sources == 0  # every event tagged with a connector name
    assert len(task_rows) == 2  # goto sms + gmail
    for title, description in task_rows:
        assert "{" not in title and "{" not in (description or "")  # plain language
