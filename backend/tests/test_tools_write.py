"""Write tools (Module 5a, Task 4), gated on NEXUS_APP_DB_URL.

Proves: create_task runs immediately (safe); the entity write tools queue (unsafe)
with plain-language, name-bearing task titles from gate_describe, and actually
mutate the record after approval; input validation fails cleanly post-approval;
send_sms performs a real (stubbed) send only after approval; and the registry
exposes all new tools with the cache breakpoint on the last entry only.

Mutations are reverted / cleaned up; events are immutable and left in place.
"""
import asyncio
import uuid

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"  # status 'new'
WALTER_CLIENT = "44444444-0000-0000-0000-000000000001"  # status 'active'
ALICIA_RESOURCE = "55555555-0000-0000-0000-000000000001"
COMPLETED_SCHEDULE = "66666666-0000-0000-0000-000000000001"  # status 'completed'


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


async def _scalar(conn, sql, params):
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        row = await cur.fetchone()
        return row[0] if row else None


async def _cleanup(conn, task_ids):
    for tid in task_ids:
        await conn.execute("delete from public.pending_actions where task_id=%s", (tid,))
        await conn.execute("delete from public.tasks where id=%s", (tid,))


async def _scenario():
    from app import db
    from app.services.approvals import approve_action
    from app.services.tools import anthropic_tool_defs, execute_tool

    out = {}
    task_ids = []
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            # --- create_task: safe, runs immediately ---
            ct = await execute_tool(
                conn, DEMO_TENANT, "create_task",
                {"title": "Coordinate coverage", "priority": "high"},
            )
            out["create_task"] = ct
            ct_task = await _task(conn, ct.data["task_id"])
            out["create_task_row"] = ct_task
            task_ids.append(ct.data["task_id"])

            # --- update_lead_status: queues with the lead's NAME in the title ---
            uq = await execute_tool(
                conn, DEMO_TENANT, "update_lead_status",
                {"lead_id": MARGARET_LEAD, "status": "contacted"},
            )
            out["update_queued"] = uq
            uq_task = await _task(conn, uq.data["task_id"])
            out["update_task_title"] = uq_task["title"]
            task_ids.append(uq.data["task_id"])
            # lead unchanged while queued
            out["lead_status_queued"] = await _scalar(
                conn, "select status from public.leads where id=%s", (MARGARET_LEAD,)
            )
            # approve -> lead actually changes
            await approve_action(conn, DEMO_TENANT, uq.data["pending_action_id"])
            out["lead_status_approved"] = await _scalar(
                conn, "select status from public.leads where id=%s", (MARGARET_LEAD,)
            )
            # the handler emitted a lead.stage_changed alongside tool.called (9a):
            # a recent stage event for Margaret with a truthful new->contacted payload.
            out["stage_event"] = await _scalar(
                conn,
                """select payload from public.events
                    where entity_type='lead' and entity_id=%s
                      and event_type='lead.stage_changed'
                    order by created_at desc limit 1""",
                (MARGARET_LEAD,),
            )
            # revert the mutation
            await conn.execute(
                "update public.leads set status='new' where id=%s", (MARGARET_LEAD,)
            )

            # --- create_schedule: end <= start fails cleanly post-approval ---
            bad = await execute_tool(
                conn, DEMO_TENANT, "create_schedule",
                {
                    "resource_id": ALICIA_RESOURCE,
                    "client_id": WALTER_CLIENT,
                    "start_time": "2030-01-01T12:00:00+00:00",
                    "end_time": "2030-01-01T11:00:00+00:00",
                },
            )
            task_ids.append(bad.data["task_id"])
            await approve_action(conn, DEMO_TENANT, bad.data["pending_action_id"])
            out["bad_schedule_action"] = await _action(conn, bad.data["pending_action_id"])

            # --- cancel_schedule: refuses an already-completed visit ---
            cs = await execute_tool(
                conn, DEMO_TENANT, "cancel_schedule", {"schedule_id": COMPLETED_SCHEDULE}
            )
            task_ids.append(cs.data["task_id"])
            await approve_action(conn, DEMO_TENANT, cs.data["pending_action_id"])
            out["cancel_completed_action"] = await _action(conn, cs.data["pending_action_id"])

            # --- send_sms: queues, and approval performs a REAL send (v1.2.0) ---
            # The provider call is stubbed. This is not squeamishness about
            # mocking: `.env` carries live GoTo credentials, so an unstubbed run
            # of this suite would put an actual text message on an actual phone.
            # A test must never do that.
            from app.services.connectors import goto_sms as _goto_sms

            sent: list[tuple[str, str]] = []

            async def _fake_send(to, body, **_kw):
                sent.append((to, body))
                return {"id": "test-msg"}

            _real_send = _goto_sms.send_sms
            _goto_sms.send_sms = _fake_send
            try:
                sms = await execute_tool(
                    conn, DEMO_TENANT, "send_sms",
                    {"to": "+16195550101", "body": "Hello from the test"},
                )
                task_ids.append(sms.data["task_id"])
                await approve_action(conn, DEMO_TENANT, sms.data["pending_action_id"])
                out["sms_action"] = await _action(conn, sms.data["pending_action_id"])
            finally:
                _goto_sms.send_sms = _real_send
            out["sms_sent"] = list(sent)

            out["tool_defs"] = anthropic_tool_defs()

            await _cleanup(conn, task_ids)
        return out
    finally:
        await db.close_pool()


def test_write_tools():
    out = asyncio.run(_scenario())

    # create_task (safe) executed immediately.
    assert out["create_task"].is_error is False
    assert out["create_task"].data.get("task_id")
    assert out["create_task_row"]["title"] == "Coordinate coverage"
    assert out["create_task_row"]["status"] == "pending"

    # update_lead_status queued, with the lead's name in the title.
    assert out["update_queued"].data["status"] == "queued"
    assert "Margaret Ellison" in out["update_task_title"]
    # unchanged while queued, changed only after approval.
    assert out["lead_status_queued"] == "new"
    assert out["lead_status_approved"] == "contacted"
    # the approved stage move also left a first-class lead.stage_changed event (9a).
    assert out["stage_event"] is not None
    assert out["stage_event"]["from"] == "new" and out["stage_event"]["to"] == "contacted"

    # bad create_schedule failed cleanly post-approval (plain error, action failed).
    bad = out["bad_schedule_action"]
    assert bad["status"] == "failed"
    assert "after" in bad["result"]["error"].lower()

    # cancel_schedule refused a completed visit.
    cs = out["cancel_completed_action"]
    assert cs["status"] == "failed"
    assert "completed" in cs["result"]["error"].lower()

    # send_sms (v1.2.0): approval performs a real send through GoTo, and the
    # summary says so rather than announcing a placeholder.
    sms = out["sms_action"]
    assert sms["status"] == "executed"
    assert sms["result"]["summary"].startswith("Sent an SMS")
    assert "placeholder" not in sms["result"]["summary"].lower()
    # `delivered: True` in the tool's data payload is asserted in test_goto_sms;
    # here the point is the gated round-trip, so assert the send itself happened.
    assert out["sms_sent"] == [("+16195550101", "Hello from the test")]

    # registry: all new tools present; cache breakpoint on the last entry only.
    names = [d["name"] for d in out["tool_defs"]]
    for n in [
        "update_lead_status", "update_client_status", "create_schedule",
        "cancel_schedule", "create_task", "send_sms", "send_email",
    ]:
        assert n in names
    with_bp = [i for i, d in enumerate(out["tool_defs"]) if "cache_control" in d]
    assert with_bp == [len(out["tool_defs"]) - 1]
