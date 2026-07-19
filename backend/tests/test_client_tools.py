"""Client oversight tools (Module 16a, Task 3), gated on NEXUS_APP_DB_URL.

Proves the four client tools go through the same `execute_tool` seam as every
other tool:
  * update_client_status QUEUES when called un-approved, and on approval runs
    through the clients seam — leaving the same `client.status_changed` event a
    coordinator's UI click would (the test_update_applicant_stage_approved shape).
  * record_visit_check_in / _out queue with a gate description naming the client,
    and on approval stamp the visit (check-out also completing it).
  * get_census is SAFE: it executes immediately, with no pending_action, and
    still writes its `tool.called` audit row.

Every row/task created is cleaned up; events are immutable and left in place.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

UTC = timezone.utc
START = datetime(2028, 9, 4, 8, 0, tzinfo=UTC)
END = datetime(2028, 9, 4, 12, 0, tzinfo=UTC)


async def _one(conn, sql, params):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def _scenario():
    from app import db
    from app.services.approvals import approve_action
    from app.services.tools import execute_tool

    ids = {k: str(uuid.uuid4()) for k in ("client", "resource", "visit")}
    sfx = uuid.uuid4().hex[:6]
    out: dict = {}
    task_ids: list[str] = []

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                """insert into public.clients
                     (id, tenant_id, name, status, payer, authorized_hours_per_week)
                   values (%s, app.current_tenant_id(), %s, 'active', 'medicaid', 20)""",
                (ids["client"], f"tool-client-{sfx}"),
            )
            await conn.execute(
                "insert into public.resources (id, tenant_id, name) "
                "values (%s, app.current_tenant_id(), %s)",
                (ids["resource"], f"tool-cg-{sfx}"),
            )
            await conn.execute(
                """insert into public.schedules
                     (id, tenant_id, resource_id, client_id, start_time, end_time, status)
                   values (%s, app.current_tenant_id(), %s, %s, %s, %s, 'scheduled')""",
                (ids["visit"], ids["resource"], ids["client"], START, END),
            )

            # --- update_client_status: gated, then approved ---
            queued = await execute_tool(
                conn, DEMO_TENANT, "update_client_status",
                {"client_id": ids["client"], "status": "hospital_hold"},
                source_system="chat",
            )
            out["status_queued"] = queued
            task_ids.append(queued.data["task_id"])
            out["status_task_title"] = (
                await _one(conn, "select title from public.tasks where id=%s",
                           (queued.data["task_id"],))
            )["title"]

            await approve_action(conn, DEMO_TENANT, queued.data["pending_action_id"])
            out["status_action"] = await _one(
                conn, "select status from public.pending_actions where id=%s",
                (queued.data["pending_action_id"],),
            )
            out["client_status"] = (
                await _one(conn, "select status from public.clients where id=%s",
                           (ids["client"],))
            )["status"]
            out["status_events"] = (
                await _one(
                    conn,
                    "select count(*) as n from public.events where entity_type='client' "
                    "and entity_id=%s and event_type='client.status_changed'",
                    (ids["client"],),
                )
            )["n"]

            # Back to active so the visit tools operate on a live client.
            back = await execute_tool(
                conn, DEMO_TENANT, "update_client_status",
                {"client_id": ids["client"], "status": "active"}, source_system="chat",
            )
            task_ids.append(back.data["task_id"])
            await approve_action(conn, DEMO_TENANT, back.data["pending_action_id"])

            # --- record_visit_check_in: gate description names the client ---
            ci = await execute_tool(
                conn, DEMO_TENANT, "record_visit_check_in",
                {"schedule_id": ids["visit"],
                 "time": (START + timedelta(minutes=5)).isoformat()},
                source_system="chat",
            )
            task_ids.append(ci.data["task_id"])
            out["checkin_task_title"] = (
                await _one(conn, "select title from public.tasks where id=%s",
                           (ci.data["task_id"],))
            )["title"]
            await approve_action(conn, DEMO_TENANT, ci.data["pending_action_id"])

            # --- record_visit_check_out: also completes the visit ---
            co = await execute_tool(
                conn, DEMO_TENANT, "record_visit_check_out",
                {"schedule_id": ids["visit"],
                 "time": (END + timedelta(minutes=15)).isoformat()},
                source_system="chat",
            )
            task_ids.append(co.data["task_id"])
            await approve_action(conn, DEMO_TENANT, co.data["pending_action_id"])

            out["visit"] = await _one(
                conn,
                "select status, check_in_at, check_out_at from public.schedules where id=%s",
                (ids["visit"],),
            )

            # --- get_census: safe, executes immediately, no gate ---
            census = await execute_tool(
                conn, DEMO_TENANT, "get_census", {}, source_system="chat"
            )
            out["census"] = census
            out["census_audit"] = (
                await _one(
                    conn,
                    "select count(*) as n from public.events where event_type='tool.called' "
                    "and payload->>'tool_name'='get_census'",
                    (),
                )
            )["n"]

            # --- get_client is enriched with the care picture ---
            await conn.execute(
                """insert into public.client_contacts
                     (tenant_id, client_id, name, relationship, is_primary)
                   values (app.current_tenant_id(), %s, %s, 'daughter', true)""",
                (ids["client"], f"contact-{sfx}"),
            )
            got = await execute_tool(
                conn, DEMO_TENANT, "get_client", {"client_id": ids["client"]},
                source_system="chat",
            )
            out["get_client"] = got.data["client"]

            # cleanup (schedules before resources/clients for the FKs)
            await conn.execute("delete from public.schedules where id=%s", (ids["visit"],))
            await conn.execute(
                "delete from public.client_contacts where client_id=%s", (ids["client"],)
            )
            await conn.execute("delete from public.resources where id=%s", (ids["resource"],))
            await conn.execute("delete from public.clients where id=%s", (ids["client"],))
            for tid in task_ids:
                await conn.execute(
                    "delete from public.pending_actions where task_id=%s", (tid,)
                )
                await conn.execute("delete from public.tasks where id=%s", (tid,))
    finally:
        await db.close_pool()
    return out


def test_client_tools_through_the_gate():
    r = asyncio.run(_scenario())

    # A queued gated call is a SUCCESSFUL tool result, not an error (CLAUDE.md).
    assert r["status_queued"].data["status"] == "queued"
    assert "hospital hold" in r["status_task_title"].lower()
    assert r["status_action"]["status"] == "executed"
    assert r["client_status"] == "hospital_hold"
    # Approval executed through the seam, so the event exists exactly once.
    assert r["status_events"] == 1

    assert "check-in" in r["checkin_task_title"].lower()

    status, check_in_at, check_out_at = (
        r["visit"]["status"], r["visit"]["check_in_at"], r["visit"]["check_out_at"]
    )
    assert status == "completed"
    assert check_in_at == START + timedelta(minutes=5)
    assert check_out_at == END + timedelta(minutes=15)


def test_get_census_is_safe_and_audited():
    r = asyncio.run(_scenario())
    census = r["census"]

    # Safe tools execute in-request: no queue, no pending action.
    assert census.data.get("status") != "queued"
    assert "pending_action_id" not in census.data
    assert census.data["active_clients"] >= 1
    for key in ("authorized_hours", "scheduled_hours", "delivered_hours",
                "open_hours", "leakage_hours", "by_region", "by_payer"):
        assert key in census.data
    assert "active client(s)" in census.summary
    assert r["census_audit"] >= 1


def test_get_client_carries_the_care_picture():
    r = asyncio.run(_scenario())
    client = r["get_client"]

    assert client["payer_label"] == "Medicaid"
    assert client["hours_this_week"]["authorized_hours"] == 20.0
    assert len(client["contacts"]) == 1
    assert client["contacts"][0]["relationship"] == "daughter"
    assert client["contacts"][0]["is_primary"] is True
