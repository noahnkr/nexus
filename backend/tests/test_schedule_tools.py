"""Scheduling tools (Module 12a, Task 4), gated on NEXUS_APP_DB_URL.

Proves the safe ranking tool and the gated scheduling tools go through the same
execute_tool seam as every other tool: find_available_caregivers ranks (by
schedule_id and by ad-hoc window) and audits a tool.called row; record_call_out
queues a name-bearing task and, on approval, runs the call-out through the seam
(same schedule.called_out event the REST path leaves); create_schedule without a
resource queues an "open shift" and creates an open row on approval; an unknown id
is a clean tool error, never a 500.

Every row/task created is cleaned up; events are immutable and left in place.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

WALTER = "44444444-0000-0000-0000-000000000001"
CARMEN = "55555555-0000-0000-0000-000000000003"
SEED_OPEN = "66666666-0000-0000-0000-000000000009"
UTC = timezone.utc


async def _action(conn, action_id):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.pending_actions where id=%s", (action_id,))
        return await cur.fetchone()


async def _task(conn, task_id):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.tasks where id=%s", (task_id,))
        return await cur.fetchone()


async def _scenario():
    from psycopg.rows import dict_row

    from app import db
    from app.services.approvals import approve_action
    from app.services.tools import execute_tool

    out: dict = {}
    task_ids: list[str] = []
    sched_ids: list[str] = []
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            # --- find_available_caregivers by schedule_id (safe, runs now) ---
            by_id = await execute_tool(
                conn, DEMO_TENANT, "find_available_caregivers",
                {"schedule_id": SEED_OPEN}, source_system="chat",
            )
            out["by_id"] = by_id

            # --- find_available_caregivers by ad-hoc window ---
            adhoc = await execute_tool(
                conn, DEMO_TENANT, "find_available_caregivers",
                {"client_id": WALTER,
                 "start_time": "2028-05-01T09:00:00+00:00",
                 "end_time": "2028-05-01T13:00:00+00:00"},
                source_system="chat",
            )
            out["adhoc"] = adhoc

            # tool.called audit rows for the ranking tool.
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select count(*) as n from public.events where event_type='tool.called' "
                    "and payload->>'tool_name'='find_available_caregivers'"
                )
                out["find_audit_count"] = (await cur.fetchone())["n"]

            # --- record_call_out: queue names caregiver+client+time; approve executes ---
            co_sid = str(uuid.uuid4())
            sched_ids.append(co_sid)
            await conn.execute(
                """insert into public.schedules
                     (id, tenant_id, resource_id, client_id, start_time, end_time, status)
                   values (%s, app.current_tenant_id(), %s, %s, %s, %s, 'scheduled')""",
                (co_sid, CARMEN, WALTER, datetime(2028, 6, 5, 9, tzinfo=UTC),
                 datetime(2028, 6, 5, 13, tzinfo=UTC)),
            )
            queued = await execute_tool(
                conn, DEMO_TENANT, "record_call_out", {"schedule_id": co_sid},
                source_system="chat",
            )
            task_ids.append(queued.data["task_id"])
            out["callout_task_title"] = (await _task(conn, queued.data["task_id"]))["title"]
            await approve_action(conn, DEMO_TENANT, queued.data["pending_action_id"])
            out["callout_action"] = await _action(conn, queued.data["pending_action_id"])
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("select status from public.schedules where id=%s", (co_sid,))
                out["callout_original_status"] = (await cur.fetchone())["status"]
                await cur.execute(
                    "select id, status from public.schedules where replaces_schedule_id=%s",
                    (co_sid,),
                )
                repl = await cur.fetchone()
                out["callout_replacement_status"] = repl["status"] if repl else None
                if repl:
                    sched_ids.append(str(repl["id"]))
                await cur.execute(
                    "select count(*) as n from public.events where entity_type='schedule' "
                    "and entity_id=%s and event_type='schedule.called_out'",
                    (co_sid,),
                )
                out["callout_event_count"] = (await cur.fetchone())["n"]

            # --- create_schedule without resource_id: queues "open shift", approves to open ---
            open_start = datetime(2028, 7, 3, 9, tzinfo=UTC)
            cs = await execute_tool(
                conn, DEMO_TENANT, "create_schedule",
                {"client_id": WALTER, "start_time": open_start.isoformat(),
                 "end_time": (open_start + timedelta(hours=4)).isoformat()},
                source_system="chat",
            )
            task_ids.append(cs.data["task_id"])
            out["create_task_title"] = (await _task(conn, cs.data["task_id"]))["title"]
            await approve_action(conn, DEMO_TENANT, cs.data["pending_action_id"])
            out["create_action_status"] = (await _action(conn, cs.data["pending_action_id"]))["status"]
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select id, status, resource_id from public.schedules "
                    "where client_id=%s and start_time=%s",
                    (WALTER, open_start),
                )
                new_open = await cur.fetchone()
            out["created_open_status"] = new_open["status"] if new_open else None
            out["created_open_resource"] = new_open["resource_id"] if new_open else "MISSING"
            if new_open:
                sched_ids.append(str(new_open["id"]))

            # --- unknown schedule id -> clean tool error, never a 500 ---
            bad = await execute_tool(
                conn, DEMO_TENANT, "find_available_caregivers",
                {"schedule_id": str(uuid.uuid4())}, source_system="chat",
            )
            out["bad"] = bad

            # cleanup
            await conn.execute(
                "update public.schedules set replaces_schedule_id = null where id = any(%s)",
                (sched_ids,),
            )
            await conn.execute("delete from public.schedules where id = any(%s)", (sched_ids,))
            for tid in task_ids:
                await conn.execute("delete from public.pending_actions where task_id=%s", (tid,))
                await conn.execute("delete from public.tasks where id=%s", (tid,))
        return out
    finally:
        await db.close_pool()


def test_schedule_tools():
    out = asyncio.run(_scenario())

    # ranking by schedule_id: non-error, plain summary, structured candidates.
    assert out["by_id"].is_error is False
    assert out["by_id"].data["count"] >= 1
    assert "score" in out["by_id"].summary.lower() or "caregiver" in out["by_id"].summary.lower()
    assert isinstance(out["by_id"].data["candidates"], list)

    # ranking by ad-hoc window works too.
    assert out["adhoc"].is_error is False
    assert out["adhoc"].data["count"] >= 1
    assert out["find_audit_count"] >= 2  # both ranking calls audited

    # record_call_out queued with a name-bearing title, executed on approval.
    assert "Carmen Ruiz" in out["callout_task_title"]
    assert "Walter Grimes" in out["callout_task_title"]
    assert out["callout_action"]["status"] == "executed"
    assert out["callout_original_status"] == "called_out"
    assert out["callout_replacement_status"] == "open"
    assert out["callout_event_count"] == 1  # same schedule.called_out the REST path leaves

    # create_schedule without a resource queues an "open shift" and creates an open row.
    assert "open shift" in out["create_task_title"].lower()
    assert out["create_action_status"] == "executed"
    assert out["created_open_status"] == "open"
    assert out["created_open_resource"] is None

    # unknown id -> clean tool error, not a crash.
    assert out["bad"].is_error is True
    assert "no visit" in out["bad"].summary.lower()
