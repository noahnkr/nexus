"""Clients view API (Module 16a, Tasks 1 + 4), gated on NEXUS_APP_DB_URL.

Drives the real router via the app-client pattern (httpx ASGITransport + a minted
tenant JWT), mirroring test_applicants_api. Four groups:

  * SCHEMA/SEEDS (Task 1) — the oversight columns exist, the retired M0 statuses
    are rejected by the new CHECK, client_contacts is tenant-isolated, the EVV
    coherence CHECK holds, and the seeded completed visit carries both stamps.
  * DIRECTORY — list filters (status/payer/region/q) + total, facets, create,
    PATCH basic fields (one client.updated naming them), PATCH status (through
    change_status), no-op silence, 401, and cross-tenant RLS isolation.
  * CONTACTS — add/edit/delete round-trip, the primary-flag swap in one tx, and
    the client.updated event each write leaves ON THE CLIENT.
  * EVV ROUTES — check-in/check-out happy + reject paths, and the board feed
    carrying a server-computed `evv` flag for an overdue visit.

Every row the test creates is deleted afterward (events are immutable and left in
place). Census assertions are deltas — see test_client_seam for why.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

NORTH_COUNTY = "11111111-0000-0000-0000-000000000001"
CENTRAL = "11111111-0000-0000-0000-000000000002"
WALTER = "44444444-0000-0000-0000-000000000001"
FRANK = "44444444-0000-0000-0000-000000000003"
SEEDED_EVV_VISIT = "66666666-0000-0000-0000-000000000003"
UTC = timezone.utc


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
# Task 1 — schema + seeds
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
                    "select column_name from information_schema.columns "
                    "where table_schema='public' and table_name='clients'"
                )
                out["client_columns"] = {r["column_name"] for r in await cur.fetchall()}
                await cur.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema='public' and table_name='schedules'"
                )
                out["schedule_columns"] = {r["column_name"] for r in await cur.fetchall()}

                await cur.execute(
                    "select name, status, payer, authorized_hours_per_week "
                    "from public.clients where id=%s", (FRANK,)
                )
                out["frank"] = await cur.fetchone()

                await cur.execute(
                    "select check_in_at, check_out_at, status from public.schedules "
                    "where id=%s", (SEEDED_EVV_VISIT,)
                )
                out["seeded_evv"] = await cur.fetchone()

            # The retired M0 statuses must be rejected by the new CHECK.
            for retired in ("paused", "ended"):
                try:
                    async with conn.transaction():
                        await conn.execute(
                            "update public.clients set status=%s where id=%s",
                            (retired, WALTER),
                        )
                    out[f"rejected_{retired}"] = False
                except Exception:
                    out[f"rejected_{retired}"] = True

            # check_out_at without check_in_at must be rejected.
            try:
                async with conn.transaction():
                    await conn.execute(
                        "update public.schedules set check_out_at=now() where id=%s",
                        ("66666666-0000-0000-0000-000000000006",),
                    )
                out["rejected_orphan_checkout"] = False
            except Exception:
                out["rejected_orphan_checkout"] = True

            # A contact written by the demo tenant...
            contact_id = str(uuid.uuid4())
            await conn.execute(
                """insert into public.client_contacts (id, tenant_id, client_id, name)
                   values (%s, app.current_tenant_id(), %s, 'RLS probe contact')""",
                (contact_id, WALTER),
            )

        # ...must be invisible to the probe tenant.
        async with db.tenant_tx(PROBE_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.client_contacts where id=%s", (contact_id,)
                )
                out["probe_sees_contact"] = (await cur.fetchone())[0]

        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.client_contacts where id=%s", (contact_id,)
            )
    finally:
        await db.close_pool()
    return out


def test_client_schema_and_seeds():
    r = asyncio.run(_schema_scenario())

    for col in ("region_id", "payer", "authorized_hours_per_week", "care_summary"):
        assert col in r["client_columns"], f"clients.{col} missing"
    for col in ("check_in_at", "check_out_at"):
        assert col in r["schedule_columns"], f"schedules.{col} missing"

    # The data migration renamed paused -> hospital_hold.
    assert r["frank"]["status"] == "hospital_hold"
    assert r["frank"]["payer"] == "ltc_insurance"
    assert float(r["frank"]["authorized_hours_per_week"]) == 12.0

    assert r["rejected_paused"] is True
    assert r["rejected_ended"] is True
    assert r["rejected_orphan_checkout"] is True

    # The seeded completed visit has both stamps, with an actual duration that
    # differs from its scheduled window (so the census is visibly actuals-first).
    ci, co, status = (
        r["seeded_evv"]["check_in_at"], r["seeded_evv"]["check_out_at"],
        r["seeded_evv"]["status"],
    )
    assert ci is not None and co is not None and status == "completed"
    assert (co - ci) != timedelta(hours=5)

    assert r["probe_sees_contact"] == 0


# ===========================================================================
# Task 4 — directory, contacts, EVV routes
# ===========================================================================
async def _api_scenario():
    from app import db
    from app.main import app

    out: dict = {}
    created: dict = {}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # --- 401 without a token ---
            noauth = httpx.AsyncClient(transport=transport, base_url="http://t")
            out["noauth_code"] = (await noauth.get("/api/clients")).status_code
            out["noauth_metrics_code"] = (
                await noauth.get("/api/clients/metrics")
            ).status_code
            await noauth.aclose()

            # --- facets ---
            out["facets"] = (await ac.get("/api/clients/facets")).json()

            # --- create ---
            name = f"api-client-{uuid.uuid4().hex[:6]}"
            resp = await ac.post("/api/clients", json={
                "name": name, "phone": "+16195559999", "email": "api@example.com",
                "region_id": NORTH_COUNTY, "payer": "va",
                "authorized_hours_per_week": 18.5, "zip": "92008",
                "care_summary": "Companionship and light housekeeping.",
                "languages": ["en"],
            })
            out["create_code"] = resp.status_code
            created["client"] = resp.json()
            cid = created["client"]["id"]
            out["created"] = created["client"]

            # invalid payer / negative hours are rejected
            out["bad_payer_code"] = (await ac.post("/api/clients", json={
                "name": "bad", "payer": "crypto"})).status_code
            out["bad_hours_code"] = (await ac.post("/api/clients", json={
                "name": "bad", "authorized_hours_per_week": -3})).status_code

            # --- list filters ---
            out["by_name"] = (await ac.get(f"/api/clients?q={name}")).json()
            out["by_payer"] = (await ac.get("/api/clients?payer=va")).json()
            out["by_status"] = (await ac.get("/api/clients?status=hospital_hold")).json()
            out["by_region"] = (
                await ac.get(f"/api/clients?region_id={CENTRAL}")
            ).json()

            # --- metrics ---
            out["metrics"] = (await ac.get("/api/clients/metrics")).json()
            out["metrics_bad_week_code"] = (
                await ac.get("/api/clients/metrics?week=nope")
            ).status_code

            # --- PATCH basic fields -> ONE client.updated naming them ---
            out["patch_basic"] = (await ac.patch(f"/api/clients/{cid}", json={
                "care_summary": "Updated care notes.",
                "authorized_hours_per_week": 22.0,
            })).json()
            # --- no-op PATCH emits nothing ---
            await ac.patch(f"/api/clients/{cid}", json={
                "care_summary": "Updated care notes."})
            # --- PATCH status -> change_status path ---
            out["patch_status"] = (await ac.patch(
                f"/api/clients/{cid}", json={"status": "hospital_hold"})).json()
            out["bad_status_code"] = (await ac.patch(
                f"/api/clients/{cid}", json={"status": "paused"})).status_code

            # --- contacts round-trip ---
            c1 = (await ac.post(f"/api/clients/{cid}/contacts", json={
                "name": "Dana Reyes", "relationship": "daughter",
                "phone": "+16195558888", "is_primary": True})).json()
            c2 = (await ac.post(f"/api/clients/{cid}/contacts", json={
                "name": "Sam Reyes", "relationship": "son"})).json()
            out["contact_created"] = c1
            # promoting c2 must demote c1 in the same tx
            out["contact_patched"] = (await ac.patch(
                f"/api/clients/{cid}/contacts/{c2['id']}",
                json={"is_primary": True, "email": "sam@example.com"})).json()
            detail = (await ac.get(f"/api/clients/{cid}")).json()
            out["detail"] = detail
            out["primaries"] = [c["is_primary"] for c in detail["contacts"]]
            out["delete_code"] = (await ac.delete(
                f"/api/clients/{cid}/contacts/{c1['id']}")).status_code
            out["after_delete"] = (
                await ac.get(f"/api/clients/{cid}")
            ).json()["contacts"]
            out["delete_missing_code"] = (await ac.delete(
                f"/api/clients/{cid}/contacts/{uuid.uuid4()}")).status_code

            # --- EVV routes ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                res_id = str(uuid.uuid4())
                await conn.execute(
                    "insert into public.resources (id, tenant_id, name) "
                    "values (%s, app.current_tenant_id(), %s)",
                    (res_id, f"api-cg-{uuid.uuid4().hex[:6]}"),
                )
                created["resource"] = res_id

                # An OVERDUE visit (started 3h ago, ended 1h ago, no check-in) —
                # the board feed must flag it 'missed' at read time.
                overdue = str(uuid.uuid4())
                await conn.execute(
                    """insert into public.schedules
                         (id, tenant_id, resource_id, client_id, start_time, end_time,
                          status)
                       values (%s, app.current_tenant_id(), %s, %s,
                               now() - interval '3 hours', now() - interval '1 hour',
                               'scheduled')""",
                    (overdue, res_id, cid),
                )
                created["overdue"] = overdue

                live = str(uuid.uuid4())
                await conn.execute(
                    """insert into public.schedules
                         (id, tenant_id, resource_id, client_id, start_time, end_time,
                          status)
                       values (%s, app.current_tenant_id(), %s, %s, %s, %s, 'scheduled')""",
                    (live, res_id, cid,
                     datetime(2029, 4, 2, 8, tzinfo=UTC),
                     datetime(2029, 4, 2, 12, tzinfo=UTC)),
                )
                created["live"] = live

            # check-out before check-in -> 422
            out["out_first_code"] = (await ac.post(
                f"/api/schedules/{live}/check-out")).status_code
            ci = await ac.post(f"/api/schedules/{live}/check-in",
                               json={"time": "2029-04-02T08:05:00+00:00"})
            out["checkin"] = ci.json()
            co = await ac.post(f"/api/schedules/{live}/check-out",
                               json={"time": "2029-04-02T12:20:00+00:00"})
            out["checkout"] = co.json()
            out["double_out_code"] = (await ac.post(
                f"/api/schedules/{live}/check-out")).status_code
            out["unknown_visit_code"] = (await ac.post(
                f"/api/schedules/{uuid.uuid4()}/check-in")).status_code

            # board feed carries evv for the overdue visit
            board = (await ac.get("/api/schedule")).json()
            out["overdue_evv"] = next(
                (v["evv"] for v in board["visits"] if v["id"] == overdue), "ABSENT"
            )

        # --- RLS isolation: the probe tenant sees none of this ---
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t",
            headers=bearer_headers(PROBE_TENANT),
        ) as probe:
            out["probe_list_total"] = (await probe.get("/api/clients")).json()["total"]
            out["probe_detail_code"] = (await probe.get(f"/api/clients/{cid}")).status_code
            out["probe_metrics"] = (await probe.get("/api/clients/metrics")).json()

        # --- events written for the created client ---
        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["events"] = await _events_for(conn, cid)

            for key in ("overdue", "live"):
                await conn.execute(
                    "delete from public.schedules where id=%s", (created[key],)
                )
            await conn.execute(
                "delete from public.resources where id=%s", (created["resource"],)
            )
            await conn.execute("delete from public.client_contacts where client_id=%s", (cid,))
            await conn.execute("delete from public.entity_summaries where entity_id=%s", (cid,))
            await conn.execute("delete from public.clients where id=%s", (cid,))
    finally:
        await db.close_pool()
    return out


@pytest.fixture(scope="module")
def api():
    return asyncio.run(_api_scenario())


def test_auth_and_rls_isolation(api):
    assert api["noauth_code"] == 401
    assert api["noauth_metrics_code"] == 401
    # The probe tenant seeds no clients, so it sees an empty directory and an
    # all-zero census — never the demo tenant's rows.
    assert api["probe_list_total"] == 0
    assert api["probe_detail_code"] == 404
    assert api["probe_metrics"]["active_clients"] == 0
    assert api["probe_metrics"]["authorized_hours"] == 0.0


def test_facets_and_create(api):
    facets = api["facets"]
    assert "active" in facets["statuses"]
    assert "hospital_hold" in facets["statuses"]
    assert {"private_pay", "medicaid", "ltc_insurance"} <= set(facets["payers"])
    assert "North County" in [r["name"] for r in facets["regions"]]

    assert api["create_code"] == 201
    created = api["created"]
    assert created["status"] == "active"  # the DB default
    assert created["payer"] == "va"
    assert created["authorized_hours_per_week"] == 18.5
    assert created["region_name"] == "North County"

    assert api["bad_payer_code"] == 422
    assert api["bad_hours_code"] == 422


def test_list_filters(api):
    assert api["by_name"]["total"] == 1
    assert all(c["payer"] == "va" for c in api["by_payer"]["clients"])
    assert api["by_payer"]["total"] >= 1
    assert all(c["status"] == "hospital_hold" for c in api["by_status"]["clients"])
    assert all(c["region_id"] == CENTRAL for c in api["by_region"]["clients"])


def test_metrics_shape(api):
    m = api["metrics"]
    for key in ("active_clients", "authorized_hours", "scheduled_hours",
                "delivered_hours", "open_hours", "leakage_hours", "by_region",
                "by_payer", "week_start", "week_end"):
        assert key in m
    assert m["active_clients"] >= 2  # the two seeded active clients
    # Leakage is clamped at zero and never exceeds authorized.
    assert 0 <= m["leakage_hours"] <= m["authorized_hours"]
    assert api["metrics_bad_week_code"] == 422


def test_patch_and_status_events(api):
    assert api["patch_basic"]["authorized_hours_per_week"] == 22.0
    assert api["patch_status"]["status"] == "hospital_hold"
    assert api["bad_status_code"] == 422

    types = [e["event_type"] for e in api["events"]]
    assert types.count("client.created") == 1
    # ONE update event for the two-field PATCH; the no-op repeat emitted nothing.
    updated = [e for e in api["events"] if e["event_type"] == "client.updated"]
    field_events = [e for e in updated if "fields" in e["payload"]
                    and "care_summary" in e["payload"].get("fields", [])]
    assert len(field_events) == 1
    assert set(field_events[0]["payload"]["fields"]) == {
        "care_summary", "authorized_hours_per_week"
    }
    # The status move went through change_status, not a raw UPDATE.
    status_events = [e for e in api["events"] if e["event_type"] == "client.status_changed"]
    assert len(status_events) == 1
    assert status_events[0]["payload"]["to"] == "hospital_hold"
    assert "hospital hold" in status_events[0]["payload"]["summary"]
    # Every write is attributed to the human clicking their own UI.
    assert all(e["source_system"] == "user" for e in api["events"])


def test_contacts_crud_and_primary_swap(api):
    assert api["contact_created"]["is_primary"] is True
    assert api["contact_patched"]["email"] == "sam@example.com"
    # Exactly one primary survives the swap.
    assert api["primaries"].count(True) == 1
    assert api["delete_code"] == 204
    assert len(api["after_delete"]) == 1
    assert api["after_delete"][0]["name"] == "Sam Reyes"
    assert api["delete_missing_code"] == 404

    # Each contact write logs a plain-language client.updated ON THE CLIENT.
    contact_events = [
        e for e in api["events"]
        if e["event_type"] == "client.updated" and "Family contact" in e["payload"]["summary"]
    ]
    # two adds (Dana, Sam) + one update (Sam promoted) + one remove (Dana)
    assert len(contact_events) == 4
    assert "(daughter)" in contact_events[0]["payload"]["summary"]


def test_detail_carries_the_care_overview(api):
    detail = api["detail"]
    for key in ("contacts", "caregivers", "hours_this_week", "documents"):
        assert key in detail
    assert detail["hours_this_week"]["authorized_hours"] == 22.0


def test_evv_routes_and_board_flag(api):
    assert api["out_first_code"] == 422  # cannot check out before checking in
    assert api["checkin"]["check_in_at"].startswith("2029-04-02T08:05")
    # Check-out completes the visit.
    assert api["checkout"]["status"] == "completed"
    assert api["checkout"]["check_out_at"].startswith("2029-04-02T12:20")
    assert api["double_out_code"] == 422
    assert api["unknown_visit_code"] == 404
    # A scheduled visit whose window has passed with nobody clocked in reads as
    # 'missed' — derived per request, never stored.
    assert api["overdue_evv"] == "missed"
