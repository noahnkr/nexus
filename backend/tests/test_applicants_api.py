"""Caregivers view API + tools (Module 10a), gated on NEXUS_APP_DB_URL.

Drives the real router via the app-client pattern (httpx ASGITransport + a minted
tenant JWT), mirroring test_leads_api. Proves: create -> stage 'applied' + a plain,
entity-linked applicant.created event; PATCH stage -> a truthful
applicant.stage_changed; PATCH to 'hired' -> an atomic caregiver (resources) row
with copied quals/regions + applicant_id provenance + a resource.created event, the
response carrying promoted_resource_id; a second move-out/move-back-to-hired does
NOT duplicate the caregiver; a basic/array field PATCH -> one applicant.updated;
a no-op PATCH emits nothing; invalid stage -> 422; list filters + total + offset
paging + limit cap; facets return seeded sources/regions/qualifications; 401 without
a token; RLS isolation (the probe tenant is invisible).

The tool section proves list_applicants stage filtering, get_applicant name
resolution, that update_applicant_stage queues a gated action whose task names the
applicant (handler does NOT run), and that approving it drives the SAME
move_stage() path (identical applicant.stage_changed) as the REST route.

Every applicant/resource the test creates is deleted afterward (events are
immutable and left in place).
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

NORTH_COUNTY = "11111111-0000-0000-0000-000000000001"
CENTRAL = "11111111-0000-0000-0000-000000000002"
CNA = "22222222-0000-0000-0000-000000000001"
HHA = "22222222-0000-0000-0000-000000000002"
PROBE_APPLICANT = None  # probe tenant seeds no applicant; RLS proven via list scope


async def _events_for(conn, entity_id, event_type=None):
    from psycopg.rows import dict_row

    sql = ("select event_type, source_system, payload, entity_type, entity_id "
           "from public.events where entity_id=%s")
    params = [entity_id]
    if event_type:
        sql += " and event_type=%s"
        params.append(event_type)
    sql += " order by created_at"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def _resources_for(conn, applicant_id):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, name, qualification_ids, region_ids, availability, applicant_id "
            "from public.resources where applicant_id=%s",
            (applicant_id,),
        )
        return await cur.fetchall()


async def _scenario():
    from app import db
    from app.main import app

    token = uuid.uuid4().hex[:8]
    out = {"token": token, "created_ids": []}

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # --- 401 without a token ---
            noauth = httpx.AsyncClient(transport=transport, base_url="http://t")
            out["noauth_code"] = (await noauth.get("/api/applicants")).status_code
            await noauth.aclose()

            # --- facets: seeded sources + regions + qualifications ---
            facets = (await ac.get("/api/applicants/facets")).json()
            out["facet_sources"] = facets["sources"]
            out["facet_region_names"] = [r["name"] for r in facets["regions"]]
            out["facet_qual_names"] = [q["name"] for q in facets["qualifications"]]

            # --- create: name required ---
            missing = await ac.post("/api/applicants", json={"name": "  "})
            out["missing_name_code"] = missing.status_code

            # --- create: valid, with two qualifications + a region ---
            created = await ac.post("/api/applicants", json={
                "name": f"Test Applicant {token}",
                "phone": "+15625551234",
                "email": f"{token}@example.com",
                "source": f"src-{token}",
                "qualification_ids": [CNA, HHA],
                "region_ids": [NORTH_COUNTY],
            })
            out["create_code"] = created.status_code
            app_row = created.json()
            out["create_stage"] = app_row["stage"]
            out["create_qual_names"] = app_row["qualification_names"]
            out["create_region_names"] = app_row["region_names"]
            applicant_id = app_row["id"]
            out["created_ids"].append(applicant_id)

            # --- bad qualification id on create -> 422 ---
            bad = await ac.post("/api/applicants", json={
                "name": f"Bad {token}", "qualification_ids": [str(uuid.uuid4())]})
            out["bad_qual_code"] = bad.status_code

            # --- PATCH stage -> applicant.stage_changed with truthful {from,to} ---
            ps = await ac.patch(f"/api/applicants/{applicant_id}", json={"stage": "screening"})
            out["patch_stage_code"] = ps.status_code
            out["patch_stage_value"] = ps.json()["stage"]

            # --- invalid stage -> 422 ---
            out["bad_stage_code"] = (
                await ac.patch(f"/api/applicants/{applicant_id}", json={"stage": "nope"})
            ).status_code

            # --- PATCH a basic field (notes) -> applicant.updated naming it ---
            pf = await ac.patch(f"/api/applicants/{applicant_id}", json={"notes": "Called back."})
            out["patch_field_code"] = pf.status_code

            # --- no-op PATCH (same notes) emits nothing ---
            await ac.patch(f"/api/applicants/{applicant_id}", json={"notes": "Called back."})

            # --- PATCH quals (array) -> applicant.updated with qualification_ids ---
            await ac.patch(f"/api/applicants/{applicant_id}", json={"qualification_ids": [CNA]})

            # --- PATCH to hired -> caregiver created, response carries promotion ---
            hire = await ac.patch(f"/api/applicants/{applicant_id}", json={"stage": "hired"})
            out["hire_code"] = hire.status_code
            hj = hire.json()
            out["hire_stage"] = hj["stage"]
            out["promoted_resource_id"] = hj["promoted_resource_id"]
            out["promoted_resource_name"] = hj["promoted_resource_name"]

            # --- move out of hired then back -> no duplicate caregiver ---
            await ac.patch(f"/api/applicants/{applicant_id}", json={"stage": "rejected"})
            rehire = await ac.patch(f"/api/applicants/{applicant_id}", json={"stage": "hired"})
            out["rehire_promoted"] = rehire.json()["promoted_resource_id"]

            # --- list filters: stage=hired + source=src-token + q=token ---
            filtered = (await ac.get("/api/applicants", params={
                "stage": "hired", "source": f"src-{token}", "q": token})).json()
            out["filtered_ids"] = [x["id"] for x in filtered["applicants"]]
            out["filtered_total"] = filtered["total"]

            # --- offset paging over two src-token applicants ---
            created2 = await ac.post("/api/applicants", json={
                "name": f"Test Applicant2 {token}", "source": f"src-{token}"})
            out["created_ids"].append(created2.json()["id"])
            page0 = (await ac.get("/api/applicants", params={
                "source": f"src-{token}", "limit": 1, "offset": 0})).json()
            page1 = (await ac.get("/api/applicants", params={
                "source": f"src-{token}", "limit": 1, "offset": 1})).json()
            out["page_total"] = page0["total"]
            out["page0_len"] = len(page0["applicants"])
            out["page_ids_distinct"] = (
                page0["applicants"][0]["id"] != page1["applicants"][0]["id"])

            # --- limit cap ---
            capped = (await ac.get("/api/applicants", params={"limit": 500})).json()
            out["capped_ok"] = len(capped["applicants"]) <= 100

            # --- events + resources for the hired applicant ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                out["created_events"] = await _events_for(conn, applicant_id, "applicant.created")
                out["stage_events"] = await _events_for(conn, applicant_id, "applicant.stage_changed")
                out["updated_events"] = await _events_for(conn, applicant_id, "applicant.updated")
                out["resources"] = await _resources_for(conn, applicant_id)
                out["resource_created_events"] = [
                    e for e in await _events_for(conn, str(out["resources"][0]["id"]))
                    if e["event_type"] == "resource.created"
                ] if out["resources"] else []

            # --- RLS: probe tenant's applicants invisible through demo API ---
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t",
                headers=bearer_headers(PROBE_TENANT),
            ) as pc:
                pr = (await pc.get("/api/applicants")).json()
                out["probe_total"] = pr["total"]

        # cleanup: resources (promoted) then applicants (events immutable, kept)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for aid in out["created_ids"]:
                await conn.execute("delete from public.resources where applicant_id=%s", (aid,))
                await conn.execute("delete from public.applicants where id=%s", (aid,))
        return out
    finally:
        await db.close_pool()


def test_applicants_api():
    out = asyncio.run(_scenario())

    assert out["noauth_code"] == 401

    assert "indeed" in out["facet_sources"] and "referral" in out["facet_sources"]
    assert "North County" in out["facet_region_names"]
    assert "CNA" in out["facet_qual_names"] and "HHA" in out["facet_qual_names"]

    # create
    assert out["missing_name_code"] == 422
    assert out["create_code"] == 201
    assert out["create_stage"] == "applied"  # stage always starts 'applied'
    assert set(out["create_qual_names"]) == {"CNA", "HHA"}  # resolved names
    assert out["create_region_names"] == ["North County"]
    assert out["bad_qual_code"] == 422

    # created event: entity-linked, plain, source_system=user
    ce = out["created_events"]
    assert len(ce) == 1
    assert ce[0]["source_system"] == "user"
    assert ce[0]["entity_type"] == "applicant"
    assert f"Test Applicant {out['token']}" in ce[0]["payload"]["summary"]

    # stage change
    assert out["patch_stage_code"] == 200
    assert out["patch_stage_value"] == "screening"
    assert out["bad_stage_code"] == 422
    se = out["stage_events"]
    # applied->screening, screening->hired, hired->rejected, rejected->hired
    assert len(se) == 4
    assert se[0]["payload"]["from"] == "applied" and se[0]["payload"]["to"] == "screening"
    assert "Applied" in se[0]["payload"]["summary"] and "Screening" in se[0]["payload"]["summary"]

    # basic/array update -> applicant.updated (notes + qualification_ids), no-op emitted none
    assert out["patch_field_code"] == 200
    ue = out["updated_events"]
    assert len(ue) == 2  # notes edit, then quals edit (the repeat notes was a no-op)
    assert "notes" in ue[0]["payload"]["fields"]
    assert "qualification_ids" in ue[1]["payload"]["fields"]

    # hire promotion: caregiver row with copied quals/regions + provenance + event
    assert out["hire_code"] == 200
    assert out["hire_stage"] == "hired"
    assert out["promoted_resource_id"] is not None
    assert out["promoted_resource_name"] == f"Test Applicant {out['token']}"
    res = out["resources"]
    assert len(res) == 1  # exactly one caregiver, even after move-out/move-back
    assert str(res[0]["id"]) == out["promoted_resource_id"]
    assert str(CNA) in [str(q) for q in res[0]["qualification_ids"]]  # quals copied
    assert str(NORTH_COUNTY) in [str(r) for r in res[0]["region_ids"]]  # regions copied
    assert str(res[0]["applicant_id"]) == out["created_ids"][0]  # provenance
    assert len(out["resource_created_events"]) == 1  # one resource.created

    # re-hire (move out then back) did NOT create a second caregiver
    assert out["rehire_promoted"] is None

    # filters (only the hired src-token applicant matches)
    assert out["filtered_ids"] == [out["created_ids"][0]]
    assert out["filtered_total"] == 1

    # offset paging over the two src-token applicants
    assert out["page_total"] == 2
    assert out["page0_len"] == 1
    assert out["page_ids_distinct"]

    assert out["capped_ok"]
    assert out["probe_total"] == 0  # probe tenant sees none of the demo applicants


# ---------------------------------------------------------------------------
# Task 1 (10b) — hiring metrics
# ---------------------------------------------------------------------------
async def _metrics_scenario():
    from app import db
    from app.main import app

    token = uuid.uuid4().hex[:8]
    out = {"created_ids": []}

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        noauth = httpx.AsyncClient(transport=transport, base_url="http://t")
        out["noauth_code"] = (await noauth.get("/api/applicants/metrics")).status_code
        await noauth.aclose()

        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # A fresh applicant moved to hired -> a stage_changed→hired event with
            # created_at ≈ now, so avg_days_to_hire is non-null and plausible (~0).
            created = await ac.post("/api/applicants", json={
                "name": f"Metrics Applicant {token}", "source": f"metric-{token}"})
            applicant_id = created.json()["id"]
            out["created_ids"].append(applicant_id)
            await ac.patch(f"/api/applicants/{applicant_id}", json={"stage": "hired"})

            out["metrics"] = (await ac.get("/api/applicants/metrics")).json()

            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor() as cur:
                    await cur.execute("select count(*) from public.applicants")
                    out["db_total"] = (await cur.fetchone())[0]
                    await cur.execute(
                        "select count(*) from public.applicants where stage='hired'")
                    out["db_hired"] = (await cur.fetchone())[0]

        # probe tenant has no applicants -> null avg, 0 rate, no 500
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers(PROBE_TENANT),
        ) as pc:
            r = await pc.get("/api/applicants/metrics")
            out["probe_code"] = r.status_code
            out["probe_metrics"] = r.json()

        async with db.tenant_tx(DEMO_TENANT) as conn:
            for aid in out["created_ids"]:
                await conn.execute("delete from public.resources where applicant_id=%s", (aid,))
                await conn.execute("delete from public.applicants where id=%s", (aid,))
        return out
    finally:
        await db.close_pool()


def test_applicant_metrics():
    out = asyncio.run(_metrics_scenario())

    assert out["noauth_code"] == 401

    m = out["metrics"]
    # all six stages present, counts sum to the total applicant count
    assert [s["stage"] for s in m["stages"]] == [
        "applied", "screening", "interview", "offer", "hired", "rejected"
    ]
    assert sum(s["count"] for s in m["stages"]) == out["db_total"]

    # hire rate matches hired ÷ total
    expected_rate = round(100.0 * out["db_hired"] / out["db_total"], 1)
    assert m["hire_rate"] == expected_rate

    # our fresh applicant is within the last 7 days
    assert m["new_last_7_days"] >= 1

    # the move-to-hired event makes avg_days_to_hire non-null and plausible
    assert m["avg_days_to_hire"] is not None
    assert m["avg_days_to_hire"] >= 0

    # top_sources ordered by count desc
    counts = [s["count"] for s in m["top_sources"]]
    assert counts == sorted(counts, reverse=True)

    # empty tenant: 200 with nulls/zeroes, not a 500
    assert out["probe_code"] == 200
    assert out["probe_metrics"]["avg_days_to_hire"] is None
    assert out["probe_metrics"]["hire_rate"] == 0.0


# ---------------------------------------------------------------------------
# Tool layer: read tools + the gated update_applicant_stage (Task 1)
# ---------------------------------------------------------------------------
async def _tools_scenario():
    from app import db
    from app.services.tools import execute_tool
    from app.services.tools.registry import get_tool

    token = uuid.uuid4().hex[:8]
    out = {"token": token, "created_ids": []}

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            # a tagged applicant at 'applied' with a known qualification
            async with conn.cursor() as cur:
                await cur.execute(
                    """insert into public.applicants
                         (tenant_id, name, source, stage, qualification_ids)
                       values (%s, %s, %s, 'applied', %s) returning id""",
                    (DEMO_TENANT, f"Tool Applicant {token}", f"tool-{token}", [CNA]),
                )
                applicant_id = str((await cur.fetchone())[0])
            out["created_ids"].append(applicant_id)

            # list_applicants filtered by stage + source
            lr = await execute_tool(
                conn, DEMO_TENANT, "list_applicants",
                {"stage": "applied", "source": f"tool-{token}"}, source_system="chat",
            )
            out["list_ids"] = [a["id"] for a in lr.data["applicants"]]

            # get_applicant resolves qualification ids -> names
            gr = await execute_tool(
                conn, DEMO_TENANT, "get_applicant",
                {"applicant_id": applicant_id}, source_system="chat",
            )
            out["get_qual_names"] = gr.data["applicant"]["qualifications"]

            # update_applicant_stage is gated: it QUEUES (handler does not run)
            ur = await execute_tool(
                conn, DEMO_TENANT, "update_applicant_stage",
                {"applicant_id": applicant_id, "stage": "offer"}, source_system="chat",
            )
            out["queued_status"] = ur.data.get("status")
            out["queue_is_error"] = ur.is_error
            task_id = ur.data["task_id"]
            from psycopg.rows import dict_row
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("select title from public.tasks where id=%s", (task_id,))
                out["task_title"] = (await cur.fetchone())["title"]
                await cur.execute("select stage from public.applicants where id=%s", (applicant_id,))
                out["stage_after_queue"] = (await cur.fetchone())["stage"]
            # cleanup the queued task/action
            await conn.execute("delete from public.pending_actions where task_id=%s", (task_id,))
            await conn.execute("delete from public.tasks where id=%s", (task_id,))

            # sanity: the tool exists and is gated
            out["tool_safe"] = get_tool("update_applicant_stage").safe

            for aid in out["created_ids"]:
                await conn.execute("delete from public.applicants where id=%s", (aid,))
        return out
    finally:
        await db.close_pool()


def test_applicant_tools():
    out = asyncio.run(_tools_scenario())
    assert out["list_ids"] == out["created_ids"]  # stage+source filter hit exactly it
    assert out["get_qual_names"] == ["CNA"]  # id resolved to name
    assert out["queued_status"] == "queued"  # gated tool queued, not executed
    assert out["queue_is_error"] is False  # a queued call is a success
    assert f"Tool Applicant {out['token']}" in out["task_title"]  # names the applicant
    assert "offer" in out["task_title"]
    assert out["stage_after_queue"] == "applied"  # handler did NOT move it
    assert out["tool_safe"] is False


# ---------------------------------------------------------------------------
# Shared path: approving the gated tool drives the SAME move_stage() (Task 2)
# ---------------------------------------------------------------------------
async def _approved_scenario():
    from app import db
    from app.services.approvals import approve_action
    from app.services.tools import execute_tool

    token = uuid.uuid4().hex[:8]
    out = {"token": token, "created_ids": []}

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """insert into public.applicants (tenant_id, name, stage)
                       values (%s, %s, 'screening') returning id""",
                    (DEMO_TENANT, f"Approve Applicant {token}"),
                )
                applicant_id = str((await cur.fetchone())[0])
            out["created_ids"].append(applicant_id)

            queued = await execute_tool(
                conn, DEMO_TENANT, "update_applicant_stage",
                {"applicant_id": applicant_id, "stage": "interview"}, source_system="mcp",
            )
            action_id = queued.data["pending_action_id"]
            task_id = queued.data["task_id"]

            await approve_action(conn, DEMO_TENANT, action_id, resolved_by="tester")

            from psycopg.rows import dict_row
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("select stage from public.applicants where id=%s", (applicant_id,))
                out["stage_after_approve"] = (await cur.fetchone())["stage"]
            out["stage_events"] = await _events_for(conn, applicant_id, "applicant.stage_changed")

            await conn.execute("delete from public.pending_actions where task_id=%s", (task_id,))
            await conn.execute("delete from public.tasks where id=%s", (task_id,))
            for aid in out["created_ids"]:
                await conn.execute("delete from public.applicants where id=%s", (aid,))
        return out
    finally:
        await db.close_pool()


def test_update_applicant_stage_approved():
    out = asyncio.run(_approved_scenario())
    # Approval executed move_stage(): the applicant advanced and the SAME
    # applicant.stage_changed event was emitted (source_system=mcp, the caller's).
    assert out["stage_after_approve"] == "interview"
    se = out["stage_events"]
    assert len(se) == 1
    assert se[0]["source_system"] == "mcp"
    assert se[0]["payload"]["from"] == "screening" and se[0]["payload"]["to"] == "interview"
