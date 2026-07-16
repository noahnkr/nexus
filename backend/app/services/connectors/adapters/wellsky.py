"""WellSky Personal Care (ex-ClearCare) EHR adapter (category: ehr).

REAL INTEGRATION FLOW (documented from the parent-plan research):
  * Auth: Connect API access granted via a WellSky account rep (partner-gated).
  * Subscription: webhook subscriptions deliver FHIR-shaped resources
    (Patient / Practitioner / Encounter). Some deliveries are minimal
    notifications that need a fetch-back for the full resource.
  * Delivery: full-payload FHIR webhook (with a poll/export fallback: if webhook
    access isn't granted, an external poller — n8n, M7 — POSTs into this same
    ingress, so the seam is unchanged).
  * Verify (real): WellSky's documented scheme.
  * Cursors/renewal: per the granted subscription; a poller keeps its own cursor
    in `connector_state`.
  Docs: https://apidocs.clearcareonline.com/

PLACEHOLDER PAYLOAD SHAPE — a single FHIR resource, mapped by resourceType:
  {"resourceType": "Patient" | "Practitioner" | "Encounter", "id": "fhir-...",
   "name": [{"text": "..."}]}
  Patient      -> client.updated   (entity_type client)
  Practitioner -> resource.updated (entity_type resource)
  Encounter    -> schedule.updated (entity_type schedule)
All are references (matched to a pre-seeded EHR external_id) — this phase records
the linked event; applying field updates onto business rows is the real adapter's
job.
"""
from __future__ import annotations

from ..base import ConnectorAdapter, NormalizedEvent, NormalizedResult
from ..registry import register_adapter

# FHIR resourceType -> (canonical event_type, canonical entity_type).
_RESOURCE_MAP = {
    "Patient": ("client.updated", "client"),
    "Practitioner": ("resource.updated", "resource"),
    "Encounter": ("schedule.updated", "schedule"),
}


def _display_name(payload: dict) -> str:
    name = payload.get("name")
    if isinstance(name, list) and name:
        first = name[0]
        if isinstance(first, dict) and first.get("text"):
            return str(first["text"])
    return payload.get("resourceType", "record")


class WellSkyAdapter(ConnectorAdapter):
    source = "wellsky"
    category = "ehr"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        resource_type = str(payload.get("resourceType", "")).strip()
        external_id = str(payload.get("id") or "").strip()
        mapping = _RESOURCE_MAP.get(resource_type)
        if mapping is None or not external_id:
            return NormalizedResult(ack_only=True)

        event_type, entity_type = mapping
        label = _display_name(payload)
        return NormalizedResult(events=[
            NormalizedEvent(
                event_type=event_type,
                entity_type=entity_type,
                external_id=external_id,
                summary=f"WellSky {resource_type} '{label}' updated",
                detail=payload,
            )
        ])


register_adapter(WellSkyAdapter())
