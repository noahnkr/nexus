"""Gmail poll runner (v1.3.0, Task 2) — offline, against a fake Google client.

What is under test is the CURSOR CONTRACT, because that is where a poll runner
either works forever or silently loses mail:

  * a first-ever run adopts the mailbox's position and imports nothing;
  * a normal cycle ingests what arrived and advances past it;
  * a second cycle from the advanced cursor fetches nothing again;
  * an expired cursor re-bootstraps instead of stalling forever;
  * a message that cannot be fetched is skipped, not retried into a wedge.

The ingest seam is captured rather than exercised — `test_connector_adapters`
and `test_goto_resolution` already drive the real path end to end, and repeating
it here would test the seam rather than the runner.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

from app.config import settings
from app.services.connectors import gmail_runner as runner_module
from app.services.connectors.gmail_runner import GmailRunner
from app.services.connectors.google_client import HistoryGone


def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(message_id: str, sender: str = "margaret@example.com",
                  labels=None, attachments=None) -> dict:
    parts = [{"mimeType": "text/plain", "body": {"data": b64url("Body text here")}}]
    for att in attachments or []:
        parts.append({
            "mimeType": att["mime_type"], "filename": att["filename"],
            "body": {"attachmentId": att["attachment_id"], "size": att["size"]},
        })
    return {
        "id": message_id,
        "threadId": "t-1",
        "labelIds": labels if labels is not None else ["INBOX"],
        "snippet": "preview",
        "internalDate": "1784500000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "office@example.com"},
                {"name": "Subject", "value": "Hello"},
            ],
            "parts": parts,
        },
    }


class FakeGoogle:
    """Stands in for `GoogleClient`, recording what the runner asked for."""

    def __init__(self, *, profile_history_id="1000", history_pages=None,
                 messages=None, history_raises=None):
        self.profile_history_id = profile_history_id
        self.history_pages = history_pages or []
        self.messages = messages or {}
        self.history_raises = history_raises
        self.profile_calls = 0
        self.history_calls: list[tuple] = []
        self.message_calls: list[str] = []
        self.attachment_calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def gmail_profile(self):
        self.profile_calls += 1
        return {"emailAddress": "office@example.com", "historyId": self.profile_history_id}

    async def gmail_history(self, start_history_id, page_token=None):
        self.history_calls.append((start_history_id, page_token))
        if self.history_raises:
            raise self.history_raises
        index = 0 if page_token is None else int(page_token)
        if index >= len(self.history_pages):
            return {"historyId": self.profile_history_id}
        return self.history_pages[index]

    async def gmail_message(self, message_id):
        self.message_calls.append(message_id)
        message = self.messages.get(message_id)
        if message is None:
            raise RuntimeError(f"no such message {message_id}")
        return message

    async def gmail_attachment(self, message_id, attachment_id):
        self.attachment_calls.append((message_id, attachment_id))
        return {"data": b64url("attachment bytes")}


@pytest.fixture
def captured_ingest(monkeypatch):
    """Every payload the runner hands to the shared ingest seam."""
    seen: list[dict] = []

    async def fake_ingest(source, payload, headers=None, *, tenant_id, **kwargs):
        seen.append({"source": source, "payload": payload, "tenant_id": tenant_id})
        return {"received": 1, "matched": 1, "created": 0, "tasks": 0}

    monkeypatch.setattr(runner_module, "ingest_payload", fake_ingest)
    return seen


def _run(state, fake):
    runner = GmailRunner(client_factory=lambda: fake)
    return asyncio.run(runner.run(None, "tenant-1", state))


# --------------------------------------------------------------------------- #
# bootstrap — no backfill
# --------------------------------------------------------------------------- #
def test_a_first_run_adopts_the_mailbox_position_and_imports_nothing(captured_ingest):
    """The office's mail archive stays in Gmail. This system mirrors the business
    going forward; it does not re-platform years of a mailbox."""
    fake = FakeGoogle(profile_history_id="5000")
    state = _run({}, fake)

    assert state == {"history_id": "5000"}
    assert captured_ingest == [], "a first run must not import history"
    assert fake.message_calls == []


# --------------------------------------------------------------------------- #
# a normal cycle
# --------------------------------------------------------------------------- #
def test_new_mail_is_fetched_and_handed_to_the_shared_ingest_seam(captured_ingest):
    fake = FakeGoogle(
        profile_history_id="5100",
        history_pages=[{
            "historyId": "5100",
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
        }],
        messages={"m1": gmail_message("m1")},
    )
    state = _run({"history_id": "5000"}, fake)

    assert len(captured_ingest) == 1
    assert captured_ingest[0]["source"] == "gmail"
    message = captured_ingest[0]["payload"]["messages"][0]
    assert message["counterpart"] == "margaret@example.com"
    assert message["direction"] == "inbound"
    assert state is not None and state["history_id"] == "5100"


def test_the_cursor_advances_so_the_next_cycle_fetches_nothing_again(captured_ingest):
    fake = FakeGoogle(
        profile_history_id="5100",
        history_pages=[{
            "historyId": "5100",
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
        }],
        messages={"m1": gmail_message("m1")},
    )
    state = _run({"history_id": "5000"}, fake)
    assert len(captured_ingest) == 1

    # Second cycle: history from the advanced cursor reports nothing added.
    quiet = FakeGoogle(profile_history_id="5100", history_pages=[{"historyId": "5100"}])
    _run(state, quiet)
    assert len(captured_ingest) == 1, "the same message must not be ingested twice"


def test_a_quiet_mailbox_still_moves_the_cursor_forward(captured_ingest):
    """A cursor that never advances eventually ages out, and then a week of mail
    is unrecoverable. Advancing to the reported head on an empty sweep prevents
    that on a mailbox that is simply quiet."""
    fake = FakeGoogle(profile_history_id="6000",
                      history_pages=[{"historyId": "6000"}])
    state = _run({"history_id": "5000"}, fake)
    assert state is not None and state["history_id"] == "6000"


def test_outbound_mail_is_ingested_with_its_own_direction(captured_ingest):
    """Sent mail is correspondence too — it belongs on the timeline. It is
    distinguished by direction rather than dropped."""
    fake = FakeGoogle(
        profile_history_id="5100",
        history_pages=[{
            "historyId": "5100",
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
        }],
        messages={"m1": gmail_message("m1", labels=["SENT"])},
    )
    _run({"history_id": "5000"}, fake)
    assert captured_ingest[0]["payload"]["messages"][0]["direction"] == "outbound"


# --------------------------------------------------------------------------- #
# failure handling
# --------------------------------------------------------------------------- #
def test_an_expired_cursor_rebootstraps_instead_of_stalling(captured_ingest):
    """Gmail's history reaches back about a week. Once the cursor is older than
    that the gap cannot be recovered — so re-anchor and keep working, rather than
    failing this cycle and every cycle after it."""
    fake = FakeGoogle(profile_history_id="9000", history_raises=HistoryGone("404"))
    state = _run({"history_id": "1"}, fake)

    assert state is not None and state["history_id"] == "9000"
    assert captured_ingest == []


def test_a_message_that_cannot_be_fetched_is_skipped_not_retried_forever(captured_ingest):
    """One poison message must not wedge every email behind it. The cursor moves
    past it and the failure is logged."""
    fake = FakeGoogle(
        profile_history_id="5100",
        history_pages=[{
            "historyId": "5100",
            "history": [{"messagesAdded": [
                {"message": {"id": "broken"}},
                {"message": {"id": "m2"}},
            ]}],
        }],
        messages={"m2": gmail_message("m2")},  # "broken" is absent → raises
    )
    state = _run({"history_id": "5000"}, fake)

    assert len(captured_ingest) == 1, "the good message should still be ingested"
    assert state is not None and state["history_id"] == "5100"


def test_a_draft_is_never_ingested(captured_ingest):
    fake = FakeGoogle(
        profile_history_id="5100",
        history_pages=[{
            "historyId": "5100",
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
        }],
        messages={"m1": gmail_message("m1", labels=["DRAFT"])},
    )
    _run({"history_id": "5000"}, fake)
    assert captured_ingest == []


def test_pagination_collects_across_pages(captured_ingest):
    fake = FakeGoogle(
        profile_history_id="5200",
        history_pages=[
            {"historyId": "5150", "nextPageToken": "1",
             "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]},
            {"historyId": "5200",
             "history": [{"messagesAdded": [{"message": {"id": "m2"}}]}]},
        ],
        messages={"m1": gmail_message("m1"), "m2": gmail_message("m2")},
    )
    _run({"history_id": "5000"}, fake)
    assert len(captured_ingest) == 2


# --------------------------------------------------------------------------- #
# activation
# --------------------------------------------------------------------------- #
def test_the_runner_is_disabled_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "google_refresh_token", "")
    assert GmailRunner().enabled() is False


def test_the_runner_is_enabled_when_all_three_credentials_are_present(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_refresh_token", "refresh")
    assert GmailRunner().enabled() is True
