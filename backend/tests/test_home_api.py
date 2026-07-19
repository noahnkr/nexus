"""Home summary API (Module 6b, Task 3), gated on NEXUS_APP_DB_URL.

Counts are tenant-wide aggregates over core tables, so seed/other-test rows form a
baseline. The test measures before, inserts a known fixture set, measures after,
and asserts the *deltas* equal what it inserted — robust against pre-existing data.
Probe-tenant isolation (RLS) and the 401-without-auth guard are checked too.
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


async def _summary(ac, headers):
    r = await ac.get("/api/home/summary", headers=headers)
    return r


async def _scenario():
    from app import db
    from app.main import app
    from app.services.tools import execute_tool

    token = uuid.uuid4().hex[:8]
    out: dict = {"task_ids": [], "doc_ids": [], "gate_task_ids": [], "sched_ids": []}
    WALTER = "44444444-0000-0000-0000-000000000001"
    ALICIA = "55555555-0000-0000-0000-000000000001"

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            demo_h = bearer_headers(DEMO_TENANT)
            probe_h = bearer_headers(PROBE_TENANT)

            # --- no auth -> 401 ---
            out["no_auth"] = (await ac.get("/api/home/summary")).status_code

            # --- baselines (demo + probe) ---
            before = (await _summary(ac, demo_h)).json()
            probe_before = (await _summary(ac, probe_h)).json()

            # --- insert a known fixture set for the demo tenant ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor() as cur:
                    # 2 open tasks (pending, in_progress) + 1 done
                    for st in ("pending", "in_progress", "done"):
                        await cur.execute(
                            "insert into public.tasks (tenant_id, title, status, priority) "
                            "values (%s, %s, %s, 'normal') returning id",
                            (DEMO_TENANT, f"hometest {token} {st}", st),
                        )
                        out["task_ids"].append(str((await cur.fetchone())[0]))
                    # documents across statuses: 1 ready, 1 processing, 1 failed
                    for st in ("ready", "processing", "failed"):
                        await cur.execute(
                            "insert into public.documents (tenant_id, filename, status) "
                            "values (%s, %s, %s) returning id",
                            (DEMO_TENANT, f"hometest_{token}_{st}.md", st),
                        )
                        out["doc_ids"].append(str((await cur.fetchone())[0]))
                    # open_shifts counts FUTURE open visits only: +1 future open counts;
                    # a past open shift and a future filled (scheduled) visit do not.
                    await cur.execute(
                        "insert into public.schedules (tenant_id, resource_id, client_id, "
                        "start_time, end_time, status) values "
                        "(%s, null, %s, now() + interval '1 day', now() + interval '1 day 4 hours', 'open') "
                        "returning id",
                        (DEMO_TENANT, WALTER),
                    )
                    out["sched_ids"].append(str((await cur.fetchone())[0]))
                    await cur.execute(
                        "insert into public.schedules (tenant_id, resource_id, client_id, "
                        "start_time, end_time, status) values "
                        "(%s, null, %s, now() - interval '2 days', now() - interval '2 days' + interval '4 hours', 'open') "
                        "returning id",
                        (DEMO_TENANT, WALTER),
                    )
                    out["sched_ids"].append(str((await cur.fetchone())[0]))
                    await cur.execute(
                        "insert into public.schedules (tenant_id, resource_id, client_id, "
                        "start_time, end_time, status) values "
                        "(%s, %s, %s, now() + interval '3 days', now() + interval '3 days 4 hours', 'scheduled') "
                        "returning id",
                        (DEMO_TENANT, ALICIA, WALTER),
                    )
                    out["sched_ids"].append(str((await cur.fetchone())[0]))

            # 1 pending approval (via a gated tool -> queues an action + task)
            async with db.tenant_tx(DEMO_TENANT) as conn:
                q = await execute_tool(
                    conn, DEMO_TENANT, "send_sms", {"to": "+16195550100", "body": "hi"},
                )
            out["gate_task_ids"].append(q.data["task_id"])

            after = (await _summary(ac, demo_h)).json()
            probe_after = (await _summary(ac, probe_h)).json()

            out["before"] = before
            out["after"] = after
            out["probe_before"] = probe_before
            out["probe_after"] = probe_after

        # cleanup
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for tid in out["gate_task_ids"]:
                await conn.execute("delete from public.pending_actions where task_id=%s", (tid,))
                await conn.execute("delete from public.tasks where id=%s", (tid,))
            for tid in out["task_ids"]:
                await conn.execute("delete from public.tasks where id=%s", (tid,))
            for did in out["doc_ids"]:
                await conn.execute("delete from public.documents where id=%s", (did,))
            for sid in out["sched_ids"]:
                await conn.execute("delete from public.schedules where id=%s", (sid,))
        return out
    finally:
        await db.close_pool()


def test_home_summary():
    out = asyncio.run(_scenario())
    b, a = out["before"], out["after"]

    assert out["no_auth"] == 401

    # +1 pending task, +1 in_progress task, +1 pending gate-approval task from the
    # queued send_sms = +3 open (the 'done' one doesn't count)
    assert a["open_tasks"] - b["open_tasks"] == 3
    # the gated send_sms queued exactly one pending approval
    assert a["pending_approvals"] - b["pending_approvals"] == 1
    # only the one FUTURE open shift counts (past-open + future-scheduled excluded)
    assert a["open_shifts"] - b["open_shifts"] == 1
    # documents by status
    assert a["documents"]["ready"] - b["documents"]["ready"] == 1
    assert a["documents"]["processing"] - b["documents"]["processing"] == 1
    assert a["documents"]["failed"] - b["documents"]["failed"] == 1
    # the fixtures + gate wrote today-events, so events_today only grows
    assert a["events_today"] >= b["events_today"] + 1

    # RLS isolation: none of the demo inserts moved the probe tenant's counts.
    assert out["probe_after"]["open_tasks"] == out["probe_before"]["open_tasks"]
    assert (
        out["probe_after"]["pending_approvals"]
        == out["probe_before"]["pending_approvals"]
    )
    assert out["probe_after"]["open_shifts"] == out["probe_before"]["open_shifts"]
    assert out["probe_after"]["documents"] == out["probe_before"]["documents"]
