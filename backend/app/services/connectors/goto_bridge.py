"""GoTo Connect WebSocket bridge (v1.2.0, Task 5).

The third lifespan background task, alongside the automations engine and the
connector sync loop. It holds one WebSocket to the notification channel the
`goto_runner` keeps alive, and turns every frame into a call to the SAME
`ingest_payload` seam the webhook route uses.

That last point is the design constraint, not an implementation detail: CLAUDE.md
allows exactly one inbound path per source. The bridge is transport, not a second
ingress — it does no verification, no resolution and no writing of its own. A
frame goes in, `ingest_payload("goto", frame)` handles the rest, and the raw
receipt lands in `events` before normalization exactly as it would for a webhook.

HOW IT COORDINATES WITH THE RUNNER. The runner owns channel lifecycle and writes
`connector_state.state.goto`; the bridge only reads it. Each iteration:

  1. read the state; no live channel yet → wait and look again (the runner runs
     on its own cycle and will create one);
  2. connect to the channel URL and pump frames;
  3. drop the socket when `channel_generation` changes — the runner replaced the
     channel and this socket is about to go dead anyway;
  4. on any disconnect, back off exponentially and retry.

Nothing here ever raises out to the lifespan. A phone integration that is down is
a degraded feature; a phone integration that takes the API process down with it
is an outage.

VERIFICATION vs THE WEBHOOK ROUTE. A frame arriving on an authenticated WebSocket
this process opened is already authenticated by construction — we hold the
channel URL, which is a capability. So the bridge calls `ingest_payload` directly
rather than posting through the HTTP route and inventing a signature for our own
traffic.
"""
from __future__ import annotations

import asyncio
import json
import logging

from ...db import tenant_tx
from ...deps import get_machine_tenant_id
from .goto_client import credentials_configured
from .ingest import ingest_payload
from .sync import read_state

log = logging.getLogger("nexus.connectors.goto.bridge")

# Reconnect backoff. Starts fast (a dropped socket is usually transient) and
# tops out well under the channel lifetime so a long outage still reconnects
# promptly once the far end returns.
_BACKOFF_START = 2.0
_BACKOFF_MAX = 60.0

# How long to wait when there is no channel yet. The runner creates one on its
# own cycle; polling faster than that just spins.
_NO_CHANNEL_WAIT = 15.0

# How often to re-read state while connected, to notice a generation change.
_STATE_CHECK_SECONDS = 30.0


async def _current_channel(tenant_id: str) -> tuple[dict, int]:
    """The live channel and its generation from `connector_state`."""
    async with tenant_tx(tenant_id) as conn:
        state = await read_state(conn, "goto")
    channel = state.get("channel") or {}
    return channel, int(state.get("channel_generation", 0))


async def handle_frame(tenant_id: str, raw) -> None:
    """Decode one WebSocket frame and hand it to the ingest seam.

    Failures are contained per frame: one malformed notification must not tear
    down a socket that is otherwise delivering calls correctly.
    """
    try:
        text = raw if isinstance(raw, str) else bytes(raw).decode("utf-8", "replace")
        payload = json.loads(text)
    except (ValueError, TypeError):
        log.warning("goto bridge received a non-JSON frame; ignoring")
        return
    if not isinstance(payload, dict):
        return

    try:
        await ingest_payload("goto", payload, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — one bad frame must not kill the socket
        log.exception("goto bridge failed to ingest a frame")


async def _pump(url: str, tenant_id: str, generation: int) -> None:
    """Hold one socket open, ingesting frames, until the channel is replaced.

    Imported lazily so the module stays importable (and testable) on a
    deployment that has not installed `websockets` yet.
    """
    import websockets

    async with websockets.connect(url) as ws:
        log.info("goto bridge connected (generation=%d)", generation)
        while True:
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=_STATE_CHECK_SECONDS)
            except asyncio.TimeoutError:
                # Idle. Use the lull to check whether our channel was replaced.
                _, current = await _current_channel(tenant_id)
                if current != generation:
                    log.info("goto channel replaced (generation %d -> %d); reconnecting",
                             generation, current)
                    return
                continue
            await handle_frame(tenant_id, frame)


async def bridge_loop() -> None:
    """The background task body. Cancellation propagates; nothing else escapes."""
    if not credentials_configured():
        log.info("goto bridge not started — credentials are not configured")
        return

    tenant_id = get_machine_tenant_id()
    backoff = _BACKOFF_START
    log.info("goto bridge started")

    while True:
        try:
            channel, generation = await _current_channel(tenant_id)
            url = channel.get("url")
            if not url:
                await asyncio.sleep(_NO_CHANNEL_WAIT)
                continue

            await _pump(str(url), tenant_id, generation)
            # A clean return means the channel was replaced, not that anything
            # went wrong — reconnect immediately rather than backing off.
            backoff = _BACKOFF_START
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a phone outage is not an app outage
            log.exception("goto bridge connection failed; retrying in %.0fs", backoff)
            # NOT wrapped in `suppress(CancelledError)`. Swallowing cancellation
            # here would make this task unstoppable: the lifespan cancels it on
            # shutdown, the sleep would absorb that, and the `while True` would
            # carry on — hanging shutdown forever. Cancellation must pass through
            # every await in this loop.
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


__all__ = ["bridge_loop", "handle_frame"]
