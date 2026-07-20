"""WelcomeHome CRM adapter (category: crm).

WelcomeHome has NO WEBHOOKS — confirmed against the live API, which has no
subscription endpoints at all. The real integration is therefore the poll-based
sync runner (`wh_runner.py`), which fetches export pages, translates them with
`wh_map.py`, and feeds them through the same ingest seam a webhook would use.
This adapter normalizes both shapes:

  * SYNC PAYLOADS (`prospect.synced` / `activity.synced`) — what the runner sends.
    The runner has already resolved WelcomeHome's reference vocabularies, so these
    payloads are flat and self-contained; the adapter only has to give them event
    shape. Keeping the refs OUT of the payload matters: they'd otherwise be copied
    into every receipt event, 55 lead sources at a time.
  * PLACEHOLDER WEBHOOK PAYLOADS (`lead.created` / `lead.updated` /
    `tour.scheduled`) — retained because the ingress and its tests are the general
    contract for a full-payload CRM push, and a future WelcomeHome (or a
    replacement CRM) may deliver exactly this.

Auth for the polled path is `Authorization: Token token={key}`, handled in
`wh_client.py`; the HMAC `verify` inherited here still guards the webhook door.
Docs: https://crm.welcomehomesoftware.com/api-docs/index.html
"""
from __future__ import annotations

from ..base import ConnectorAdapter, NormalizedEvent, NormalizedResult
from ..registry import register_adapter


class WelcomeHomeAdapter(ConnectorAdapter):
    source = "welcomehome"
    category = "crm"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        event = str(payload.get("event", "")).strip()

        if event == "prospect.synced":
            return self._prospect_synced(payload.get("prospect") or {})
        if event == "activity.synced":
            return self._activity_synced(payload.get("activity") or {})

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

    # -- polled sync shapes (wh_runner -> wh_map -> here) -------------------
    def _prospect_synced(self, prospect: dict) -> NormalizedResult:
        """One synced prospect. BOTH creates and updates: a poller re-sends every
        changed record, so the same event must stand up an unknown lead and patch
        a known one — resolution picks the branch from the external-id mapping."""
        external_id = str(prospect.get("external_id") or "").strip()
        if not external_id:
            return NormalizedResult(ack_only=True)

        name = str(prospect.get("name") or "").strip() or "Unknown prospect"
        stage = prospect.get("stage_name")
        summary = f"Prospect {name} synced from WelcomeHome"
        if stage:
            summary += f" ({stage})"

        return NormalizedResult(events=[
            NormalizedEvent(
                event_type="lead.updated",
                entity_type="lead",
                external_id=external_id,
                summary=summary,
                attributes=prospect,
                creates_entity=True,
                updates_entity=True,
                detail={"stage": stage, "source": prospect.get("source")},
            )
        ])

    def _activity_synced(self, activity: dict) -> NormalizedResult:
        """One synced activity -> a timeline entry on the lead it belongs to.

        Never `creates_entity`: an activity referencing a prospect we've never seen
        is a sync-ordering problem, and resolution's review task is the right
        outcome — inventing a nameless lead from a phone note would be worse."""
        external_id = str(activity.get("external_id") or "").strip()
        if not external_id:
            return NormalizedResult(ack_only=True)

        return NormalizedResult(
            events=[
                NormalizedEvent(
                    event_type="lead.activity_logged",
                    entity_type="lead",
                    external_id=external_id,
                    summary=activity.get("summary") or "Activity logged in WelcomeHome",
                    occurred_at=activity.get("occurred_at"),
                    detail={
                        "wh_activity_id": activity.get("activity_id"),
                        "activity_type": activity.get("activity_type"),
                        "direction": activity.get("direction"),
                        "notes": activity.get("notes"),
                        "completed_at": activity.get("occurred_at"),
                    },
                )
            ]
        )


register_adapter(WelcomeHomeAdapter())
