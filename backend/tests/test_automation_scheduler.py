"""Automations engine loops (Module 7b) — dispatcher, cron, waker, recovery, and
the approval resume/cancel hook. Gated on NEXUS_APP_DB_URL.

Every loop phase exposes a synchronous `*_once()` tick; the tests drive those
directly (the `while True` wrapper is trivial and untested). The dispatcher shares
a single durable cursor row (`connector_state._automations`), so its scenario resets
the cursor to the current tip before logging its own events — deterministic against
pre-existing history.
"""
import asyncio
import uuid

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

_CURSOR_KEY = "_automations"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _register_tool(name, handler, *, safe=True, describe=None):
    from app.services.tools import ToolDef
    from app.services.tools.registry import register

    register(ToolDef(
        name=name, description="throwaway test tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler, safe=safe, gate_describe=describe,
    ))


def _unregister(*names):
    from app.services.tools.registry import _REGISTRY

    for n in names:
        _REGISTRY.pop(n, None)


async def _insert_automation(conn, recipe, *, name="sched-test", status="active"):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.automations
                 (tenant_id, name, status, trigger, conditions, steps, next_fire_at)
               values (%s, %s, %s, %s, %s, %s, %s)
               returning id, name, trigger, conditions, steps, status""",
            (DEMO_TENANT, name, status, Json(recipe["trigger"]),
             Json(recipe.get("conditions", [])), Json(recipe.get("steps", [])),
             recipe.get("next_fire_at")),
        )
        return await cur.fetchone()


async def _run(conn, run_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.automation_runs where id=%s", (run_id,))
        return await cur.fetchone()


async def _cleanup(conn, automation_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, task_id from public.pending_actions where automation_run_id in "
            "(select id from public.automation_runs where automation_id=%s)",
            (automation_id,),
        )
        for row in await cur.fetchall():
            await conn.execute("delete from public.pending_actions where id=%s", (row["id"],))
            if row["task_id"]:
                await conn.execute("delete from public.tasks where id=%s", (row["task_id"],))
    await conn.execute(
        "delete from public.tasks where originating_event_id in "
        "(select id from public.events where source_system='automation' "
        " and payload->>'automation_id' = %s)",
        (str(automation_id),),
    )
    await conn.execute("delete from public.automations where id=%s", (automation_id,))


# ---------------------------------------------------------------------------
# Task 1 — run_cycle executes clean
# ---------------------------------------------------------------------------
async def _cycle_scenario():
    from app import db
    from app.services.automations.scheduler import run_cycle

    await db.open_pool()
    try:
        return await run_cycle()
    finally:
        await db.close_pool()


def test_cycle_runs_clean():
    counts = asyncio.run(_cycle_scenario())
    assert set(counts) == {"dispatched", "cron", "woken", "recovered"}
    assert all(isinstance(v, int) for v in counts.values())


# ---------------------------------------------------------------------------
# Task 2 — event dispatcher + durable cursor + loop guard + no history replay
# ---------------------------------------------------------------------------
async def _dispatch_scenario():
    from app import db
    from app.services.automations.scheduler import dispatch_once
    from app.services.events import log_event
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_disp_{sfx}"
    et = f"disp.event.{sfx}"  # unique event_type so only our events match

    async def echo(conn, args):
        return ToolResult("dispatched", {"name": args.get("name")})

    _register_tool(tool, echo, safe=True)
    recipe = {
        "trigger": {"type": "event", "event_type": et},
        "steps": [{"type": "tool", "tool": tool,
                   "input": {"name": "{{trigger.payload.name}}"}, "save_as": "echo"}],
    }
    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            # reset the cursor row so first-run init starts from the current tip
            await conn.execute(
                "delete from public.connector_state where source_system=%s", (_CURSOR_KEY,)
            )
            # a pre-existing matching event BEFORE the cursor is initialized
            await log_event(conn, tenant_id=DEMO_TENANT, source_system="welcomehome",
                            event_type=et, payload={"name": "PRE"})

        # first-run init: sets cursor to tip, processes nothing (no replay)
        out["first_run"] = await dispatch_once(DEMO_TENANT)

        # a new matching event AFTER init -> processed
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await log_event(conn, tenant_id=DEMO_TENANT, source_system="welcomehome",
                            event_type=et, payload={"name": "Margaret"})
        out["match"] = await dispatch_once(DEMO_TENANT)
        out["match_again"] = await dispatch_once(DEMO_TENANT)  # cursor advanced -> 0

        # loop guard: automation-sourced matching event is ignored
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await log_event(conn, tenant_id=DEMO_TENANT, source_system="automation",
                            event_type=et, payload={"name": "loop"})
        out["loop_guard"] = await dispatch_once(DEMO_TENANT)

        # non-matching event type starts nothing
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await log_event(conn, tenant_id=DEMO_TENANT, source_system="welcomehome",
                            event_type=f"other.{sfx}", payload={})
        out["non_match"] = await dispatch_once(DEMO_TENANT)

        # inspect the runs that were created + the persisted cursor
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select status, context from public.automation_runs "
                    "where automation_id=%s order by created_at",
                    (automation["id"],),
                )
                out["runs"] = await cur.fetchall()
                await cur.execute(
                    "select state from public.connector_state where source_system=%s",
                    (_CURSOR_KEY,),
                )
                out["cursor"] = (await cur.fetchone())["state"]
            await _cleanup(conn, automation["id"])
        return out
    finally:
        _unregister(tool)
        await db.close_pool()


def test_dispatcher():
    out = asyncio.run(_dispatch_scenario())
    assert out["first_run"] == 0  # no history replay
    assert out["match"] == 1  # the post-init matching event started one run
    assert out["match_again"] == 0  # cursor advanced
    assert out["loop_guard"] == 0  # automation-sourced event ignored
    assert out["non_match"] == 0  # different event_type

    # exactly one run, completed, trigger.* resolved from the event payload
    assert len(out["runs"]) == 1
    assert out["runs"][0]["status"] == "completed"
    assert out["runs"][0]["context"]["echo"] == {"name": "Margaret"}
    assert out["cursor"]["last_event_id"]  # cursor persisted in connector_state


# ---------------------------------------------------------------------------
# Task 3 — cron triggers
# ---------------------------------------------------------------------------
async def _cron_scenario():
    from app import db
    from app.services.automations.scheduler import tick_cron_once

    recipe = {"trigger": {"type": "cron", "expression": "* * * * *"},
              "steps": [{"type": "function", "function": "now", "save_as": "ts"}]}
    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            # arm it in the past so it's due now
            await conn.execute(
                "update public.automations set next_fire_at = now() - interval '1 minute' "
                "where id=%s",
                (automation["id"],),
            )
        out["first_tick"] = await tick_cron_once(DEMO_TENANT)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select next_fire_at, status from public.automations where id=%s",
                    (automation["id"],),
                )
                after = await cur.fetchone()
                await cur.execute(
                    "select count(*) as n from public.automation_runs where automation_id=%s",
                    (automation["id"],),
                )
                out["run_count_1"] = (await cur.fetchone())["n"]
            out["next_fire_future"] = after["next_fire_at"]
        # immediate second tick -> nothing (next_fire_at now future)
        out["second_tick"] = await tick_cron_once(DEMO_TENANT)

        # pausing stops firing (force due again, but paused)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "update public.automations set status='paused', "
                "next_fire_at = now() - interval '1 minute' where id=%s",
                (automation["id"],),
            )
        out["paused_tick"] = await tick_cron_once(DEMO_TENANT)

        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select count(*) as n from public.automation_runs where automation_id=%s",
                    (automation["id"],),
                )
                out["run_count_2"] = (await cur.fetchone())["n"]
            await _cleanup(conn, automation["id"])
        return out
    finally:
        await db.close_pool()


def test_cron_fires_once_then_reschedules():
    import datetime as dt

    out = asyncio.run(_cron_scenario())
    assert out["first_tick"] == 1  # exactly one run
    assert out["run_count_1"] == 1
    assert out["next_fire_future"] > dt.datetime.now(dt.timezone.utc)  # advanced to the future
    assert out["second_tick"] == 0  # not due again
    assert out["paused_tick"] == 0  # paused stops firing
    assert out["run_count_2"] == 1  # no extra run while paused


async def _cron_patch_recompute_scenario():
    """PATCH recomputes next_fire_at on (re)activation + expression change."""
    import httpx

    from app import db
    from app.main import app
    from conftest import bearer_headers

    recipe = {"name": "cron-patch", "trigger": {"type": "cron", "expression": "0 9 * * 1"},
              "steps": [{"type": "function", "function": "now", "save_as": "ts"}]}
    out = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            h = bearer_headers(DEMO_TENANT)
            created = (await ac.post("/api/automations", headers=h, json=recipe)).json()
            aid = created["id"]
            out["created_next_fire"] = created["next_fire_at"]  # paused -> null
            activated = (await ac.patch(f"/api/automations/{aid}", headers=h,
                                        json={"status": "active"})).json()
            out["activated_next_fire"] = activated["next_fire_at"]  # armed
            changed = (await ac.patch(f"/api/automations/{aid}", headers=h,
                                      json={"trigger": {"type": "cron", "expression": "* * * * *"}})).json()
            out["changed_next_fire"] = changed["next_fire_at"]
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute("delete from public.automations where id=%s", (aid,))
        return out
    finally:
        await db.close_pool()


def test_cron_patch_recomputes_next_fire():
    out = asyncio.run(_cron_patch_recompute_scenario())
    assert out["created_next_fire"] is None  # created paused, unarmed
    assert out["activated_next_fire"] is not None  # armed on activation
    # a "* * * * *" schedule fires within a minute — sooner than the weekly "0 9 * * 1"
    assert out["changed_next_fire"] is not None
    assert out["changed_next_fire"] < out["activated_next_fire"]


# ---------------------------------------------------------------------------
# Task 4 — waker + recovery sweep
# ---------------------------------------------------------------------------
async def _waker_scenario():
    from app import db
    from app.services.automations import advance_run, start_run
    from app.services.automations.scheduler import wake_due_once
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_wake_{sfx}"

    async def post(conn, args):
        return ToolResult("post", {"done": True})

    _register_tool(tool, post, safe=True)
    recipe = {"trigger": {"type": "manual"},
              "steps": [{"type": "delay", "days": 1},
                        {"type": "tool", "tool": tool, "input": {}, "save_as": "post"}]}
    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)  # parks 'waiting'
        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["parked"] = (await _run(conn, run_id))["status"]
            await conn.execute(
                "update public.automation_runs set wake_at = now() - interval '1 minute' "
                "where id=%s",
                (run_id,),
            )
        out["woken"] = await wake_due_once(DEMO_TENANT)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            done = await _run(conn, run_id)
            out["status"] = done["status"]
            out["context"] = done["context"]
            await _cleanup(conn, automation["id"])
        return out
    finally:
        _unregister(tool)
        await db.close_pool()


def test_waker_completes_due_run():
    out = asyncio.run(_waker_scenario())
    assert out["parked"] == "waiting"
    assert out["woken"] >= 1
    assert out["status"] == "completed"
    assert out["context"]["post"] == {"done": True}  # remaining step ran exactly once


async def _recovery_scenario():
    from app import db
    from app.services.automations.scheduler import recover_stale_once
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_rec_{sfx}"

    async def step(conn, args):
        return ToolResult("recovered", {"ok": True})

    _register_tool(tool, step, safe=True)
    recipe = {"trigger": {"type": "manual"},
              "steps": [{"type": "tool", "tool": tool, "input": {}, "save_as": "r"}]}
    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            # INSERT (not UPDATE) so the set_updated_at trigger doesn't overwrite the
            # stale timestamp — simulates a process that died mid-advance.
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """insert into public.automation_runs
                         (tenant_id, automation_id, status, step_index, updated_at)
                       values (%s, %s, 'running', 0, now() - interval '30 minutes')
                       returning id""",
                    (DEMO_TENANT, automation["id"]),
                )
                stale_id = str((await cur.fetchone())["id"])
                await cur.execute(
                    """insert into public.automation_runs
                         (tenant_id, automation_id, status, step_index)
                       values (%s, %s, 'running', 0)
                       returning id""",
                    (DEMO_TENANT, automation["id"]),
                )
                fresh_id = str((await cur.fetchone())["id"])
        out["recovered"] = await recover_stale_once(DEMO_TENANT)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["stale"] = await _run(conn, stale_id)
            out["fresh"] = await _run(conn, fresh_id)
            await conn.execute("delete from public.automation_runs where automation_id=%s",
                               (automation["id"],))
            await _cleanup(conn, automation["id"])
        return out
    finally:
        _unregister(tool)
        await db.close_pool()


def test_recovery_finishes_stale_run():
    out = asyncio.run(_recovery_scenario())
    assert out["recovered"] >= 1
    assert out["stale"]["status"] == "completed"  # re-advanced to completion
    assert out["fresh"]["status"] == "running"  # fresh run left alone
    assert out["fresh"]["step_index"] == 0


# ---------------------------------------------------------------------------
# Task 5 — approval resume / cancel
# ---------------------------------------------------------------------------
async def _resume_scenario():
    from app import db
    from app.services.approvals import approve_action
    from app.services.automations import advance_run, start_run

    # recipe: gated send_sms (real placeholder), then a safe function step
    recipe = {"trigger": {"type": "manual"},
              "steps": [
                  {"type": "tool", "tool": "send_sms",
                   "input": {"to": "+16195550100", "body": "hi"}, "save_as": "sent"},
                  {"type": "function", "function": "now", "save_as": "after"},
              ]}
    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)  # parks waiting_approval
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await _run(conn, run_id)
            out["parked"] = run["status"]
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select id, task_id from public.pending_actions where automation_run_id=%s",
                    (run_id,),
                )
                action = await cur.fetchone()
            action_id = str(action["id"])
            task_id = str(action["task_id"])
        # approve -> executes send_sms AND resumes the run in the same call
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await approve_action(conn, DEMO_TENANT, action_id, resolved_by="tester")
        async with db.tenant_tx(DEMO_TENANT) as conn:
            done = await _run(conn, run_id)
            out["status"] = done["status"]
            out["context"] = done["context"]
            await _cleanup(conn, automation["id"])
        return out
    finally:
        await db.close_pool()


def test_approval_resumes_run():
    out = asyncio.run(_resume_scenario())
    assert out["parked"] == "waiting_approval"
    assert out["status"] == "completed"
    assert out["context"]["sent"]["placeholder"] is True  # send result saved under save_as
    assert "after" in out["context"]  # the safe step after the gate ran


async def _reject_scenario():
    from app import db
    from app.services.approvals import reject_action
    from app.services.automations import advance_run, start_run
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_after_rej_{sfx}"
    state = {"ran": 0}

    async def after(conn, args):
        state["ran"] += 1
        return ToolResult("ran", {})

    _register_tool(tool, after, safe=True)
    recipe = {"trigger": {"type": "manual"},
              "steps": [
                  {"type": "tool", "tool": "send_sms",
                   "input": {"to": "+16195550100", "body": "hi"}, "save_as": "sent"},
                  {"type": "tool", "tool": tool, "input": {}},
              ]}
    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select id from public.pending_actions where automation_run_id=%s", (run_id,)
                )
                action_id = str((await cur.fetchone())["id"])
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await reject_action(conn, DEMO_TENANT, action_id, resolved_by="tester", note="no")
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await _run(conn, run_id)
            out["status"] = run["status"]
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select event_type, payload from public.events where payload->>'run_id'=%s",
                    (str(run_id),),
                )
                out["events"] = await cur.fetchall()
            await _cleanup(conn, automation["id"])
        out["ran"] = state["ran"]
        return out
    finally:
        _unregister(tool)
        await db.close_pool()


def test_rejection_cancels_run():
    out = asyncio.run(_reject_scenario())
    assert out["status"] == "cancelled"
    assert out["ran"] == 0  # the step after the gate never ran
    assert any(e["event_type"] == "automation.run_cancelled" for e in out["events"])


async def _approve_fail_scenario():
    from app import db
    from app.services.approvals import approve_action
    from app.services.automations import advance_run, start_run

    sfx = uuid.uuid4().hex[:8]
    gated = f"t_gfail_{sfx}"

    async def boom(conn, args):
        raise RuntimeError("post-approval boom")

    _register_tool(gated, boom, safe=False, describe=lambda c, a: "Do the risky thing")
    recipe = {"trigger": {"type": "manual"},
              "steps": [{"type": "tool", "tool": gated, "input": {}, "save_as": "x"}]}
    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select id, task_id from public.pending_actions where automation_run_id=%s",
                    (run_id,),
                )
                action = await cur.fetchone()
            action_id = str(action["id"])
            gate_task_id = str(action["task_id"])
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await approve_action(conn, DEMO_TENANT, action_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await _run(conn, run_id)
            out["run_status"] = run["status"]
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("select status from public.tasks where id=%s", (gate_task_id,))
                out["gate_task_status"] = (await cur.fetchone())["status"]
                # no SECOND review task from the fail path (none links the run_failed event)
                await cur.execute(
                    "select count(*) as n from public.tasks where originating_event_id in "
                    "(select id from public.events where payload->>'run_id'=%s "
                    " and event_type='automation.run_failed')",
                    (str(run_id),),
                )
                out["extra_review_tasks"] = (await cur.fetchone())["n"]
                await cur.execute("select status from public.pending_actions where id=%s",
                                  (action_id,))
                out["action_status"] = (await cur.fetchone())["status"]
            # cleanup (gate task + action)
            await conn.execute("delete from public.pending_actions where id=%s", (action_id,))
            await conn.execute("delete from public.tasks where id=%s", (gate_task_id,))
            await conn.execute("delete from public.automation_runs where automation_id=%s",
                               (automation["id"],))
            await conn.execute("delete from public.automations where id=%s", (automation["id"],))
        return out
    finally:
        _unregister(gated)
        await db.close_pool()


def test_post_approval_failure_no_second_task():
    out = asyncio.run(_approve_fail_scenario())
    assert out["run_status"] == "failed"
    assert out["action_status"] == "failed"
    assert out["gate_task_status"] == "pending"  # Module 5: failed action's task stays pending
    assert out["extra_review_tasks"] == 0  # no duplicate human surface
