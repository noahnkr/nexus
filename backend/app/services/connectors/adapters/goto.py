"""GoTo Connect VoIP/SMS adapter (category: phone).

REAL INTEGRATION FLOW (documented from the parent-plan research):
  * Auth: OAuth2.
  * Subscription: create a Notification Channel (webhook URL or WebSocket), then
    subscribe it to the Call Events API (`POST /call-events/v1/subscriptions`).
  * Delivery: full-payload webhook (or WebSocket) — call/SMS events arrive with
    the caller number and metadata.
  * Verify (real): GoTo's signature keys on the channel.
  * Cursors/renewal: notification channels EXPIRE — store the channel id and
    expiry in `connector_state` and renew before it lapses. The WebSocket variant
    is an alternate delivery that a poller (M7) would bridge into this ingress.
  * Outbound SMS (`messaging.v1.send`) is an M5 gated tool, not this module.
  Docs: https://developer.goto.com/guides/GoToConnect/14_HOW_useNotificationChannelApi/

PLACEHOLDER PAYLOAD SHAPE:
  {"type": "call.completed" | "sms.received",
   "call": {"id": "...", "from": "+1 (619) 555-0101", "durationSeconds": 42},
   "message": {"id": "...", "from": "...", "text": "..."}}   # sms.received
The caller number, normalized to E.164, is the external_id — matching a known
lead requires a seeded phone mapping in external_ids; an unknown number becomes a
review task.
"""
from __future__ import annotations

import re

from ..base import ConnectorAdapter, NormalizedEvent, NormalizedResult
from ..registry import register_adapter


def _e164(raw: str | None) -> str:
    """Best-effort E.164 normalization of a caller number: keep a leading '+'
    and digits only. Placeholder-grade — a real adapter uses GoTo's normalized
    field or libphonenumber."""
    if not raw:
        return ""
    s = str(raw).strip()
    digits = re.sub(r"[^0-9]", "", s)
    if s.startswith("+"):
        return "+" + digits
    if len(digits) == 10:  # bare US number
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits if digits else ""


class GoToAdapter(ConnectorAdapter):
    source = "goto"
    category = "phone"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        kind = str(payload.get("type", "")).strip()

        if kind == "call.completed":
            call = payload.get("call") or {}
            number = _e164(call.get("from"))
            if not number:
                return NormalizedResult(ack_only=True)
            return NormalizedResult(events=[
                NormalizedEvent(
                    event_type="call.completed",
                    entity_type="lead",
                    external_id=number,
                    summary=f"Completed call from {number}",
                    detail=payload,
                )
            ])

        if kind == "sms.received":
            message = payload.get("message") or {}
            number = _e164(message.get("from"))
            if not number:
                return NormalizedResult(ack_only=True)
            return NormalizedResult(events=[
                NormalizedEvent(
                    event_type="sms.received",
                    entity_type="lead",
                    external_id=number,
                    summary=f"SMS received from {number}",
                    detail=payload,
                )
            ])

        return NormalizedResult(ack_only=True)


register_adapter(GoToAdapter())
