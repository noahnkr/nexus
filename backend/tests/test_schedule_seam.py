"""Schedule seam EVV transitions (Module 16a, Task 2), gated on NEXUS_APP_DB_URL.

`check_in` / `check_out` are the only writers of the EVV clock stamps. What
matters here is the guard rails (you cannot clock into an open or finished visit,
cannot clock out of one you never clocked into, cannot clock out backwards) and
the one deliberate asymmetry: CHECK-OUT COMPLETES THE VISIT, so the delivered-hours
math sees the actual duration it just recorded.

Fixtures live in one tenant transaction and are deleted before it commits.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

# Fixed times far from the seeded relative visits, so nothing collides.
START = datetime(2027, 6, 7, 8, 0, tzinfo=timezone.utc)   # Mon 08:00
END = datetime(2027, 6, 7, 12, 0, tzinfo=timezone.utc)    # Mon 12:00


async def _scenario():
    from app import db
    from app.services.views.schedule import ScheduleError, check_in, check_out

    ids = {k: str(uuid.uuid4()) for k in ("client", "resource")}
    sfx = uuid.uuid4().hex[:6]
    out: dict = {}
    sched_ids: list[str] = []

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "insert into public.clients (id, tenant_id, name, status) "
                "values (%s, app.current_tenant_id(), %s, 'active')",
                (ids["client"], f"evv-client-{sfx}"),
            )
            await conn.execute(
                "insert into public.resources (id, tenant_id, name) "
                "values (%s, app.current_tenant_id(), %s)",
                (ids["resource"], f"evv-cg-{sfx}"),
            )

            async def visit(status, *, assigned=True, offset_days=0):
                sid = str(uuid.uuid4())
                sched_ids.append(sid)
                await conn.execute(
                    """insert into public.schedules
                         (id, tenant_id, resource_id, client_id, start_time, end_time, status)
                       values (%s, app.current_tenant_id(), %s, %s, %s, %s, %s)""",
                    (sid, ids["resource"] if assigned else None, ids["client"],
                     START + timedelta(days=offset_days),
                     END + timedelta(days=offset_days), status),
                )
                return sid

            async def rejects(coro):
                try:
                    await coro
                except ScheduleError as exc:
                    return exc
                return None

            # --- happy path: check in, then check out (which completes it) ---
            happy = await visit("scheduled")
            out["in"] = await check_in(conn, happy, "user", at=START + timedelta(minutes=5))
            out["out"] = await check_out(
                conn, happy, "user", at=END + timedelta(minutes=10)
            )

            async with conn.cursor() as cur:
                await cur.execute(
                    "select status, check_in_at, check_out_at from public.schedules "
                    "where id = %s",
                    (happy,),
                )
                out["row"] = await cur.fetchone()
                await cur.execute(
                    "select event_type, payload from public.events "
                    "where entity_type = 'schedule' and entity_id = %s "
                    "order by created_at",
                    (happy,),
                )
                out["events"] = await cur.fetchall()

            # --- rejections ---
            # `happy` is now `completed`, so re-checking IN hits the status guard
            # (not the already-checked-in guard — that one needs a still-scheduled
            # visit, exercised on `fresh` below).
            out["completed_in_after_out"] = await rejects(check_in(conn, happy, "user"))
            out["double_out"] = await rejects(check_out(conn, happy, "user"))

            open_shift = await visit("open", assigned=False, offset_days=1)
            out["open_in"] = await rejects(check_in(conn, open_shift, "user"))

            done = await visit("completed", offset_days=2)
            out["completed_in"] = await rejects(check_in(conn, done, "user"))

            fresh = await visit("scheduled", offset_days=3)
            out["out_before_in"] = await rejects(check_out(conn, fresh, "user"))
            await check_in(conn, fresh, "user", at=START + timedelta(days=3, hours=1))
            # Still `scheduled` and checked in — this is the already-checked-in guard.
            out["double_in"] = await rejects(check_in(conn, fresh, "user"))
            out["backwards_out"] = await rejects(
                check_out(conn, fresh, "user", at=START + timedelta(days=3))
            )

            out["unknown"] = await rejects(check_in(conn, str(uuid.uuid4()), "user"))

            for sid in sched_ids:
                await conn.execute("delete from public.events where entity_id = %s", (sid,))
                await conn.execute("delete from public.schedules where id = %s", (sid,))
            await conn.execute("delete from public.resources where id = %s", (ids["resource"],))
            await conn.execute("delete from public.clients where id = %s", (ids["client"],))
    finally:
        await db.close_pool()
    return out


def test_evv_check_in_out_transitions():
    r = asyncio.run(_scenario())

    status, check_in_at, check_out_at = r["row"]
    # Check-out completes the visit — the whole reason it is not just a stamp.
    assert status == "completed"
    assert check_in_at == START + timedelta(minutes=5)
    assert check_out_at == END + timedelta(minutes=10)
    # 08:05 -> 12:10 is 4h05m of actual worked time.
    assert r["out"]["actual_hours"] == 4.08
    assert r["out"]["status"] == "completed"

    types = [e[0] for e in r["events"]]
    assert types == ["schedule.checked_in", "schedule.checked_out"]
    # Every new writer sets a plain-language summary (CLAUDE.md).
    for _, payload in r["events"]:
        assert payload["summary"] and "{" not in payload["summary"]
    assert "4h 5m" in r["events"][1][1]["summary"]
    assert r["events"][1][1]["actual_hours"] == 4.08

    # Every rejection is a ScheduleError, never a leaked DB error.
    for key in ("double_in", "double_out", "open_in", "completed_in",
                "completed_in_after_out", "out_before_in", "backwards_out", "unknown"):
        assert r[key] is not None, f"{key} should have been rejected"
    assert r["unknown"].not_found
    assert not r["open_in"].not_found
    assert "no caregiver" in str(r["open_in"]) or "scheduled visit" in str(r["open_in"])
    assert "already checked in" in str(r["double_in"])
    assert "already checked out" in str(r["double_out"])
    assert "not been checked in" in str(r["out_before_in"])
    assert "after check-in" in str(r["backwards_out"])
