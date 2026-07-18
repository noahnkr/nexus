"""Sequence supersede on stage advance (WS1 / issue #1), gated on NEXUS_APP_DB_URL.

When a lead advances a stage, the prior stage's in-flight sequence run must be
cancelled so the lead can't receive a colder stage's message after moving on. Two
cases: a run parked on a `delay` (cancelled directly) and a run parked at
`waiting_approval` (its pending action rejected through the approvals seam, never
orphaned). Drives the real engine via `dispatch_once` (no browser).

Sequences bind to the real view "leads" (the PATCH route hardcodes that view), so
the scenario clears any pre-existing leads-bound sequences for the stages it uses,
then cleans up after — idempotent against reruns.
"""
import asyncio
import uuid

import httpx
import pytest
from psycopg.rows import dict_row

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

_CURSOR_KEY = "_automations"


async def _delete_automation_full(conn, automation_id):
    """FK-safe delete: purge a run's pending_actions (+ tasks) before deleting the
    automation, since pending_actions.automation_run_id doesn't cascade."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, task_id from public.pending_actions where automation_run_id in "
            "(select id from public.automation_runs where automation_id=%s)",
            (automation_id,),
        )
        for r in await cur.fetchall():
            await conn.execute("delete from public.pending_actions where id=%s", (r["id"],))
            if r["task_id"]:
                await conn.execute("delete from public.tasks where id=%s", (r["task_id"],))
    await conn.execute("delete from public.automations where id=%s", (automation_id,))


async def _clear_leads_sequences(conn, stages):
    """Remove any pre-existing leads-bound sequences for these stages (FK-safe), so
    the test's own creates don't hit the one-per-stage unique index, and a prior
    failed run can't leave active sequences that pollute other tests."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id from public.automations "
            "where binding->>'view'='leads' and binding->>'stage' = any(%s)",
            (stages,),
        )
        ids = [str(r["id"]) for r in await cur.fetchall()]
    for aid in ids:
        await _delete_automation_full(conn, aid)


async def _seq_body(name, stage, steps):
    return {
        "name": name,
        "trigger": {"type": "event", "event_type": "lead.stage_changed"},
        "conditions": [{"field": "trigger.payload.to", "op": "eq", "value": stage}],
        "steps": steps,
        "binding": {"view": "leads", "stage": stage},
    }


async def _run_for(conn, automation_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, status from public.automation_runs where automation_id=%s "
            "order by created_at desc limit 1",
            (automation_id,),
        )
        return await cur.fetchone()


async def _scenario():
    from app import db
    from app.main import app
    from app.services.automations.scheduler import dispatch_once

    token = uuid.uuid4().hex[:8]
    out = {"automations": [], "leads": []}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await _clear_leads_sequences(conn, ["contacted", "qualified"])

            # contacted sequence parks on a delay; qualified completes fast.
            contacted = (await ac.post("/api/automations", json=await _seq_body(
                f"contacted {token}", "contacted",
                [{"type": "delay", "minutes": 60},
                 {"type": "function", "function": "now", "save_as": "ts"}],
            ))).json()
            qualified = (await ac.post("/api/automations", json=await _seq_body(
                f"qualified {token}", "qualified",
                [{"type": "function", "function": "now", "save_as": "ts"}],
            ))).json()
            out["automations"] += [contacted["id"], qualified["id"]]
            for aid in (contacted["id"], qualified["id"]):
                await ac.patch(f"/api/automations/{aid}", json={"status": "active"})

            lead = (await ac.post("/api/leads", json={"name": f"Supersede {token}"})).json()
            lead_id = lead["id"]
            out["leads"].append(lead_id)

            # prime the dispatch cursor to the current tip
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await conn.execute(
                    "delete from public.connector_state where source_system=%s", (_CURSOR_KEY,)
                )
            await dispatch_once(DEMO_TENANT)

            # --- move to contacted: sequence starts and parks on the delay ---
            await ac.patch(f"/api/leads/{lead_id}", json={"status": "contacted"})
            await dispatch_once(DEMO_TENANT)
            async with db.tenant_tx(DEMO_TENANT) as conn:
                out["contacted_parked"] = (await _run_for(conn, contacted["id"]))["status"]

            # --- advance to qualified: the contacted run is superseded ---
            await ac.patch(f"/api/leads/{lead_id}", json={"status": "qualified"})
            async with db.tenant_tx(DEMO_TENANT) as conn:
                out["contacted_after"] = (await _run_for(conn, contacted["id"]))["status"]
            await dispatch_once(DEMO_TENANT)
            async with db.tenant_tx(DEMO_TENANT) as conn:
                qrun = await _run_for(conn, qualified["id"])
                out["qualified_started"] = qrun is not None

            # === gated variant: waiting_approval run is rejected, not orphaned ===
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await _clear_leads_sequences(conn, ["contacted"])
            gated = (await ac.post("/api/automations", json=await _seq_body(
                f"contacted-gated {token}", "contacted",
                [{"type": "tool", "tool": "send_sms",
                  "input": {"to": "+16195550100", "body": "hi"}, "save_as": "sent"}],
            ))).json()
            out["automations"].append(gated["id"])
            await ac.patch(f"/api/automations/{gated['id']}", json={"status": "active"})

            lead2 = (await ac.post("/api/leads", json={"name": f"Supersede2 {token}"})).json()
            lead2_id = lead2["id"]
            out["leads"].append(lead2_id)

            await ac.patch(f"/api/leads/{lead2_id}", json={"status": "contacted"})
            await dispatch_once(DEMO_TENANT)
            async with db.tenant_tx(DEMO_TENANT) as conn:
                grun = await _run_for(conn, gated["id"])
                out["gated_parked"] = grun["status"]
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select id from public.pending_actions where automation_run_id=%s",
                        (grun["id"],),
                    )
                    action_id = str((await cur.fetchone())["id"])

            # advance to qualified: supersede rejects the pending action + cancels run
            await ac.patch(f"/api/leads/{lead2_id}", json={"status": "qualified"})
            async with db.tenant_tx(DEMO_TENANT) as conn:
                out["gated_after"] = (await _run_for(conn, gated["id"]))["status"]
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select status from public.pending_actions where id=%s", (action_id,)
                    )
                    out["action_after"] = (await cur.fetchone())["status"]

            # cleanup
            async with db.tenant_tx(DEMO_TENANT) as conn:
                for aid in out["automations"]:
                    await _delete_automation_full(conn, aid)
                for lid in out["leads"]:
                    await conn.execute("delete from public.leads where id=%s", (lid,))
        return out
    finally:
        await db.close_pool()


def test_sequence_supersede():
    out = asyncio.run(_scenario())

    # delay case: contacted parked, then cancelled when the lead advanced
    assert out["contacted_parked"] == "waiting"
    assert out["contacted_after"] == "cancelled"
    assert out["qualified_started"]  # the new stage's sequence started fresh

    # gated case: parked for approval, then the action rejected + run cancelled
    assert out["gated_parked"] == "waiting_approval"
    assert out["gated_after"] == "cancelled"
    assert out["action_after"] == "rejected"  # approval resolved, not orphaned
