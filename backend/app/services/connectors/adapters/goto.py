"""GoTo Connect VoIP/SMS adapter (category: phone) — v1.2.0, real payloads.

Contract unchanged from the seam's point of view: `verify` + `normalize`, pure.
All translation lives in `gt_map.py`; this module decides only *what canonical
event* a translated payload becomes.

DELIVERY. Events arrive over a Notification Channel (WebSocket) rather than an
inbound HTTP webhook — `goto_bridge.py` reads frames and calls the same
`ingest_payload` seam the webhook route uses, so there is exactly one inbound
path (CLAUDE.md). The HTTP route stays wired for shape-compatibility and tests.

RESOLUTION. A phone number does not tell you what kind of person it belongs to,
so these events carry `resolve_by="phone"`: resolution looks the number up across
leads, clients, caregivers and their contact rows and lets the match decide the
entity type. The old hardcoded `entity_type="lead"` was wrong the moment a
caregiver called in — it is now only the fallback used to phrase the review task
when nothing matches.

THE COUNTERPART, NOT THE OFFICE LINE, is the resolution key (gt_map's rule): an
inbound call resolves on the caller, an outbound call on the callee. A call whose
counterpart is on `GOTO_IGNORED_NUMBERS` — WelcomeHome's provisional bridge
number — is acked without resolution: the raw receipt is still written, so the
audit trail is intact, but no timeline entry and no communication is created,
because that leg is plumbing rather than correspondence.

CALLS CARRY NO BODY TEXT. This account produces no recordings or transcripts
(established empirically 2026-07-21 — see `gt_map`'s module docstring), so a call
event is metadata: who, when, how long, which direction. SMS does carry text and
reaches the communications tier in full.
"""
from __future__ import annotations

from .. import gt_map
from ..base import ConnectorAdapter, NormalizedEvent, NormalizedResult
from ..registry import register_adapter

# The placeholder payload shapes the pre-v1.2.0 ingress tests use. Still accepted:
# those tests assert the general webhook contract (verify → receipt → resolution),
# not GoTo specifics, and breaking them would be testing the test.
_LEGACY_CALL = "call.completed"
_LEGACY_SMS = "sms.received"


class GoToAdapter(ConnectorAdapter):
    source = "goto"
    category = "phone"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        kind = str(payload.get("type", "")).strip()

        # --- legacy placeholder shapes -----------------------------------
        if kind == _LEGACY_CALL:
            return self._call(payload.get("call") or {}, payload)
        if kind == _LEGACY_SMS:
            return self._sms(payload.get("message") or {}, payload)

        # --- real notification frames ------------------------------------
        frame_kind = gt_map.frame_kind(payload)
        if frame_kind == "call":
            return self._call(gt_map.frame_content(payload), payload)
        if frame_kind == "sms":
            return self._sms(gt_map.frame_content(payload), payload)

        return NormalizedResult(ack_only=True)

    # -- canonical events ---------------------------------------------------
    def _call(self, record: dict, payload: dict) -> NormalizedResult:
        mapped = gt_map.map_call(record)
        if mapped is None:
            # Un-resolvable, internal, or guarded. The receipt is already written
            # by the ingest seam; stopping here is the whole point of the guard.
            return NormalizedResult(ack_only=True)
        return NormalizedResult(events=[
            NormalizedEvent(
                event_type="call.completed",
                entity_type="lead",  # fallback only — see module docstring
                external_id=mapped["counterpart"],
                resolve_by="phone",
                summary=mapped["summary"],
                occurred_at=mapped["occurred_at"],
                attributes={
                    "channel": "call",
                    "direction": mapped["direction"],
                    "phone": mapped["counterpart"],
                    "duration_seconds": mapped["duration_seconds"],
                    "external_call_id": mapped["external_call_id"],
                },
                detail=payload,
            )
        ])

    def _sms(self, record: dict, payload: dict) -> NormalizedResult:
        mapped = gt_map.map_sms(record)
        if mapped is None:
            return NormalizedResult(ack_only=True)
        event_type = (
            "sms.received" if mapped["direction"] == "inbound" else "sms.sent"
        )
        return NormalizedResult(events=[
            NormalizedEvent(
                event_type=event_type,
                entity_type="lead",  # fallback only — see module docstring
                external_id=mapped["counterpart"],
                resolve_by="phone",
                summary=mapped["summary"],
                occurred_at=mapped["occurred_at"],
                attributes={
                    "channel": "sms",
                    "direction": mapped["direction"],
                    "phone": mapped["counterpart"],
                    "body": mapped["body"],
                    "external_message_id": mapped["external_message_id"],
                },
                detail=payload,
            )
        ])


register_adapter(GoToAdapter())
