"""Referrals dashboard (Module 17), gated on NEXUS_APP_DB_URL.

Three groups, mirroring the plan's Tasks 1–3:

  * SCHEMA/SEEDS (Task 1) — the referral_partners table exists with its unique
    (tenant, name) join key and category CHECK, the two seeded partners join the two
    renamed lead sources (Harold -> St. Mary's Hospital, Estelle -> Sunrise Senior
    Living), and a partner written by the demo tenant is invisible to the probe.
  * SEAM (Task 2) — referral_metrics returns hand-checked rows (St. Mary's 1 lead/0
    converted; Sunrise 1 lead/1 converted/100%/hours_won = Estelle's 25), untracked
    sources carry partner=null, monthly buckets are zero-filled to `months`, and an
    empty-ish tenant yields zeroes without a 500.
  * API (Task 3) — metrics endpoint, partner CRUD (create/409/PATCH-names-fields/
    no-op-silence/delete-leaves-source-untracked), 401 without a token, and
    cross-tenant 404 on PATCH/DELETE (RLS).

Created rows are deleted afterward; events are immutable and left in place.

NOTE (deviation from the plan's Task-2 note): the note said best_converter stays
null on the seed, but the seed actually has 3 `website` leads, so the ≥3-lead
threshold (decision 3) surfaces website at 33.3%. The code follows the explicit
payload contract; the test asserts the real value.
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

ST_MARYS = "ee000000-0000-0000-0000-000000000001"
SUNRISE = "ee000000-0000-0000-0000-000000000002"
ESTELLE_HOURS = 25.0
WALTER_HOURS = 40.0


async def _events_for(conn, entity_id, event_type=None):
    from psycopg.rows import dict_row

    sql = ("select event_type, source_system, payload from public.events "
           "where entity_id=%s")
    params = [entity_id]
    if event_type:
        sql += " and event_type=%s"
        params.append(event_type)
    sql += " order by created_at"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


# ===========================================================================
# Task 1 — schema + seeds + RLS
# ===========================================================================
async def _schema_scenario():
    from psycopg.rows import dict_row

    from app import db

    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select name, category from public.referral_partners order by name"
                )
                out["partners"] = await cur.fetchall()

                # Seeded partners join the two renamed lead sources by exact name.
                await cur.execute(
                    """select p.name as partner, count(l.id) as leads
                         from public.referral_partners p
                         left join public.leads l on l.source = p.name
                        group by p.name order by p.name"""
                )
                out["joins"] = {r["partner"]: r["leads"] for r in await cur.fetchall()}

            # Duplicate (tenant, name) is rejected by the unique index.
            try:
                async with conn.transaction():
                    await conn.execute(
                        "insert into public.referral_partners (tenant_id, name) "
                        "values (app.current_tenant_id(), %s)",
                        ("St. Mary's Hospital",),
                    )
                out["dup_rejected"] = False
            except Exception:
                out["dup_rejected"] = True

            # A bad category is rejected by the CHECK.
            try:
                async with conn.transaction():
                    await conn.execute(
                        "insert into public.referral_partners (tenant_id, name, category) "
                        "values (app.current_tenant_id(), %s, %s)",
                        (f"bad-cat-{uuid.uuid4().hex[:6]}", "clinic"),
                    )
                out["bad_category_rejected"] = False
            except Exception:
                out["bad_category_rejected"] = True

            probe_name = f"probe-visible-{uuid.uuid4().hex[:6]}"
            probe_id = str(uuid.uuid4())
            await conn.execute(
                "insert into public.referral_partners (id, tenant_id, name) "
                "values (%s, app.current_tenant_id(), %s)",
                (probe_id, probe_name),
            )

        # ...invisible to the probe tenant.
        async with db.tenant_tx(PROBE_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.referral_partners where id=%s", (probe_id,)
                )
                out["probe_sees"] = (await cur.fetchone())[0]

        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.referral_partners where id=%s", (probe_id,)
            )
    finally:
        await db.close_pool()
    return out


def test_partner_schema_and_seeds():
    r = asyncio.run(_schema_scenario())
    names = {p["name"]: p["category"] for p in r["partners"]}
    assert names.get("St. Mary's Hospital") == "hospital"
    assert names.get("Sunrise Senior Living") == "senior_living"
    # Enrichment-by-name: each seeded partner joins exactly its one renamed lead.
    assert r["joins"]["St. Mary's Hospital"] == 1
    assert r["joins"]["Sunrise Senior Living"] == 1
    assert r["dup_rejected"] is True
    assert r["bad_category_rejected"] is True
    assert r["probe_sees"] == 0


# ===========================================================================
# Task 2 — seam
# ===========================================================================
async def _seam_scenario():
    from app import db
    from app.services.views.referrals import referral_metrics

    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["metrics"] = await referral_metrics(conn, months=6)
        async with db.tenant_tx(PROBE_TENANT) as conn:
            out["probe"] = await referral_metrics(conn, months=6)
    finally:
        await db.close_pool()
    return out


def _row(metrics, source):
    return next((s for s in metrics["sources"] if s["source"] == source), None)


def test_seam_hand_computed_rows():
    r = asyncio.run(_seam_scenario())
    m = r["metrics"]

    sunrise = _row(m, "Sunrise Senior Living")
    assert sunrise is not None
    assert sunrise["partner"] is not None
    assert sunrise["partner"]["category"] == "senior_living"
    assert sunrise["leads_total"] == 1
    assert sunrise["converted"] == 1
    assert sunrise["conversion_rate"] == 100.0
    assert sunrise["hours_won"] == ESTELLE_HOURS

    st_marys = _row(m, "St. Mary's Hospital")
    assert st_marys is not None
    assert st_marys["partner"] is not None
    assert st_marys["leads_total"] == 1
    assert st_marys["converted"] == 0
    assert st_marys["conversion_rate"] == 0.0
    assert st_marys["hours_won"] == 0.0

    # Untracked seed sources appear with partner=null.
    for src in ("website", "phone"):
        row = _row(m, src)
        assert row is not None, f"{src} missing"
        assert row["partner"] is None

    # website: Margaret (new) + Walter (converted) + Raymond (lost) = 3 leads,
    # Walter's client carries 40 authorized hours.
    website = _row(m, "website")
    assert website["leads_total"] == 3
    assert website["hours_won"] == WALTER_HOURS

    # Monthly is zero-filled to exactly `months` buckets on every row.
    assert len(m["months"]) == 6
    assert len(m["monthly"]) == 6
    for s in m["sources"]:
        assert len(s["monthly"]) == 6

    assert m["totals"]["tracked_partners"] == 2
    # total_hours_won = website's Walter (40) + Sunrise's Estelle (25).
    assert m["totals"]["total_hours_won"] == WALTER_HOURS + ESTELLE_HOURS
    # Only website clears the ≥3-lead bar on the seed (see module note).
    bc = m["totals"]["best_converter"]
    assert bc is not None and bc["source"] == "website"
    assert bc["conversion_rate"] == 33.3

    # Empty-ish tenant: no partners, no won hours, no best converter, no 500.
    p = r["probe"]["totals"]
    assert p["tracked_partners"] == 0
    assert p["total_hours_won"] == 0.0
    assert p["best_converter"] is None


# ===========================================================================
# Task 3 — API
# ===========================================================================
async def _api_scenario():
    from app import db
    from app.main import app

    out: dict = {}
    created: dict = {}
    token = uuid.uuid4().hex[:6]
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # --- 401 without a token ---
            noauth = httpx.AsyncClient(transport=transport, base_url="http://t")
            out["noauth_code"] = (await noauth.get("/api/referrals/metrics")).status_code
            await noauth.aclose()

            # --- metrics endpoint matches the seam shape/values ---
            out["metrics"] = (await ac.get("/api/referrals/metrics")).json()
            out["metrics_clamp_code"] = (
                await ac.get("/api/referrals/metrics?months=999")
            ).status_code

            # --- create ---
            name = f"api-partner-{token}"
            resp = await ac.post("/api/referrals/partners", json={
                "name": name, "category": "home_health",
                "contact_name": "Jo Rivera", "phone": "+16195559090",
            })
            out["create_code"] = resp.status_code
            created["partner"] = resp.json()
            pid = created["partner"]["id"]

            # duplicate name -> 409; bad category -> 422
            out["dup_code"] = (await ac.post("/api/referrals/partners", json={
                "name": name})).status_code
            out["bad_cat_code"] = (await ac.post("/api/referrals/partners", json={
                "name": f"other-{token}", "category": "clinic"})).status_code

            # --- PATCH changed fields -> ONE updated event naming them ---
            out["patch"] = (await ac.patch(f"/api/referrals/partners/{pid}", json={
                "contact_name": "Jo Rivera-Smith", "phone": "+16195551234",
            })).json()
            # --- no-op PATCH emits nothing ---
            await ac.patch(f"/api/referrals/partners/{pid}", json={
                "contact_name": "Jo Rivera-Smith"})

            # --- the tracked-but-quiet partner appears as a source row ---
            after_create = (await ac.get("/api/referrals/metrics")).json()
            out["quiet_row"] = _row(after_create, name)

            # --- Track an existing untracked source ("phone"), then delete it ---
            tracked = (await ac.post("/api/referrals/partners", json={
                "name": "phone", "category": "other"})).json()
            created["phone_partner_id"] = tracked["id"]
            m_tracked = (await ac.get("/api/referrals/metrics")).json()
            out["phone_tracked"] = _row(m_tracked, "phone")

            out["delete_code"] = (await ac.delete(
                f"/api/referrals/partners/{tracked['id']}")).status_code
            m_untracked = (await ac.get("/api/referrals/metrics")).json()
            out["phone_untracked"] = _row(m_untracked, "phone")

            out["delete_missing_code"] = (await ac.delete(
                f"/api/referrals/partners/{uuid.uuid4()}")).status_code

            # --- partner list ---
            out["list"] = (await ac.get("/api/referrals/partners")).json()

        # --- cross-tenant: the probe tenant cannot touch the demo partner ---
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t",
            headers=bearer_headers(PROBE_TENANT),
        ) as probe:
            out["probe_patch_code"] = (await probe.patch(
                f"/api/referrals/partners/{pid}", json={"notes": "hi"})).status_code
            out["probe_delete_code"] = (await probe.delete(
                f"/api/referrals/partners/{pid}")).status_code

        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["events"] = await _events_for(conn, pid)
            await conn.execute(
                "delete from public.referral_partners where id=%s", (pid,)
            )
    finally:
        await db.close_pool()
    return out


@pytest.fixture(scope="module")
def api():
    return asyncio.run(_api_scenario())


def test_auth_and_metrics(api):
    assert api["noauth_code"] == 401
    assert api["metrics_clamp_code"] == 200  # months clamped, not rejected
    sunrise = _row(api["metrics"], "Sunrise Senior Living")
    assert sunrise["hours_won"] == ESTELLE_HOURS
    assert sunrise["conversion_rate"] == 100.0


def test_create_and_conflicts(api):
    assert api["create_code"] == 201
    assert api["dup_code"] == 409
    assert api["bad_cat_code"] == 422
    # A brand-new partner with no matching leads still surfaces as a source row.
    assert api["quiet_row"] is not None
    assert api["quiet_row"]["leads_total"] == 0
    assert api["quiet_row"]["partner"] is not None


def test_patch_and_events(api):
    assert api["patch"]["contact_name"] == "Jo Rivera-Smith"
    types = [e["event_type"] for e in api["events"]]
    assert types.count("referral_partner.created") == 1
    # ONE update event for the two-field PATCH; the no-op repeat emitted nothing.
    updated = [e for e in api["events"] if e["event_type"] == "referral_partner.updated"]
    assert len(updated) == 1
    assert set(updated[0]["payload"]["fields"]) == {"contact_name", "phone"}
    assert all(e["source_system"] == "user" for e in api["events"])


def test_delete_leaves_source_untracked(api):
    assert api["phone_tracked"]["partner"] is not None
    assert api["delete_code"] == 204
    # After delete, the "phone" source is still there — just no longer tracked.
    assert api["phone_untracked"] is not None
    assert api["phone_untracked"]["partner"] is None
    assert api["delete_missing_code"] == 404
    assert any(p["name"] == "phone" for p in api["list"]) is False


def test_cross_tenant_isolation(api):
    # RLS hides the demo partner from the probe tenant -> not found, not forbidden.
    assert api["probe_patch_code"] == 404
    assert api["probe_delete_code"] == 404
