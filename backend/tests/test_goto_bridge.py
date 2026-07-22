"""GoTo WebSocket bridge (v1.2.0, Task 5).

Two layers, both offline:

  * `handle_frame` — the decode-and-ingest step, with `ingest_payload` captured.
    This is where the "one inbound path" contract is asserted: the bridge must
    call the same seam the webhook route calls, with the frame unchanged.
  * `_pump` against a REAL local WebSocket server. A fake object with an
    `async recv` would prove the loop's shape but not that it can hold a socket,
    notice a replaced channel, or survive the far end hanging up — which are the
    three things that actually go wrong in production.

Deliberately not covered here: the ingest side's own behaviour (receipt →
resolution → event). `test_goto_resolution.py` drives that through the real
ingress, and duplicating it against a socket would test the seam twice.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.connectors import goto_bridge

pytest.importorskip("websockets")

CALL_FRAME = {
    "data": {
        "source": "call-events-report",
        "type": "REPORT_SUMMARY",
        "content": {
            "conversationSpaceId": "conv-1",
            "direction": "INBOUND",
            "caller": {"name": "Someone", "number": "+12025550101"},
            "callee": {"name": "Office", "number": "1000"},
        },
    }
}


@pytest.fixture
def captured(monkeypatch):
    """Every payload the bridge hands to the ingest seam."""
    seen: list[tuple[str, dict, str]] = []

    async def fake_ingest(source, payload, headers=None, *, tenant_id, **kwargs):
        seen.append((source, payload, tenant_id))
        return {"received": 1}

    monkeypatch.setattr(goto_bridge, "ingest_payload", fake_ingest)
    return seen


# --------------------------------------------------------------------------- #
# handle_frame
# --------------------------------------------------------------------------- #
def test_a_frame_reaches_the_shared_ingest_seam_unchanged(captured):
    """CLAUDE.md allows one inbound path per source. The bridge is transport: it
    must not normalize, resolve, or reshape before handing the frame over."""
    asyncio.run(goto_bridge.handle_frame("tenant-1", json.dumps(CALL_FRAME)))
    assert len(captured) == 1
    source, payload, tenant_id = captured[0]
    assert source == "goto"
    assert payload == CALL_FRAME
    assert tenant_id == "tenant-1"


def test_binary_frames_are_decoded(captured):
    asyncio.run(goto_bridge.handle_frame("t", json.dumps(CALL_FRAME).encode()))
    assert captured[0][1] == CALL_FRAME


def test_a_malformed_frame_is_dropped_rather_than_raised(captured):
    """One bad notification must not tear down a socket that is otherwise
    delivering calls correctly."""
    asyncio.run(goto_bridge.handle_frame("t", "not json at all"))
    assert captured == []


def test_a_non_object_frame_is_ignored(captured):
    asyncio.run(goto_bridge.handle_frame("t", "[1, 2, 3]"))
    assert captured == []


def test_an_ingest_failure_is_contained(monkeypatch):
    """If ingestion raises, the frame is lost but the socket lives. The opposite
    trade — dropping the connection on one bad record — loses every later call."""
    async def boom(*_a, **_kw):
        raise RuntimeError("database is having a moment")

    monkeypatch.setattr(goto_bridge, "ingest_payload", boom)
    asyncio.run(goto_bridge.handle_frame("t", json.dumps(CALL_FRAME)))  # must not raise


# --------------------------------------------------------------------------- #
# _pump, against a real local WebSocket server
# --------------------------------------------------------------------------- #
async def _serve(handler):
    """Start a local WS server and return `(url, server)`."""
    import websockets

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    return f"ws://127.0.0.1:{port}", server


def test_pushed_frames_are_ingested_over_a_live_socket(captured, monkeypatch):
    async def scenario():
        async def handler(ws):
            await ws.send(json.dumps(CALL_FRAME))
            await ws.send(json.dumps(CALL_FRAME))
            await asyncio.sleep(0.05)

        url, server = await _serve(handler)
        # The channel is never replaced, so the pump exits when the server closes.
        async def same_generation(_tenant):
            return {"url": url}, 1

        monkeypatch.setattr(goto_bridge, "_current_channel", same_generation)
        try:
            with pytest.raises(Exception):
                # The server hangs up; `_pump` propagates so `bridge_loop` can
                # back off and reconnect.
                await asyncio.wait_for(
                    goto_bridge._pump(url, "tenant-1", 1), timeout=5
                )
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
    assert len(captured) == 2


def test_the_pump_returns_cleanly_when_the_channel_generation_changes(
    captured, monkeypatch
):
    """A replaced channel means this socket is about to go dead. Returning
    (rather than raising) is what tells `bridge_loop` to reconnect immediately
    instead of backing off — a renewal is not a failure."""
    monkeypatch.setattr(goto_bridge, "_STATE_CHECK_SECONDS", 0.05)

    async def scenario():
        async def handler(ws):
            await asyncio.sleep(5)  # stay open, send nothing → idle check fires

        url, server = await _serve(handler)

        async def bumped_generation(_tenant):
            return {"url": url}, 2  # the runner replaced it

        monkeypatch.setattr(goto_bridge, "_current_channel", bumped_generation)
        try:
            await asyncio.wait_for(goto_bridge._pump(url, "tenant-1", 1), timeout=5)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())  # returns, does not raise


def test_the_loop_waits_instead_of_spinning_when_no_channel_exists(monkeypatch):
    """Before the runner's first cycle there is no channel. The bridge must wait
    for one rather than busy-looping on empty state."""
    monkeypatch.setattr(goto_bridge, "_NO_CHANNEL_WAIT", 0.01)
    monkeypatch.setattr(goto_bridge, "credentials_configured", lambda: True)
    monkeypatch.setattr(goto_bridge, "get_machine_tenant_id", lambda: "t")

    checks = {"n": 0}

    async def no_channel(_tenant):
        checks["n"] += 1
        return {}, 0

    monkeypatch.setattr(goto_bridge, "_current_channel", no_channel)

    async def scenario():
        task = asyncio.create_task(goto_bridge.bridge_loop())
        await asyncio.sleep(0.06)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    # Several checks (it kept looking) but not hundreds (it waited between them).
    assert 2 <= checks["n"] <= 20


def test_the_bridge_is_cancellable_while_backing_off(monkeypatch):
    """Shutdown must not hang on a bridge that is mid-retry.

    This is a regression test for a real defect: the backoff sleep was wrapped in
    `contextlib.suppress(asyncio.CancelledError)`, so the lifespan's `task.cancel()`
    was absorbed and the `while True` simply carried on. The app would have hung
    on every shutdown once GoTo credentials were configured and the far end was
    unreachable — and nothing else in the suite would have noticed.
    """
    monkeypatch.setattr(goto_bridge, "credentials_configured", lambda: True)
    monkeypatch.setattr(goto_bridge, "get_machine_tenant_id", lambda: "t")
    monkeypatch.setattr(goto_bridge, "_BACKOFF_START", 30.0)  # long enough to be caught in it

    async def a_channel(_tenant):
        return {"url": "ws://127.0.0.1:1"}, 1

    async def always_fails(*_a, **_kw):
        raise OSError("connection refused")

    monkeypatch.setattr(goto_bridge, "_current_channel", a_channel)
    monkeypatch.setattr(goto_bridge, "_pump", always_fails)

    async def scenario():
        task = asyncio.create_task(goto_bridge.bridge_loop())
        await asyncio.sleep(0.05)  # let it fail once and enter the 30s backoff
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # The timeout IS the assertion: without cancellation propagating, this hangs.
    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_the_bridge_does_not_start_without_credentials(monkeypatch):
    monkeypatch.setattr(goto_bridge, "credentials_configured", lambda: False)
    asyncio.run(asyncio.wait_for(goto_bridge.bridge_loop(), timeout=2))


def test_a_connection_failure_retries_without_hammering(monkeypatch):
    """An unreachable channel URL must keep retrying, spaced out.

    Asserted by counting attempts over a fixed window rather than by intercepting
    `asyncio.sleep`: patching sleep on the real module perturbs the event loop and
    the WebSocket client itself, so the measurement would change what it measures.
    Attempts-per-second is the property that actually matters — a bridge that
    reconnects in a tight loop is a self-inflicted denial of service against
    GoTo, and this catches that whatever the mechanism.
    """
    monkeypatch.setattr(goto_bridge, "credentials_configured", lambda: True)
    monkeypatch.setattr(goto_bridge, "get_machine_tenant_id", lambda: "t")
    monkeypatch.setattr(goto_bridge, "_BACKOFF_START", 0.05)
    monkeypatch.setattr(goto_bridge, "_BACKOFF_MAX", 0.10)

    attempts = {"n": 0}

    async def a_channel(_tenant):
        return {"url": "ws://127.0.0.1:1"}, 1

    async def refuses_instantly(*_a, **_kw):
        # `_pump` raising is exactly what a refused/dropped connection looks like
        # to the loop. Raising synthetically rather than dialling a dead port
        # keeps the timing under the test's control — a real refused connect can
        # take longer than the measurement window on Windows, which made this
        # test measure the OS rather than the backoff.
        attempts["n"] += 1
        raise OSError("connection refused")

    monkeypatch.setattr(goto_bridge, "_current_channel", a_channel)
    monkeypatch.setattr(goto_bridge, "_pump", refuses_instantly)

    async def scenario():
        task = asyncio.create_task(goto_bridge.bridge_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(asyncio.wait_for(scenario(), timeout=10))
    assert attempts["n"] >= 2, "it should keep retrying a dead channel"
    # 0.5s of window against a 0.05-0.10s backoff: a dozen or so is spaced out,
    # hundreds would mean no backoff at all.
    assert attempts["n"] < 60, f"retrying too fast ({attempts['n']} attempts in 0.5s)"
