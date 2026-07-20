"""Workforce & compliance (Module 18a), gated on NEXUS_APP_DB_URL.

Four groups, mirroring the plan's Tasks 1–4:

  * SCHEMA/SEEDS (Task 1) — `resource_credentials` exists with its unique
    (tenant, resource, qualification) key, the `resources.status` CHECK bites, a
    deleted resource takes its credentials with it (cascade), a demo-tenant
    credential is invisible to the probe tenant, and the four seeded rows land one
    of each read-time status under `credential_status`.
  * SEAM (Task 2) — `available_week_hours` unit cases, hand-checked utilization on
    a seeded caregiver, `roster_metrics` counts (including the "inactive
    caregivers' credentials don't count" rule, proven by deactivating one in-test),
    and `expiring_credentials(60)` ordering + days_left.
  * TOOL (Task 3) — `execute_tool("list_expiring_credentials")` returns immediately
    with NO gate, writes a `tool.called` audit row, clamps `days_ahead`, and its
    content line is plain language (no UUIDs).
  * API (Task 4) — roster feed matches the seam, credential create/409/PATCH/DELETE
    with their `credential.*` events on the RESOURCE entity, 401 without a token,
    and cross-tenant 404 (RLS).

The inactive-exclusion cases for matching and the board roster live in
test_matching.py / test_schedule_api.py, next to what they regress.

Created rows are deleted afterward; events are immutable and left in place.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

ALICIA = "55555555-0000-0000-0000-000000000001"   # CNA valid + Hoyer no-expiry
BRIAN = "55555555-0000-0000-0000-000000000002"    # HHA expired
CARMEN = "55555555-0000-0000-0000-000000000003"   # Dementia Care expiring
DEREK = "55555555-0000-0000-0000-000000000004"
QUAL_CNA = "22222222-0000-0000-0000-000000000001"
QUAL_MEDS = "22222222-0000-0000-0000-000000000005"


async def _events_for(conn, entity_id, event_type=None, since=None):
    """Events on one entity. `since` scopes to THIS run — events are immutable, so
    a re-run of the suite would otherwise keep counting the previous run's rows."""
    from psycopg.rows import dict_row

    sql = ("select event_type, source_system, payload from public.events "
           "where entity_id=%s")
    params = [entity_id]
    if event_type:
        sql += " and event_type=%s"
        params.append(event_type)
    if since is not None:
        sql += " and created_at >= %s"
        params.append(since)
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
                    """select r.name as caregiver, q.name as credential, rc.expires_at
                         from public.resource_credentials rc
                         join public.resources r on r.id = rc.resource_id
                         join public.qualifications q on q.id = rc.qualification_id
                        order by r.name, q.name"""
                )
                out["seeded"] = await cur.fetchall()

            # Duplicate (resource, qualification) is rejected by the unique index.
            try:
                async with conn.transaction():
                    await conn.execute(
                        """insert into public.resource_credentials
                             (tenant_id, resource_id, qualification_id)
                           values (app.current_tenant_id(), %s, %s)""",
                        (ALICIA, QUAL_CNA),
                    )
                out["dup_rejected"] = False
            except Exception:
                out["dup_rejected"] = True

            # A bad resources.status is rejected by the CHECK.
            try:
                async with conn.transaction():
                    await conn.execute(
                        "update public.resources set status = %s where id = %s",
                        ("retired", ALICIA),
                    )
                out["bad_status_rejected"] = False
            except Exception:
                out["bad_status_rejected"] = True

            # Cascade: deleting a resource takes its credentials with it.
            temp_resource = str(uuid.uuid4())
            await conn.execute(
                "insert into public.resources (id, tenant_id, name) "
                "values (%s, app.current_tenant_id(), %s)",
                (temp_resource, f"cascade-probe-{uuid.uuid4().hex[:6]}"),
            )
            await conn.execute(
                """insert into public.resource_credentials
                     (tenant_id, resource_id, qualification_id)
                   values (app.current_tenant_id(), %s, %s)""",
                (temp_resource, QUAL_CNA),
            )
            await conn.execute("delete from public.resources where id = %s", (temp_resource,))
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.resource_credentials where resource_id = %s",
                    (temp_resource,),
                )
                out["after_cascade"] = (await cur.fetchone())[0]

            # A demo-tenant credential...
            probe_id = str(uuid.uuid4())
            await conn.execute(
                """insert into public.resource_credentials
                     (id, tenant_id, resource_id, qualification_id)
                   values (%s, app.current_tenant_id(), %s, %s)""",
                (probe_id, DEREK, QUAL_CNA),
            )

        # ...is invisible to the probe tenant.
        async with db.tenant_tx(PROBE_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.resource_credentials where id = %s",
                    (probe_id,),
                )
                out["probe_sees"] = (await cur.fetchone())[0]

        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.resource_credentials where id = %s", (probe_id,)
            )
    finally:
        await db.close_pool()
    return out


def test_workforce_schema_and_seeds():
    from app.services.views.workforce import credential_status

    r = asyncio.run(_schema_scenario())
    assert r["dup_rejected"] is True
    assert r["bad_status_rejected"] is True
    assert r["after_cascade"] == 0
    assert r["probe_sees"] == 0

    # The seed lays down exactly one of each read-time status.
    statuses = {
        (row["caregiver"], row["credential"]): credential_status(row["expires_at"])
        for row in r["seeded"]
    }
    assert statuses[("Alicia Moreno", "CNA")] == "valid"
    assert statuses[("Alicia Moreno", "Hoyer Lift Certified")] == "no_expiry"
    assert statuses[("Carmen Ruiz", "Dementia Care")] == "expiring"
    assert statuses[("Brian Okafor", "HHA")] == "expired"


# ===========================================================================
# Task 2 — seam
# ===========================================================================
def test_available_week_hours_units():
    from app.services.views.workforce import available_week_hours

    # Nothing declared -> None (unknown capacity), never 0.0.
    assert available_week_hours(None) is None
    assert available_week_hours({}) is None
    assert available_week_hours({"mon": []}) is None
    # Split windows on one day sum; days sum across.
    assert available_week_hours({"mon": ["08:00-16:00"]}) == 8.0
    assert available_week_hours({"tue": ["08:00-12:00", "13:00-17:00"]}) == 8.0
    assert available_week_hours(
        {"mon": ["08:00-16:00"], "wed": ["12:00-20:00"]}
    ) == 16.0
    # Malformed / inverted windows are skipped, not raised on.
    assert available_week_hours({"mon": ["bogus", "08:00-16:00", "17:00-09:00"]}) == 8.0


def test_credential_status_boundaries():
    from app.services.views.workforce import EXPIRING_DAYS, credential_status

    today = date(2026, 7, 19)
    assert credential_status(None, today) == "no_expiry"
    assert credential_status(today - timedelta(days=1), today) == "expired"
    # Its last day is still good — today is "expiring", not "expired".
    assert credential_status(today, today) == "expiring"
    assert credential_status(today + timedelta(days=EXPIRING_DAYS), today) == "expiring"
    assert credential_status(today + timedelta(days=EXPIRING_DAYS + 1), today) == "valid"


async def _seam_scenario():
    from app import db

    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            from app.services.views.workforce import (
                expiring_credentials,
                roster_metrics,
                roster_rows,
            )

            out["rows"] = await roster_rows(conn)
            out["metrics"] = await roster_metrics(conn)
            out["expiring"] = await expiring_credentials(conn, 60)

            # Deactivate Brian (the expired-HHA caregiver) and re-measure: his
            # credential must drop out of the compliance counts and the tool query.
            await conn.execute(
                "update public.resources set status='inactive' where id=%s", (BRIAN,)
            )
            out["metrics_after"] = await roster_metrics(conn)
            out["expiring_after"] = await expiring_credentials(conn, 60)
            out["rows_after"] = await roster_rows(conn)
            await conn.execute(
                "update public.resources set status='active' where id=%s", (BRIAN,)
            )

        async with db.tenant_tx(PROBE_TENANT) as conn:
            from app.services.views.workforce import roster_metrics as rm

            out["probe_metrics"] = await rm(conn)
    finally:
        await db.close_pool()
    return out


@pytest.fixture(scope="module")
def seam():
    return asyncio.run(_seam_scenario())


def _row(rows, resource_id):
    return next((r for r in rows if r["id"] == resource_id), None)


def test_roster_rows_capacity(seam):
    from app.services.views.workforce import utilization

    alicia = _row(seam["rows"], ALICIA)
    assert alicia is not None
    # Seeded availability: mon/tue/wed 08:00-16:00 = 3 x 8 = 24 h/week.
    assert alicia["available_hours"] == 24.0
    # Utilization is exactly the seam's own arithmetic over the two numbers shown.
    assert alicia["utilization"] == utilization(
        alicia["hours_this_week"], alicia["available_hours"]
    )
    # Credentials ride the row, statuses derived.
    creds = {c["qualification_name"]: c["status"] for c in alicia["credentials"]}
    assert creds["CNA"] == "valid"
    assert creds["Hoyer Lift Certified"] == "no_expiry"


def test_roster_metrics_counts(seam):
    m = seam["metrics"]
    # The seed has one expiring (Carmen) and one expired (Brian) credential.
    assert m["expiring_count"] == 1
    assert m["expired_count"] == 1
    assert m["inactive_count"] == 0
    assert m["active_count"] >= 5
    assert m["avg_utilization"] is not None

    # Deactivating Brian removes his lapsed HHA from the compliance counts...
    after = seam["metrics_after"]
    assert after["expired_count"] == 0
    assert after["expiring_count"] == 1
    assert after["inactive_count"] == 1
    assert after["active_count"] == m["active_count"] - 1
    # ...but he is STILL listed on the roster — this is the one surface that
    # shows inactive caregivers.
    assert _row(seam["rows_after"], BRIAN)["status"] == "inactive"


def test_expiring_credentials_ordering(seam):
    rows = seam["expiring"]
    names = [(r["caregiver"], r["credential"]) for r in rows]
    assert ("Brian Okafor", "HHA") in names       # already expired: included
    assert ("Carmen Ruiz", "Dementia Care") in names
    # Alicia's CNA (+180 d) and her no-expiry Hoyer row are both out of window.
    assert ("Alicia Moreno", "CNA") not in names
    assert ("Alicia Moreno", "Hoyer Lift Certified") not in names
    # Soonest first — the expired row leads.
    assert [r["days_left"] for r in rows] == sorted(r["days_left"] for r in rows)
    assert rows[0]["days_left"] < 0
    assert rows[0]["status"] == "expired"

    # An inactive caregiver's lapsed credential drops out of the digest query.
    after = [(r["caregiver"], r["credential"]) for r in seam["expiring_after"]]
    assert ("Brian Okafor", "HHA") not in after


def test_empty_tenant_metrics(seam):
    p = seam["probe_metrics"]
    assert p["active_count"] == 0
    assert p["expiring_count"] == 0
    assert p["expired_count"] == 0
    assert p["avg_utilization"] is None


# ===========================================================================
# Task 3 — the safe tool
# ===========================================================================
async def _tool_scenario():
    from app import db
    from app.services.tools import execute_tool

    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            r = await execute_tool(
                conn, DEMO_TENANT, "list_expiring_credentials", {"days_ahead": 60},
                source_system="chat",
            )
            out["default"] = {"summary": r.summary, "data": r.data}

            # days_ahead clamped at both ends rather than rejected.
            low = await execute_tool(
                conn, DEMO_TENANT, "list_expiring_credentials", {"days_ahead": 0},
                source_system="chat",
            )
            high = await execute_tool(
                conn, DEMO_TENANT, "list_expiring_credentials", {"days_ahead": 9999},
                source_system="chat",
            )
            out["low"] = low.data
            out["high"] = high.data

            # A junk value falls back to the default rather than raising.
            junk = await execute_tool(
                conn, DEMO_TENANT, "list_expiring_credentials", {"days_ahead": "soon"},
                source_system="chat",
            )
            out["junk"] = junk.data

            from psycopg.rows import dict_row
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """select payload from public.events
                        where event_type = 'tool.called'
                          and payload->>'tool_name' = 'list_expiring_credentials'
                        order by created_at desc limit 1"""
                )
                out["audit"] = await cur.fetchone()
    finally:
        await db.close_pool()
    return out


def test_tool_is_safe_and_plain(seam):
    # Depends on `seam` only for ordering: that fixture leaves Brian reactivated,
    # which the expiring-credential assertions below rely on.
    del seam
    r = asyncio.run(_tool_scenario())
    data = r["default"]["data"]

    # A safe tool RETURNS its result — no gate, no queued pending_action.
    assert "pending_action_id" not in data
    assert data["count"] == len(data["credentials"]) >= 2
    assert data["days_ahead"] == 60

    # Plain language in the content line: names, no UUIDs, no raw ISO dates.
    summary = r["default"]["summary"]
    assert "Brian Okafor's HHA expired" in summary
    assert "Carmen Ruiz's Dementia Care expires in" in summary
    assert "-0000-0000-" not in summary

    # Clamping, not rejection.
    assert r["low"]["days_ahead"] == 1
    assert r["high"]["days_ahead"] == 365
    assert r["junk"]["days_ahead"] == 60

    # The call is on the audit trail.
    assert r["audit"] is not None
    assert r["audit"]["payload"]["tool_name"] == "list_expiring_credentials"


# ===========================================================================
# Task 4 — REST
# ===========================================================================
async def _api_scenario():
    from app import db
    from app.main import app

    out: dict = {}
    await db.open_pool()
    started_at = datetime.now(timezone.utc)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # --- 401 without a token ---
            noauth = httpx.AsyncClient(transport=transport, base_url="http://t")
            out["noauth_code"] = (await noauth.get("/api/workforce/roster")).status_code
            await noauth.aclose()

            out["roster"] = (await ac.get("/api/workforce/roster")).json()
            out["bad_week_code"] = (
                await ac.get("/api/workforce/roster?week=nonsense")
            ).status_code

            # --- create a credential on Derek (seed leaves him with none) ---
            expires = (date.today() + timedelta(days=45)).isoformat()
            resp = await ac.post("/api/workforce/credentials", json={
                "resource_id": DEREK,
                "qualification_id": QUAL_MEDS,
                "issued_at": (date.today() - timedelta(days=320)).isoformat(),
                "expires_at": expires,
                "notes": "Renewal course booked.",
            })
            out["create_code"] = resp.status_code
            created = resp.json()
            out["created"] = created
            cid = created["id"]

            # duplicate pair -> 409; unknown caregiver -> 404; inverted dates -> 422
            out["dup_code"] = (await ac.post("/api/workforce/credentials", json={
                "resource_id": DEREK, "qualification_id": QUAL_MEDS,
            })).status_code
            out["missing_resource_code"] = (await ac.post(
                "/api/workforce/credentials",
                json={"resource_id": str(uuid.uuid4()), "qualification_id": QUAL_MEDS},
            )).status_code
            out["inverted_code"] = (await ac.post("/api/workforce/credentials", json={
                "resource_id": DEREK, "qualification_id": QUAL_CNA,
                "issued_at": date.today().isoformat(),
                "expires_at": (date.today() - timedelta(days=5)).isoformat(),
            })).status_code

            # --- PATCH names changed fields; the repeat no-op emits nothing ---
            new_expiry = (date.today() + timedelta(days=400)).isoformat()
            out["patch"] = (await ac.patch(f"/api/workforce/credentials/{cid}", json={
                "expires_at": new_expiry, "notes": "Renewed.",
            })).json()
            await ac.patch(f"/api/workforce/credentials/{cid}", json={"notes": "Renewed."})

            # The roster reflects the write without any client-side math.
            after = (await ac.get("/api/workforce/roster")).json()
            out["derek_after"] = next(
                (c for c in after["caregivers"] if c["id"] == DEREK), None
            )

            # --- roster PATCH status (schedule router, single resources writer) ---
            out["deactivate"] = (await ac.patch(f"/api/roster/{DEREK}", json={
                "status": "inactive", "phone": "+16195559999",
            })).json()
            out["bad_status_code"] = (await ac.patch(
                f"/api/roster/{DEREK}", json={"status": "retired"})).status_code
            # Board roster drops him; the workforce roster keeps him.
            board = (await ac.get("/api/roster")).json()
            out["derek_on_board"] = any(c["id"] == DEREK for c in board)
            wf = (await ac.get("/api/workforce/roster")).json()
            out["derek_on_workforce"] = any(
                c["id"] == DEREK for c in wf["caregivers"]
            )
            await ac.patch(f"/api/roster/{DEREK}", json={"status": "active"})

            out["delete_code"] = (await ac.delete(
                f"/api/workforce/credentials/{cid}")).status_code
            out["delete_missing_code"] = (await ac.delete(
                f"/api/workforce/credentials/{uuid.uuid4()}")).status_code

        # --- cross-tenant: the probe tenant cannot touch the demo credential ---
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t",
            headers=bearer_headers(PROBE_TENANT),
        ) as probe:
            recreate = await httpx.AsyncClient(
                transport=transport, base_url="http://t", headers=bearer_headers()
            ).post("/api/workforce/credentials", json={
                "resource_id": DEREK, "qualification_id": QUAL_MEDS,
            })
            demo_cid = recreate.json()["id"]
            out["probe_patch_code"] = (await probe.patch(
                f"/api/workforce/credentials/{demo_cid}", json={"notes": "hi"})).status_code
            out["probe_delete_code"] = (await probe.delete(
                f"/api/workforce/credentials/{demo_cid}")).status_code

        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["events"] = await _events_for(conn, DEREK, since=started_at)
            await conn.execute(
                "delete from public.resource_credentials where resource_id = %s", (DEREK,)
            )
            await conn.execute(
                "update public.resources set status='active', phone=%s where id=%s",
                ("+16195550204", DEREK),
            )
    finally:
        await db.close_pool()
    return out


@pytest.fixture(scope="module")
def api():
    return asyncio.run(_api_scenario())


def test_roster_feed(api):
    assert api["noauth_code"] == 401
    assert api["bad_week_code"] == 422
    roster = api["roster"]
    assert roster["metrics"]["expired_count"] == 1
    assert roster["metrics"]["expiring_count"] == 1
    alicia = next(c for c in roster["caregivers"] if c["id"] == ALICIA)
    assert alicia["available_hours"] == 24.0
    assert len(alicia["credentials"]) == 2


def test_credential_create_and_conflicts(api):
    assert api["create_code"] == 201
    assert api["created"]["status"] == "expiring"   # +45 days, inside the window
    assert api["created"]["qualification_name"] == "Medication Management"
    assert api["dup_code"] == 409
    assert api["missing_resource_code"] == 404
    assert api["inverted_code"] == 422


def test_credential_patch_and_events(api):
    # +400 days moves it out of the expiring window — status is re-derived, not stored.
    assert api["patch"]["status"] == "valid"
    assert api["derek_after"] is not None
    assert len(api["derek_after"]["credentials"]) == 1

    types = [e["event_type"] for e in api["events"]]
    assert types.count("credential.added") >= 1
    # ONE update event for the two-field PATCH; the no-op repeat emitted nothing.
    updated = [e for e in api["events"] if e["event_type"] == "credential.updated"]
    assert len(updated) == 1
    assert set(updated[0]["payload"]["fields"]) == {"expires_at", "notes"}
    # Credential events ride the RESOURCE entity, in plain language.
    assert all(e["source_system"] == "user" for e in api["events"])
    added = next(e for e in api["events"] if e["event_type"] == "credential.added")
    assert "Medication Management credential added for Derek Hsu" in added["payload"]["summary"]
    assert api["delete_code"] == 204
    assert api["delete_missing_code"] == 404
    assert any(e["event_type"] == "credential.removed" for e in api["events"])


def test_status_change_events_and_exclusion(api):
    assert api["deactivate"]["status"] == "inactive"
    assert api["bad_status_code"] == 422
    # Deactivated: off the board roster, still on the workforce roster.
    assert api["derek_on_board"] is False
    assert api["derek_on_workforce"] is True

    status_events = [
        e for e in api["events"] if e["event_type"] == "resource.status_changed"
    ]
    # Deactivate + the reactivate at the end of the scenario.
    assert len(status_events) == 2
    assert status_events[0]["payload"]["from"] == "active"
    assert status_events[0]["payload"]["to"] == "inactive"
    assert "deactivated" in status_events[0]["payload"]["summary"]
    # The mixed PATCH (status + phone) ALSO emitted a plain resource.updated.
    updated = [e for e in api["events"] if e["event_type"] == "resource.updated"]
    assert any("phone" in e["payload"].get("fields", []) for e in updated)


def test_cross_tenant_isolation(api):
    assert api["probe_patch_code"] == 404
    assert api["probe_delete_code"] == 404
