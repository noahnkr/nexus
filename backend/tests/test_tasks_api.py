"""Tasks & approvals API (Module 5a, Tasks 1 & 5), gated on NEXUS_APP_DB_URL.

Task 1: structural checks that the migration added the pending_actions columns
and put tasks/pending_actions in the Realtime publication (direct SQL).
Task 5: drives the real router via the app-client pattern — create/list round-trip,
comma-separated status filter, keyset pagination, PATCH transitions incl. 409s,
approve/reject endpoints, 404/409 cases, RLS isolation, and the limit cap.
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


# ---------------------------------------------------------------------------
# Task 1 — migration structure
# ---------------------------------------------------------------------------
def test_migration_structure():
    import psycopg

    from app.config import settings

    conn = psycopg.connect(settings.nexus_app_db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select column_name from information_schema.columns "
                "where table_schema='public' and table_name='pending_actions' "
                "and column_name in ('source_system','resolved_by','result')"
            )
            cols = {r[0] for r in cur.fetchall()}
            assert cols == {"source_system", "resolved_by", "result"}

            cur.execute(
                "select tablename from pg_publication_tables "
                "where pubname='supabase_realtime' and schemaname='public' "
                "and tablename in ('tasks','pending_actions')"
            )
            pub = {r[0] for r in cur.fetchall()}
            assert pub == {"tasks", "pending_actions"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Task 5 — API behavior
# ---------------------------------------------------------------------------
async def _api_scenario():
    from app import db
    from app.main import app
    from app.services.tools import execute_tool

    token = uuid.uuid4().hex[:8]
    out = {"token": token, "created_task_ids": []}

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            # --- create three tasks, tagged in the title so we can isolate them ---
            created = []
            for i, prio in enumerate(["low", "normal", "high"]):
                r = await ac.post("/api/tasks", json={
                    "title": f"apitest {token} #{i}",
                    "description": "created by test",
                    "priority": prio,
                })
                assert r.status_code == 201, r.text
                created.append(r.json())
            out["created"] = created
            out["created_task_ids"] = [c["id"] for c in created]

            # --- list + priority filter round-trip ---
            hi = (await ac.get("/api/tasks", params={"priority": "high", "limit": 100})).json()
            out["high_titles"] = [t["title"] for t in hi["tasks"]]

            # --- comma-separated status filter: all three are 'pending' ---
            open_page = (await ac.get("/api/tasks", params={
                "status": "pending,in_progress", "limit": 100})).json()
            out["open_has_all"] = all(
                cid in {t["id"] for t in open_page["tasks"]} for cid in out["created_task_ids"]
            )

            # --- keyset pagination walks a filtered set exactly once ---
            seen, cursor, pages = [], None, 0
            while True:
                params = {"priority": "low", "limit": 1}
                if cursor:
                    params["cursor"] = cursor
                page = (await ac.get("/api/tasks", params=params)).json()
                seen.extend(t["id"] for t in page["tasks"])
                pages += 1
                cursor = page["next_cursor"]
                if not cursor or pages > 50:
                    break
            out["low_ids_unique"] = len(seen) == len(set(seen))

            # --- PATCH transitions ---
            t0 = created[0]["id"]
            out["patch_in_progress"] = (await ac.patch(
                f"/api/tasks/{t0}", json={"status": "in_progress"})).json()
            out["patch_done"] = (await ac.patch(
                f"/api/tasks/{t0}", json={"status": "done"})).json()
            # terminal is immutable -> 409
            out["patch_terminal_code"] = (await ac.patch(
                f"/api/tasks/{t0}", json={"status": "pending"})).status_code

            # --- queue a gated action so we can test approve + the 409-while-pending ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                queued = await execute_tool(
                    conn, DEMO_TENANT, "update_lead_status",
                    {"lead_id": "33333333-0000-0000-0000-000000000001", "status": "contacted"},
                )
            action_id = queued.data["pending_action_id"]
            gate_task_id = queued.data["task_id"]
            out["created_task_ids"].append(gate_task_id)

            # closing a task with a pending action -> 409
            out["close_pending_code"] = (await ac.patch(
                f"/api/tasks/{gate_task_id}", json={"status": "cancelled"})).status_code

            # approve endpoint executes and returns refreshed action + task
            appr = await ac.post(f"/api/pending-actions/{action_id}/approve")
            out["approve_code"] = appr.status_code
            out["approve_body"] = appr.json()
            # revert lead mutation
            async with db.tenant_tx(DEMO_TENANT) as conn:
                await conn.execute(
                    "update public.leads set status='new' where id=%s",
                    ("33333333-0000-0000-0000-000000000001",),
                )

            # double-approve -> 409
            out["reapprove_code"] = (
                await ac.post(f"/api/pending-actions/{action_id}/approve")).status_code
            # unknown action -> 404
            out["unknown_code"] = (
                await ac.post(f"/api/pending-actions/{uuid.uuid4()}/approve")).status_code

            # reject flow on a fresh gated action
            async with db.tenant_tx(DEMO_TENANT) as conn:
                q2 = await execute_tool(
                    conn, DEMO_TENANT, "send_sms", {"to": "+16195550101", "body": "hi"},
                )
            out["created_task_ids"].append(q2.data["task_id"])
            rej = await ac.post(
                f"/api/pending-actions/{q2.data['pending_action_id']}/reject",
                json={"note": "not now"},
            )
            out["reject_body"] = rej.json()

            # limit cap: 500 clamped to <=100
            capped = (await ac.get("/api/tasks", params={"limit": 500})).json()
            out["capped_ok"] = len(capped["tasks"]) <= 100

            # RLS: a probe-tenant task is invisible through the demo-scoped API
            async with db.tenant_tx(PROBE_TENANT) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "insert into public.tasks (tenant_id, title, status, priority) "
                        "values (%s, %s, 'pending', 'normal') returning id",
                        (PROBE_TENANT, f"probe {token}"),
                    )
                    probe_task_id = str((await cur.fetchone())[0])
            allrows = (await ac.get("/api/tasks", params={"limit": 100})).json()
            out["probe_invisible"] = probe_task_id not in {t["id"] for t in allrows["tasks"]}
            out["probe_task_id"] = probe_task_id

        # cleanup (delete-what-you-seeded; events are immutable)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for tid in out["created_task_ids"]:
                await conn.execute("delete from public.pending_actions where task_id=%s", (tid,))
                await conn.execute("delete from public.tasks where id=%s", (tid,))
        async with db.tenant_tx(PROBE_TENANT) as conn:
            await conn.execute("delete from public.tasks where id=%s", (out["probe_task_id"],))
        return out
    finally:
        await db.close_pool()


def test_tasks_api():
    out = asyncio.run(_api_scenario())

    assert len(out["created"]) == 3
    assert f"apitest {out['token']} #2" in out["high_titles"]  # priority filter works
    assert out["open_has_all"]  # comma-separated status returns all pending
    assert out["low_ids_unique"]  # keyset pages don't duplicate

    assert out["patch_in_progress"]["status"] == "in_progress"
    assert out["patch_done"]["status"] == "done"
    assert out["patch_done"]["resolved_at"] is not None
    assert out["patch_terminal_code"] == 409  # terminal immutable

    assert out["close_pending_code"] == 409  # can't close over a pending action
    assert out["approve_code"] == 200
    assert out["approve_body"]["action"]["status"] == "executed"
    assert out["approve_body"]["task"]["status"] == "done"
    assert out["reapprove_code"] == 409  # already resolved
    assert out["unknown_code"] == 404

    assert out["reject_body"]["action"]["status"] == "rejected"
    assert out["reject_body"]["task"]["status"] == "cancelled"

    assert out["capped_ok"]
    assert out["probe_invisible"]  # RLS isolates the probe tenant through the API
