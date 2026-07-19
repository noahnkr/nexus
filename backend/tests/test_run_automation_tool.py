"""`run_automation` tool + deferred start (Module 15c, Task 2). Gated on
NEXUS_APP_DB_URL.

The tool exists so chat/MCP can start a manual automation. The design constraint
worth testing: a tool handler runs inside execute_tool's savepoint on an
uncommitted transaction, so it CANNOT advance a run itself. It therefore queues
the run `waiting` with `wake_at=now()` and the M7b waker picks it up — these tests
prove that hand-off end to end, plus every refusal.
"""
import asyncio
import uuid

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


# --- helpers (mirrors test_automation_scheduler.py) --------------------------
def _register_tool(name, handler, *, safe=True):
    from app.services.tools import ToolDef
    from app.services.tools.registry import register

    register(ToolDef(
        name=name,
        description="throwaway test tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        safe=safe,
    ))


def _unregister(*names):
    from app.services.tools.registry import _REGISTRY

    for n in names:
        _REGISTRY.pop(n, None)


async def _insert_automation(conn, recipe, *, name, status="active"):
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


async def _run(conn, run_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.automation_runs where id=%s", (run_id,))
        return await cur.fetchone()


async def _cleanup(conn, automation_id):
    await conn.execute(
        "delete from public.automation_runs where automation_id=%s", (automation_id,)
    )
    await conn.execute("delete from public.automations where id=%s", (automation_id,))


# ---------------------------------------------------------------------------
# happy path: queued deferred, then the waker finishes it
# ---------------------------------------------------------------------------
async def _happy_scenario():
    from app import db
    from app.services.automations.scheduler import wake_due_once
    from app.services.tools import ToolResult, execute_tool

    sfx = uuid.uuid4().hex[:8]
    step_tool = f"t_ran_{sfx}"
    name = f"Score this lead {sfx}"
    ran = {"count": 0}

    async def marker(conn, args):
        ran["count"] += 1
        return ToolResult("did it", {"ok": True})

    _register_tool(step_tool, marker, safe=True)
    recipe = {
        "trigger": {"type": "manual"},
        "steps": [{"type": "tool", "tool": step_tool, "input": {}, "save_as": "out"}],
    }
    out = {}
    automation = None
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            automation = await _insert_automation(conn, recipe, name=name)

        # The tool call and its transaction commit together, as in a chat turn.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            result = await execute_tool(
                conn, DEMO_TENANT, "run_automation", {"automation": name},
                source_system="chat",
            )
            out["summary"] = result.summary
            out["is_error"] = result.is_error
            out["data"] = result.data

        run_id = result.data["run_id"]
        async with db.tenant_tx(DEMO_TENANT) as conn:
            queued = await _run(conn, run_id)
            out["queued_status"] = queued["status"]
            out["wake_at_set"] = queued["wake_at"] is not None
            out["step_log"] = queued["step_log"]
        out["ran_before_waker"] = ran["count"]

        # One waker poll claims it and advances to completion.
        out["woken"] = await wake_due_once(DEMO_TENANT)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            done = await _run(conn, run_id)
            out["final_status"] = done["status"]
            out["context"] = done["context"]
        out["ran_after_waker"] = ran["count"]
        return out
    finally:
        # Clean up even on a failed assertion: an orphaned `waiting` run in the
        # shared demo tenant gets claimed by the next test's waker tick and
        # executes a tool that is no longer registered, poisoning unrelated runs.
        if automation is not None:
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await _cleanup(conn, automation["id"])
        _unregister(step_tool)
        await db.close_pool()


def test_run_automation_queues_then_waker_completes():
    out = asyncio.run(_happy_scenario())

    # A queued run is a SUCCESS result, not an error.
    assert out["is_error"] is False
    assert out["data"]["status"] == "queued"
    assert "will run within a few seconds" in out["summary"]

    # Deferred: parked `waiting` with a due wake_at, and NOT executed in-request
    # (the handler's transaction hadn't committed yet — advancing there would have
    # run steps against a run row nobody else could see).
    assert out["queued_status"] == "waiting"
    assert out["wake_at_set"] is True
    assert out["ran_before_waker"] == 0
    assert "queued" in str(out["step_log"])

    # The existing waker finishes it — no new execution machinery.
    assert out["woken"] >= 1
    assert out["final_status"] == "completed"
    assert out["context"]["out"] == {"ok": True}
    assert out["ran_after_waker"] == 1


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------
async def _refusal_scenario():
    from app import db
    from app.services.tools import execute_tool

    sfx = uuid.uuid4().hex[:8]
    manual_name = f"Manual one {sfx}"
    event_name = f"Event one {sfx}"
    out = {}
    manual = evented = None
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            manual = await _insert_automation(
                conn, {"trigger": {"type": "manual"}, "steps": []}, name=manual_name
            )
            evented = await _insert_automation(
                conn,
                {"trigger": {"type": "event", "event_type": "lead.created"}, "steps": []},
                name=event_name,
            )

        async with db.tenant_tx(DEMO_TENANT) as conn:
            # Unknown name -> lists what CAN be run manually.
            unknown = await execute_tool(
                conn, DEMO_TENANT, "run_automation",
                {"automation": f"nope-{sfx}"}, source_system="chat",
            )
            out["unknown"] = (unknown.is_error, unknown.summary)

            # Non-manual trigger -> refused.
            wrong = await execute_tool(
                conn, DEMO_TENANT, "run_automation",
                {"automation": event_name}, source_system="chat",
            )
            out["non_manual"] = (wrong.is_error, wrong.summary)

            # An automation may not start an automation (loop guard at the tool layer).
            looped = await execute_tool(
                conn, DEMO_TENANT, "run_automation",
                {"automation": manual_name}, source_system="automation",
            )
            out["loop"] = (looped.is_error, looped.summary)

            # Case-insensitive name match works.
            ok = await execute_tool(
                conn, DEMO_TENANT, "run_automation",
                {"automation": manual_name.upper()}, source_system="chat",
            )
            out["case_insensitive"] = (ok.is_error, ok.data.get("status"))

            # The concurrency guard is per (automation, ENTITY) and deliberately
            # excludes entity-less runs, so it only fires when an entity is given.
            entity = str(uuid.uuid4())
            first = await execute_tool(
                conn, DEMO_TENANT, "run_automation",
                {"automation": manual_name, "entity_type": "lead", "entity_id": entity},
                source_system="chat",
            )
            out["entity_first"] = (first.is_error, first.data.get("status"))
            again = await execute_tool(
                conn, DEMO_TENANT, "run_automation",
                {"automation": manual_name, "entity_type": "lead", "entity_id": entity},
                source_system="mcp",
            )
            out["already"] = (again.is_error, again.summary, again.data.get("status"))

        out["manual_name"] = manual_name
        return out
    finally:
        # See _happy_scenario: orphaned deferred runs poison later tests.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for row in (manual, evented):
                if row is not None:
                    await _cleanup(conn, row["id"])
        await db.close_pool()


def test_run_automation_refusals():
    out = asyncio.run(_refusal_scenario())

    is_error, summary = out["unknown"]
    assert is_error is True
    assert "no automation called" in summary
    assert out["manual_name"] in summary  # names the ones that CAN be run

    is_error, summary = out["non_manual"]
    assert is_error is True
    assert "runs on its own trigger" in summary

    is_error, summary = out["loop"]
    assert is_error is True
    assert "Automations can't start other automations" in summary

    assert out["case_insensitive"] == (False, "queued")

    # Same automation + same entity: the first queues, the second hits the
    # concurrency guard and comes back as a plain explanation, not a failure.
    assert out["entity_first"] == (False, "queued")
    is_error, summary, status = out["already"]
    assert is_error is False
    assert status == "already_running"
    assert "already running" in summary


# ---------------------------------------------------------------------------
# the builder must not offer this tool as a step
# ---------------------------------------------------------------------------
async def _vocabulary_scenario():
    import httpx

    from app import db
    from app.main import app
    from conftest import bearer_headers

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=bearer_headers(DEMO_TENANT)
        ) as ac:
            vocab = (await ac.get("/api/automations/vocabulary")).json()
        return vocab
    finally:
        await db.close_pool()


def test_vocabulary_excludes_run_automation_but_registry_keeps_it():
    from app.services.tools.registry import get_tool

    vocab = asyncio.run(_vocabulary_scenario())
    step_tools = {t["name"] for t in vocab["tools"]}

    # Excluded from the step palette: it always refuses under source_system=
    # 'automation', so a builder step calling it could only ever fail.
    assert "run_automation" not in step_tools
    # ...but other tools are still listed, so this isn't an empty-list false pass.
    assert "create_task" in step_tools
    # Chat and MCP still see it through the registry.
    assert get_tool("run_automation") is not None

    # The formula function IS offered (Task 1). weighted_score was retired.
    fn_names = {f["name"] for f in vocab["functions"]}
    assert "formula" in fn_names
    assert "weighted_score" not in fn_names
