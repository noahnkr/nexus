"""Google Calendar poll runner (v1.3.0, Task 4) — offline, fake client.

The token lifecycle is the whole job, so that is what these assert: a first run
windows and captures a token, later runs send the token and nothing else, an
expired token re-windows rather than stalling, and the token is only taken from
the last page — the mistake that would otherwise force a full re-list forever.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services.connectors import gcal_runner as runner_module
from app.services.connectors.gcal_runner import GoogleCalendarRunner
from app.services.connectors.google_client import SyncTokenExpired


def event(event_id="evt-1", summary="Tour with the Ellisons", **kwargs):
    base = {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": "2026-07-24T14:00:00-05:00"},
        "end": {"dateTime": "2026-07-24T15:00:00-05:00"},
        "status": "confirmed",
        "attendees": [{"email": "margaret@example.com"}],
    }
    base.update(kwargs)
    return base


class FakeGoogle:
    def __init__(self, pages=None, raises_on_token=False):
        self.pages = pages or []
        self.raises_on_token = raises_on_token
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def calendar_events(self, *, sync_token=None, time_min=None,
                              page_token=None, time_max=None, max_results=250):
        self.calls.append({"sync_token": sync_token, "time_min": time_min,
                           "time_max": time_max, "page_token": page_token})
        if sync_token and self.raises_on_token:
            raise SyncTokenExpired("410")
        index = 0 if page_token is None else int(page_token)
        if index >= len(self.pages):
            return {"items": []}
        return self.pages[index]


@pytest.fixture
def captured_ingest(monkeypatch):
    seen: list[dict] = []

    async def fake_ingest(source, payload, headers=None, *, tenant_id, **kwargs):
        seen.append({"source": source, "payload": payload, "headers": headers})
        return {"received": 1, "matched": 0, "created": 0, "tasks": 1}

    monkeypatch.setattr(runner_module, "ingest_payload", fake_ingest)
    return seen


def _run(state, fake):
    runner = GoogleCalendarRunner(client_factory=lambda: fake)
    return asyncio.run(runner.run(None, "tenant-1", state))


# --------------------------------------------------------------------------- #
# first run — window, capture the token
# --------------------------------------------------------------------------- #
def test_a_first_run_lists_a_bounded_window_and_keeps_the_token(captured_ingest):
    fake = FakeGoogle(pages=[{"items": [event()], "nextSyncToken": "tok-1"}])
    state = _run({}, fake)

    assert state is not None and state["sync_token"] == "tok-1"
    assert fake.calls[0]["time_min"] is not None, "a first run must bound the window"
    assert fake.calls[0]["sync_token"] is None


def test_later_runs_send_the_token_and_no_window(captured_ingest):
    """`syncToken` and `timeMin` are mutually exclusive — sending both is a 400,
    which would break every incremental sync."""
    fake = FakeGoogle(pages=[{"items": [], "nextSyncToken": "tok-2"}])
    _run({"sync_token": "tok-1"}, fake)

    assert fake.calls[0]["sync_token"] == "tok-1"
    assert fake.calls[0]["time_min"] is None


# --------------------------------------------------------------------------- #
# ingestion
# --------------------------------------------------------------------------- #
def test_changed_events_reach_the_shared_ingest_seam(captured_ingest):
    fake = FakeGoogle(pages=[{"items": [event()], "nextSyncToken": "tok-1"}])
    _run({}, fake)

    assert len(captured_ingest) == 1
    assert captured_ingest[0]["source"] == "gcal"
    events = captured_ingest[0]["payload"]["events"]
    assert events[0]["id"] == "evt-1"
    assert events[0]["summary"] == "Tour with the Ellisons"


def test_the_runner_marks_its_payload_as_a_real_change(captured_ingest):
    """The gcal adapter treats `x-goog-resource-state: sync` as a handshake and
    acks it. A poll always carries real changes, so it must say `exists` or every
    event would be silently dropped."""
    fake = FakeGoogle(pages=[{"items": [event()], "nextSyncToken": "tok-1"}])
    _run({}, fake)
    assert captured_ingest[0]["headers"]["x-goog-resource-state"] == "exists"


def test_event_times_are_flattened_out_of_googles_wrapper(captured_ingest):
    fake = FakeGoogle(pages=[{"items": [event()], "nextSyncToken": "t"}])
    _run({}, fake)
    mapped = captured_ingest[0]["payload"]["events"][0]
    assert mapped["start"] == "2026-07-24T14:00:00-05:00"
    assert mapped["end"] == "2026-07-24T15:00:00-05:00"


def test_an_all_day_event_uses_its_date(captured_ingest):
    """All-day events carry `date`, not `dateTime`. A consumer should not have to
    know which."""
    fake = FakeGoogle(pages=[{
        "items": [event(start={"date": "2026-07-24"}, end={"date": "2026-07-25"})],
        "nextSyncToken": "t",
    }])
    _run({}, fake)
    assert captured_ingest[0]["payload"]["events"][0]["start"] == "2026-07-24"


def test_a_cancelled_event_is_still_ingested(captured_ingest):
    """A cancelled visit is exactly the kind of change someone needs to see.
    Dropping it would leave the last known state looking current."""
    fake = FakeGoogle(pages=[{
        "items": [event(status="cancelled")], "nextSyncToken": "t",
    }])
    _run({}, fake)
    assert captured_ingest[0]["payload"]["events"][0]["status"] == "cancelled"


def test_nothing_changed_means_nothing_ingested(captured_ingest):
    fake = FakeGoogle(pages=[{"items": [], "nextSyncToken": "tok-1"}])
    _run({"sync_token": "tok-1"}, fake)
    assert captured_ingest == []


# --------------------------------------------------------------------------- #
# pagination and token capture
# --------------------------------------------------------------------------- #
def test_the_token_is_taken_from_the_last_page_not_the_first(captured_ingest):
    """`nextSyncToken` only appears on the final page. Stopping at the first
    batch would drop it and force a full re-window on every single cycle."""
    fake = FakeGoogle(pages=[
        {"items": [event("evt-1")], "nextPageToken": "1"},
        {"items": [event("evt-2")], "nextSyncToken": "tok-final"},
    ])
    state = _run({}, fake)

    assert state is not None and state["sync_token"] == "tok-final"
    assert len(captured_ingest[0]["payload"]["events"]) == 2


def test_a_first_run_bounds_the_window_forward_as_well_as_back(captured_ingest):
    """The forward bound is the load-bearing one. `singleEvents=True` expands
    recurring series, so an unbounded future asks Google to expand every repeat
    to the end of time — which is what pushed the real calendar past the page
    cap, cost it the token, and left it re-sweeping ~2,500 events every cycle."""
    fake = FakeGoogle(pages=[{"items": [event()], "nextSyncToken": "tok-1"}])
    _run({}, fake)

    assert fake.calls[0]["time_min"] is not None
    assert fake.calls[0]["time_max"] is not None, (
        "an unbounded future window is the 2026-07-22 defect"
    )
    assert fake.calls[0]["time_min"] < fake.calls[0]["time_max"]


def test_an_incremental_run_sends_neither_bound(captured_ingest):
    """`syncToken` is mutually exclusive with BOTH bounds — adding `timeMax`
    would be a 400 in exactly the same way `timeMin` is."""
    fake = FakeGoogle(pages=[{"items": [], "nextSyncToken": "tok-2"}])
    _run({"sync_token": "tok-1"}, fake)

    assert fake.calls[0]["time_min"] is None
    assert fake.calls[0]["time_max"] is None


def test_truncating_at_the_page_cap_raises_instead_of_storing_no_token():
    """The regression test this file was missing.

    Every earlier fixture fits in one page, so the cap was never reached and the
    token always arrived — which is why a suite of 11 green tests said nothing
    about a runner that could not sync a real calendar at all. A sweep that hits
    the cap has no `nextSyncToken`, so storing it would repeat verbatim forever;
    raising surfaces it as `connector.sync_failed` every cycle instead.
    """
    endless = [
        {"items": [event(f"evt-{i}")], "nextPageToken": str(i + 1)}
        for i in range(runner_module._MAX_PAGES + 5)
    ]
    fake = FakeGoogle(pages=endless)

    with pytest.raises(runner_module.CalendarWindowTooLarge):
        _run({}, fake)

    assert len(fake.calls) == runner_module._MAX_PAGES, "it should stop at the cap"


def test_a_sweep_that_fills_the_cap_exactly_still_captures_its_token(captured_ingest):
    """The boundary either side of the raise: a sweep needing every allowed page
    is fine, as long as the last one carries the token."""
    pages = [
        {"items": [event(f"evt-{i}")], "nextPageToken": str(i + 1)}
        for i in range(runner_module._MAX_PAGES - 1)
    ]
    pages.append({"items": [event("evt-last")], "nextSyncToken": "tok-final"})
    fake = FakeGoogle(pages=pages)

    state = _run({}, fake)

    assert state is not None and state["sync_token"] == "tok-final"
    assert len(captured_ingest[0]["payload"]["events"]) == runner_module._MAX_PAGES


# --------------------------------------------------------------------------- #
# 410 recovery
# --------------------------------------------------------------------------- #
def test_an_expired_token_rewindows_instead_of_failing(captured_ingest):
    """Google prunes sync tokens after a few weeks of disuse. Re-delivered events
    are idempotent by external id, so re-windowing is safe."""
    class ExpiringOnce(FakeGoogle):
        def __init__(self):
            super().__init__(pages=[{"items": [event()], "nextSyncToken": "tok-new"}])
            self.raised = False

        async def calendar_events(self, *, sync_token=None, **kwargs):
            if sync_token and not self.raised:
                self.raised = True
                raise SyncTokenExpired("410")
            return await super().calendar_events(sync_token=None, **kwargs)

    fake = ExpiringOnce()
    state = _run({"sync_token": "stale"}, fake)

    assert state is not None and state["sync_token"] == "tok-new"
    assert len(captured_ingest) == 1, "the re-listed events should still be ingested"


# --------------------------------------------------------------------------- #
# activation
# --------------------------------------------------------------------------- #
def test_the_runner_is_disabled_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "google_refresh_token", "")
    assert GoogleCalendarRunner().enabled() is False
