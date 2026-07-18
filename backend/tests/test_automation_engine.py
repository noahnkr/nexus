"""Engine core — start_run / advance_run step semantics (Module 7a, Task 3).

Gated on NEXUS_APP_DB_URL. Registers throwaway tools inside the test (the
test_approval_gate trick) so step behavior is proven independently of the real
tool set. Each scenario inserts its own `automations` row, drives the engine, then
cleans up its runs/tasks/actions/automation; events are immutable so assertions
scope by the automation_id / run_id embedded in the event payload.
"""
import asyncio
import uuid

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


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


async def _insert_automation(conn, recipe, *, name="engine-test", status="active"):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.automations
                 (tenant_id, name, status, trigger, conditions, steps)
               values (%s, %s, %s, %s, %s, %s)
               returning id, name, trigger, conditions, steps, status""",
            (DEMO_TENANT, name, status, Json(recipe["trigger"]),
             Json(recipe.get("conditions", [])), Json(recipe.get("steps", []))),
        )
        return await cur.fetchone()


async def _make_event(conn, event_type, payload, *, source="welcomehome",
                      entity_type=None, entity_id=None):
    from app.services.events import log_event

    eid = await log_event(
        conn, tenant_id=DEMO_TENANT, source_system=source, event_type=event_type,
        entity_type=entity_type, entity_id=entity_id, payload=payload,
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, event_type, source_system, entity_type, entity_id, payload, "
            "created_at from public.events where id=%s",
            (eid,),
        )
        return await cur.fetchone()


async def _events_for_run(conn, run_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select event_type, source_system, payload from public.events "
            "where payload->>'run_id' = %s order by created_at",
            (str(run_id),),
        )
        return await cur.fetchall()


async def _cleanup(conn, automation_id):
    # pending_actions -> its tasks -> runs (cascade via automation delete last).
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
    # review tasks created by the fail path link via originating_event_id -> run_failed
    await conn.execute(
        "delete from public.tasks where originating_event_id in "
        "(select id from public.events where source_system='automation' "
        " and payload->>'automation_id' = %s)",
        (str(automation_id),),
    )
    await conn.execute("delete from public.automations where id=%s", (automation_id,))


# ---------------------------------------------------------------------------
# 1. linear run: tool (safe, save_as) -> function -> condition (true) -> complete
# ---------------------------------------------------------------------------
async def _linear_scenario():
    from app import db
    from app.services.automations import advance_run, get_run, start_run
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_echo_{sfx}"

    async def echo(conn, args):
        return ToolResult("echoed input", {"got": args.get("greeting")})

    _register_tool(tool, echo, safe=True)
    recipe = {
        "trigger": {"type": "event", "event_type": "lead.created"},
        "steps": [
            {"type": "tool", "tool": tool,
             "input": {"greeting": "Hi {{trigger.payload.name}}"}, "save_as": "echo"},
            {"type": "function", "function": "now", "save_as": "ts"},
            {"type": "condition", "conditions": [{"field": "context.ts", "op": "exists"}]},
        ],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            ev = await _make_event(conn, "lead.created", {"name": "Margaret"})
            run_id = await start_run(conn, DEMO_TENANT, automation, trigger_event=ev)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await get_run(conn, run_id)
            events = await _events_for_run(conn, run_id)
            # tool.called rows are generic audit rows (no run_id in payload); fetch by name.
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select event_type, source_system, payload from public.events "
                    "where payload->>'tool_name' = %s order by created_at",
                    (tool,),
                )
                tool_events = await cur.fetchall()
            await _cleanup(conn, automation["id"])
        return run, events, tool_events
    finally:
        _unregister(tool)
        await db.close_pool()


def test_linear_run_completes():
    run, events, tool_events = asyncio.run(_linear_scenario())

    assert run["status"] == "completed"
    assert run["finished_at"] is not None
    # context accumulated both outputs
    assert run["context"]["echo"] == {"got": "Hi Margaret"}
    assert "ts" in run["context"]
    # one step_log entry per step, all ok
    assert [e["status"] for e in run["step_log"]] == ["ok", "ok", "ok"]
    assert [e["type"] for e in run["step_log"]] == ["tool", "function", "condition"]

    types = [e["event_type"] for e in events]
    assert types[0] == "automation.run_started"
    assert "automation.run_completed" in types
    called = [e for e in tool_events if e["event_type"] == "tool.called"]
    assert len(called) == 1 and called[0]["source_system"] == "automation"


# ---------------------------------------------------------------------------
# 2. condition-false mid-sequence -> completed early, later steps never ran
# ---------------------------------------------------------------------------
async def _early_stop_scenario():
    from app import db
    from app.services.automations import advance_run, get_run, start_run
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_after_{sfx}"
    state = {"ran": 0}

    async def after(conn, args):
        state["ran"] += 1
        return ToolResult("ran", {})

    _register_tool(tool, after, safe=True)
    recipe = {
        "trigger": {"type": "manual"},
        "steps": [
            {"type": "condition", "conditions": [{"field": "context.missing", "op": "exists"}]},
            {"type": "tool", "tool": tool, "input": {}},
        ],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await get_run(conn, run_id)
            events = await _events_for_run(conn, run_id)
            await _cleanup(conn, automation["id"])
        return run, events, state
    finally:
        _unregister(tool)
        await db.close_pool()


def test_condition_false_stops_early():
    run, events, state = asyncio.run(_early_stop_scenario())
    assert run["status"] == "completed"
    assert state["ran"] == 0  # the tool after the failed condition never ran
    assert run["step_index"] == 0  # stopped at the condition step
    completed = [e for e in events if e["event_type"] == "automation.run_completed"]
    assert completed and "stopped early" in completed[0]["payload"]["summary"]


# ---------------------------------------------------------------------------
# 3. entry conditions false -> no run row, no events
# ---------------------------------------------------------------------------
async def _entry_filtered_scenario():
    from app import db
    from app.services.automations import start_run

    recipe = {
        "trigger": {"type": "event", "event_type": "lead.created"},
        "conditions": [{"field": "trigger.payload.status", "op": "eq", "value": "hot"}],
        "steps": [{"type": "function", "function": "now", "save_as": "ts"}],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            ev = await _make_event(conn, "lead.created", {"status": "cold"})
            run_id = await start_run(conn, DEMO_TENANT, automation, trigger_event=ev)
            # count runs + automation events
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.automation_runs where automation_id=%s",
                    (automation["id"],),
                )
                run_count = (await cur.fetchone())[0]
                await cur.execute(
                    "select count(*) from public.events where source_system='automation' "
                    "and payload->>'automation_id' = %s",
                    (str(automation["id"]),),
                )
                event_count = (await cur.fetchone())[0]
            await _cleanup(conn, automation["id"])
        return run_id, run_count, event_count
    finally:
        await db.close_pool()


def test_entry_conditions_filter_silently():
    run_id, run_count, event_count = asyncio.run(_entry_filtered_scenario())
    assert run_id is None
    assert run_count == 0
    assert event_count == 0  # condition-false at entry is normal filtering, not an event


# ---------------------------------------------------------------------------
# 3b. condition VALUES render templates (Module 11a): a templated value is
# compared as its resolved value; an unresolvable value -> condition FALSE
# (never a run failure). A plain literal is unchanged (regression).
# ---------------------------------------------------------------------------
MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"  # region North County


async def _templated_condition_scenario(payload):
    from app import db
    from app.services.automations import advance_run, get_run, start_run
    from app.services.tools import ToolResult

    tool = f"t_tpl_{uuid.uuid4().hex[:8]}"
    state = {"ran": 0}

    async def after(conn, args):
        state["ran"] += 1
        return ToolResult("ran", {})

    _register_tool(tool, after, safe=True)
    recipe = {
        "trigger": {"type": "event", "event_type": "lead.created"},
        "steps": [
            {"type": "condition", "conditions": [
                {"field": "entity.region_id", "op": "eq",
                 "value": "{{trigger.payload.region_id}}"}]},
            {"type": "tool", "tool": tool, "input": {}},
        ],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            ev = await _make_event(conn, "lead.created", payload,
                                   entity_type="lead", entity_id=MARGARET_LEAD)
            run_id = await start_run(conn, DEMO_TENANT, automation, trigger_event=ev)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await get_run(conn, run_id)
            await _cleanup(conn, automation["id"])
        return run, state
    finally:
        _unregister(tool)
        await db.close_pool()


def test_condition_value_template_matches():
    # {{trigger.payload.region_id}} resolves to North County == entity.region_id ->
    # condition true -> the tool after it runs, run completes.
    run, state = asyncio.run(_templated_condition_scenario(
        {"region_id": "11111111-0000-0000-0000-000000000001"}))
    assert run["status"] == "completed"
    assert state["ran"] == 1


def test_condition_value_template_unresolvable_is_false():
    # payload has no region_id -> the value can't resolve -> condition FALSE, the run
    # stops early (completed, NOT failed), and the tool never runs.
    run, state = asyncio.run(_templated_condition_scenario({}))
    assert run["status"] == "completed"
    assert state["ran"] == 0


async def _entry_context_value_scenario():
    from app import db
    from app.services.automations import start_run

    recipe = {
        "trigger": {"type": "event", "event_type": "lead.created"},
        # VALUE references context, which is empty at entry -> unresolvable -> false
        "conditions": [{"field": "trigger.payload.status", "op": "eq",
                        "value": "{{context.threshold}}"}],
        "steps": [{"type": "function", "function": "now", "save_as": "ts"}],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            ev = await _make_event(conn, "lead.created", {"status": "hot"})
            run_id = await start_run(conn, DEMO_TENANT, automation, trigger_event=ev)
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.automation_runs where automation_id=%s",
                    (automation["id"],),
                )
                run_count = (await cur.fetchone())[0]
            await _cleanup(conn, automation["id"])
        return run_id, run_count
    finally:
        await db.close_pool()


def test_entry_condition_context_value_skips_without_crash():
    # An entry condition whose VALUE references context (empty at entry) resolves
    # false and silently skips the automation — no dispatcher crash, no run row.
    run_id, run_count = asyncio.run(_entry_context_value_scenario())
    assert run_id is None
    assert run_count == 0


# ---------------------------------------------------------------------------
# 4. delay -> waiting + future wake_at; simulate wake -> completes
# ---------------------------------------------------------------------------
async def _delay_scenario():
    from app import db
    from app.services.automations import advance_run, get_run, start_run
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_post_{sfx}"

    async def post(conn, args):
        return ToolResult("post-delay", {"done": True})

    _register_tool(tool, post, safe=True)
    recipe = {
        "trigger": {"type": "manual"},
        "steps": [
            {"type": "delay", "days": 2},
            {"type": "tool", "tool": tool, "input": {}, "save_as": "post"},
        ],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            parked = await get_run(conn, run_id)
        # simulate the waker: force wake_at past + flip to running
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "update public.automation_runs set status='running', "
                "wake_at = now() - interval '1 minute' where id=%s",
                (run_id,),
            )
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            done = await get_run(conn, run_id)
            await _cleanup(conn, automation["id"])
        return parked, done
    finally:
        _unregister(tool)
        await db.close_pool()


def test_delay_parks_then_completes():
    parked, done = asyncio.run(_delay_scenario())
    assert parked["status"] == "waiting"
    assert parked["wake_at"] is not None
    assert parked["step_index"] == 1  # index bumped past the delay
    assert done["status"] == "completed"
    assert done["context"]["post"] == {"done": True}


# ---------------------------------------------------------------------------
# 5. gated tool -> waiting_approval, pending_actions carries automation_run_id
# ---------------------------------------------------------------------------
async def _gate_scenario():
    from app import db
    from app.services.automations import advance_run, get_run, start_run
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_gated_{sfx}"

    async def gated(conn, args):
        return ToolResult("should-not-run-yet", {})

    _register_tool(tool, gated, safe=False, describe=lambda c, a: "Do the gated thing")
    recipe = {
        "trigger": {"type": "manual"},
        "steps": [{"type": "tool", "tool": tool, "input": {}, "save_as": "res"}],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await get_run(conn, run_id)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select id, status, automation_run_id from public.pending_actions "
                    "where automation_run_id=%s",
                    (run_id,),
                )
                action = await cur.fetchone()
            await _cleanup(conn, automation["id"])
        return run, action
    finally:
        _unregister(tool)
        await db.close_pool()


def test_gated_tool_parks_waiting_approval():
    run, action = asyncio.run(_gate_scenario())
    assert run["status"] == "waiting_approval"
    assert run["step_index"] == 0  # not advanced past the gated step (resume bumps it)
    assert action is not None
    assert action["status"] == "pending"
    assert str(action["automation_run_id"]) == str(run["id"])


# ---------------------------------------------------------------------------
# 6. failing tool step -> run failed + run_failed event + linked review task
# ---------------------------------------------------------------------------
async def _fail_scenario():
    from app import db
    from app.services.automations import advance_run, get_run, start_run

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_boom_{sfx}"

    async def boom(conn, args):
        raise RuntimeError("kaboom")

    _register_tool(tool, boom, safe=True)
    recipe = {
        "trigger": {"type": "manual"},
        "steps": [{"type": "tool", "tool": tool, "input": {}}],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await get_run(conn, run_id)
            events = await _events_for_run(conn, run_id)
            failed_ev = [e for e in events if e["event_type"] == "automation.run_failed"]
            task = None
            if failed_ev:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select t.* from public.tasks t "
                        "where t.originating_event_id in "
                        "(select id from public.events where payload->>'run_id'=%s "
                        " and event_type='automation.run_failed')",
                        (str(run_id),),
                    )
                    task = await cur.fetchone()
            await _cleanup(conn, automation["id"])
        return run, events, task
    finally:
        _unregister(tool)
        await db.close_pool()


def test_failing_step_fails_run_with_task():
    run, events, task = asyncio.run(_fail_scenario())
    assert run["status"] == "failed"
    assert run["error"]
    assert [e for e in events if e["event_type"] == "automation.run_failed"]
    assert task is not None
    assert task["title"].startswith("Automation failed:")
    assert task["priority"] == "high"
    assert task["status"] == "pending"


# ---------------------------------------------------------------------------
# 7. template referencing missing path -> fail path (not a crash)
# ---------------------------------------------------------------------------
async def _template_fail_scenario():
    from app import db
    from app.services.automations import advance_run, get_run, start_run
    from app.services.tools import ToolResult

    sfx = uuid.uuid4().hex[:8]
    tool = f"t_tmpl_{sfx}"

    async def echo(conn, args):
        return ToolResult("ran", {})

    _register_tool(tool, echo, safe=True)
    recipe = {
        "trigger": {"type": "manual"},
        "steps": [{"type": "tool", "tool": tool,
                   "input": {"x": "{{trigger.payload.does_not_exist}}"}}],
    }
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
            run_id = await start_run(conn, DEMO_TENANT, automation)
        await advance_run(DEMO_TENANT, run_id)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run = await get_run(conn, run_id)
            await _cleanup(conn, automation["id"])
        return run
    finally:
        _unregister(tool)
        await db.close_pool()


def test_missing_template_path_fails_run():
    run = asyncio.run(_template_fail_scenario())
    assert run["status"] == "failed"
    assert "resolve" in (run["error"] or "").lower()


# ---------------------------------------------------------------------------
# 8. concurrency: second start_run for same (automation, entity) -> None + skip
# ---------------------------------------------------------------------------
async def _concurrency_scenario():
    from app import db
    from app.services.automations import start_run

    recipe = {
        "trigger": {"type": "event", "event_type": "lead.created"},
        "steps": [{"type": "function", "function": "now", "save_as": "ts"}],
    }
    entity_a = str(uuid.uuid4())
    entity_b = str(uuid.uuid4())
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe)
        # first run for entity A (left 'running' — occupies the slot)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run_a = await start_run(conn, DEMO_TENANT, automation,
                                    entity_type="lead", entity_id=entity_a)
        # second run for entity A while the first is active -> skipped
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run_a2 = await start_run(conn, DEMO_TENANT, automation,
                                     entity_type="lead", entity_id=entity_a)
        # different entity -> allowed
        async with db.tenant_tx(DEMO_TENANT) as conn:
            run_b = await start_run(conn, DEMO_TENANT, automation,
                                    entity_type="lead", entity_id=entity_b)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.events where "
                    "event_type='automation.run_skipped' and payload->>'automation_id'=%s",
                    (str(automation["id"]),),
                )
                skipped = (await cur.fetchone())[0]
            await _cleanup(conn, automation["id"])
        return run_a, run_a2, run_b, skipped
    finally:
        await db.close_pool()


def test_concurrency_guard_skips_second_run():
    run_a, run_a2, run_b, skipped = asyncio.run(_concurrency_scenario())
    assert run_a is not None
    assert run_a2 is None  # same (automation, entity) while active -> skipped
    assert run_b is not None  # different entity -> allowed
    assert skipped == 1
