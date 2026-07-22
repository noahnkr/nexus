"""Google Calendar sync runner (v1.3.0, Task 4).

Polling with a `syncToken`, same chassis as the Gmail runner.

    events.list(syncToken)  ->  changed events
    ingest_payload("gcal")  ->  receipt -> resolve -> calendar.event.updated

THE TOKEN LIFECYCLE is the whole job, and it has one sharp edge worth stating:
`syncToken` and `timeMin` are MUTUALLY EXCLUSIVE in Google's API. A first run has
no token, so it lists a bounded window and keeps the `nextSyncToken` that comes
back on the last page; every run after that sends the token and no window.
Sending both is a 400.

A 410 means the token expired — Google prunes them after a few weeks of disuse.
The recovery is to drop it and re-window, which does re-deliver events that were
already seen. That is harmless: ingestion is idempotent by external id, so a
re-delivered event updates the same row rather than creating a second one.

WHY A BOUNDED WINDOW rather than the whole calendar: the same scope rule as the
Gmail runner. This mirrors the business going forward; the office's calendar
history stays in Google, which is already better at showing it.

THE WINDOW IS BOUNDED AT BOTH ENDS, and the forward bound is the load-bearing
one. `singleEvents=True` expands recurring series into individual instances, so a
window with no `timeMax` asks Google to expand every repeat of every series to
the end of time. Against the real calendar (2026-07-22) that produced instances
dated 2039 and blew straight through the page cap: 10 pages × 250 = exactly the
2,500 events collected, the last page never reached, `nextSyncToken` never seen,
`{}` stored — and so the identical 2,500-event sweep repeated every single cycle
with incremental sync never once beginning. A first run must reach the LAST page
or it gets no token at all, which makes "how many pages can this window need" a
correctness question rather than a tuning one.

Hence the third rule: TRUNCATION IS A FAILURE, NOT A RESULT. If the window still
has pages when the cap is reached, this raises rather than ingesting a partial
sweep with no cursor to continue from. A loud `connector.sync_failed` every cycle
is recoverable — someone sees it. Silently storing no token looks healthy from
every angle while the connector does nothing but re-list the same events forever,
which is precisely how this survived a green test suite.

CANCELLED EVENTS still arrive on the sync feed (with `status: "cancelled"`) and
are ingested as ordinary updates — a cancelled visit is exactly the kind of change
someone needs to see on a timeline, and dropping it would leave the last known
state looking current.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .google_client import GoogleClient, SyncTokenExpired, credentials_configured
from .ingest import SYNC_RECEIPT, ingest_payload
from .sync import register_runner

log = logging.getLogger("nexus.connectors.gcal")

SOURCE = "gcal"

# How far back a first run (or a post-410 re-window) reaches. A week covers the
# current schedule without importing years of history.
_WINDOW_DAYS = 7

# How far FORWARD it reaches. Without this the window is unbounded in the future
# and `singleEvents=True` expands every recurring series to the end of time — the
# defect described above. A quarter covers the scheduling horizon anyone acts on;
# events further out arrive on the incremental feed as they are created or edited.
_WINDOW_FORWARD_DAYS = 90

# RFC 3339, which is what `timeMin`/`timeMax` expect.
_STAMP = "%Y-%m-%dT%H:%M:%SZ"

# The header the gcal adapter reads to tell a real change from a watch handshake.
# The poll runner always has real changes, so it always says `exists`.
_EXISTS_HEADERS = {"x-goog-resource-state": "exists"}

# 40 × 250 = 10,000 events inside the bounded window. Generous on purpose: the
# cap exists to stop a runaway, not to trim a legitimate first sweep, and the
# whole failure mode above came from a cap that a real calendar could reach.
_MAX_PAGES = 40


class CalendarWindowTooLarge(RuntimeError):
    """The bounded window needs more pages than the cap allows.

    Raised instead of returning a truncated sweep, because a truncated sweep
    carries no `nextSyncToken` and would be repeated verbatim every cycle.
    """


class GoogleCalendarRunner:
    """SyncRunner for Google Calendar. Registered at import; inert without creds."""

    source = SOURCE

    def __init__(self, client_factory=GoogleClient) -> None:
        self._client_factory = client_factory

    def enabled(self) -> bool:
        return credentials_configured()

    async def run(self, conn, tenant_id: str, state: dict) -> dict | None:
        new_state = dict(state)
        sync_token = new_state.get("sync_token")

        async with self._client_factory() as google:
            try:
                events, next_token = await self._collect(google, sync_token)
            except SyncTokenExpired:
                # Google pruned the token. Re-window from scratch; the re-delivered
                # events are idempotent by external id, so this is safe to repeat.
                log.warning("gcal sync token expired; re-listing the window")
                events, next_token = await self._collect(google, None)

        if events:
            await ingest_payload(
                SOURCE,
                {"events": events},
                _EXISTS_HEADERS,
                tenant_id=tenant_id,
                receipt_event_type=SYNC_RECEIPT,
                conn=conn,
            )
            log.info("gcal sweep: %d changed event(s)", len(events))

        if next_token and next_token != sync_token:
            new_state["sync_token"] = next_token
            return new_state
        # Nothing changed and the token is the same — leave the stored state
        # alone rather than rewriting an identical row every cycle.
        return new_state if events else None

    async def _collect(self, google, sync_token: str | None):
        """Every changed event and the token to continue from.

        `nextSyncToken` only appears on the LAST page, which is why this pages to
        exhaustion rather than stopping at the first batch — returning early would
        drop the token and force a full re-window every single cycle.
        """
        collected: list[dict] = []
        page_token: str | None = None
        next_token: str | None = None
        time_min = time_max = None

        if not sync_token:
            now = datetime.now(timezone.utc)
            time_min = (now - timedelta(days=_WINDOW_DAYS)).strftime(_STAMP)
            time_max = (now + timedelta(days=_WINDOW_FORWARD_DAYS)).strftime(_STAMP)

        for _ in range(_MAX_PAGES):
            page = await google.calendar_events(
                sync_token=sync_token,
                time_min=time_min,
                time_max=time_max,
                page_token=page_token,
            )
            for item in page.get("items") or []:
                mapped = _map_event(item)
                if mapped is not None:
                    collected.append(mapped)
            next_token = page.get("nextSyncToken") or next_token
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        else:
            # Fell out of the loop with pages still pending — see the module
            # docstring. Failing here is what keeps this visible.
            raise CalendarWindowTooLarge(
                f"calendar window still had pages after {_MAX_PAGES} "
                f"({len(collected)} events collected); refusing to store a "
                "sweep with no syncToken to continue from"
            )

        if not next_token:
            # Pagination completed but Google returned no token. Not fatal — the
            # next cycle re-windows — but it means no incremental sync, so say so
            # rather than letting it look like a quiet success.
            log.warning(
                "gcal listing finished without a nextSyncToken; the next cycle "
                "will re-window instead of syncing incrementally"
            )

        return collected, next_token


def _map_event(item: dict) -> dict | None:
    """A Calendar event → the shape `adapters/gcal.py` already reads.

    Times are flattened out of Google's `{dateTime}` / `{date}` wrapper: a timed
    event carries `dateTime`, an all-day event carries `date`, and a consumer
    should not have to know which.
    """
    event_id = str(item.get("id") or "").strip()
    if not event_id:
        return None
    return {
        "id": event_id,
        "summary": str(item.get("summary") or "").strip() or "calendar event",
        "start": _when(item.get("start")),
        "end": _when(item.get("end")),
        "status": item.get("status"),
        "attendees": [
            a.get("email") for a in (item.get("attendees") or []) if a.get("email")
        ],
        "location": item.get("location"),
        "description": item.get("description"),
    }


def _when(slot) -> str | None:
    if not isinstance(slot, dict):
        return None
    return slot.get("dateTime") or slot.get("date")


register_runner(GoogleCalendarRunner())
