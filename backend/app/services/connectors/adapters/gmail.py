"""Gmail adapter (category: email).

REAL INTEGRATION FLOW (documented from the parent-plan research) — this is a
"ping + fetch-back" source, which is why `normalize` is async:
  * Auth: OAuth2.
  * Subscription: `users.watch` -> a Cloud Pub/Sub topic -> a push subscription
    that POSTs to this ingress.
  * Delivery: the push body is only `{emailAddress, historyId}` (base64 inside a
    Pub/Sub envelope) — a WATERMARK, no message content.
  * Fetch-back (real): call `users.history.list` starting from the stored
    historyId to get the new messages, then advance the cursor.
  * Verify (real): validate the Pub/Sub push OIDC token (replace the placeholder
    HMAC).
  * Cursors/renewal: store/advance `historyId` in `connector_state`; `watch` must
    be renewed at least every 7 days.
  Docs: https://developers.google.com/workspace/gmail/api/guides/push

PLACEHOLDER PAYLOAD SHAPE — the Pub/Sub push envelope, with the decoded data
carrying messages inline (as if the history.list fetch-back already ran):
  {"message": {"data": <base64 of
        {"emailAddress": "...", "historyId": "...",
         "messages": [{"from": "sender@x.com", "subject": "...", "snippet": "..."}]}>,
      "messageId": "..."},
   "subscription": "..."}
Each message emits `email.received` (reference; external_id = sender address).
"""
from __future__ import annotations

import base64
import json

from ..base import ConnectorAdapter, NormalizedEvent, NormalizedResult
from ..registry import register_adapter


def _decode_data(payload: dict) -> dict:
    """Decode the base64 `message.data` of the Pub/Sub envelope into a dict.
    Returns {} when absent or unparseable (handshake pings)."""
    message = payload.get("message") or {}
    data = message.get("data")
    if not data:
        return {}
    try:
        raw = base64.b64decode(data)
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (ValueError, TypeError):
        return {}


class GmailAdapter(ConnectorAdapter):
    source = "gmail"
    category = "email"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        data = _decode_data(payload)
        messages = data.get("messages") or []
        if not messages:
            # Watermark-only ping with nothing to fetch (or handshake): ack.
            return NormalizedResult(ack_only=True)

        events = []
        for msg in messages:
            sender = str(msg.get("from") or "").strip()
            if not sender:
                continue
            subject = str(msg.get("subject") or "(no subject)").strip()
            events.append(
                NormalizedEvent(
                    event_type="email.received",
                    entity_type="lead",
                    external_id=sender,
                    summary=f"Email from {sender}: {subject}",
                    detail={"message": msg, "historyId": data.get("historyId")},
                )
            )
        if not events:
            return NormalizedResult(ack_only=True)
        return NormalizedResult(events=events)


register_adapter(GmailAdapter())
