"""Connector webhook ingress — the HTTP door onto the shared ingest seam.

POST /api/webhooks/{source}

This route owns HTTP concerns only: verify the signature BEFORE any DB access
(unauthenticated garbage never touches the database), parse the body, map
failures to status codes. Everything after that — the raw receipt, adapter
normalization, entity resolution — lives in `services/connectors/ingest.py`, the
single ingest seam that poll-based sync runners share (Module 18a).

Tenant identity comes from `get_machine_tenant_id` (env `NEXUS_TENANT_ID`), not a
user JWT: this ingress authenticates by HMAC signature, so it must never require
the user-surface bearer token. No user-facing JSON: task titles/summaries are plain
language, raw payloads live only in `events.payload`.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_machine_tenant_id
from ..services.connectors import get_adapter
from ..services.connectors.ingest import WEBHOOK_RECEIPT, ingest_payload

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/{source}")
async def receive_webhook(
    source: str,
    request: Request,
    tenant_id: str = Depends(get_machine_tenant_id),
):
    adapter = get_adapter(source)
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector source '{source}'")

    raw_body = await request.body()

    # Verify BEFORE any DB work. The default HMAC verify also fails closed when
    # NEXUS_WEBHOOK_SECRET is unset, so an unconfigured ingress rejects everything.
    if not adapter.verify(request.headers, raw_body):
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    try:
        payload = json.loads(raw_body) if raw_body else {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Malformed JSON body: {exc}")

    # Headers as a plain dict so the ingest seam doesn't hold the Request.
    headers = {k.lower(): v for k, v in request.headers.items()}
    return await ingest_payload(
        source,
        payload,
        headers,
        tenant_id=tenant_id,
        receipt_event_type=WEBHOOK_RECEIPT,
    )
