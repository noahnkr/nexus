"""Leads per-stage sequence — end-to-end walk (Module 9b, Task 5), gated on
NEXUS_APP_DB_URL.

Drives the whole 9b path through the REAL M7 engine (no browser): a bound sequence
created via the standard API fires when a lead moves stage, its gated step parks the
run for approval, approving completes it, and the run is entity-linked to the lead.
This is the automated stand-in for the plan's live walk — the dispatcher's
synchronous `dispatch_once()` tick is driven directly (the loop wrapper is trivial).

Mirrors test_automation_scheduler's cursor discipline: the dispatcher shares one
durable cursor (`connector_state._automations`), so the scenario resets it to the
current tip after seeding its automation + lead, then logs the triggering stage
move so only that event is processed.
"""
import asyncio
import uuid

import httpx
import pytest
from psycopg.rows import dict_row

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

_CURSOR_KEY = "_automations"


async def _scenario():
    from app import db
    from app.main import app
    from app.services.approvals import approve_action
    from app.services.automations.scheduler import dispatch_once

    token = uuid.uuid4().hex[:8]
    out = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # --- a bound "Contacted" sequence, built the builder's way: trigger on
            # lead.stage_changed + the managed to=contacted condition, one gated step.
            seq = await ac.post("/api/automations", json={
                "name": f"Leads · Contacted sequence {token}",
                "trigger": {"type": "event", "event_type": "lead.stage_changed"},
                "conditions": [{"field": "trigger.payload.to", "op": "eq", "value": "contacted"}],
                "steps": [{
                    "type": "tool", "tool": "send_sms",
                    "input": {"to": "+16195550100", "body": "Thanks for your interest!"},
                    "save_as": "sent",
                }],
                "binding": {"view": "leads", "stage": "contacted"},
            })
            assert seq.status_code == 201, seq.text
            automation_id = seq.json()["id"]
            await ac.patch(f"/api/automations/{automation_id}", json={"status": "active"})

            # --- a fresh lead to work ---
            lead = await ac.post("/api/leads", json={"name": f"Walk Lead {token}"})
            lead_id = lead.json()["id"]

            # --- prime the dispatch cursor to the current tip (process no history) ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await conn.execute(
                    "delete from public.connector_state where source_system=%s", (_CURSOR_KEY,)
                )
            out["init"] = await dispatch_once(DEMO_TENANT)  # 0 — first-run init

            # --- move the lead to Contacted: emits lead.stage_changed (source=user) ---
            await ac.patch(f"/api/leads/{lead_id}", json={"status": "contacted"})

            # --- dispatcher fires the bound sequence; the gated step parks the run ---
            out["dispatched"] = await dispatch_once(DEMO_TENANT)

            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select id, status, entity_type, entity_id from public.automation_runs "
                        "where automation_id=%s order by created_at desc limit 1",
                        (automation_id,),
                    )
                    run = await cur.fetchone()
                    out["parked_status"] = run["status"]
                    out["run_entity_type"] = run["entity_type"]
                    out["run_entity_matches"] = str(run["entity_id"]) == lead_id
                    run_id = str(run["id"])
                    await cur.execute(
                        "select id from public.pending_actions where automation_run_id=%s",
                        (run_id,),
                    )
                    action_id = str((await cur.fetchone())["id"])

            # --- approve -> executes send_sms and resumes the run to completion ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await approve_action(conn, DEMO_TENANT, action_id, resolved_by="tester")

            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select status from public.automation_runs where id=%s", (run_id,)
                    )
                    out["final_status"] = (await cur.fetchone())["status"]
                    # the run's tool call is audited as automation-sourced
                    await cur.execute(
                        "select count(*) as n from public.events "
                        "where event_type='tool.called' and source_system='automation' "
                        "and payload->>'tool_name'='send_sms' "
                        "and payload->>'pending_action_id'=%s",
                        (action_id,),
                    )
                    out["tool_called"] = (await cur.fetchone())["n"]
                    # the lead's own timeline carries the stage move
                    await cur.execute(
                        "select count(*) as n from public.events "
                        "where entity_type='lead' and entity_id=%s "
                        "and event_type='lead.stage_changed'",
                        (lead_id,),
                    )
                    out["stage_events"] = (await cur.fetchone())["n"]

            # --- cleanup: actions/tasks for the run, the automation (cascades runs), lead ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select id, task_id from public.pending_actions "
                        "where automation_run_id in (select id from public.automation_runs "
                        "where automation_id=%s)",
                        (automation_id,),
                    )
                    for r in await cur.fetchall():
                        await conn.execute("delete from public.pending_actions where id=%s", (r["id"],))
                        if r["task_id"]:
                            await conn.execute("delete from public.tasks where id=%s", (r["task_id"],))
                await conn.execute("delete from public.automations where id=%s", (automation_id,))
                await conn.execute("delete from public.leads where id=%s", (lead_id,))
        return out
    finally:
        await db.close_pool()


def test_leads_sequence_walk():
    out = asyncio.run(_scenario())

    assert out["init"] == 0  # cursor primed, no history replayed
    assert out["dispatched"] >= 1  # the stage move fired the bound sequence

    # the gated send_sms parked the run for approval, entity-linked to the lead
    assert out["parked_status"] == "waiting_approval"
    assert out["run_entity_type"] == "lead"
    assert out["run_entity_matches"]

    # approving completed the run and audited the send_sms as automation-sourced
    assert out["final_status"] == "completed"
    assert out["tool_called"] == 1
    assert out["stage_events"] >= 1
