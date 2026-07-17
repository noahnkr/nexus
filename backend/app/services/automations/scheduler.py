"""Automations engine loops (Module 7b) — the background machinery that moves runs
without a request: an event dispatcher, a cron scheduler, a wait-waker, and a
stale-run recovery sweep.

`run_cycle()` runs the four phases in order under `get_machine_tenant_id()` (the
single-tenant phase; a multi-tenant deployment later wraps the cycle in a tenant
loop — that is the documented seam). Each phase is a self-contained, bounded
`*_once()` tick that opens its own `tenant_tx`(s) and returns a count, so tests can
drive each deterministically; the `while True` wrapper (`engine_loop`) stays trivial
and never lets one bad cycle kill the loop.

Invariants (do not weaken):
  * the event cursor advances only AFTER its batch commits (survives restarts);
  * `next_fire_at` advances BEFORE a cron recipe runs (a slow run never double-fires);
  * every claim uses `for update skip locked` (no two cycles grab the same work);
  * `start_run` commits before `advance_run` (advance opens its own per-step txns).

Loop guard (user-locked): events with `source_system='automation'` are never
dispatched — automations cannot trigger automations this phase.
"""
from __future__ import annotations

import asyncio
import logging

from psycopg.rows import dict_row
from psycopg.types.json import Json

from ...config import settings
from ...db import tenant_tx
from ...deps import get_machine_tenant_id
from .engine import advance_run, start_run

logger = logging.getLogger("nexus.automations")

# Reserved connector_state key holding the dispatcher's durable cursor.
_CURSOR_KEY = "_automations"
# Per-tick batch caps so a burst in one phase can't starve the others.
_DISPATCH_BATCH = 200
_CRON_BATCH = 100
_WAKE_BATCH = 100
_RECOVER_BATCH = 100


# ---------------------------------------------------------------------------
# durable cursor (connector_state under the reserved _automations key)
# ---------------------------------------------------------------------------
async def _read_cursor(conn) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select state from public.connector_state where source_system = %s",
            (_CURSOR_KEY,),
        )
        row = await cur.fetchone()
    return row["state"] if row else None


async def _write_cursor(conn, tenant_id: str, created_at, event_id) -> None:
    state = {
        "last_event_created_at": created_at.isoformat()
        if hasattr(created_at, "isoformat") else created_at,
        "last_event_id": str(event_id),
    }
    await conn.execute(
        """insert into public.connector_state (tenant_id, source_system, state)
             values (%s, %s, %s)
           on conflict (tenant_id, source_system)
             do update set state = excluded.state""",
        (tenant_id, _CURSOR_KEY, Json(state)),
    )


def _matches(automation: dict, event: dict) -> bool:
    trig = automation.get("trigger") or {}
    if trig.get("event_type") != event["event_type"]:
        return False
    want_source = trig.get("source_system")
    return want_source is None or want_source == event["source_system"]


# ---------------------------------------------------------------------------
# phase 1 — event dispatcher
# ---------------------------------------------------------------------------
# events.id is gen_random_uuid() (not monotonic), so the cursor is a (created_at, id)
# keyset — the Module 4 pagination pattern — stored as both values in connector_state.
async def dispatch_once(tenant_id: str) -> int:
    started: list[str] = []
    async with tenant_tx(tenant_id) as conn:
        cursor = await _read_cursor(conn)

        if cursor is None:
            # First ever run: initialize the cursor to the latest event and process
            # nothing — never replay pre-existing history.
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select created_at, id from public.events "
                    "order by created_at desc, id desc limit 1"
                )
                latest = await cur.fetchone()
            if latest is not None:
                await _write_cursor(conn, tenant_id, latest["created_at"], latest["id"])
            else:
                await _write_cursor(conn, tenant_id, "1970-01-01T00:00:00+00:00",
                                    "00000000-0000-0000-0000-000000000000")
            return 0

        # New events strictly after the cursor, oldest first.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """select id, event_type, source_system, entity_type, entity_id,
                          payload, created_at
                     from public.events
                    where (created_at, id) > (%s::timestamptz, %s::uuid)
                    order by created_at asc, id asc
                    limit %s""",
                (cursor["last_event_created_at"], cursor["last_event_id"], _DISPATCH_BATCH),
            )
            events = await cur.fetchall()
        if not events:
            return 0

        # Active event-trigger automations, loaded once for the batch.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id, name, trigger, conditions, steps from public.automations "
                "where status = 'active' and trigger->>'type' = 'event'"
            )
            automations = await cur.fetchall()

        for ev in events:
            if ev["source_system"] == "automation":
                continue  # loop guard — automations never trigger automations
            for auto in automations:
                if _matches(auto, ev):
                    run_id = await start_run(conn, tenant_id, auto, trigger_event=ev)
                    if run_id:
                        started.append(run_id)

        # Cursor advances only after the whole batch is processed in this tx.
        last = events[-1]
        await _write_cursor(conn, tenant_id, last["created_at"], last["id"])

    # start_run rows are committed; drive each new run forward.
    for run_id in started:
        await advance_run(tenant_id, run_id)
    return len(started)


# ---------------------------------------------------------------------------
# phase 2 — cron scheduler
# ---------------------------------------------------------------------------
def next_fire(expression: str):
    """Next occurrence strictly after now. Base = now, so a run that missed slots
    while the app was down fires once on catch-up, not once per missed slot. Also
    used by the API's PATCH to (re)arm `next_fire_at` on activation/expression change."""
    from datetime import datetime, timezone

    from croniter import croniter

    return croniter(expression, datetime.now(timezone.utc)).get_next(datetime)


async def tick_cron_once(tenant_id: str) -> int:
    started: list[str] = []
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """select id, name, trigger, conditions, steps
                     from public.automations
                    where status = 'active' and trigger->>'type' = 'cron'
                      and next_fire_at is not null and next_fire_at <= now()
                    order by next_fire_at asc
                    limit %s
                    for update skip locked""",
                (_CRON_BATCH,),
            )
            due = await cur.fetchall()

        for auto in due:
            # Advance next_fire_at BEFORE running so a slow recipe can't double-fire.
            expr = (auto["trigger"] or {}).get("expression")
            if not expr:
                continue
            await conn.execute(
                "update public.automations set next_fire_at = %s where id = %s",
                (next_fire(expr), auto["id"]),
            )
            run_id = await start_run(conn, tenant_id, auto)  # cron runs have no entity
            if run_id:
                started.append(run_id)

    for run_id in started:
        await advance_run(tenant_id, run_id)
    return len(started)


# ---------------------------------------------------------------------------
# phase 3 — waker (due `waiting` runs)
# ---------------------------------------------------------------------------
async def wake_due_once(tenant_id: str) -> int:
    claimed: list[str] = []
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """select id from public.automation_runs
                    where status = 'waiting' and wake_at is not null and wake_at <= now()
                    order by wake_at asc
                    limit %s
                    for update skip locked""",
                (_WAKE_BATCH,),
            )
            rows = await cur.fetchall()
        for row in rows:
            await conn.execute(
                "update public.automation_runs set status = 'running' where id = %s",
                (row["id"],),
            )
            claimed.append(str(row["id"]))

    for run_id in claimed:
        await advance_run(tenant_id, run_id)
    return len(claimed)


# ---------------------------------------------------------------------------
# phase 4 — recovery sweep (stuck `running` runs + un-armed cron)
# ---------------------------------------------------------------------------
async def recover_stale_once(tenant_id: str) -> int:
    stale: list[str] = []
    async with tenant_tx(tenant_id) as conn:
        # A run stuck in `running` past the stale threshold means a process died
        # mid-advance; the one-tx-per-step design makes re-entry safe.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """select id from public.automation_runs
                    where status = 'running'
                      and updated_at < now() - make_interval(mins => %s)
                    order by updated_at asc
                    limit %s
                    for update skip locked""",
                (settings.nexus_automations_stale_minutes, _RECOVER_BATCH),
            )
            stale = [str(r["id"]) for r in await cur.fetchall()]

        # Arm any active cron automation missing a next_fire_at (e.g. armed before
        # this code shipped, or activated out-of-band) so it starts firing.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """select id, trigger from public.automations
                    where status = 'active' and trigger->>'type' = 'cron'
                      and next_fire_at is null
                    for update skip locked"""
            )
            unarmed = await cur.fetchall()
        for auto in unarmed:
            expr = (auto["trigger"] or {}).get("expression")
            if expr:
                await conn.execute(
                    "update public.automations set next_fire_at = %s where id = %s",
                    (next_fire(expr), auto["id"]),
                )

    for run_id in stale:
        await advance_run(tenant_id, run_id)
    return len(stale)


# ---------------------------------------------------------------------------
# cycle + loop
# ---------------------------------------------------------------------------
async def run_cycle() -> dict:
    """One full pass: dispatch -> cron -> wake -> recover. Each phase is guarded so
    a failure in one still lets the others run; the counts feed logging/tests."""
    tenant_id = get_machine_tenant_id()
    counts = {"dispatched": 0, "cron": 0, "woken": 0, "recovered": 0}
    for key, fn in (
        ("dispatched", dispatch_once),
        ("cron", tick_cron_once),
        ("woken", wake_due_once),
        ("recovered", recover_stale_once),
    ):
        try:
            counts[key] = await fn(tenant_id)
        except Exception:  # noqa: BLE001 — one bad phase must not abort the cycle
            logger.exception("automation phase '%s' failed", key)
    return counts


async def engine_loop() -> None:
    """The background task body — run a cycle, sleep, repeat. A broad guard keeps a
    bad cycle from killing the loop; cancellation (lifespan shutdown) propagates."""
    logger.info("automations engine loop started (poll=%ss)",
                settings.nexus_automations_poll_seconds)
    while True:
        try:
            await run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("automation cycle failed")
        await asyncio.sleep(settings.nexus_automations_poll_seconds)


