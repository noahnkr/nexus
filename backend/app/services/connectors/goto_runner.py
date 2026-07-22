"""GoTo Connect sync runner (v1.2.0, Task 5) — channel upkeep.

Unlike the WelcomeHome runner this one ingests nothing on its own. GoTo pushes;
`goto_bridge.py` receives. What this runner does each cycle is keep the pipe
alive, which is a polling job even though the data flow is not:

  * discover the account key once and cache it;
  * create the notification channel when there isn't a live one;
  * re-create it when the current one is near expiry;
  * do nothing at all when the channel is healthy.

**Why upkeep is per-cycle rather than daily.** A WebSocket notification channel's
`channelLifetime` comes back at roughly 1200 seconds — twenty minutes. A daily
renewal job would leave the office's phone integration dead for 23 hours and 40
minutes of every day. The connector poll interval is the natural cadence.

STATE (`connector_state.state` for source `goto`)::

    {"account_key": "...",
     "channel": {"id": ..., "url": "wss://...", "expires_at": <unix seconds>,
                 "subscription_ids": [...]},
     "channel_generation": 7}

`channel_generation` is what tells the bridge to reconnect: the bridge watches it
and drops its socket when it changes. A counter rather than a URL comparison
because it makes "the channel was replaced" a single unambiguous signal, even if
GoTo ever hands back the same URL twice.

RENEWAL IS RE-CREATION. There is no renew endpoint; a channel is replaced by
creating a new one and re-subscribing. So renewal deliberately happens EARLY —
`_RENEW_MARGIN_SECONDS` before expiry — because a channel that lapses before its
replacement exists is a window where calls are silently not ingested.
"""
from __future__ import annotations

import logging
import time

from ...config import settings
from .goto_client import GoToClient, credentials_configured
from .sync import register_runner

log = logging.getLogger("nexus.connectors.goto")

# The channel is replaced once it is within this long of expiring. A third of a
# ~1200s lifetime: comfortably more than one poll interval, so the replacement
# always happens a full cycle before the old channel could lapse.
_RENEW_MARGIN_SECONDS = 420

# Shows up in GoTo's channel list; makes it obvious which app owns a channel.
CHANNEL_NICKNAME = "nexus-connect"


def _expired_or_expiring(channel: dict, now: float) -> bool:
    expires_at = channel.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        return True
    return expires_at - now <= _RENEW_MARGIN_SECONDS


class GoToRunner:
    """Keeps the notification channel alive. Registered like any other runner."""

    source = "goto"

    def __init__(self, client_factory=GoToClient) -> None:
        # Injectable so tests drive a mocked client without patching imports.
        self._client_factory = client_factory

    def enabled(self) -> bool:
        return credentials_configured()

    async def run(self, conn, tenant_id: str, state: dict) -> dict | None:
        """One upkeep pass. Returns the new state, or None when nothing changed.

        Returning None on the healthy path matters: it is the common case (a
        channel is good for twenty minutes and cycles are far more frequent than
        that), and writing an identical state row every cycle would be pure churn
        on a table other runners share.
        """
        new_state = dict(state)
        channel = new_state.get("channel") or {}

        if not _expired_or_expiring(channel, time.time()):
            return None

        async with self._client_factory() as goto:
            account_key = new_state.get("account_key")
            if not account_key:
                account_key = await goto.account_key()
                new_state["account_key"] = account_key

            created = await goto.create_channel(CHANNEL_NICKNAME)
            subscription_ids = await goto.subscribe_calls(
                created["channel_id"], str(account_key)
            )
            # Inbound SMS rides the same channel (established empirically — see
            # `subscribe_messages`), so one channel serves both and there is no
            # second poll path to maintain. Both calls raise if their
            # subscription does not take: a channel that is up but subscribed to
            # nothing is the failure mode that looked healthy for all of v1.2.0,
            # and a loud `connector.sync_failed` each cycle is the cure.
            subscription_ids += await goto.subscribe_messages(
                created["channel_id"], str(account_key)
            )

        lifetime = created["lifetime_seconds"] or 1200
        new_state["channel"] = {
            "id": created["channel_id"],
            "url": created["url"],
            "expires_at": time.time() + lifetime,
            "subscription_ids": subscription_ids,
        }
        new_state["channel_generation"] = int(new_state.get("channel_generation", 0)) + 1

        log.info(
            "goto channel %s (lifetime=%ss, subscriptions=%d, generation=%d)",
            "created" if not channel else "renewed",
            lifetime,
            len(subscription_ids),
            new_state["channel_generation"],
        )
        return new_state


def business_number() -> str:
    """The office's own E.164 number, for callers that need the 'us' side."""
    return settings.goto_business_number


register_runner(GoToRunner())
