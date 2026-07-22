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
    """The decoded event data, whichever way it arrived.

    Two shapes reach here and both are legitimate:
      * the **poll runner's** payload, which already carries `messages` at the top
        level because it did the `history.list` + `messages.get` fetch itself;
      * the **Pub/Sub push envelope**, whose `message.data` is base64 JSON.

    Falling through to the top level first means the runner does not have to
    base64-wrap its own data purely to satisfy a shape that only exists because
    Pub/Sub requires it — and push delivery, if it is ever added, reuses this
    adapter unchanged.
    """
    if isinstance(payload.get("messages"), list):
        return payload

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
            # `counterpart` is the runner's decoded shape (the other party,
            # whichever direction the mail went); `from` is the placeholder
            # fixture's. Accepting both keeps the pre-v1.3.0 ingress tests — which
            # assert the general webhook contract, not Gmail specifics — passing.
            counterpart = str(
                msg.get("counterpart") or msg.get("from") or ""
            ).strip().lower()
            if not counterpart:
                continue
            subject = str(msg.get("subject") or "(no subject)").strip()
            direction = str(msg.get("direction") or "inbound").strip().lower()
            who = str(msg.get("counterpart_name") or counterpart).strip()
            verb = "Email from" if direction == "inbound" else "Email to"

            events.append(
                NormalizedEvent(
                    event_type=(
                        "email.received" if direction == "inbound" else "email.sent"
                    ),
                    # A fallback only: an address carries no entity type, so
                    # `resolve_by="email"` lets the match decide it (v1.3.0).
                    entity_type="lead",
                    external_id=counterpart,
                    resolve_by="email",
                    summary=f"{verb} {who}: {subject}",
                    occurred_at=msg.get("occurred_at"),
                    attributes={
                        "channel": "email",
                        "direction": direction,
                        "email": counterpart,
                        "subject": subject,
                        "body": msg.get("body") or "",
                        "external_message_id": msg.get("message_id"),
                        "attachments": msg.get("attachments") or [],
                    },
                    detail={"message": msg, "historyId": data.get("historyId")},
                )
            )
        if not events:
            return NormalizedResult(ack_only=True)
        return NormalizedResult(events=events)


register_adapter(GmailAdapter())
