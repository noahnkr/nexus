"""Real outbound SMS through GoTo (v1.2.0, Task 8) — offline, mocked provider.

Two things are under test and they matter for different reasons:

  * the SEND — that the right payload goes to the right endpoint from the right
    line, and that every failure mode produces a plain-language refusal rather
    than a half-truth;
  * the GATE — that making the send real did not weaken it. `send_sms` must stay
    `safe=False` with its `gate_describe` and `editable_fields` intact, because
    the whole reason a placeholder shipped first was to prove the gate before
    anything could actually leave the building.

The failure assertions are the point of this file. The placeholder returned
`delivered: false` inside a cheerful summary; a real send that fails the same way
would leave an approver believing a message went out, and a client waiting on a
reply that is never coming.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services.connectors.goto_client import GoToError
from app.services.connectors.goto_sms import SmsError, send_sms

BUSINESS = "+16303602784"
RECIPIENT = "+12025550101"


class FakeGoTo:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.sent: list[tuple[str, str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def send_sms(self, owner_number, to_number, body):
        if self.error:
            raise self.error
        self.sent.append((owner_number, to_number, body))
        return {"id": "msg-out-1"}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "goto_connect_client_id", "id")
    monkeypatch.setattr(settings, "goto_connect_client_secret", "secret")
    monkeypatch.setattr(settings, "goto_connect_refresh_token", "refresh")
    monkeypatch.setattr(settings, "goto_business_number", BUSINESS)


def _send(to, body, fake):
    return asyncio.run(send_sms(to, body, client_factory=lambda: fake))


# --------------------------------------------------------------------------- #
# the send
# --------------------------------------------------------------------------- #
def test_the_message_goes_out_from_the_business_line():
    fake = FakeGoTo()
    result = _send(RECIPIENT, "Your caregiver is running 10 minutes late.", fake)
    assert result == {"id": "msg-out-1"}
    assert fake.sent == [
        (BUSINESS, RECIPIENT, "Your caregiver is running 10 minutes late.")
    ]


def test_a_loosely_typed_recipient_is_normalized_before_sending():
    """The number can reach here from an approver's edit or a lead record, so it
    arrives however a human wrote it."""
    fake = FakeGoTo()
    _send("(202) 555-0101", "hello", fake)
    assert fake.sent[0][1] == RECIPIENT


def test_the_body_is_sent_verbatim():
    """No truncation on the way out — the summary shortens for display, the
    message must not."""
    fake = FakeGoTo()
    long_body = "x" * 400
    _send(RECIPIENT, long_body, fake)
    assert fake.sent[0][2] == long_body


# --------------------------------------------------------------------------- #
# failure is loud
# --------------------------------------------------------------------------- #
def test_a_provider_rejection_says_the_message_was_not_delivered():
    fake = FakeGoTo(error=GoToError("HTTP 400: {'errorCode':'BAD_REQUEST'}"))
    with pytest.raises(SmsError) as exc:
        _send(RECIPIENT, "hello", fake)
    message = str(exc.value)
    assert "not been delivered" in message
    # Provider internals stay in the log, not in front of the approver.
    assert "errorCode" not in message
    assert "HTTP 400" not in message


def test_missing_credentials_refuse_rather_than_pretend(monkeypatch):
    monkeypatch.setattr(settings, "goto_connect_refresh_token", "")
    with pytest.raises(SmsError) as exc:
        _send(RECIPIENT, "hello", FakeGoTo())
    assert "not sent" in str(exc.value)


def test_a_missing_business_number_is_reported_as_such(monkeypatch):
    monkeypatch.setattr(settings, "goto_business_number", "")
    with pytest.raises(SmsError) as exc:
        _send(RECIPIENT, "hello", FakeGoTo())
    assert "GOTO_BUSINESS_NUMBER" in str(exc.value)


def test_an_untextable_recipient_is_rejected_before_any_call():
    fake = FakeGoTo()
    with pytest.raises(SmsError):
        _send("1000", "hello", fake)  # an extension, not a phone number
    assert fake.sent == [], "nothing should have been sent"


# --------------------------------------------------------------------------- #
# the gate is untouched
# --------------------------------------------------------------------------- #
def test_send_sms_is_still_gated_with_its_original_contract():
    """Making the send real must not have relaxed the approval gate."""
    from app.services.tools.registry import get_tool

    tool = get_tool("send_sms")
    assert tool is not None
    assert tool.safe is False, "send_sms must still require human approval"
    assert tool.gate_describe is not None
    assert tool.editable_fields == ["body"], "the recipient is not editable"


def test_the_handler_reports_a_real_send_not_a_placeholder(monkeypatch):
    from app.services.tools import outbound
    from app.services.connectors import goto_sms

    async def ok(to, body, **_kw):
        return {"id": "x"}

    monkeypatch.setattr(goto_sms, "send_sms", ok)
    result = asyncio.run(outbound._send_sms(None, {"to": RECIPIENT, "body": "hi"}))
    assert result.data["delivered"] is True
    assert "placeholder" not in result.summary.lower()


def test_the_handler_surfaces_a_failed_send_as_a_tool_error(monkeypatch):
    from app.services.tools import outbound
    from app.services.tools.core import ToolInputError
    from app.services.connectors import goto_sms

    async def fails(to, body, **_kw):
        raise SmsError("The text could not be sent.")

    monkeypatch.setattr(goto_sms, "send_sms", fails)
    with pytest.raises(ToolInputError):
        asyncio.run(outbound._send_sms(None, {"to": RECIPIENT, "body": "hi"}))
