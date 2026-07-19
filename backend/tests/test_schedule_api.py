"""Scheduling backend (Module 12a) — schema/seeds (Task 1), transition seam
(Task 2), and the board/roster REST API (Task 5). Gated on NEXUS_APP_DB_URL.

The seam tests call services/views/schedule.py directly on a tenant transaction (the
move_stage precedent); the API tests drive the real router via httpx ASGITransport +
a minted tenant JWT. Every schedule/roster row a test creates is deleted afterward;
events are immutable and left in place.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

WALTER = "44444444-0000-0000-0000-000000000001"
ALICIA = "55555555-0000-0000-0000-000000000001"
BRIAN = "55555555-0000-0000-0000-000000000002"
CARMEN = "55555555-0000-0000-0000-000000000003"
DEREK = "55555555-0000-0000-0000-000000000004"
CNA = "22222222-0000-0000-0000-000000000001"
DEMENTIA = "22222222-0000-0000-0000-000000000003"
SEED_OPEN = "66666666-0000-0000-0000-000000000009"
SEED_CALLED_OUT = "66666666-0000-0000-0000-00000000000a"
SEED_REPLACEMENT = "66666666-0000-0000-0000-00000000000b"

UTC = timezone.utc


async def _events(conn, entity_id, event_type=None):
    from psycopg.rows import dict_row

    sql = ("select event_type, payload from public.events "
           "where entity_type='schedule' and entity_id=%s")
    params = [entity_id]
    if event_type:
        sql += " and event_type=%s"
        params.append(event_type)
    sql += " order by created_at"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def _raw_schedule(conn, resource_id, client_id, start, end, status):
    sid = str(uuid.uuid4())
    await conn.execute(
        """insert into public.schedules
             (id, tenant_id, resource_id, client_id, start_time, end_time, status)
           values (%s, app.current_tenant_id(), %s, %s, %s, %s, %s)""",
        (sid, resource_id, client_id, start, end, status),
    )
    return sid


# ===========================================================================
# Task 1 — schema coherence + seeds + RLS
# ===========================================================================
async def _schema_scenario():
    from psycopg import errors
    from psycopg.rows import dict_row

    from app import db

    out = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            # open + resource_id violates schedules_open_unassigned
            try:
                async with conn.transaction():
                    await conn.execute(
                        """insert into public.schedules
                             (tenant_id, resource_id, client_id, start_time, end_time, status)
                           values (app.current_tenant_id(), %s, %s, now(),
                                   now() + interval '1 hour', 'open')""",
                        (ALICIA, WALTER),
                    )
                out["open_with_resource_blocked"] = False
            except errors.CheckViolation:
                out["open_with_resource_blocked"] = True

            # scheduled + null resource violates schedules_assigned_has_resource
            try:
                async with conn.transaction():
                    await conn.execute(
                        """insert into public.schedules
                             (tenant_id, resource_id, client_id, start_time, end_time, status)
                           values (app.current_tenant_id(), null, %s, now(),
                                   now() + interval '1 hour', 'scheduled')""",
                        (WALTER,),
                    )
                out["scheduled_without_resource_blocked"] = False
            except errors.CheckViolation:
                out["scheduled_without_resource_blocked"] = True

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("select count(*) as n from public.schedules where status='open'")
                out["open_count"] = (await cur.fetchone())["n"]
                await cur.execute(
                    "select id, replaces_schedule_id, status from public.schedules "
                    "where replaces_schedule_id is not null"
                )
                out["replacement_rows"] = [dict(r) for r in await cur.fetchall()]

        # RLS: probe tenant sees zero schedules.
        async with db.tenant_tx(PROBE_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute("select count(*) from public.schedules")
                out["probe_schedule_count"] = (await cur.fetchone())[0]
        return out
    finally:
        await db.close_pool()


def test_schedule_schema_and_seeds():
    out = asyncio.run(_schema_scenario())
    assert out["open_with_resource_blocked"] is True
    assert out["scheduled_without_resource_blocked"] is True
    assert out["open_count"] >= 1  # the seeded open shift(s)
    # A called-out/replacement pair linked by replaces_schedule_id exists.
    assert any(str(r["replaces_schedule_id"]) == SEED_CALLED_OUT for r in out["replacement_rows"])
    assert out["probe_schedule_count"] == 0


async def _vocabulary_scenario():
    from app import db
    from app.main import app

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            return (await ac.get("/api/automations/vocabulary")).json()
    finally:
        await db.close_pool()


def test_schedule_in_vocabulary():
    vocab = asyncio.run(_vocabulary_scenario())
    event_types = vocab["triggers"]["event_types"]
    for t in ("schedule.created", "schedule.assigned", "schedule.called_out",
              "schedule.cancelled", "schedule.updated"):
        assert t in event_types
    # Field catalog exposes the schedule entity + its new columns, and maps the
    # call-out event to the schedule entity.
    entities = vocab["field_catalog"]["entities"]
    assert "schedule" in entities
    paths = {f["path"] for f in entities["schedule"]["fields"]}
    assert "entity.status" in paths
    assert "entity.required_qualification_ids" in paths
    assert vocab["field_catalog"]["event_entity"]["schedule.called_out"] == "schedule"


# ===========================================================================
# Task 2 — transition seam
# ===========================================================================
async def _seam_scenario():
    from app import db
    from app.services.views.schedule import (
        ScheduleError,
        assign,
        call_out,
        cancel,
        create_visits,
        set_outcome,
    )

    out = {}
    created: list[str] = []
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            # --- create unassigned -> open + schedule.created ---
            open_rows = await create_visits(
                conn, client_id=WALTER, resource_id=None,
                start=datetime(2027, 5, 3, 9, tzinfo=UTC),
                end=datetime(2027, 5, 3, 13, tzinfo=UTC),
                source_system="user",
            )
            created += [str(r["id"]) for r in open_rows]
            out["open_status"] = open_rows[0]["status"]
            out["open_events"] = await _events(conn, str(open_rows[0]["id"]), "schedule.created")

            # --- repeat_weekly_until +3 weeks -> 4 rows, all events, one tx ---
            base = datetime(2027, 6, 7, 9, tzinfo=UTC)
            series = await create_visits(
                conn, client_id=WALTER, resource_id=CARMEN, start=base,
                end=base + timedelta(hours=4),
                repeat_weekly_until=base + timedelta(weeks=3), source_system="user",
            )
            created += [str(r["id"]) for r in series]
            out["series_len"] = len(series)
            out["series_all_scheduled"] = all(r["status"] == "scheduled" for r in series)
            series_events = 0
            for r in series:
                series_events += len(await _events(conn, str(r["id"]), "schedule.created"))
            out["series_events"] = series_events

            # --- 13-week repeat rejects ---
            try:
                await create_visits(
                    conn, client_id=WALTER, resource_id=CARMEN,
                    start=datetime(2027, 9, 6, 9, tzinfo=UTC),
                    end=datetime(2027, 9, 6, 13, tzinfo=UTC),
                    repeat_weekly_until=datetime(2027, 9, 6, 9, tzinfo=UTC) + timedelta(weeks=13),
                    source_system="user",
                )
                out["repeat_13_rejected"] = False
            except ScheduleError:
                out["repeat_13_rejected"] = True

            # --- overlap in week 3 of an assigned series rejects the whole series ---
            dbase = datetime(2027, 6, 7, 9, tzinfo=UTC)  # Derek, weeks 0..3
            conflict_sid = await _raw_schedule(
                conn, DEREK, WALTER, dbase + timedelta(weeks=2),
                dbase + timedelta(weeks=2, hours=4), "scheduled",
            )
            created.append(conflict_sid)
            try:
                await create_visits(
                    conn, client_id=WALTER, resource_id=DEREK, start=dbase,
                    end=dbase + timedelta(hours=4),
                    repeat_weekly_until=dbase + timedelta(weeks=3), source_system="user",
                )
                out["series_conflict_rejected"] = False
            except ScheduleError:
                out["series_conflict_rejected"] = True
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.schedules where resource_id=%s "
                    "and start_time >= %s and start_time < %s",
                    (DEREK, dbase - timedelta(days=1), dbase + timedelta(weeks=5)),
                )
                out["derek_count_after_reject"] = (await cur.fetchone())[0]

            # --- assign on open -> scheduled + schedule.assigned ---
            to_fill = await create_visits(
                conn, client_id=WALTER, resource_id=None,
                start=datetime(2027, 7, 19, 9, tzinfo=UTC),
                end=datetime(2027, 7, 19, 13, tzinfo=UTC),
                required_qualification_ids=[DEMENTIA], source_system="user",
            )
            fill_id = str(to_fill[0]["id"])
            created.append(fill_id)
            assign_res = await assign(conn, fill_id, ALICIA, "user")  # Alicia lacks Dementia
            out["assign_status"] = assign_res["status"]
            out["assign_warnings"] = assign_res["warnings"]
            out["assign_events"] = await _events(conn, fill_id, "schedule.assigned")

            # --- assign hard conflict rejects ---
            brian_conf = await _raw_schedule(
                conn, BRIAN, WALTER, datetime(2027, 7, 5, 9, tzinfo=UTC),
                datetime(2027, 7, 5, 13, tzinfo=UTC), "scheduled",
            )
            created.append(brian_conf)
            open_overlap = await create_visits(
                conn, client_id=WALTER, resource_id=None,
                start=datetime(2027, 7, 5, 10, tzinfo=UTC),
                end=datetime(2027, 7, 5, 12, tzinfo=UTC), source_system="user",
            )
            created.append(str(open_overlap[0]["id"]))
            try:
                await assign(conn, str(open_overlap[0]["id"]), BRIAN, "user")
                out["assign_conflict_rejected"] = False
            except ScheduleError as exc:
                out["assign_conflict_rejected"] = exc.conflict

            # --- call_out -> original called_out + linked open replacement + both events ---
            co_rows = await create_visits(
                conn, client_id=WALTER, resource_id=CARMEN,
                start=datetime(2027, 8, 2, 9, tzinfo=UTC),
                end=datetime(2027, 8, 2, 13, tzinfo=UTC), source_system="user",
            )
            co_id = str(co_rows[0]["id"])
            created.append(co_id)
            co_res = await call_out(conn, co_id, "user")
            repl_id = co_res["replacement_schedule_id"]
            created.append(repl_id)
            out["co_id"] = co_id
            out["repl_id"] = repl_id
            async with conn.cursor() as cur:
                await cur.execute(
                    "select status, resource_id, replaces_schedule_id from public.schedules where id=%s",
                    (repl_id,),
                )
                r = await cur.fetchone()
                out["repl_status"], out["repl_resource"], out["repl_replaces"] = (
                    r[0], r[1], str(r[2]),
                )
                await cur.execute("select status from public.schedules where id=%s", (co_id,))
                out["original_status"] = (await cur.fetchone())[0]
            co_events = await _events(conn, co_id, "schedule.called_out")
            out["called_out_events"] = co_events
            out["repl_created_events"] = await _events(conn, repl_id, "schedule.created")

            # --- call_out on a completed visit rejects ---
            done_rows = await create_visits(
                conn, client_id=WALTER, resource_id=CARMEN,
                start=datetime(2027, 8, 9, 9, tzinfo=UTC),
                end=datetime(2027, 8, 9, 13, tzinfo=UTC), source_system="user",
            )
            done_id = str(done_rows[0]["id"])
            created.append(done_id)
            await set_outcome(conn, done_id, "completed", "user")
            out["outcome_events"] = await _events(conn, done_id, "schedule.updated")
            try:
                await call_out(conn, done_id, "user")
                out["callout_completed_rejected"] = False
            except ScheduleError:
                out["callout_completed_rejected"] = True

            # --- cancel emits its event ---
            cancel_rows = await create_visits(
                conn, client_id=WALTER, resource_id=None,
                start=datetime(2027, 8, 16, 9, tzinfo=UTC),
                end=datetime(2027, 8, 16, 13, tzinfo=UTC), source_system="user",
            )
            cancel_id = str(cancel_rows[0]["id"])
            created.append(cancel_id)
            await cancel(conn, cancel_id, "user")
            out["cancel_events"] = await _events(conn, cancel_id, "schedule.cancelled")

            # cleanup (null the replacement link first so deletes don't hit the FK)
            await conn.execute(
                "update public.schedules set replaces_schedule_id = null where id = any(%s)",
                (created,),
            )
            await conn.execute("delete from public.schedules where id = any(%s)", (created,))
        return out
    finally:
        await db.close_pool()


def test_schedule_seam():
    out = asyncio.run(_seam_scenario())

    assert out["open_status"] == "open"
    assert len(out["open_events"]) == 1
    assert "Open shift" in out["open_events"][0]["payload"]["summary"]

    assert out["series_len"] == 4
    assert out["series_all_scheduled"] is True
    assert out["series_events"] == 4

    assert out["repeat_13_rejected"] is True
    assert out["series_conflict_rejected"] is True
    assert out["derek_count_after_reject"] == 1  # only the pre-inserted conflict, no series rows

    assert out["assign_status"] == "scheduled"
    assert any("Missing qualification" in w for w in out["assign_warnings"])
    assert len(out["assign_events"]) == 1
    assert out["assign_conflict_rejected"] is True

    assert out["original_status"] == "called_out"
    assert out["repl_status"] == "open"
    assert out["repl_resource"] is None
    assert len(out["called_out_events"]) == 1
    # the called-out event points at the replacement; the replacement points back.
    assert out["called_out_events"][0]["payload"]["replacement_schedule_id"] == out["repl_id"]
    assert out["repl_replaces"] == out["co_id"]
    assert len(out["repl_created_events"]) == 1

    assert len(out["outcome_events"]) == 1
    assert out["callout_completed_rejected"] is True
    assert len(out["cancel_events"]) == 1


# ===========================================================================
# Task 5 — board / roster / transitions REST API
# ===========================================================================
async def _api_scenario():
    from app import db
    from app.main import app

    token = uuid.uuid4().hex[:8]
    out: dict = {"created_ids": [], "task_ids": []}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # --- 401 without a token ---
            noauth = httpx.AsyncClient(transport=transport, base_url="http://t")
            out["noauth_code"] = (await noauth.get("/api/schedule")).status_code
            await noauth.aclose()

            # --- week feed for the seeded open shift's week (3 days from now) ---
            week_of = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
            board = (await ac.get("/api/schedule", params={"week": week_of})).json()
            out["board_statuses"] = {v["status"] for v in board["visits"]}
            out["board_has_open"] = any(v["status"] == "open" for v in board["visits"])
            out["board_qual_names"] = [
                n for v in board["visits"] for n in v["required_qualification_names"]
            ]
            alicia = next((c for c in board["caregivers"] if c["id"] == ALICIA), None)
            out["alicia_hours"] = alicia["hours_this_week"] if alicia else None

            # --- POST expands a 3-week series (4 rows) ---
            base = "2028-02-07T09:00:00+00:00"  # a Monday far in the future
            created = await ac.post("/api/schedules", json={
                "client_id": WALTER, "resource_id": CARMEN,
                "start_time": base, "end_time": "2028-02-07T13:00:00+00:00",
                "repeat_weekly_until": "2028-02-28",
            })
            out["create_code"] = created.status_code
            visits = created.json()["visits"]
            out["series_len"] = len(visits)
            out["created_ids"] += [v["id"] for v in visits]

            # --- an open shift to exercise assign warnings + candidates + call-out ---
            open_shift = await ac.post("/api/schedules", json={
                "client_id": WALTER,
                "start_time": "2028-03-06T09:00:00+00:00",
                "end_time": "2028-03-06T13:00:00+00:00",
                "required_qualification_ids": [DEMENTIA],
            })
            open_id = open_shift.json()["visits"][0]["id"]
            out["created_ids"].append(open_id)
            out["open_shift_status"] = open_shift.json()["visits"][0]["status"]

            # candidates rank for open shifts (200) and still for scheduled (reassign)
            cand = await ac.get(f"/api/schedules/{open_id}/candidates")
            out["candidates_code"] = cand.status_code
            out["candidate_count"] = len(cand.json()["candidates"])

            assigned = await ac.post(f"/api/schedules/{open_id}/assign", json={"resource_id": ALICIA})
            out["assign_code"] = assigned.status_code
            out["assign_warnings"] = assigned.json()["warnings"]

            # scheduled visit still ranks (reassign flow); terminal visits 409.
            cand_scheduled = await ac.get(f"/api/schedules/{open_id}/candidates")
            out["candidates_scheduled_code"] = cand_scheduled.status_code

            # --- call-out round-trips the drawer payload shape ---
            call_shift = await ac.post("/api/schedules", json={
                "client_id": WALTER, "resource_id": CARMEN,
                "start_time": "2028-04-03T09:00:00+00:00",
                "end_time": "2028-04-03T13:00:00+00:00",
            })
            call_id = call_shift.json()["visits"][0]["id"]
            out["created_ids"].append(call_id)
            co = await ac.post(f"/api/schedules/{call_id}/call-out")
            out["callout_code"] = co.status_code
            out["callout_body"] = co.json()
            out["created_ids"].append(co.json()["replacement_schedule_id"])

            # --- PATCH refuses a stage-like status, accepts an outcome ---
            bad_patch = await ac.patch(f"/api/schedules/{open_id}", json={"status": "open"})
            out["bad_status_code"] = bad_patch.status_code
            outcome = await ac.patch(f"/api/schedules/{open_id}", json={"status": "completed"})
            out["outcome_code"] = outcome.status_code
            out["outcome_status"] = outcome.json()["status"]

            # a completed (terminal) visit has nothing to rank -> 409
            cand_terminal = await ac.get(f"/api/schedules/{open_id}/candidates")
            out["candidates_terminal_code"] = cand_terminal.status_code

            # --- roster PATCH emits one resource.updated naming changed fields ---
            rp = await ac.patch(f"/api/roster/{BRIAN}", json={
                "address": f"Addr {token}", "zip": "92104",
            })
            out["roster_code"] = rp.status_code
            out["roster_zip"] = rp.json()["zip"]

            # --- notify returns a queued action id ---
            notify = await ac.post(f"/api/schedules/{call_id}/notify", json={
                "resource_id": CARMEN, "message": f"Cover check {token}",
            })
            out["notify_code"] = notify.status_code
            out["notify_body"] = notify.json()
            if notify.json().get("task_id"):
                out["task_ids"].append(notify.json()["task_id"])

            # --- RLS isolation on the week feed (probe tenant sees nothing) ---
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers(PROBE_TENANT)
        ) as pc:
            pboard = await pc.get("/api/schedule", params={"week": week_of})
            out["probe_code"] = pboard.status_code
            out["probe_visits"] = len(pboard.json()["visits"])

        # inspect events / task, then clean up
        async with db.tenant_tx(DEMO_TENANT) as conn:
            out["roster_events"] = await _events_resource(conn, BRIAN)
            if out["task_ids"]:
                out["notify_task"] = await _task_with_action(conn, out["task_ids"][0])
            # restore Brian's address + clean up created schedules and notify task
            await conn.execute(
                "update public.resources set address='210 Market Street, San Diego', "
                "zip='92101' where id=%s", (BRIAN,)
            )
            await conn.execute(
                "update public.schedules set replaces_schedule_id = null where id = any(%s)",
                (out["created_ids"],),
            )
            await conn.execute(
                "delete from public.schedules where id = any(%s)", (out["created_ids"],)
            )
            for tid in out["task_ids"]:
                await conn.execute("delete from public.pending_actions where task_id=%s", (tid,))
                await conn.execute("delete from public.tasks where id=%s", (tid,))
        return out
    finally:
        await db.close_pool()


async def _events_resource(conn, resource_id):
    """The most recent resource.updated payload for a caregiver (the roster PATCH's)."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select payload from public.events where entity_type='resource' "
            "and entity_id=%s and event_type='resource.updated' "
            "order by created_at desc limit 1",
            (resource_id,),
        )
        row = await cur.fetchone()
        return row["payload"] if row else None


async def _task_with_action(conn, task_id):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select tool_name, tool_input, status from public.pending_actions where task_id=%s",
            (task_id,),
        )
        return await cur.fetchone()


def test_schedule_api():
    out = asyncio.run(_api_scenario())

    assert out["noauth_code"] == 401
    # week feed: the seeded open shift is present, its required qual resolved to a name.
    assert out["board_has_open"] is True
    assert "CNA" in out["board_qual_names"]
    assert out["alicia_hours"] is not None and out["alicia_hours"] >= 0

    assert out["create_code"] == 201
    assert out["series_len"] == 4
    assert out["open_shift_status"] == "open"

    assert out["candidates_code"] == 200
    assert out["assign_code"] == 200
    assert any("Missing qualification" in w for w in out["assign_warnings"])
    assert out["candidates_scheduled_code"] == 200  # scheduled still ranks (reassign)
    assert out["candidates_terminal_code"] == 409  # terminal visit has nothing to rank

    assert out["callout_code"] == 200
    assert out["callout_body"]["schedule_id"] and out["callout_body"]["replacement_schedule_id"]

    assert out["bad_status_code"] == 422  # stage-like status refused
    assert out["outcome_code"] == 200
    assert out["outcome_status"] == "completed"

    assert out["roster_code"] == 200
    assert out["roster_zip"] == "92104"
    assert out["roster_events"] is not None
    assert "address" in out["roster_events"]["fields"] and "zip" in out["roster_events"]["fields"]

    # notify queued a gated send_sms action (not executed inline).
    assert out["notify_code"] == 200
    assert out["notify_body"]["status"] == "queued"
    assert out["notify_body"]["pending_action_id"]
    assert out["notify_task"]["tool_name"] == "send_sms"
    assert out["notify_task"]["status"] == "pending"

    assert out["probe_code"] == 200
    assert out["probe_visits"] == 0
