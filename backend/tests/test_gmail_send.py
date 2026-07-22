"""Real outbound email through Gmail (v1.3.0, Task 3) — offline, mocked provider.

Mirrors `test_goto_sms.py`, and for the same reasons: the send has to be correct,
every failure has to be loud, and making it real must not have weakened the
approval gate.
"""
from __future__ import annotations

import asyncio
import base64
import email
import email.header
import email.policy

import pytest

from app.config import settings
from app.services.connectors.gmail_send import EmailError, build_message, send_email
from app.services.connectors.google_client import GoogleError

RECIPIENT = "margaret@example.com"


class FakeGoogle:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def gmail_send(self, raw_base64url):
        if self.error:
            raise self.error
        self.sent.append(raw_base64url)
        return {"id": "sent-1", "labelIds": ["SENT"]}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_refresh_token", "refresh")


def _send(to, subject, body, fake):
    return asyncio.run(send_email(to, subject, body, client_factory=lambda: fake))


def _decode(raw_base64url: str):
    """Parse the sent bytes back. `policy.default` is what exposes `get_content`
    and decodes headers — the compat32 default returns raw encoded strings."""
    padded = raw_base64url + "=" * (-len(raw_base64url) % 4)
    return email.message_from_bytes(
        base64.urlsafe_b64decode(padded), policy=email.policy.default
    )


# --------------------------------------------------------------------------- #
# the message itself
# --------------------------------------------------------------------------- #
def test_the_message_carries_recipient_subject_and_body():
    parsed = _decode(build_message(RECIPIENT, "Care hours", "Tuesday at 2pm works."))
    assert parsed["To"] == RECIPIENT
    assert parsed["Subject"] == "Care hours"
    assert "Tuesday at 2pm works." in parsed.get_content()


def test_a_non_ascii_subject_survives_encoding():
    """A header with an accent in it should not be the thing that breaks outbound
    mail — which is why this builds through `EmailMessage` rather than by string
    concatenation."""
    parsed = _decode(build_message(RECIPIENT, "Café visit — confirmé", "hi"))
    assert "Café visit" in str(email.header.make_header(
        email.header.decode_header(parsed["Subject"])
    ))


def test_the_encoding_is_base64url_not_plain_base64():
    """Gmail's `raw` field rejects the standard alphabet's '+' and '/'."""
    raw = build_message(RECIPIENT, "x" * 200, "y" * 200)
    assert "+" not in raw and "/" not in raw


def test_the_send_posts_the_encoded_message():
    fake = FakeGoogle()
    result = _send(RECIPIENT, "Hello", "Body", fake)
    assert result["id"] == "sent-1"
    assert len(fake.sent) == 1
    assert _decode(fake.sent[0])["To"] == RECIPIENT


# --------------------------------------------------------------------------- #
# failure is loud
# --------------------------------------------------------------------------- #
def test_a_provider_rejection_says_the_message_was_not_delivered():
    fake = FakeGoogle(error=GoogleError("HTTP 400: {'error':{'message':'Invalid To'}}"))
    with pytest.raises(EmailError) as exc:
        _send(RECIPIENT, "Hello", "Body", fake)
    message = str(exc.value)
    assert "not been delivered" in message
    # Provider internals stay in the log, not in front of the approver.
    assert "HTTP 400" not in message


def test_missing_credentials_refuse_rather_than_pretend(monkeypatch):
    monkeypatch.setattr(settings, "google_refresh_token", "")
    with pytest.raises(EmailError) as exc:
        _send(RECIPIENT, "Hello", "Body", FakeGoogle())
    assert "not sent" in str(exc.value)


def test_a_malformed_recipient_is_rejected_before_any_call():
    fake = FakeGoogle()
    with pytest.raises(EmailError):
        _send("not-an-address", "Hello", "Body", fake)
    assert fake.sent == [], "nothing should have been sent"


# --------------------------------------------------------------------------- #
# the gate is untouched
# --------------------------------------------------------------------------- #
def test_send_email_is_still_gated_with_its_original_contract():
    from app.services.tools.registry import get_tool

    tool = get_tool("send_email")
    assert tool is not None
    assert tool.safe is False, "send_email must still require human approval"
    assert tool.gate_describe is not None


def test_the_handler_reports_a_real_send_not_a_placeholder(monkeypatch):
    from app.services.connectors import gmail_send
    from app.services.tools import outbound

    async def ok(to, subject, body, **_kw):
        return {"id": "x"}

    monkeypatch.setattr(gmail_send, "send_email", ok)
    result = asyncio.run(outbound._send_email(
        None, {"to": RECIPIENT, "subject": "Hi", "body": "there"}
    ))
    assert result.data["delivered"] is True
    assert "placeholder" not in result.summary.lower()


def test_the_handler_surfaces_a_failed_send_as_a_tool_error(monkeypatch):
    from app.services.connectors import gmail_send
    from app.services.tools import outbound
    from app.services.tools.core import ToolInputError

    async def fails(to, subject, body, **_kw):
        raise EmailError("The email could not be sent.")

    monkeypatch.setattr(gmail_send, "send_email", fails)
    with pytest.raises(ToolInputError):
        asyncio.run(outbound._send_email(
            None, {"to": RECIPIENT, "subject": "Hi", "body": "there"}
        ))


def test_no_email_sent_event_is_written_here(monkeypatch):
    """The Gmail poll ingests the mailbox in both directions, so a sent message
    comes back on the next cycle with its real Gmail id. Writing an event here
    too would put every approved email on the timeline twice, with no key to
    join the two."""
    import inspect

    from app.services.connectors import gmail_send

    source = inspect.getsource(gmail_send)
    assert "log_event" not in source
    assert "email.sent" not in source.split('"""')[2] if '"""' in source else True
