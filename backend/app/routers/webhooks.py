"""Connector webhook ingress — the single inbound path for external systems.

POST /api/webhooks/{source}

Every inbound event enters here (real webhooks now; poll/export sources re-POST
through the same URL in M7). The order is deliberate and enforces the CLAUDE.md
rules: verify the signature BEFORE any DB access (unauthenticated garbage never
touches the database), then write the raw receipt to `events`, then resolve each
normalized event to a canonical entity via `external_ids`.

Tenant identity comes from `get_machine_tenant_id` (env `NEXUS_TENANT_ID`), not a
user JWT: this ingress authenticates by HMAC signature, so it must never require
the user-surface bearer token. No user-facing JSON: task titles/summaries are plain
language, raw payloads live only in `events.payload`.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import tenant_tx
from ..deps import get_machine_tenant_id
from ..llm import traceable
from ..services.connectors import get_adapter
from ..services.connectors.resolution import route_normalized_event
from ..services.events import log_event

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

    # Headers as a plain dict so the traced function doesn't hold the Request.
    headers = {k.lower(): v for k, v in request.headers.items()}
    return await _process_ingress(adapter, tenant_id, payload, headers)


@traceable(run_type="chain", name="webhook_ingress")
async def _process_ingress(adapter, tenant_id: str, payload: dict, headers: dict) -> dict:
    async with tenant_tx(tenant_id) as conn:
        # 1. Raw receipt — audit-worthy on its own, kept even for ack-only pings.
        receipt_id = await log_event(
            conn,
            tenant_id=tenant_id,
            source_system=adapter.source,
            event_type="webhook.received",
            payload={"source": adapter.source, "body": payload},
        )

        # 2. Normalize (async: real ping+fetch-back adapters call the source here).
        result = await adapter.normalize(payload, headers)
        if result.ack_only:
            return {"status": "ack"}

        # 3. Resolve each event to a canonical entity.
        counts = {"received": len(result.events), "matched": 0, "created": 0, "tasks": 0}
        for ev in result.events:
            outcome = await route_normalized_event(conn, tenant_id, adapter, ev, receipt_id)
            if outcome.resolution == "matched":
                counts["matched"] += 1
            elif outcome.resolution == "created":
                counts["created"] += 1
            else:
                counts["tasks"] += 1
        return counts
