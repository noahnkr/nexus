"""Calendar tools (v1.3.0, Task 5) — offline, mocked Google client.

`list_calendar_events` is safe (reading a calendar changes nothing);
`create_calendar_event` is gated, because an event exists outside the system the
moment it is created and its attendees are emailed an invitation. Those two
classifications are the interesting assertions here — getting either wrong is
either a needless approval or an unapproved external effect.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services.connectors import google_client as google_module
from app.services.connectors.google_client import GoogleError
from app.services.tools.core import ToolInputError
from app.services.tools.entities import _create_calendar_event, _list_calendar_events
from app.services.tools.registry import get_tool


class FakeGoogle:
    def __init__(self, *, items=None, created=None, error=None):
        self.items = items or []
        self.created = created or {"id": "evt-new"}
        self.error = error
        self.inserted: list[dict] = []
        self.listed: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def calendar_events(self, *, sync_token=None, time_min=None,
                              time_max=None, page_token=None, max_results=250):
        if self.error:
            raise self.error
        self.listed.append({"time_min": time_min, "time_max": time_max})
        return {"items": self.items}

    async def calendar_insert(self, event):
        if self.error:
            raise self.error
        self.inserted.append(event)
        return self.created


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_refresh_token", "refresh")


@pytest.fixture
def fake(monkeypatch):
    client = FakeGoogle()
    monkeypatch.setattr(google_module, "GoogleClient", lambda *a, **kw: client)
    return client


class FakeConn:
    """Enough of a connection for the handler's optional writes."""

    def __init__(self):
        self.executed: list[tuple] = []

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self


# --------------------------------------------------------------------------- #
# classification — the assertions that matter most
# --------------------------------------------------------------------------- #
def test_reading_the_calendar_is_safe_and_needs_no_approval():
    tool = get_tool("list_calendar_events")
    assert tool is not None
    assert tool.safe is True


def test_creating_an_event_is_gated():
    """An event exists outside the system the moment it is created, and its
    attendees are emailed an invitation. That is precisely the class of action
    CLAUDE.md requires a human to approve."""
    tool = get_tool("create_calendar_event")
    assert tool is not None
    assert tool.safe is False
    assert tool.gate_describe is not None


def test_the_approver_may_reword_but_not_reschedule():
    """Editing the title or details changes how it reads; moving the time or
    changing who is invited would change WHAT was approved."""
    tool = get_tool("create_calendar_event")
    assert tool is not None
    assert tool.editable_fields == ["title", "description"]


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #
def test_listing_returns_a_normalized_shape(fake):
    fake.items = [{
        "id": "evt-1", "summary": "Tour with the Ellisons",
        "start": {"dateTime": "2026-07-24T14:00:00-05:00"},
        "end": {"dateTime": "2026-07-24T15:00:00-05:00"},
        "attendees": [{"email": "margaret@example.com"}],
        "location": "Client home",
    }]
    result = asyncio.run(_list_calendar_events(
        None, {"start": "2026-07-24T00:00:00Z", "end": "2026-07-25T00:00:00Z"}
    ))
    assert result.data["count"] == 1
    event = result.data["events"][0]
    assert event == {
        "id": "evt-1", "title": "Tour with the Ellisons",
        "start": "2026-07-24T14:00:00-05:00", "end": "2026-07-24T15:00:00-05:00",
        "attendees": ["margaret@example.com"], "location": "Client home",
    }


def test_an_all_day_event_lists_its_date(fake):
    fake.items = [{"id": "e", "summary": "Holiday",
                   "start": {"date": "2026-07-24"}, "end": {"date": "2026-07-25"}}]
    result = asyncio.run(_list_calendar_events(
        None, {"start": "2026-07-01T00:00:00Z", "end": "2026-08-01T00:00:00Z"}
    ))
    assert result.data["events"][0]["start"] == "2026-07-24"


def test_listing_requires_a_range(fake):
    with pytest.raises(ToolInputError):
        asyncio.run(_list_calendar_events(None, {"start": "2026-07-24T00:00:00Z"}))


def test_an_empty_range_reports_zero_plainly(fake):
    result = asyncio.run(_list_calendar_events(
        None, {"start": "2026-07-24T00:00:00Z", "end": "2026-07-25T00:00:00Z"}
    ))
    assert result.data["count"] == 0
    assert "0 calendar event" in result.summary


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #
def test_creating_sends_the_event_body(fake):
    asyncio.run(_create_calendar_event(FakeConn(), {
        "title": "Tour with the Ellisons",
        "start": "2026-07-24T14:00:00-05:00",
        "end": "2026-07-24T15:00:00-05:00",
        "attendees": ["margaret@example.com"],
        "description": "Initial home visit",
    }))
    sent = fake.inserted[0]
    assert sent["summary"] == "Tour with the Ellisons"
    assert sent["start"] == {"dateTime": "2026-07-24T14:00:00-05:00"}
    assert sent["attendees"] == [{"email": "margaret@example.com"}]
    assert sent["description"] == "Initial home visit"


def test_creating_requires_a_title_and_both_times(fake):
    for args in (
        {"start": "2026-07-24T14:00:00Z", "end": "2026-07-24T15:00:00Z"},
        {"title": "x", "end": "2026-07-24T15:00:00Z"},
        {"title": "x", "start": "2026-07-24T14:00:00Z"},
    ):
        with pytest.raises(ToolInputError):
            asyncio.run(_create_calendar_event(FakeConn(), args))
    assert fake.inserted == [], "nothing should have been created"


def test_a_rejected_creation_says_nothing_was_scheduled(fake):
    """The approver believes an event now exists. A silent failure means someone
    turns up to a visit that was never in the calendar."""
    fake.error = GoogleError("HTTP 400: bad request")
    with pytest.raises(ToolInputError) as exc:
        asyncio.run(_create_calendar_event(FakeConn(), {
            "title": "Tour", "start": "2026-07-24T14:00:00Z",
            "end": "2026-07-24T15:00:00Z",
        }))
    assert "Nothing was scheduled" in str(exc.value)


def test_missing_credentials_refuse_plainly(monkeypatch, fake):
    monkeypatch.setattr(settings, "google_refresh_token", "")
    with pytest.raises(ToolInputError) as exc:
        asyncio.run(_create_calendar_event(FakeConn(), {
            "title": "Tour", "start": "2026-07-24T14:00:00Z",
            "end": "2026-07-24T15:00:00Z",
        }))
    assert "isn't connected" in str(exc.value)


# --------------------------------------------------------------------------- #
# the approval task's wording
# --------------------------------------------------------------------------- #
def test_the_gate_description_is_plain_language():
    """CLAUDE.md: task surfaces name things in plain language, never payloads."""
    from app.services.tools.entities import _describe_create_calendar_event

    text = asyncio.run(_describe_create_calendar_event(None, {
        "title": "Tour with the Ellisons",
        "start": "2026-07-24T14:00:00-05:00",
        "attendees": ["margaret@example.com"],
    }))
    assert "Tour with the Ellisons" in text
    assert "margaret@example.com" in text
    assert "{" not in text


def test_the_gate_description_survives_missing_details():
    from app.services.tools.entities import _describe_create_calendar_event

    text = asyncio.run(_describe_create_calendar_event(None, {}))
    assert "{" not in text and text.strip()
