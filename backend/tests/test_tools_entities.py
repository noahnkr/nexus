"""Entity read tools + the execute_tool audit seam (Module 2, Tasks 1–2).

Runs against the real nexus_app RLS path (skipped until NEXUS_APP_DB_URL is set),
so it proves: handlers answer from seed data, resolve id arrays to names, reject
bad ids cleanly, and — critically — never cross the tenant boundary. The seam
test proves execute_tool writes an audit event, refuses gated tools, and turns a
raising handler into an is_error result while still auditing.
"""
import asyncio
import uuid

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT

pytestmark = pytest.mark.skipif(
    not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

WALTER_CLIENT = "44444444-0000-0000-0000-000000000001"


async def _call(conn, name, args):
    from app.services.tools import get_tool

    return await get_tool(name).handler(conn, args)


# ---------------------------------------------------------------------------
# entity tools against seed data
# ---------------------------------------------------------------------------
async def _entities_scenario():
    from app import db

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            leads_new = await _call(conn, "list_leads", {"status": "new"})
            client = await _call(conn, "get_client", {"client_id": WALTER_CLIENT})
            dementia = await _call(conn, "list_resources", {"qualification": "Dementia Care"})
            scheduled = await _call(conn, "list_schedules", {"status": "scheduled"})
            all_leads = await _call(conn, "list_leads", {"limit": 100})

            from app.services.tools import execute_tool

            bad_uuid = await execute_tool(conn, DEMO_TENANT, "get_lead", {"lead_id": "nope"})
        return leads_new, client, dementia, scheduled, all_leads, bad_uuid
    finally:
        await db.close_pool()


def test_entity_tools():
    leads_new, client, dementia, scheduled, all_leads, bad_uuid = asyncio.run(
        _entities_scenario()
    )

    # list_leads(status='new') -> Margaret Ellison in North County.
    margaret = [l for l in leads_new.data["leads"] if l["name"] == "Margaret Ellison"]
    assert margaret, "Margaret Ellison should be a 'new' lead"
    assert margaret[0]["region"] == "North County"
    assert all(l["status"] == "new" for l in leads_new.data["leads"])

    # get_client(Walter) -> CRM external id + an upcoming scheduled visit.
    c = client.data["client"]
    assert c["name"] == "Walter Grimes"
    ext = [e["external_id"] for e in c["external_ids"]]
    assert "CRM-CLIENT-2001" in ext
    assert len(c["upcoming_schedules"]) >= 1

    # list_resources(qualification='Dementia Care') -> Carmen Ruiz + Evelyn Park,
    # with qualification NAMES (not UUIDs).
    names = {r["name"] for r in dementia.data["resources"]}
    assert names == {"Carmen Ruiz", "Evelyn Park"}
    for r in dementia.data["resources"]:
        assert "Dementia Care" in r["qualifications"]
        for q in r["qualifications"]:
            # names, never raw uuids
            with pytest.raises(ValueError):
                uuid.UUID(q)

    # list_schedules(status='scheduled') ordered by start_time.
    starts = [s["start_time"] for s in scheduled.data["schedules"]]
    assert starts == sorted(starts)
    assert all(s["status"] == "scheduled" for s in scheduled.data["schedules"])

    # bad uuid -> clean is_error result (not an exception).
    assert bad_uuid.is_error is True

    # tenant isolation: the probe tenant's lead is never visible under demo.
    all_names = {l["name"] for l in all_leads.data["leads"]}
    assert "Probe Lead" not in all_names


# ---------------------------------------------------------------------------
# execute_tool audit seam
# ---------------------------------------------------------------------------
async def _seam_scenario():
    from app import db
    from app.services.tools import ToolDef, execute_tool
    from app.services.tools.core import ToolResult
    from app.services.tools.registry import _REGISTRY, register

    sfx = uuid.uuid4().hex[:8]
    ok_name, gated_name, boom_name = f"t_ok_{sfx}", f"t_gated_{sfx}", f"t_boom_{sfx}"
    schema = {"type": "object", "properties": {}}

    async def ok_handler(conn, args):
        return ToolResult("seam-ok-summary", {"value": 42})

    async def boom_handler(conn, args):
        raise RuntimeError("kaboom")

    register(ToolDef(ok_name, "throwaway", schema, ok_handler, True))
    register(ToolDef(gated_name, "throwaway", schema, ok_handler, False))
    register(ToolDef(boom_name, "throwaway", schema, boom_handler, True))

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            ok = await execute_tool(conn, DEMO_TENANT, ok_name, {"x": 1})
            gated = await execute_tool(conn, DEMO_TENANT, gated_name, {})
            boom = await execute_tool(conn, DEMO_TENANT, boom_name, {})
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select payload->>'summary' from public.events "
                    "where event_type='tool.called' and payload->>'tool_name'=%s",
                    (ok_name,),
                )
                ok_ev = await cur.fetchone()
                await cur.execute(
                    "select count(*) from public.events "
                    "where event_type='tool.called' and payload->>'tool_name'=%s",
                    (boom_name,),
                )
                boom_ev_count = (await cur.fetchone())[0]
        return ok, gated, boom, ok_ev, boom_ev_count
    finally:
        for n in (ok_name, gated_name, boom_name):
            _REGISTRY.pop(n, None)
        await db.close_pool()


def test_execute_tool_seam():
    ok, gated, boom, ok_ev, boom_ev_count = asyncio.run(_seam_scenario())

    # safe tool: returns its result AND writes a plain-language audit event.
    assert ok.is_error is False
    assert ok.summary == "seam-ok-summary"
    assert ok_ev is not None and ok_ev[0] == "seam-ok-summary"

    # unsafe tool: queued for approval — a success (not an error), with a task.
    assert gated.is_error is False
    assert gated.data["status"] == "queued"
    assert gated.data.get("task_id") and gated.data.get("pending_action_id")
    assert "approval" in gated.summary.lower()

    # raising handler: is_error result, and the event is still written.
    assert boom.is_error is True
    assert boom_ev_count >= 1
