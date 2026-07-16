"""Connector adapter seam.

An adapter turns one external system's webhook delivery into a list of canonical
`NormalizedEvent`s. The resolution router (resolution.py) is what actually writes
anything — adapters are pure: verify the request, then translate the payload.

Three delivery shapes are supported by this one seam (see the parent plan's
research table):
  * full-payload webhook (WelcomeHome, GoTo, WellSky) — normalize from the body;
  * ping + fetch-back (Gmail, GCal) — the body is a watermark, so `normalize` is
    async (real adapters do the API call-back there) and may return `ack_only`
    for handshake/sync pings that carry no event;
  * poll/export — lives outside the core (n8n, M7); the poller re-POSTs into the
    same ingress, so nothing here changes.

Placeholder adapters carry their data inline as if the fetch-back already ran;
the real adapters add the fetch-back inside `normalize` without touching the seam.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

from ...config import settings

# The header carrying the hex HMAC-SHA256 of the raw body for placeholder verify.
SIGNATURE_HEADER = "x-nexus-signature"


def sign(body: bytes) -> str:
    """Hex HMAC-SHA256 of the raw body under the shared webhook secret. Used by
    the default `verify` and by tests to sign fixtures. Empty secret ⇒ empty
    string, which `verify` treats as fail-closed."""
    secret = settings.nexus_webhook_secret
    if not secret:
        return ""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _header(headers: Any, name: str) -> str | None:
    """Case-insensitive header lookup that works for a dict or a Starlette
    Headers object."""
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is not None:
        # Starlette Headers.get is already case-insensitive; a plain dict is not,
        # so fall back to a manual scan for the dict case.
        val = getter(name)
        if val is not None:
            return val
    try:
        lname = name.lower()
        for k, v in dict(headers).items():
            if str(k).lower() == lname:
                return v
    except (TypeError, ValueError):
        return None
    return None


@dataclass
class NormalizedEvent:
    """One canonical event translated from an external payload."""

    event_type: str  # canonical, e.g. "lead.created"
    entity_type: str  # "lead" | "client" | "resource" | "schedule"
    external_id: str  # the source's id for the entity (or a phone/email/etc.)
    summary: str  # plain language — reaches tasks/UI
    attributes: dict = field(default_factory=dict)  # canonical fields; auto-create only
    creates_entity: bool = False  # True ⇒ this event stands up a new entity
    occurred_at: str | None = None
    detail: dict = field(default_factory=dict)  # technical payload for the event row


@dataclass
class NormalizedResult:
    """The outcome of `normalize`. `ack_only` is for handshake/sync pings that
    carry no business event (the raw receipt is still recorded)."""

    ack_only: bool = False
    events: list[NormalizedEvent] = field(default_factory=list)


class ConnectorAdapter:
    """Base adapter. Subclasses set `source`/`category` and implement `normalize`;
    they override `verify` only when the real platform uses a different scheme
    (GoTo signature keys, Google channel token / Pub/Sub OIDC, etc.).

    `source` is the URL segment and `events.source_system`; `category` is the
    `external_ids.source_system` bucket (`crm|phone|ehr|email|calendar`).
    """

    source: str = ""
    category: str = ""

    def verify(self, headers: Any, body: bytes) -> bool:
        """Default: constant-time compare of the hex HMAC-SHA256 of the raw body
        against the X-Nexus-Signature header. Fail closed when the secret is unset
        or the header is missing."""
        expected = sign(body)
        if not expected:
            return False
        provided = _header(headers, SIGNATURE_HEADER)
        if not provided:
            return False
        return hmac.compare_digest(expected, provided)

    async def normalize(self, payload: dict, headers: Any) -> NormalizedResult:
        raise NotImplementedError
