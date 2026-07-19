"""Clients view seam (Module 16a, Task 2), gated on NEXUS_APP_DB_URL.

Covers the three things `services/views/clients.py` owns:
  * change_status — the single writer: event + payload on a real move, silence on
    a no-op, ClientError(not_found) on an unknown id.
  * census / client_week_hours — hand-checked against purpose-built visits, with
    the actuals-first delivered rule proven by a visit whose clocked duration
    differs from its scheduled window.
  * evv_flag — pure-function unit cases (no DB).

The census fixtures live in their own tenant transaction and are deleted before
it commits, so the seeded demo data is untouched. Because census sums over the
WHOLE tenant, every assertion is a DELTA against a baseline taken in the same
transaction — an absolute number would depend on where the seeded relative-time
visits happen to land in the current week.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


# --- evv_flag: pure, no DB ---------------------------------------------------
def test_evv_flag_rules():
    from app.services.views.clients import EVV_GRACE_MINUTES, evv_flag

    now = datetime(2027, 5, 3, 12, 0, tzinfo=timezone.utc)

    def visit(**kw):
        base = {
            "status": "scheduled",
            "check_in_at": None,
            "start_time": now,
            "end_time": now + timedelta(hours=4),
        }
        base.update(kw)
        return base

    # Inside the grace window -> nothing to flag yet.
    assert evv_flag(visit(), now + timedelta(minutes=EVV_GRACE_MINUTES - 1)) is None
    # Past grace, window still open -> late.
    assert evv_flag(visit(), now + timedelta(minutes=EVV_GRACE_MINUTES + 1)) == "late"
    # Past the end of the window with nobody clocked in -> missed.
    assert evv_flag(visit(), now + timedelta(hours=5)) == "missed"
    # Someone clocked in -> no derived flag, however late they were.
    assert evv_flag(visit(check_in_at=now), now + timedelta(hours=5)) is None
    # Terminal statuses speak for themselves.
    for status in ("completed", "no_show", "cancelled", "called_out", "open"):
        assert evv_flag(visit(status=status), now + timedelta(hours=5)) is None


# --- change_status -----------------------------------------------------------
async def _status_scenario():
    from app import db
    from app.services.views.clients import ClientError, change_status

    client_id = str(uuid.uuid4())
    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                """insert into public.clients (id, tenant_id, name, status)
                   values (%s, app.current_tenant_id(), %s, 'active')""",
                (client_id, f"seam-client-{uuid.uuid4().hex[:6]}"),
            )

            out["moved"] = await change_status(
                conn, DEMO_TENANT, "user", client_id, "hospital_hold"
            )
            out["noop"] = await change_status(
                conn, DEMO_TENANT, "user", client_id, "hospital_hold"
            )
            try:
                await change_status(
                    conn, DEMO_TENANT, "user", str(uuid.uuid4()), "active"
                )
            except ClientError as exc:
                out["unknown"] = exc
            try:
                await change_status(conn, DEMO_TENANT, "user", client_id, "paused")
            except ClientError as exc:
                out["retired_status"] = exc

            async with conn.cursor() as cur:
                await cur.execute(
                    "select event_type, payload from public.events "
                    "where entity_type = 'client' and entity_id = %s",
                    (client_id,),
                )
                out["events"] = await cur.fetchall()
                await cur.execute(
                    "select status from public.clients where id = %s", (client_id,)
                )
                out["final_status"] = (await cur.fetchone())[0]

            await conn.execute("delete from public.events where entity_id = %s", (client_id,))
            await conn.execute("delete from public.clients where id = %s", (client_id,))
    finally:
        await db.close_pool()
    return out


def test_change_status_is_the_single_writer():
    from app.services.views.clients import ClientError

    r = asyncio.run(_status_scenario())

    assert r["moved"] == {
        "changed": True, "from": "active", "to": "hospital_hold",
        "name": r["moved"]["name"],
    }
    assert r["final_status"] == "hospital_hold"

    # Exactly ONE event, despite two calls — the no-op emits nothing.
    assert r["noop"]["changed"] is False
    assert len(r["events"]) == 1
    event_type, payload = r["events"][0]
    assert event_type == "client.status_changed"
    assert payload["from"] == "active" and payload["to"] == "hospital_hold"
    assert "hospital hold" in payload["summary"]

    assert isinstance(r["unknown"], ClientError) and r["unknown"].not_found
    # The retired M0 statuses are rejected, not silently written.
    assert isinstance(r["retired_status"], ClientError)
    assert not r["retired_status"].not_found


# --- census / client_week_hours ----------------------------------------------
async def _census_scenario():
    from app import db
    from app.services.views.clients import (
        census_metrics,
        client_week_hours,
        week_bounds,
    )

    ids = {k: str(uuid.uuid4()) for k in ("client", "resource")}
    start, _ = week_bounds()
    sfx = uuid.uuid4().hex[:6]
    out: dict = {}

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["before"] = await census_metrics(conn)

            await conn.execute(
                """insert into public.clients
                     (id, tenant_id, name, status, payer, authorized_hours_per_week)
                   values (%s, app.current_tenant_id(), %s, 'active', 'medicaid', 30)""",
                (ids["client"], f"census-client-{sfx}"),
            )
            await conn.execute(
                "insert into public.resources (id, tenant_id, name) "
                "values (%s, app.current_tenant_id(), %s)",
                (ids["resource"], f"census-cg-{sfx}"),
            )

            sched_ids = []

            async def visit(day, s_h, e_h, status, *, ci=None, co=None, assigned=True):
                sid = str(uuid.uuid4())
                sched_ids.append(sid)
                base = start + timedelta(days=day)
                await conn.execute(
                    """insert into public.schedules
                         (id, tenant_id, resource_id, client_id, start_time, end_time,
                          status, check_in_at, check_out_at)
                       values (%s, app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        sid, ids["resource"] if assigned else None, ids["client"],
                        base + timedelta(hours=s_h), base + timedelta(hours=e_h),
                        status,
                        base + timedelta(hours=ci) if ci is not None else None,
                        base + timedelta(hours=co) if co is not None else None,
                    ),
                )
                return sid

            # completed WITH clock data: scheduled 4h, actually worked 3h.
            await visit(0, 8, 12, "completed", ci=8.5, co=11.5)
            # completed WITHOUT clock data: falls back to its scheduled 5h.
            await visit(1, 9, 14, "completed")
            # scheduled (not yet delivered): 2h of committed hours.
            await visit(2, 8, 10, "scheduled")
            # no_show: committed hours that delivered nothing — this is leakage.
            await visit(3, 8, 11, "no_show")
            # open shift: unfilled hours, reported separately (NOT "scheduled").
            await visit(4, 8, 12, "open", assigned=False)
            # cancelled: counts nowhere.
            await visit(5, 8, 12, "cancelled")

            out["after"] = await census_metrics(conn)
            out["client"] = await client_week_hours(conn, ids["client"])

            for sid in sched_ids:
                await conn.execute("delete from public.schedules where id = %s", (sid,))
            await conn.execute("delete from public.resources where id = %s", (ids["resource"],))
            await conn.execute("delete from public.clients where id = %s", (ids["client"],))
    finally:
        await db.close_pool()
    return out


def test_census_math_is_actuals_first():
    r = asyncio.run(_census_scenario())
    before, after, client = r["before"], r["after"], r["client"]

    def delta(key):
        return round(after[key] - before[key], 1)

    assert after["active_clients"] - before["active_clients"] == 1
    assert delta("authorized_hours") == 30.0
    # scheduled = 4 + 5 + 2 + 3 (completed/scheduled/no_show), open excluded.
    assert delta("scheduled_hours") == 14.0
    # delivered = 3 (CLOCKED, not the scheduled 4) + 5 (no clock -> scheduled).
    assert delta("delivered_hours") == 8.0
    assert delta("open_hours") == 4.0

    # The per-client view sees only this client's visits, so it is absolute.
    assert client["authorized_hours"] == 30.0
    assert client["scheduled_hours"] == 14.0
    assert client["delivered_hours"] == 8.0
    assert client["open_hours"] == 4.0
    # Leakage is what the business is paid for minus what it delivered.
    assert client["leakage_hours"] == 22.0
    assert client["delivery_rate"] == round(100.0 * 8.0 / 30.0, 1)


def test_census_leakage_never_goes_negative():
    """Delivering more than authorized is an overtime question, not leakage."""
    from app.services.views.clients import _h

    assert _h(max(10.0 - 40.0, 0.0)) == 0.0
