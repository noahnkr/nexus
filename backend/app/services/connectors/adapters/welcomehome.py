"""WelcomeHome CRM adapter (category: crm).

REAL INTEGRATION FLOW (documented from the parent-plan research; only the
internals of verify/normalize change when this becomes live):
  * Auth: account-scoped API token sent in the `Authorization` header. Smoke-test
    with `GET /api/ping`.
  * Subscription: register this ingress URL via WelcomeHome's
    webhook-subscriptions endpoint for lead-lifecycle events. Test accounts are
    provisioned through their support.
  * Delivery: full-payload webhook — the POST body carries the prospect record,
    so no fetch-back is needed (normalize reads the body directly).
  * Verify (real): swap the placeholder HMAC for WelcomeHome's signing scheme.
  * Cursors/renewal: none — full-payload push, no watermark to store.
  Docs: https://crm.welcomehomesoftware.com/api-docs/index.html

PLACEHOLDER PAYLOAD SHAPE (data inline, as a real full-payload webhook delivers):
  {"event": "lead.created" | "lead.updated" | "tour.scheduled",
   "prospect": {"id": "WH-PROSPECT-5001", "name": "...", "phone": "...",
                "email": "...", "source": "..."},
   "tour": {"start": "..."}}   # tour.scheduled only
The prospect id is the external_id; `lead.created` auto-creates a lead.
"""
from __future__ import annotations

from ..base import ConnectorAdapter, NormalizedEvent, NormalizedResult
from ..registry import register_adapter


class WelcomeHomeAdapter(ConnectorAdapter):
    source = "welcomehome"
    category = "crm"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        event = str(payload.get("event", "")).strip()
        prospect = payload.get("prospect") or {}
        external_id = str(prospect.get("id") or "").strip()
        name = str(prospect.get("name") or "").strip() or "Unknown prospect"

        if not external_id:
            return NormalizedResult(ack_only=True)

        if event == "lead.created":
            return NormalizedResult(events=[
                NormalizedEvent(
                    event_type="lead.created",
                    entity_type="lead",
                    external_id=external_id,
                    summary=f"New lead {name} from WelcomeHome",
                    attributes={
                        "name": name,
                        "phone": prospect.get("phone"),
                        "email": prospect.get("email"),
                        "source": prospect.get("source") or "welcomehome",
                    },
                    creates_entity=True,
                    detail=payload,
                )
            ])

        if event == "lead.updated":
            return NormalizedResult(events=[
                NormalizedEvent(
                    event_type="lead.updated",
                    entity_type="lead",
                    external_id=external_id,
                    summary=f"Lead {name} updated in WelcomeHome",
                    detail=payload,
                )
            ])

        if event == "tour.scheduled":
            return NormalizedResult(events=[
                NormalizedEvent(
                    event_type="tour.scheduled",
                    entity_type="lead",
                    external_id=external_id,
                    summary=f"Tour scheduled for lead {name}",
                    detail=payload,
                )
            ])

        # Unknown event type: record the receipt, produce nothing to resolve.
        return NormalizedResult(ack_only=True)


register_adapter(WelcomeHomeAdapter())
