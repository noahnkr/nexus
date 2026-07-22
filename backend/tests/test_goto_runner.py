"""GoTo channel upkeep (v1.2.0, Task 5) — offline, against a fake client.

The runner's whole job is deciding *when* to replace a notification channel, so
these tests are about that decision and nothing else. A fake client stands in for
the API: it records what was asked of it, which is what lets "did nothing" be an
assertable outcome rather than an absence of evidence.

Why the healthy-path test matters most: a channel lives ~1200 seconds and the
connector cycle runs far more often than that, so "do nothing" is the overwhelming
majority of all cycles. A runner that re-created the channel every cycle would
still look like it worked — calls would keep arriving — while burning API quota
and forcing the bridge to reconnect constantly.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services.connectors.goto_runner import (
    _RENEW_MARGIN_SECONDS,
    CHANNEL_NICKNAME,
    GoToRunner,
)


class FakeGoTo:
    """Stands in for `GoToClient`, counting the calls the runner makes."""

    def __init__(self, *, lifetime=1200, subscriptions=("sub-1",), account="acct-9"):
        self.lifetime = lifetime
        self.subscriptions = list(subscriptions)
        self.account = account
        self.channels_created: list[str] = []
        self.subscribed: list[tuple[str, str]] = []
        self.message_subscribed: list[tuple[str, str]] = []
        self.account_lookups = 0
        self._serial = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def account_key(self):
        self.account_lookups += 1
        return self.account

    async def create_channel(self, nickname):
        self.channels_created.append(nickname)
        self._serial += 1
        return {
            "channel_id": f"chan-{self._serial}",
            "url": f"wss://example.test/{self._serial}",
            "lifetime_seconds": self.lifetime,
        }

    async def subscribe_calls(self, channel_id, account_key):
        self.subscribed.append((channel_id, account_key))
        return list(self.subscriptions)

    async def subscribe_messages(self, channel_id, account_key):
        self.message_subscribed.append((channel_id, account_key))
        return ["msg-sub-1"]


def _run(state, fake):
    runner = GoToRunner(client_factory=lambda: fake)
    return asyncio.run(runner.run(None, "tenant-1", state))


def _healthy_channel(seconds_left=1000):
    return {
        "channel": {
            "id": "chan-old",
            "url": "wss://example.test/old",
            "expires_at": time.time() + seconds_left,
            "subscription_ids": ["sub-old"],
        },
        "channel_generation": 4,
        "account_key": "acct-9",
    }


# --------------------------------------------------------------------------- #
# create-when-absent
# --------------------------------------------------------------------------- #
def test_first_ever_run_discovers_the_account_and_creates_a_channel():
    fake = FakeGoTo()
    state = _run({}, fake)

    assert fake.account_lookups == 1
    assert fake.channels_created == [CHANNEL_NICKNAME]
    assert state is not None
    assert state["account_key"] == "acct-9"
    assert state["channel"]["url"] == "wss://example.test/1"
    assert state["channel"]["subscription_ids"] == ["sub-1", "msg-sub-1"]


def test_the_channel_is_subscribed_to_call_events():
    """A channel nobody subscribed to is an open socket that receives nothing —
    the failure mode that looks healthiest from the outside."""
    fake = FakeGoTo()
    _run({}, fake)
    assert fake.subscribed == [("chan-1", "acct-9")]


def test_one_channel_carries_both_calls_and_inbound_sms():
    """SMS rides the notification channel rather than needing a Messaging-API
    poll (established empirically at A2), so both subscriptions go on the same
    channel and there is no second inbound path to maintain."""
    fake = FakeGoTo()
    state = _run({}, fake)
    assert fake.message_subscribed == [("chan-1", "acct-9")]
    assert state is not None
    assert state["channel"]["subscription_ids"] == ["sub-1", "msg-sub-1"]


def test_the_account_key_is_discovered_once_and_then_cached():
    fake = FakeGoTo()
    state = _run({}, fake)
    _run(state | {"channel": {}}, fake)  # force another create
    assert fake.account_lookups == 1, "the account key should be looked up once"


# --------------------------------------------------------------------------- #
# idempotent-when-healthy
# --------------------------------------------------------------------------- #
def test_a_healthy_channel_is_left_completely_alone():
    fake = FakeGoTo()
    assert _run(_healthy_channel(), fake) is None
    assert fake.channels_created == []
    assert fake.account_lookups == 0


def test_returning_none_avoids_rewriting_identical_state_every_cycle():
    """None means 'leave the stored state untouched' to `run_one`. On the common
    path that is the difference between one row write per cycle and none."""
    assert _run(_healthy_channel(), FakeGoTo()) is None


# --------------------------------------------------------------------------- #
# renew-near-expiry
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "seconds_left", [0, 1, _RENEW_MARGIN_SECONDS - 1, _RENEW_MARGIN_SECONDS]
)
def test_a_channel_inside_the_renewal_margin_is_replaced(seconds_left):
    fake = FakeGoTo()
    state = _run(_healthy_channel(seconds_left), fake)
    assert state is not None
    assert fake.channels_created == [CHANNEL_NICKNAME]
    assert state["channel"]["id"] == "chan-1"


def test_renewal_happens_before_expiry_not_after():
    """There is no renew endpoint — replacement is creation — so a channel left
    to actually lapse is a window where calls are silently not ingested."""
    fake = FakeGoTo()
    assert _run(_healthy_channel(_RENEW_MARGIN_SECONDS + 60), fake) is None
    assert fake.channels_created == []


def test_replacing_the_channel_bumps_the_generation_so_the_bridge_reconnects():
    fake = FakeGoTo()
    state = _run(_healthy_channel(10), fake)
    assert state is not None
    assert state["channel_generation"] == 5  # was 4


def test_an_expired_channel_with_no_expiry_recorded_is_treated_as_dead():
    """Missing state is not evidence of health. A channel dict with no
    `expires_at` — a half-written state row, or one from an older schema —
    must be replaced rather than trusted."""
    fake = FakeGoTo()
    state = _run({"channel": {"id": "x", "url": "wss://old"}}, fake)
    assert state is not None
    assert fake.channels_created == [CHANNEL_NICKNAME]


def test_a_missing_lifetime_falls_back_to_a_safe_default():
    """A zero/absent `channelLifetime` must not compute an already-expired
    channel, which would make the runner re-create one every single cycle."""
    fake = FakeGoTo(lifetime=0)
    state = _run({}, fake)
    assert state is not None
    assert state["channel"]["expires_at"] > time.time() + _RENEW_MARGIN_SECONDS


# --------------------------------------------------------------------------- #
# activation
# --------------------------------------------------------------------------- #
def test_the_runner_is_disabled_without_credentials(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "goto_connect_client_id", "")
    assert GoToRunner().enabled() is False


def test_the_runner_is_enabled_when_all_three_credentials_are_present(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "goto_connect_client_id", "id")
    monkeypatch.setattr(settings, "goto_connect_client_secret", "secret")
    monkeypatch.setattr(settings, "goto_connect_refresh_token", "refresh")
    assert GoToRunner().enabled() is True
