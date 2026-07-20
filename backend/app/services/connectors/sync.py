"""Connector sync loop (Module 18a) — the inbound path for sources with no webhooks.

Some systems don't push. WelcomeHome has no webhook endpoints at all (verified
against the live API), so the only way in is to poll its export tables. This is
the in-process loop that does that, and it deliberately mirrors the proven M7
automations engine: a trivial `while True` wrapper around a bounded, testable
`connectors_cycle()` that can never let one bad source take the process down.

The seam is one small protocol:

    class SyncRunner(Protocol):
        source: str                                   # events.source_system
        def enabled(self) -> bool: ...                # credentials configured?
        async def run(self, conn, tenant_id, state: dict) -> dict | None: ...
        async def after_commit(self, tenant_id) -> None: ...   # optional

`run` receives the runner's durable state and returns the state to persist (or
None to leave it untouched). It runs inside ONE `tenant_tx`, so a runner's writes
and its cursor advance commit together — a crash mid-page replays that page
rather than skipping it. At-least-once, never at-most-once: connector ingestion
is idempotent by entity resolution, so a replay is cheap and a skip is data loss.

`after_commit` is the optional escape hatch for work that must NOT hold a
database transaction open — chiefly calling out to an embeddings API to ingest
documents a sweep discovered. Doing that inside `run` would pin a pooled
connection for the length of a network round-trip per document, which is how a
sync loop becomes a connection-pool outage. It runs only after `run` committed,
and its failures are isolated the same way.

Isolation is the other half. A runner that raises gets its failure recorded as a
`connector.sync_failed` event (plain summary for the Event Log, exception detail
for the technical view) and is simply skipped for that cycle; every other runner
in the same cycle still runs, and the loop continues. A connector outage must
never look like an application outage.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

from psycopg.rows import dict_row
from psycopg.types.json import Json

from ...config import settings
from ...db import tenant_tx
from ...deps import get_machine_tenant_id
from ...llm import traceable
from ..events import log_event

logger = logging.getLogger("nexus.connectors.sync")


@runtime_checkable
class SyncRunner(Protocol):
    """One pollable source. Implementations live next to their client/mapper
    (e.g. `wh_runner.WelcomeHomeRunner`) and register via `register_runner`."""

    source: str

    def enabled(self) -> bool:
        """False when this source's credentials aren't configured — an unset key
        means "no runner", not a failing one."""
        ...

    async def run(self, conn, tenant_id: str, state: dict) -> dict | None:
        """Do one sweep on the caller's tenant-scoped connection. Return the new
        durable state, or None to leave the stored state unchanged."""
        ...


# source -> runner. Populated at import time by each runner module, exactly like
# the adapter registry.
_RUNNERS: dict[str, SyncRunner] = {}


def register_runner(runner: SyncRunner) -> None:
    _RUNNERS[runner.source] = runner


def get_runner(source: str) -> SyncRunner | None:
    return _RUNNERS.get(source)


def active_runners() -> list[SyncRunner]:
    """Registered runners whose credentials are configured, in registration order.
    A runner whose `enabled()` itself raises is treated as disabled and logged —
    misconfiguration must not break the cycle before it starts."""
    active: list[SyncRunner] = []
    for runner in _RUNNERS.values():
        try:
            if runner.enabled():
                active.append(runner)
        except Exception:  # noqa: BLE001
            logger.exception("connector runner '%s' failed its enabled() check", runner.source)
    return active


# ---------------------------------------------------------------------------
# durable per-source state (connector_state, keyed by source_system)
# ---------------------------------------------------------------------------
async def read_state(conn, source: str) -> dict:
    """The runner's stored state, or `{}` on its first ever run."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select state from public.connector_state where source_system = %s",
            (source,),
        )
        row = await cur.fetchone()
    return (row["state"] or {}) if row else {}


async def write_state(conn, tenant_id: str, source: str, state: dict) -> None:
    await conn.execute(
        """insert into public.connector_state (tenant_id, source_system, state)
             values (%s, %s, %s)
           on conflict (tenant_id, source_system)
             do update set state = excluded.state""",
        (tenant_id, source, Json(state)),
    )


# ---------------------------------------------------------------------------
# one runner, one cycle
# ---------------------------------------------------------------------------
async def run_one(runner: SyncRunner, tenant_id: str) -> bool:
    """Run a single runner in its own transaction. Returns True on success.

    A failure is recorded, not raised: the caller keeps going. The failure event
    is written in a SEPARATE transaction because the runner's own transaction is
    already rolled back by the time we get here — writing the record inside it
    would roll the record back too.
    """
    try:
        async with tenant_tx(tenant_id) as conn:
            state = await read_state(conn, runner.source)
            new_state = await runner.run(conn, tenant_id, state)
            if new_state is not None:
                await write_state(conn, tenant_id, runner.source, new_state)

        after = getattr(runner, "after_commit", None)
        if after is not None:
            await after(tenant_id)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — one bad source must not stop the cycle
        logger.exception("connector sync '%s' failed", runner.source)
        await _log_failure(tenant_id, runner.source, exc)
        return False


async def _log_failure(tenant_id: str, source: str, exc: Exception) -> None:
    """Record `connector.sync_failed`. Best-effort: if even this write fails (the
    database is what's down), log and move on rather than escalate."""
    try:
        async with tenant_tx(tenant_id) as conn:
            await log_event(
                conn,
                tenant_id=tenant_id,
                source_system=source,
                event_type="connector.sync_failed",
                payload={
                    "summary": f"Sync with {source} failed — it will retry automatically",
                    "detail": {"error": f"{type(exc).__name__}: {exc}"[:1000]},
                },
            )
    except Exception:  # noqa: BLE001
        logger.exception("could not record connector.sync_failed for '%s'", source)


@traceable(run_type="chain", name="connector_sync")
async def connectors_cycle() -> dict:
    """One pass over every enabled runner. Returns `{source: ok}` for logging and
    tests. Never raises — that is the whole contract with the loop."""
    tenant_id = get_machine_tenant_id()
    results: dict[str, bool] = {}
    for runner in active_runners():
        results[runner.source] = await run_one(runner, tenant_id)
    return results


async def connectors_loop() -> None:
    """The background task body — cycle, sleep, repeat. Cancellation (lifespan
    shutdown) propagates; nothing else gets out."""
    logger.info(
        "connector sync loop started (poll=%ss, runners=%s)",
        settings.nexus_connectors_poll_seconds,
        [r.source for r in active_runners()] or "none",
    )
    while True:
        try:
            await connectors_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("connector sync cycle failed")
        await asyncio.sleep(settings.nexus_connectors_poll_seconds)
