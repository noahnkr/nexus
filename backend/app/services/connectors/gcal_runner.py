"""Google Calendar sync runner (v1.3.0, Task 4).

Polling with a `syncToken`, same chassis as the Gmail runner.

    events.list(syncToken)  ->  changed events
    ingest_payload("gcal")  ->  receipt -> resolve -> calendar.event.updated

THE TOKEN LIFECYCLE is the whole job, and it has one sharp edge worth stating:
`syncToken` and `timeMin` are MUTUALLY EXCLUSIVE in Google's API. A first run has
no token, so it lists a bounded window (`timeMin` = now − 7 days) and keeps the
`nextSyncToken` that comes back on the last page; every run after that sends the
token and no window. Sending both is a 400.

A 410 means the token expired — Google prunes them after a few weeks of disuse.
The recovery is to drop it and re-window, which does re-deliver events that were
already seen. That is harmless: ingestion is idempotent by external id, so a
re-delivered event updates the same row rather than creating a second one.

WHY A BOUNDED WINDOW rather than the whole calendar: the same scope rule as the
Gmail runner. This mirrors the business going forward; the office's calendar
history stays in Google, which is already better at showing it.

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

# The header the gcal adapter reads to tell a real change from a watch handshake.
# The poll runner always has real changes, so it always says `exists`.
_EXISTS_HEADERS = {"x-goog-resource-state": "exists"}

_MAX_PAGES = 10


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
        time_min = None

        if not sync_token:
            time_min = (
                datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        for _ in range(_MAX_PAGES):
            page = await google.calendar_events(
                sync_token=sync_token, time_min=time_min, page_token=page_token
            )
            for item in page.get("items") or []:
                mapped = _map_event(item)
                if mapped is not None:
                    collected.append(mapped)
            next_token = page.get("nextSyncToken") or next_token
            page_token = page.get("nextPageToken")
            if not page_token:
                break

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
