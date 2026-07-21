"""Leads view API (Module 9a, Task 1), gated on NEXUS_APP_DB_URL.

Drives the real router via the app-client pattern (httpx ASGITransport + a minted
tenant JWT), the same shape as test_tasks_api. Proves: create -> row status 'new'
+ a plain-language, entity-linked lead.created event; PATCH status -> a truthful
lead.stage_changed; PATCH a basic field -> one lead.updated naming the field; a
no-op PATCH emits nothing; invalid status -> 422; list filters (status/source/q)
+ total + offset paging + limit cap; facets return seeded sources/regions; 401
without a token; and RLS isolation (the probe tenant's lead is invisible).

Seeded rows are read-only; every lead the test creates is deleted afterward
(events are immutable and left in place).
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

NORTH_COUNTY = "11111111-0000-0000-0000-000000000001"


async def _events_for(conn, entity_id, event_type=None):
    from psycopg.rows import dict_row

    sql = ("select event_type, source_system, payload from public.events "
           "where entity_type='lead' and entity_id=%s")
    params = [entity_id]
    if event_type:
        sql += " and event_type=%s"
        params.append(event_type)
    sql += " order by created_at"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
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
            out["noauth_code"] = (await noauth.get("/api/leads")).status_code
            await noauth.aclose()

            # --- facets: seeded sources + regions ---
            facets = (await ac.get("/api/leads/facets")).json()
            out["facet_sources"] = facets["sources"]
            out["facet_region_names"] = [r["name"] for r in facets["regions"]]

            # --- create: name required ---
            missing = await ac.post("/api/leads", json={"name": "  "})
            out["missing_name_code"] = missing.status_code

            # --- create: valid, tagged name so we can isolate it ---
            created = await ac.post("/api/leads", json={
                "name": f"Test Lead {token}",
                "phone": "+15625551234",
                "email": f"{token}@example.com",
                "source": f"src-{token}",
                "region_id": NORTH_COUNTY,
            })
            out["create_code"] = created.status_code
            lead = created.json()
            out["create_status"] = lead["status"]
            out["create_region_name"] = lead["region_name"]
            lead_id = lead["id"]
            out["created_ids"].append(lead_id)

            # --- bad region id on create -> 422 ---
            bad_region = await ac.post("/api/leads", json={
                "name": f"Bad {token}", "region_id": str(uuid.uuid4()),
            })
            out["bad_region_code"] = bad_region.status_code

            # --- PATCH status -> lead.stage_changed with truthful {from,to} ---
            ps = await ac.patch(f"/api/leads/{lead_id}", json={"status": "contacted"})
            out["patch_status_code"] = ps.status_code
            out["patch_status_value"] = ps.json()["status"]

            # --- invalid status -> 422 ---
            bad_status = await ac.patch(f"/api/leads/{lead_id}", json={"status": "nope"})
            out["bad_status_code"] = bad_status.status_code

            # --- PATCH a basic field -> lead.updated naming it ---
            pf = await ac.patch(f"/api/leads/{lead_id}", json={"phone": "+15625559999"})
            out["patch_field_code"] = pf.status_code

            # --- no-op PATCH (same phone) emits nothing ---
            await ac.patch(f"/api/leads/{lead_id}", json={"phone": "+15625559999"})

            # --- list filters: status=contacted + source=src-token + q=token ---
            filtered = (await ac.get("/api/leads", params={
                "status": "contacted", "source": f"src-{token}", "q": token,
            })).json()
            out["filtered_ids"] = [x["id"] for x in filtered["leads"]]
            out["filtered_total"] = filtered["total"]

            # --- offset paging: create a second matching lead, page size 1 ---
            created2 = await ac.post("/api/leads", json={
                "name": f"Test Lead2 {token}", "source": f"src-{token}",
            })
            out["created_ids"].append(created2.json()["id"])
            page0 = (await ac.get("/api/leads", params={
                "source": f"src-{token}", "limit": 1, "offset": 0})).json()
            page1 = (await ac.get("/api/leads", params={
                "source": f"src-{token}", "limit": 1, "offset": 1})).json()
            out["page_total"] = page0["total"]
            out["page0_len"] = len(page0["leads"])
            out["page_ids_distinct"] = (
                page0["leads"][0]["id"] != page1["leads"][0]["id"]
            )

            # --- limit cap: 500 clamps to <=100 ---
            capped = (await ac.get("/api/leads", params={"limit": 500})).json()
            out["capped_ok"] = len(capped["leads"]) <= 100

            # --- events written for the created lead ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                out["created_events"] = await _events_for(conn, lead_id, "lead.created")
                out["stage_events"] = await _events_for(conn, lead_id, "lead.stage_changed")
                out["updated_events"] = await _events_for(conn, lead_id, "lead.updated")

            # --- RLS: probe tenant's seeded lead invisible through demo API ---
            allrows = (await ac.get("/api/leads", params={"limit": 100})).json()
            out["probe_invisible"] = "bbbbbbbb-0000-0000-0000-000000000001" not in {
                x["id"] for x in allrows["leads"]
            }

        # cleanup created leads (events immutable, left in place)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for lid in out["created_ids"]:
                await conn.execute("delete from public.leads where id=%s", (lid,))
        return out
    finally:
        await db.close_pool()


def test_leads_api():
    out = asyncio.run(_scenario())

    assert out["noauth_code"] == 401  # fails closed without a token

    # facets reflect seed data
    # Harold's/Estelle's seed sources are now referral-partner names (Module 17
    # renamed them from the old flat "referral" so the enrichment join has a target).
    assert "website" in out["facet_sources"] and "St. Mary's Hospital" in out["facet_sources"]
    assert "North County" in out["facet_region_names"]

    # create
    assert out["missing_name_code"] == 422
    assert out["create_code"] == 201
    assert out["create_status"] == "new"  # status always starts 'new'
    assert out["create_region_name"] == "North County"  # left join resolved
    assert out["bad_region_code"] == 422

    # created lead.created event: entity-linked, plain summary, source_system=user
    ce = out["created_events"]
    assert len(ce) == 1
    assert ce[0]["source_system"] == "user"
    assert f"Test Lead {out['token']}" in ce[0]["payload"]["summary"]

    # stage change
    assert out["patch_status_code"] == 200
    assert out["patch_status_value"] == "contacted"
    assert out["bad_status_code"] == 422
    se = out["stage_events"]
    assert len(se) == 1
    assert se[0]["payload"]["from"] == "new" and se[0]["payload"]["to"] == "contacted"
    assert "New" in se[0]["payload"]["summary"] and "Contacted" in se[0]["payload"]["summary"]

    # basic field update -> exactly one lead.updated naming the field (no-op emitted none)
    assert out["patch_field_code"] == 200
    ue = out["updated_events"]
    assert len(ue) == 1
    assert "phone" in ue[0]["payload"]["fields"]

    # filters
    assert out["filtered_ids"] == [out["created_ids"][0]]
    assert out["filtered_total"] == 1

    # offset paging over the two src-token leads
    assert out["page_total"] == 2
    assert out["page0_len"] == 1
    assert out["page_ids_distinct"]

    assert out["capped_ok"]
    assert out["probe_invisible"]


# ---------------------------------------------------------------------------
# Task 2 (9b) — funnel metrics
# ---------------------------------------------------------------------------
async def _metrics_scenario():
    from app import db
    from app.main import app

    token = uuid.uuid4().hex[:8]
    out = {"created_ids": []}

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        # 401 without a token
        noauth = httpx.AsyncClient(transport=transport, base_url="http://t")
        out["noauth_code"] = (await noauth.get("/api/leads/metrics")).status_code
        await noauth.aclose()

        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # A fresh lead moved to converted -> a stage_changed→converted event with
            # created_at ≈ now, so avg_days_to_convert is non-null and plausible (~0).
            created = await ac.post("/api/leads", json={
                "name": f"Metrics Lead {token}", "source": f"metric-{token}"})
            lead_id = created.json()["id"]
            out["created_ids"].append(lead_id)
            await ac.patch(f"/api/leads/{lead_id}", json={"status": "converted"})

            out["metrics"] = (await ac.get("/api/leads/metrics")).json()

            # raw counts to cross-check the endpoint's arithmetic
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor() as cur:
                    await cur.execute("select count(*) from public.leads")
                    out["db_total"] = (await cur.fetchone())[0]
                    await cur.execute(
                        "select count(*) from public.leads where status='converted'"
                    )
                    out["db_converted"] = (await cur.fetchone())[0]

        # probe tenant has one lead, zero conversions -> null avg, 0 rate, no 500
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t",
            headers=bearer_headers(PROBE_TENANT),
        ) as pc:
            r = await pc.get("/api/leads/metrics")
            out["probe_code"] = r.status_code
            out["probe_metrics"] = r.json()

        async with db.tenant_tx(DEMO_TENANT) as conn:
            for lid in out["created_ids"]:
                await conn.execute("delete from public.leads where id=%s", (lid,))
        return out
    finally:
        await db.close_pool()


def test_lead_metrics():
    out = asyncio.run(_metrics_scenario())

    assert out["noauth_code"] == 401

    m = out["metrics"]
    # all seven stages present, counts sum to the total lead count
    assert [s["stage"] for s in m["stages"]] == [
        "new", "contact_attempted", "contacted", "visit_scheduled",
        "visit_completed", "converted", "lost",
    ]
    assert sum(s["count"] for s in m["stages"]) == out["db_total"]

    # conversion rate matches converted ÷ total
    expected_rate = round(100.0 * out["db_converted"] / out["db_total"], 1)
    assert m["conversion_rate"] == expected_rate

    # our fresh lead is within the last 7 days
    assert m["new_last_7_days"] >= 1

    # the move-to-converted event makes avg_days_to_convert non-null and plausible
    assert m["avg_days_to_convert"] is not None
    assert m["avg_days_to_convert"] >= 0

    # top_sources ordered by count desc
    counts = [s["count"] for s in m["top_sources"]]
    assert counts == sorted(counts, reverse=True)

    # empty-ish tenant (no conversions): 200 with nulls/zeroes, not a 500
    assert out["probe_code"] == 200
    assert out["probe_metrics"]["avg_days_to_convert"] is None
    assert out["probe_metrics"]["conversion_rate"] == 0.0
