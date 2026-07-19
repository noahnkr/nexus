"""Automations Center backend additions (Module 8a, Task 1): run cancellation,
the definition-edit guard, list enrichment (active_runs / last_run /
requires_approval), and the Home automations counts. Gated on NEXUS_APP_DB_URL.

Drives the ASGI app over httpx with `bearer_headers` (the JWT-protected `/api`
surface). Automations created here cascade their runs on delete; parked runs are
cleaned up directly.
"""
import asyncio
import uuid

import httpx
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


async def _insert_automation(conn, recipe, *, name="center-test", status="active"):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.automations
                 (tenant_id, name, status, trigger, conditions, steps)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (DEMO_TENANT, name, status, Json(recipe["trigger"]),
             Json(recipe.get("conditions", [])), Json(recipe.get("steps", []))),
        )
        return str((await cur.fetchone())["id"])


async def _run(conn, run_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.automation_runs where id=%s", (run_id,))
        return await cur.fetchone()


async def _cleanup(conn, automation_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, task_id from public.pending_actions where automation_run_id in "
            "(select id from public.automation_runs where automation_id=%s)", (automation_id,))
        for row in await cur.fetchall():
            await conn.execute("delete from public.pending_actions where id=%s", (row["id"],))
            if row["task_id"]:
                await conn.execute("delete from public.tasks where id=%s", (row["task_id"],))
    await conn.execute("delete from public.automation_runs where automation_id=%s", (automation_id,))
    await conn.execute("delete from public.automations where id=%s", (automation_id,))


# ---------------------------------------------------------------------------
# cancel run (waiting + waiting_approval) + terminal 409
# ---------------------------------------------------------------------------
async def _cancel_scenario():
    from app import db
    from app.main import app
    from app.services.automations import advance_run, start_run

    out = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            h = bearer_headers(DEMO_TENANT)

            # --- cancel a `waiting` run (delay step parks it) ---
            wait_recipe = {"trigger": {"type": "manual"},
                           "steps": [{"type": "delay", "days": 1}]}
            async with db.tenant_tx(DEMO_TENANT) as conn:
                aid = await _insert_automation(conn, wait_recipe)
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("select * from public.automations where id=%s", (aid,))
                    automation = await cur.fetchone()
                run_id = await start_run(conn, DEMO_TENANT, automation)
            await advance_run(DEMO_TENANT, run_id)  # parks waiting
            r = await ac.post(f"/api/automation-runs/{run_id}/cancel", headers=h)
            out["cancel_waiting_code"] = r.status_code
            out["cancel_waiting_status"] = r.json().get("status")
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select 1 from public.events where event_type='automation.run_cancelled' "
                        "and payload->>'run_id'=%s", (str(run_id),))
                    out["cancel_waiting_event"] = (await cur.fetchone()) is not None
                # cancel again -> 409 (terminal)
            out["cancel_terminal_code"] = (
                await ac.post(f"/api/automation-runs/{run_id}/cancel", headers=h)
            ).status_code
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await _cleanup(conn, aid)

            # --- cancel a `waiting_approval` run (gated send_sms) -> reject seam ---
            gate_recipe = {"trigger": {"type": "manual"},
                           "steps": [{"type": "tool", "tool": "send_sms",
                                      "input": {"to": "+16195550100", "body": "hi"}, "save_as": "s"}]}
            async with db.tenant_tx(DEMO_TENANT) as conn:
                aid2 = await _insert_automation(conn, gate_recipe)
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("select * from public.automations where id=%s", (aid2,))
                    automation2 = await cur.fetchone()
                run_id2 = await start_run(conn, DEMO_TENANT, automation2)
            await advance_run(DEMO_TENANT, run_id2)  # parks waiting_approval
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "select id, task_id from public.pending_actions where automation_run_id=%s",
                        (run_id2,))
                    pa = await cur.fetchone()
            r2 = await ac.post(f"/api/automation-runs/{run_id2}/cancel", headers=h)
            out["cancel_appr_code"] = r2.status_code
            out["cancel_appr_run_status"] = r2.json().get("status")
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("select status from public.pending_actions where id=%s",
                                      (pa["id"],))
                    out["cancel_appr_action_status"] = (await cur.fetchone())["status"]
                    await cur.execute("select status from public.tasks where id=%s", (pa["task_id"],))
                    out["cancel_appr_task_status"] = (await cur.fetchone())["status"]
                await _cleanup(conn, aid2)
        return out
    finally:
        await db.close_pool()


def test_cancel_run():
    out = asyncio.run(_cancel_scenario())
    assert out["cancel_waiting_code"] == 200
    assert out["cancel_waiting_status"] == "cancelled"
    assert out["cancel_waiting_event"] is True
    assert out["cancel_terminal_code"] == 409
    # waiting_approval cancel routed through reject_action: full chain resolved
    assert out["cancel_appr_code"] == 200
    assert out["cancel_appr_run_status"] == "cancelled"
    assert out["cancel_appr_action_status"] == "rejected"
    assert out["cancel_appr_task_status"] == "cancelled"


# ---------------------------------------------------------------------------
# edit guard + list enrichment
# ---------------------------------------------------------------------------
async def _guard_and_enrichment_scenario():
    from app import db
    from app.main import app
    from app.services.automations import advance_run, start_run

    out = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            h = bearer_headers(DEMO_TENANT)
            # gated-step recipe with a delay so a run parks and stays in flight
            recipe = {"name": f"guard {uuid.uuid4().hex[:6]}",
                      "trigger": {"type": "manual"},
                      "steps": [{"type": "delay", "days": 1},
                                {"type": "tool", "tool": "send_sms",
                                 "input": {"to": "+16195550100", "body": "hi"}, "save_as": "s"}]}
            created = (await ac.post("/api/automations", headers=h, json=recipe)).json()
            aid = created["id"]
            out["requires_approval"] = created["requires_approval"]

            # park a run so it's in flight
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("select * from public.automations where id=%s", (aid,))
                    automation = await cur.fetchone()
                run_id = await start_run(conn, DEMO_TENANT, automation)
            await advance_run(DEMO_TENANT, run_id)  # parks waiting

            # PATCH steps while in flight -> 409
            bad = await ac.patch(f"/api/automations/{aid}", headers=h,
                                 json={"steps": [{"type": "delay", "hours": 2}]})
            out["edit_guard_code"] = bad.status_code
            out["edit_guard_detail"] = bad.json().get("detail", "")
            # PATCH name while in flight -> 200 (metadata edits always allowed)
            name_ok = await ac.patch(f"/api/automations/{aid}", headers=h, json={"name": "renamed"})
            out["name_edit_code"] = name_ok.status_code

            # list enrichment reflects the in-flight run
            listing = (await ac.get("/api/automations", headers=h)).json()
            mine = next(a for a in listing if a["id"] == aid)
            out["active_runs"] = mine["active_runs"]
            out["last_run_status"] = (mine["last_run"] or {}).get("status")

            # cancel the run, then the edit succeeds
            await ac.post(f"/api/automation-runs/{run_id}/cancel", headers=h)
            after = await ac.patch(f"/api/automations/{aid}", headers=h,
                                   json={"steps": [{"type": "delay", "hours": 2}]})
            out["edit_after_cancel_code"] = after.status_code

            async with db.tenant_tx(DEMO_TENANT) as conn:
                await _cleanup(conn, aid)
        return out
    finally:
        await db.close_pool()


def test_edit_guard_and_enrichment():
    out = asyncio.run(_guard_and_enrichment_scenario())
    assert out["requires_approval"] is True  # has a gated send_sms step
    assert out["edit_guard_code"] == 409
    assert "in flight" in out["edit_guard_detail"]
    assert out["name_edit_code"] == 200
    assert out["active_runs"] == 1
    assert out["last_run_status"] == "waiting"
    assert out["edit_after_cancel_code"] == 200


# ---------------------------------------------------------------------------
# home summary automations block + RLS isolation
# ---------------------------------------------------------------------------
async def _home_scenario():
    from app import db
    from app.main import app

    out = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            demo_h = bearer_headers(DEMO_TENANT)
            probe_h = bearer_headers(PROBE_TENANT)

            before = (await ac.get("/api/home/summary", headers=demo_h)).json()
            probe_before = (await ac.get("/api/home/summary", headers=probe_h)).json()

            # 1 active automation + 1 run today (+ its failed status)
            async with db.tenant_tx(DEMO_TENANT) as conn:
                aid = await _insert_automation(conn, {"trigger": {"type": "manual"}, "steps": []})
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "insert into public.automation_runs (tenant_id, automation_id, status) "
                        "values (%s,%s,'failed') returning id", (DEMO_TENANT, aid))
                    run_id = str((await cur.fetchone())["id"])

            after = (await ac.get("/api/home/summary", headers=demo_h)).json()
            probe_after = (await ac.get("/api/home/summary", headers=probe_h)).json()
            out.update(before=before, after=after,
                       probe_before=probe_before, probe_after=probe_after)

            async with db.tenant_tx(DEMO_TENANT) as conn:
                await conn.execute("delete from public.automation_runs where id=%s", (run_id,))
                await conn.execute("delete from public.automations where id=%s", (aid,))
        return out
    finally:
        await db.close_pool()


def test_home_automations_block():
    out = asyncio.run(_home_scenario())
    b, a = out["before"]["automations"], out["after"]["automations"]
    assert a["active"] - b["active"] == 1
    assert a["runs_today"] - b["runs_today"] == 1
    assert a["failed_today"] - b["failed_today"] == 1
    # RLS: demo inserts don't move the probe tenant's automations counts
    assert out["probe_after"]["automations"] == out["probe_before"]["automations"]


# ---------------------------------------------------------------------------
# vocabulary endpoint (Module 8b, Task 1; + WS2 field_suggestions)
# ---------------------------------------------------------------------------
async def _vocab_scenario():
    import uuid as _uuid

    from app import db
    from app.main import app
    from app.services.events import log_event

    out = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            h = bearer_headers(DEMO_TENANT)
            out["no_auth"] = (await ac.get("/api/automations/vocabulary")).status_code

            # a freshly logged custom (non-automation) event type should surface;
            # an automation-sourced type should NOT.
            custom = f"vocab.custom.{_uuid.uuid4().hex[:8]}"
            auto_only = f"vocab.autoonly.{_uuid.uuid4().hex[:8]}"
            # (11a) a non-automation lead.created payload key should surface under
            # its event type in the catalog, humanized; an automation-sourced key
            # for the same event type must NOT.
            auto_key = f"autop{_uuid.uuid4().hex[:6]}"
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await log_event(conn, tenant_id=DEMO_TENANT, source_system="welcomehome",
                                event_type=custom, payload={})
                await log_event(conn, tenant_id=DEMO_TENANT, source_system="automation",
                                event_type=auto_only, payload={})
                await log_event(conn, tenant_id=DEMO_TENANT, source_system="welcomehome",
                                event_type="lead.created", payload={"hours_per_week": 20})
                await log_event(conn, tenant_id=DEMO_TENANT, source_system="automation",
                                event_type="lead.created", payload={auto_key: 1})

            out["vocab"] = (await ac.get("/api/automations/vocabulary", headers=h)).json()
            out["custom"] = custom
            out["auto_only"] = auto_only
            out["auto_key"] = auto_key
        return out
    finally:
        await db.close_pool()


def test_vocabulary():
    from app.services.automations.recipe import OPERATORS
    from app.services.tools import all_tools
    from app.services.automations.functions import all_functions

    out = asyncio.run(_vocab_scenario())
    assert out["no_auth"] == 401

    v = out["vocab"]
    # every registered tool + function is present, with schema + safety — except
    # the tools deliberately kept out of the step palette (M15c): run_automation
    # refuses every automation-sourced call, so a step calling it could only fail.
    from app.routers.automations import _STEP_EXCLUDED_TOOLS

    tool_names = {t["name"] for t in v["tools"]}
    assert {t.name for t in all_tools()} - _STEP_EXCLUDED_TOOLS == tool_names
    assert _STEP_EXCLUDED_TOOLS and not (_STEP_EXCLUDED_TOOLS & tool_names)
    assert all("input_schema" in t and "safe" in t and "label" in t for t in v["tools"])
    fn_names = {f["name"] for f in v["functions"]}
    assert {f.name for f in all_functions()} <= fn_names
    # operators match recipe.py exactly (drift guard)
    assert v["operators"] == list(OPERATORS)
    assert v["field_roots"] == ["trigger", "entity", "context"]
    # observed non-automation event type appears; automation-sourced one does not
    assert out["custom"] in v["triggers"]["event_types"]
    assert out["auto_only"] not in v["triggers"]["event_types"]

    # WS2: field autocomplete suggestions include core trigger paths + entity columns
    fs = v["field_suggestions"]
    assert "trigger.payload.to" in fs or "trigger.event_type" in fs
    assert any(s.startswith("entity.") for s in fs)
    assert "entity.status" in fs  # a leads column, surfaced from the entity seam

    # --- field_catalog (Module 11a) ---
    fc = v["field_catalog"]
    # 5 labeled core trigger fields, in the _trigger_scope shape order
    assert [f["path"] for f in fc["trigger_fields"]] == [
        "trigger.event_type", "trigger.source_system", "trigger.entity_type",
        "trigger.entity_id", "trigger.created_at",
    ]
    assert all(f["label"] for f in fc["trigger_fields"])

    # observed payload key surfaces under its event type, humanized; the
    # automation-sourced key for the same event type does NOT appear
    lead_payload = {f["path"]: f["label"] for f in fc["payload_by_event"].get("lead.created", [])}
    assert lead_payload.get("trigger.payload.hours_per_week") == "Hours per week"
    assert f"trigger.payload.{out['auto_key']}" not in lead_payload

    # entities keyed by type: lead has its columns, no applicant/schedule leak
    assert fc["entities"]["lead"]["label"] == "Lead"
    lead_paths = {f["path"] for f in fc["entities"]["lead"]["fields"]}
    assert "entity.status" in lead_paths
    assert "entity.stage" not in lead_paths  # applicant column — must not leak in
    assert "entity.start_time" not in lead_paths  # schedule column — must not leak in
    assert fc["entities"]["applicant"]["label"] == "Applicant"

    # event -> entity: observed maps lead.created; a core-known-but-unobserved type
    # maps via the prefix heuristic
    assert fc["event_entity"]["lead.created"] == "lead"
    assert fc["event_entity"].get("client.updated") == "client"

    # --- declared event knowledge (Module 11 fix): the registry is correct even
    # with no observed history for the event type ---
    # a declared connector event is offerable as a trigger before one ever arrived
    assert "sms.received" in v["triggers"]["event_types"]
    # its declared (nested) payload fields are present with curated labels
    sms_payload = {f["path"]: f["label"] for f in fc["payload_by_event"]["sms.received"]}
    assert sms_payload.get("trigger.payload.detail.message.text") == "Message text"
    assert sms_payload.get("trigger.payload.detail.message.from") == "Sender number"
    # declared entity mapping (no sms.received prefix table exists — must be declared)
    assert fc["event_entity"]["sms.received"] == "lead"
    # stage-change events declare from/to
    stage_payload = {f["path"] for f in fc["payload_by_event"]["lead.stage_changed"]}
    assert {"trigger.payload.from", "trigger.payload.to"} <= stage_payload
    # every event type offers `summary` (the writers' plain-language convention)
    assert "trigger.payload.summary" in lead_payload or any(
        f["path"] == "trigger.payload.summary"
        for f in fc["payload_by_event"]["lead.created"]
    )

    # --- Module 13 (Task 2): the catalog the builder's IF dropdown reads is
    # populated end-to-end. The reported "no fields appear" bug is a frontend
    # rendering gap (FieldCombobox short-circuiting on hint-only groups), not a
    # missing backend catalog — these guard the shape fieldGroups() depends on. ---
    assert len(fc["trigger_fields"]) >= 5
    assert fc["entities"]["lead"]["fields"]  # the mapped record group is non-empty
    lead_payload_paths = {f["path"] for f in fc["payload_by_event"]["lead.created"]}
    assert "trigger.payload.summary" in lead_payload_paths
    assert fc["event_entity"]["lead.created"] == "lead"
