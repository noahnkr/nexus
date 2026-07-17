"""Automations REST API (Module 7a, Task 4) + migration structure checks (Task 1).

The migration checks use the direct `db` fixture (SUPABASE_DB_URL). The API checks
drive the ASGI app over httpx with `bearer_headers` (the JWT-protected `/api`
surface), gated on NEXUS_APP_DB_URL. Fixtures created by the API scenarios are
cleaned up (their runs cascade on automation delete; events are immutable).
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import (
    DEMO_TENANT,
    NEXUS_APP_DB_URL,
    SUPABASE_DB_URL,
    bearer_headers,
)

# ---------------------------------------------------------------------------
# Task 1 — migration structure (direct db fixture)
# ---------------------------------------------------------------------------
db_gate = pytest.mark.skipif(not SUPABASE_DB_URL, reason="SUPABASE_DB_URL not set")


@db_gate
def test_tables_exist(db):
    with db.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema='public' and table_name='automations'"
        )
        cols = {r[0] for r in cur.fetchall()}
    assert {"trigger", "conditions", "steps", "status", "next_fire_at", "created_by"} <= cols


@db_gate
def test_runs_table_and_link(db):
    with db.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema='public' and table_name='automation_runs'"
        )
        cols = {r[0] for r in cur.fetchall()}
        assert {"context", "step_index", "step_log", "wake_at", "error", "finished_at"} <= cols
        # pending_actions gained the run link
        cur.execute(
            "select 1 from information_schema.columns where table_schema='public' "
            "and table_name='pending_actions' and column_name='automation_run_id'"
        )
        assert cur.fetchone() is not None


@db_gate
def test_concurrency_index_and_publication(db):
    with db.cursor() as cur:
        cur.execute(
            "select indexdef from pg_indexes where schemaname='public' "
            "and indexname='automation_runs_one_active_per_entity'"
        )
        row = cur.fetchone()
        # Postgres normalizes `status in (...)` to `status = ANY (ARRAY[...])`.
        assert row is not None
        defn = row[0].lower()
        assert "waiting_approval" in defn and "entity_id is not null" in defn
        cur.execute(
            "select tablename from pg_publication_tables where pubname='supabase_realtime' "
            "and schemaname='public' and tablename in ('automations','automation_runs')"
        )
        published = {r[0] for r in cur.fetchall()}
    assert published == {"automations", "automation_runs"}


# ---------------------------------------------------------------------------
# Task 4 — REST API (ASGI over httpx)
# ---------------------------------------------------------------------------
api_gate = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

_WELCOME_RECIPE = {
    "name": "Welcome a new lead",
    "trigger": {"type": "event", "event_type": "lead.created", "source_system": "welcomehome"},
    "conditions": [],
    "steps": [
        {"type": "function", "function": "now", "save_as": "ts"},
    ],
}


async def _api_scenario():
    from app import db
    from app.main import app

    out: dict = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            h = bearer_headers(DEMO_TENANT)
            probe_h = bearer_headers("00000000-0000-0000-0000-000000000002")

            # --- no auth -> 401 ---
            out["no_auth"] = (await ac.get("/api/automations")).status_code

            # --- create ---
            r = await ac.post("/api/automations", headers=h, json=_WELCOME_RECIPE)
            out["create_status"] = r.status_code
            created = r.json()
            aid = created["id"]
            out["created"] = created

            # --- get + list round-trip ---
            out["get"] = (await ac.get(f"/api/automations/{aid}", headers=h)).json()
            listing = (await ac.get("/api/automations", headers=h)).json()
            out["in_list"] = any(a["id"] == aid for a in listing)

            # --- invalid recipe -> 422 with plain message ---
            bad = await ac.post("/api/automations", headers=h, json={
                "name": "bad", "trigger": {"type": "manual"},
                "steps": [{"type": "tool", "tool": "does_not_exist", "input": {}}],
            })
            out["bad_status"] = bad.status_code
            out["bad_detail"] = bad.json().get("detail", "")

            # --- PATCH flips status + revalidates ---
            patched = await ac.patch(f"/api/automations/{aid}", headers=h, json={"status": "active"})
            out["patched_status"] = patched.json()["status"]
            bad_patch = await ac.patch(f"/api/automations/{aid}", headers=h, json={
                "steps": [{"type": "delay", "minutes": 0}],
            })
            out["bad_patch_status"] = bad_patch.status_code
            still = (await ac.get(f"/api/automations/{aid}", headers=h)).json()
            out["steps_unchanged"] = still["steps"] == _WELCOME_RECIPE["steps"]

            # --- manual run of a no-delay recipe -> completed run with context ---
            run = await ac.post(f"/api/automations/{aid}/run", headers=h, json={})
            out["run_status_code"] = run.status_code
            run_body = run.json()
            out["run"] = run_body
            run_id = run_body["id"]

            # --- runs list + detail ---
            runs = (await ac.get(f"/api/automations/{aid}/runs", headers=h)).json()
            out["runs_count"] = len(runs)
            out["run_detail"] = (await ac.get(f"/api/automation-runs/{run_id}", headers=h)).json()

            # --- concurrency guard -> 409 (park a run with an entity, retry same entity) ---
            entity = str(uuid.uuid4())
            # a recipe that parks (delay) so the first run stays active
            delay_recipe = {
                "name": "parker", "trigger": {"type": "manual"},
                "steps": [{"type": "delay", "days": 1}],
            }
            dr = (await ac.post("/api/automations", headers=h, json=delay_recipe)).json()
            did = dr["id"]
            first = await ac.post(f"/api/automations/{did}/run", headers=h,
                                  json={"entity_type": "lead", "entity_id": entity})
            out["first_run_status"] = first.json()["status"]
            second = await ac.post(f"/api/automations/{did}/run", headers=h,
                                   json={"entity_type": "lead", "entity_id": entity})
            out["second_run_code"] = second.status_code

            # --- probe tenant can't see the demo automation (RLS) ---
            out["probe_get_code"] = (
                await ac.get(f"/api/automations/{aid}", headers=probe_h)
            ).status_code
            probe_list = (await ac.get("/api/automations", headers=probe_h)).json()
            out["probe_sees"] = any(a["id"] == aid for a in probe_list)

            # --- DELETE cascades runs ---
            out["delete_code"] = (
                await ac.delete(f"/api/automations/{aid}", headers=h)
            ).status_code
            out["get_after_delete"] = (
                await ac.get(f"/api/automations/{aid}", headers=h)
            ).status_code
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select count(*) from public.automation_runs where automation_id=%s",
                        (aid,),
                    )
                    out["runs_after_delete"] = (await cur.fetchone())[0]

        # cleanup the delay automation (+ its parked run/task)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute("delete from public.automation_runs where automation_id=%s", (did,))
            await conn.execute("delete from public.automations where id=%s", (did,))
        return out
    finally:
        await db.close_pool()


@api_gate
def test_automations_api():
    out = asyncio.run(_api_scenario())

    assert out["no_auth"] == 401
    assert out["create_status"] == 201
    assert out["created"]["status"] == "paused"  # created paused
    assert out["get"]["id"] == out["created"]["id"]
    assert out["in_list"] is True

    assert out["bad_status"] == 422
    assert "does_not_exist" in out["bad_detail"]

    assert out["patched_status"] == "active"
    assert out["bad_patch_status"] == 422
    assert out["steps_unchanged"] is True  # bad edit left the row untouched

    assert out["run_status_code"] == 200
    assert out["run"]["status"] == "completed"
    assert "ts" in out["run"]["context"]
    assert out["runs_count"] == 1
    assert out["run_detail"]["id"] == out["run"]["id"]

    assert out["first_run_status"] == "waiting"
    assert out["second_run_code"] == 409  # concurrency guard

    assert out["probe_get_code"] == 404  # RLS: not visible to another tenant
    assert out["probe_sees"] is False

    assert out["delete_code"] == 204
    assert out["get_after_delete"] == 404
    assert out["runs_after_delete"] == 0  # runs cascaded
