"""GoTo subscription contracts (v1.2.0 fix, 2026-07-22) — offline, real bodies.

This file exists because v1.2.0 shipped green with **neither** subscription
working. `test_goto_runner.py` fakes the client, so `subscribe_calls` returning
`["sub-1"]` was all it ever saw; nothing asserted what the live API actually
answers. Every response body below is copied verbatim from the real API.

The three failures being locked down:

  * calls were subscribed with `REPORT_SUMMARY`, which this account rejects
    (`constraints: [{"field": "Events[0]", "constraint": "INVALID"}]`);
  * messages were subscribed without the REQUIRED `ownerPhoneNumber`;
  * both parsed for `items[].id`, found none in the real 207/201 shapes, and
    reported success anyway — so a channel subscribed to nothing looked healthy.

The error `message` on the call-events endpoint is boilerplate — it says "The
Notification Channel must be a WebSocket channel" for every 400 including a
parameterless GET. Only `constraints` carries information. Tests assert on the
request we send and the outcome, never on that sentence.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services.connectors.goto_client import GoToClient, GoToError

ACCOUNT_KEY = "6327799820468129299"
CHANNEL_ID = "0Vpf2S8ahAaLQ4a7Gfph"
BUSINESS = "+16303602784"

# --- verbatim live responses ------------------------------------------------ #
CALLS_207_OK = {"accountKeys": [{"id": ACCOUNT_KEY, "status": 200, "message": "Success"}]}
CALLS_207_REJECTED = {"accountKeys": [{"id": ACCOUNT_KEY, "status": 403,
                                       "message": "Forbidden"}]}
CALLS_400_BAD_EVENT = {
    "reference": "g8b0pm6gpmftunqj18pft6jq",
    "errorCode": "BAD_REQUEST",
    "message": "The Notification Channel must be a WebSocket channel",
    "constraints": [{"field": "Events[0]", "constraint": "INVALID"}],
}
MSG_201_OK = {"id": "MFZwZjJTOGFoQWFMUTRhN0dmcGhIb2RjTTFlQ0Zqe"}
MSG_400_NO_OWNER = {
    "reference": "9C6qt9tFYtlK2iJaV9Tao0WxIScuOwFK",
    "constraintViolations": [{"field": "ownerPhoneNumber", "constraint": "Invalid"}],
    "errorCode": "BAD_REQUEST",
    "message": "Provided parameter(s) must be valid",
}
MSG_409_EXISTS = {
    "reference": "M0qd7r13Fsi1QKB47ZRW1ksdkcChYOPx",
    "constraintViolations": [{"field": "ownerPhoneNumber",
                              "constraint": "Subscription already exists"}],
    "errorCode": "CONFLICT",
    "message": "The request could not be completed due to a conflict",
}


def _client(handler) -> GoToClient:
    class _Tokens:
        async def token(self):
            return "access-token"

        def invalidate(self):
            pass

        async def aclose(self):
            pass

    return GoToClient(
        tokens=_Tokens(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _business_number(monkeypatch):
    monkeypatch.setattr(settings, "goto_business_number", BUSINESS)


def _recorder(status, body):
    """A handler that records the request it was given."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content or b"{}")
        return httpx.Response(status, json=body)

    return handler, seen


# --------------------------------------------------------------------------- #
# calls
# --------------------------------------------------------------------------- #
def test_calls_subscribe_to_ending_not_report_summary():
    """`REPORT_SUMMARY` is what shipped and what the API rejects."""
    handler, seen = _recorder(207, CALLS_207_OK)
    ids = _run(_client(handler).subscribe_calls(CHANNEL_ID, ACCOUNT_KEY))

    events = seen["json"]["accountKeys"][0]["events"]
    assert events == ["ENDING"], "REPORT_SUMMARY is rejected by this account"
    assert ids == [ACCOUNT_KEY]


def test_calls_use_the_per_account_grammar_not_the_report_guides():
    """Two grammars exist in GoTo's docs. The Report guide's top-level
    `eventTypes` + `accountKeys: [str]` answers MALFORMED_REQUEST here."""
    handler, seen = _recorder(207, CALLS_207_OK)
    _run(_client(handler).subscribe_calls(CHANNEL_ID, ACCOUNT_KEY))

    body = seen["json"]
    assert "eventTypes" not in body
    assert isinstance(body["accountKeys"][0], dict), "accountKeys carries objects"
    assert body["channelId"] == CHANNEL_ID


def test_only_ending_is_subscribed_so_one_call_is_one_entry():
    """`gt_map.frame_kind` classifies any call-events frame as a call, so also
    subscribing to STARTING would put two timeline entries on one conversation."""
    handler, seen = _recorder(207, CALLS_207_OK)
    _run(_client(handler).subscribe_calls(CHANNEL_ID, ACCOUNT_KEY))
    assert "STARTING" not in seen["json"]["accountKeys"][0]["events"]


def test_a_207_that_subscribed_nothing_is_an_error_not_a_success():
    """The regression this file was written for: the old code looked for
    `items[].id`, found none in the real shape, logged a warning and returned []
    — which the runner stored as a healthy channel subscribed to nothing."""
    handler, _ = _recorder(207, CALLS_207_REJECTED)
    with pytest.raises(GoToError, match="did not take"):
        _run(_client(handler).subscribe_calls(CHANNEL_ID, ACCOUNT_KEY))


def test_a_rejected_event_vocabulary_surfaces_as_an_error():
    handler, _ = _recorder(400, CALLS_400_BAD_EVENT)
    with pytest.raises(GoToError):
        _run(_client(handler).subscribe_calls(CHANNEL_ID, ACCOUNT_KEY))


# --------------------------------------------------------------------------- #
# messages
# --------------------------------------------------------------------------- #
def test_messages_send_the_required_owner_phone_number():
    """Omitting it is a 400 every time — the subscription is per line."""
    handler, seen = _recorder(201, MSG_201_OK)
    ids = _run(_client(handler).subscribe_messages(CHANNEL_ID, ACCOUNT_KEY))

    assert seen["json"]["ownerPhoneNumber"] == BUSINESS
    assert seen["json"]["eventTypes"] == ["INCOMING_MESSAGE_SNIPPET"]
    assert ids == [MSG_201_OK["id"]], "a create answers with the object, not a list"


def test_messages_refuse_to_subscribe_without_a_business_number(monkeypatch):
    """Better to fail loudly than to create a subscription that cannot exist and
    leave inbound SMS silently dead."""
    monkeypatch.setattr(settings, "goto_business_number", "")
    handler, _ = _recorder(201, MSG_201_OK)
    with pytest.raises(GoToError, match="GOTO_BUSINESS_NUMBER"):
        _run(_client(handler).subscribe_messages(CHANNEL_ID, ACCOUNT_KEY))


def test_an_existing_message_subscription_is_not_a_failure():
    """409 is the healthy steady state: channels renew faster than subscriptions
    expire, so the line is usually already subscribed."""
    handler, _ = _recorder(409, MSG_409_EXISTS)
    assert _run(_client(handler).subscribe_messages(CHANNEL_ID, ACCOUNT_KEY)) == []


def test_a_missing_owner_number_rejection_is_no_longer_swallowed():
    """The old code turned every messaging failure into `[]`, which the caller
    read as success — so inbound SMS was dead for all of v1.2.0 while every
    surface reported healthy."""
    handler, _ = _recorder(400, MSG_400_NO_OWNER)
    with pytest.raises(GoToError):
        _run(_client(handler).subscribe_messages(CHANNEL_ID, ACCOUNT_KEY))
