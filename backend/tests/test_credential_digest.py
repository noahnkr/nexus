"""The daily credential-digest automation (Module 18a, Task 5), gated on
NEXUS_APP_DB_URL.

This proves the FLAGSHIP recipe end-to-end on the real machinery rather than
asserting it in prose: the recipe README publishes is created through the standard
`POST /api/automations` path (the only writer of `automations` rows — CLAUDE.md),
armed as due, and driven through one real `tick_cron_once` cycle. The digest task it
produces has to name the seeded expiring credential in plain language.

Three things it locks down:
  * the recipe passes `validate_recipe` and the create API (no hand-inserted row);
  * one cron cycle -> exactly one digest task; a second immediate cycle -> none
    (cron advances `next_fire_at` before running, so a slow run can't double-fire);
  * the IF short-circuits on a tenant with no expiring credentials — no empty
    "0 credentials need attention" task lands in the office user's queue.

Deliberately keyless: tool + condition + create_task steps only, no `generate`
step, so this runs with no Anthropic key.

NOTE (deviation from the plan's Task-5 note): the plan sketched the condition as
`{{steps.expiry.count}}`. The engine exposes a step's `save_as` result under the
`context.` scope root (`services/automations/engine.py::_scope`), so the recipe —
and README — use `context.expiry.count`. Same semantics, real contract.
"""
import asyncio
import uuid

import httpx
import pytest
from psycopg.rows import dict_row

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


# The recipe exactly as README publishes it. WHEN daily at 07:00 -> read the
# expiring credentials -> IF any -> file one digest task for the coordinator.
def digest_recipe(name: str) -> dict:
    return {
        "name": name,
        "trigger": {"type": "cron", "expression": "0 7 * * *"},
        "conditions": [],
        "steps": [
            {
                "type": "tool",
                "tool": "list_expiring_credentials",
                "input": {"days_ahead": 60},
                "save_as": "expiry",
            },
            {
                "type": "condition",
                "conditions": [{"field": "context.expiry.count", "op": "gt", "value": 0}],
            },
            {
                "type": "tool",
                "tool": "create_task",
                "input": {
                    "title": "Caregiver credentials need attention",
                    "description": "{{context.expiry.summary}}",
                    "priority": "high",
                },
            },
        ],
    }


async def _arm(conn, automation_id):
    """Force the automation due so one tick_cron_once fires it now."""
    await conn.execute(
        "update public.automations set next_fire_at = now() - interval '1 minute' "
        "where id = %s",
        (automation_id,),
    )


async def _digest_tasks(conn, title):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, title, description, priority from public.tasks "
            "where title = %s order by created_at",
            (title,),
        )
        return await cur.fetchall()


async def _cleanup(conn, automation_id, title):
    await conn.execute("delete from public.tasks where title = %s", (title,))
    await conn.execute(
        "delete from public.automation_runs where automation_id = %s", (automation_id,)
    )
    await conn.execute("delete from public.automations where id = %s", (automation_id,))


async def _scenario():
    from app import db
    from app.main import app
    from app.services.automations.recipe import validate_recipe
    from app.services.automations.scheduler import tick_cron_once

    sfx = uuid.uuid4().hex[:6]
    name = f"credential-digest-{sfx}"
    title = "Caregiver credentials need attention"
    recipe = digest_recipe(name)
    out: dict = {}

    # The published JSON validates before it ever reaches the API.
    validate_recipe(recipe)
    out["validated"] = True

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            resp = await ac.post("/api/automations", json=recipe)
            out["create_code"] = resp.status_code
            automation_id = resp.json()["id"]
            # Create always lands paused; activating is the deliberate second step
            # (the same click the owner makes in the builder).
            out["activate_code"] = (await ac.patch(
                f"/api/automations/{automation_id}", json={"status": "active"}
            )).status_code

        # Clear any digest tasks left by an earlier run so the count is ours.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute("delete from public.tasks where title = %s", (title,))
            await _arm(conn, automation_id)

        out["first_tick"] = await tick_cron_once(DEMO_TENANT)
        # Immediately again: next_fire_at has already advanced, so nothing fires.
        out["second_tick"] = await tick_cron_once(DEMO_TENANT)

        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["tasks"] = await _digest_tasks(conn, title)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select status, step_log from public.automation_runs "
                    "where automation_id = %s order by created_at",
                    (automation_id,),
                )
                out["runs"] = await cur.fetchall()
            await _cleanup(conn, automation_id, title)

        # --- the empty case: a tenant with no expiring credentials files nothing.
        # Created through the same standard API path, just on the probe tenant.
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t",
            headers=bearer_headers(PROBE_TENANT),
        ) as probe_ac:
            probe_id = (await probe_ac.post("/api/automations", json={
                **recipe, "name": f"{name}-probe",
            })).json()["id"]
            await probe_ac.patch(
                f"/api/automations/{probe_id}", json={"status": "active"}
            )

        async with db.tenant_tx(PROBE_TENANT) as conn:
            await _arm(conn, probe_id)

        out["probe_tick"] = await tick_cron_once(PROBE_TENANT)
        async with db.tenant_tx(PROBE_TENANT) as conn:
            out["probe_tasks"] = await _digest_tasks(conn, title)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select status from public.automation_runs where automation_id = %s",
                    (probe_id,),
                )
                out["probe_runs"] = await cur.fetchall()
            await _cleanup(conn, probe_id, title)
    finally:
        await db.close_pool()
    return out


@pytest.fixture(scope="module")
def digest():
    return asyncio.run(_scenario())


def test_recipe_validates_and_saves(digest):
    assert digest["validated"] is True
    assert digest["create_code"] == 201
    assert digest["activate_code"] == 200


def test_one_cycle_files_one_digest_task(digest):
    assert digest["first_tick"] == 1
    # Cron advances next_fire_at before running, so an immediate re-tick is a no-op.
    assert digest["second_tick"] == 0

    tasks = digest["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["priority"] == "high"
    # The description is the tool's own plain-language line — names, no UUIDs.
    assert "Brian Okafor's HHA expired" in task["description"]
    assert "Carmen Ruiz's Dementia Care expires in" in task["description"]
    assert "-0000-0000-" not in task["description"]

    assert len(digest["runs"]) == 1
    assert digest["runs"][0]["status"] == "completed"


def test_empty_tenant_short_circuits(digest):
    """No expiring credentials -> the IF stops the run before create_task. The run
    itself still completes cleanly; it just files nothing."""
    assert digest["probe_tick"] == 1
    assert digest["probe_tasks"] == []
    assert len(digest["probe_runs"]) == 1
    assert digest["probe_runs"][0]["status"] in ("completed", "stopped")
