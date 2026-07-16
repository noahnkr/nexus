"""Google Calendar adapter (category: calendar).

REAL INTEGRATION FLOW (documented from the parent-plan research) — a "ping +
fetch-back" source, so `normalize` is async:
  * Auth: OAuth2.
  * Subscription: `events.watch` opens a notification channel that POSTs a
    BODYLESS ping to this ingress. The `X-Goog-Resource-State` header is `sync`
    on the initial handshake and `exists` when something changed.
  * Delivery: the real ping carries NO event data — only headers.
  * Fetch-back (real): call `events.list` with the stored `syncToken` to get the
    changed events; a 410 means the token expired -> do a full resync.
  * Verify (real): validate the channel token (`X-Goog-Channel-Token`) set at
    watch time (replace the placeholder HMAC).
  * Cursors/renewal: store/advance `syncToken` in `connector_state`; channels
    expire -> renew.
  Docs: https://developers.google.com/workspace/calendar/api/guides/push

PLACEHOLDER: reads `X-Goog-Resource-State`. `sync` -> ack-only (handshake). For
`exists`, the placeholder body carries the changed events inline (as if the
events.list fetch-back already ran):
  {"events": [{"id": "cal-evt-1", "summary": "...", "start": "...", "end": "...",
               "attendees": [...]}]}
Each emits `calendar.event.updated` (reference; entity_type schedule, external_id
= calendar event id).
"""
from __future__ import annotations

from ..base import ConnectorAdapter, NormalizedEvent, NormalizedResult, _header
from ..registry import register_adapter

RESOURCE_STATE_HEADER = "x-goog-resource-state"


class GoogleCalendarAdapter(ConnectorAdapter):
    source = "gcal"
    category = "calendar"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        state = (_header(headers, RESOURCE_STATE_HEADER) or "").strip().lower()
        if state == "sync":
            # Watch-channel handshake: no change, nothing to resolve.
            return NormalizedResult(ack_only=True)

        events = []
        for evt in payload.get("events") or []:
            external_id = str(evt.get("id") or "").strip()
            if not external_id:
                continue
            title = str(evt.get("summary") or "calendar event").strip()
            events.append(
                NormalizedEvent(
                    event_type="calendar.event.updated",
                    entity_type="schedule",
                    external_id=external_id,
                    summary=f"Calendar event updated: {title}",
                    occurred_at=evt.get("start"),
                    detail=evt,
                )
            )
        if not events:
            return NormalizedResult(ack_only=True)
        return NormalizedResult(events=events)


register_adapter(GoogleCalendarAdapter())
