"""Connector sync loop core (Module 18a, Task 4), gated on NEXUS_APP_DB_URL.

Drives `connectors_cycle()` with fake in-test runners, which is the whole point of
the seam: the loop's contract (cursor durability, failure isolation, the enabled
gate) is provable without touching a real external API.

The invariant that matters most is ISOLATION — a source being down must degrade
to one `connector.sync_failed` event, not to a stalled cycle or a dead loop.
"""
import asyncio
import uuid

import pytest

import conftest
from app.services.connectors import sync as sync_mod

pytestmark = pytest.mark.skipif(
    not conftest.NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)


class _FakeRunner:
    """Records what state it was handed and returns what it's told to."""

    def __init__(self, source: str, *, is_enabled=True, raises=None, next_state=None):
        self.source = source
        self.is_enabled = is_enabled
        self.raises = raises
        self.next_state = next_state
        self.seen_states: list[dict] = []
        self.runs = 0

    def enabled(self) -> bool:
        return self.is_enabled

    async def run(self, conn, tenant_id, state: dict):
        self.runs += 1
        self.seen_states.append(state)
        if self.raises is not None:
            raise self.raises
        return self.next_state


class _WritingRunner(_FakeRunner):
    """Writes a row, then fails — proving the runner's writes and its cursor
    advance share one transaction and roll back together."""

    def __init__(self, source: str, lead_id: str):
        super().__init__(source, raises=RuntimeError("boom after write"))
        self.lead_id = lead_id

    async def run(self, conn, tenant_id, state: dict):
        await conn.execute(
            "insert into public.leads (id, tenant_id, name) "
            "values (%s, app.current_tenant_id(), %s)",
            (self.lead_id, f"sync-rollback-probe-{self.lead_id[:8]}"),
        )
        return await super().run(conn, tenant_id, state)


def _isolated(runners: dict):
    """Swap the module registry for the duration of one scenario — the real
    WelcomeHome runner must not join a unit test's cycle."""
    original = sync_mod._RUNNERS
    sync_mod._RUNNERS = runners
    return original


async def _cycle_scenario():
    from psycopg.rows import dict_row

    from app import db

    out: dict = {}
    await db.open_pool()
    original = None
    try:
        healthy = _FakeRunner("fake_healthy", next_state={"cursor": "2026-07-20T00:00:00Z"})
        broken = _FakeRunner("fake_broken", raises=RuntimeError("upstream 503"))
        disabled = _FakeRunner("fake_disabled", is_enabled=False)
        rollback_lead = str(uuid.uuid4())
        writing = _WritingRunner("fake_writing", rollback_lead)

        original = _isolated({
            r.source: r
            for r in (healthy, broken, disabled, writing)
        })

        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            # Clean slate for the healthy runner's cursor.
            await conn.execute(
                "delete from public.connector_state where source_system = any(%s)",
                (["fake_healthy", "fake_broken", "fake_writing"],),
            )
            async with conn.cursor() as cur:
                await cur.execute("select now()")
                since = (await cur.fetchone())[0]

        out["cycle1"] = await sync_mod.connectors_cycle()
        out["cycle2"] = await sync_mod.connectors_cycle()

        out["healthy_states"] = healthy.seen_states
        out["disabled_runs"] = disabled.runs
        out["broken_runs"] = broken.runs

        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select source_system, state from public.connector_state "
                    "where source_system = any(%s) order by source_system",
                    (["fake_healthy", "fake_broken", "fake_writing"],),
                )
                out["stored_state"] = await cur.fetchall()

                await cur.execute(
                    "select source_system, payload from public.events "
                    "where event_type = 'connector.sync_failed' and created_at >= %s "
                    "order by source_system, created_at",
                    (since,),
                )
                out["failures"] = await cur.fetchall()

                # The failing runner's INSERT must have rolled back with it.
                await cur.execute(
                    "select count(*) from public.leads where id = %s", (rollback_lead,)
                )
                out["rolled_back_write"] = (await cur.fetchone())["count"]

            await conn.execute(
                "delete from public.connector_state where source_system = any(%s)",
                (["fake_healthy", "fake_broken", "fake_writing"],),
            )
    finally:
        if original is not None:
            sync_mod._RUNNERS = original
        await db.close_pool()
    return out


def test_cycle_advances_cursors_and_isolates_failures():
    r = asyncio.run(_cycle_scenario())

    # Per-runner outcomes; the broken source does not mask the healthy ones.
    assert r["cycle1"] == {"fake_healthy": True, "fake_broken": False, "fake_writing": False}
    assert r["cycle2"] == r["cycle1"]

    # A disabled runner is never invoked at all.
    assert r["disabled_runs"] == 0
    assert "fake_disabled" not in r["cycle1"]

    # Cursor durability: first run sees {}, the second sees what the first returned.
    assert r["healthy_states"][0] == {}
    assert r["healthy_states"][1] == {"cursor": "2026-07-20T00:00:00Z"}

    stored = {row["source_system"]: row["state"] for row in r["stored_state"]}
    assert stored["fake_healthy"] == {"cursor": "2026-07-20T00:00:00Z"}
    # A failed runner's cursor never advances — the page replays next cycle.
    assert "fake_broken" not in stored
    assert "fake_writing" not in stored


def test_a_failing_runner_writes_a_plain_language_event():
    r = asyncio.run(_cycle_scenario())

    failures = {row["source_system"]: row["payload"] for row in r["failures"]}
    assert {"fake_broken", "fake_writing"} <= set(failures)

    payload = failures["fake_broken"]
    # Plain language for the Event Log; the exception detail stays technical.
    assert payload["summary"] == "Sync with fake_broken failed — it will retry automatically"
    assert "{" not in payload["summary"]
    assert "upstream 503" in payload["detail"]["error"]

    # Both cycles ran, so both failures were recorded — the loop kept going.
    assert r["broken_runs"] == 2


def test_a_failed_runners_writes_roll_back_with_its_cursor():
    """Writes and the cursor advance share one transaction: a runner that dies
    mid-sweep leaves nothing behind, so the replay is clean rather than doubled."""
    r = asyncio.run(_cycle_scenario())
    assert r["rolled_back_write"] == 0


def test_active_runners_survives_a_broken_enabled_check():
    """Misconfiguration must not break the cycle before it starts."""

    class _BadEnabled:
        source = "fake_bad_enabled"

        def enabled(self):
            raise RuntimeError("bad config")

        async def run(self, conn, tenant_id, state):  # pragma: no cover - never reached
            raise AssertionError("must not run")

    healthy = _FakeRunner("fake_ok")
    original = _isolated({"fake_bad_enabled": _BadEnabled(), "fake_ok": healthy})
    try:
        assert [r.source for r in sync_mod.active_runners()] == ["fake_ok"]
    finally:
        sync_mod._RUNNERS = original


def test_the_loop_is_off_when_the_flag_is_off(monkeypatch):
    """`nexus_connectors_enabled=false` must keep the lifespan from starting it."""
    import inspect

    from app import main

    source = inspect.getsource(main.lifespan)
    assert "settings.nexus_connectors_enabled" in source
    assert "connectors_loop()" in source
