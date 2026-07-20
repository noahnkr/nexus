"""THE single inbound path for external systems (Module 18a).

Every connector event — pushed or polled — enters through `ingest_payload`:

    webhook POST  ->  routers/webhooks.py (verify signature)  -\
                                                                >-- ingest_payload
    sync runner   ->  services/connectors/sync.py (fetch+map)  -/

The order inside is fixed and enforces the CLAUDE.md rules: write the RAW RECEIPT
to `events` first (audit-worthy on its own, kept even for ack-only pings), then
normalize through the source's adapter, then resolve each normalized event to a
canonical entity via `external_ids`. No connector ever writes a business table
without passing entity resolution.

The split of concerns with the webhook route is deliberate: the route owns HTTP
(signature verification, status codes, JSON parsing) and this owns ingestion.
That is what lets a poll-based runner — which has no request, no signature, and
no HTTP status to return — reuse the exact same path rather than growing a
second, subtly different one.

`webhook.received` vs `connector.received`: the same receipt, labeled honestly by
how it arrived. A polled export row was never a webhook, and an office user
reading the Event Log should not be told otherwise.
"""
from __future__ import annotations

from ...db import tenant_tx
from ...llm import traceable
from ..events import log_event
from . import get_adapter
from .resolution import route_normalized_event

# Receipt event types, by arrival mode.
WEBHOOK_RECEIPT = "webhook.received"
SYNC_RECEIPT = "connector.received"


class UnknownSource(LookupError):
    """No adapter registered for this source name. The webhook route turns this
    into a 404; a runner would be a programming error, since runners name their
    own source."""


async def ingest_payload(
    source: str,
    payload: dict,
    headers: dict | None = None,
    *,
    tenant_id: str,
    receipt_event_type: str = WEBHOOK_RECEIPT,
    conn=None,
) -> dict:
    """Ingest one payload from `source`. Returns the per-outcome counts
    (`{received, matched, created, tasks}`) or `{"status": "ack"}` for a payload
    the adapter judged to carry no business event.

    `conn` lets a caller that already holds a tenant-scoped transaction (the sync
    runner, which ingests a whole page inside one transaction) reuse it; omitted,
    one is opened per call — the webhook route's behavior.
    """
    adapter = get_adapter(source)
    if adapter is None:
        raise UnknownSource(f"Unknown connector source '{source}'")
    return await _process_ingress(
        adapter, tenant_id, payload, headers or {}, receipt_event_type, conn
    )


@traceable(run_type="chain", name="webhook_ingress")
async def _process_ingress(
    adapter, tenant_id: str, payload: dict, headers: dict,
    receipt_event_type: str, conn,
) -> dict:
    if conn is not None:
        return await _ingest_on(conn, adapter, tenant_id, payload, headers, receipt_event_type)
    async with tenant_tx(tenant_id) as own_conn:
        return await _ingest_on(
            own_conn, adapter, tenant_id, payload, headers, receipt_event_type
        )


async def _ingest_on(
    conn, adapter, tenant_id: str, payload: dict, headers: dict, receipt_event_type: str
) -> dict:
    # 1. Raw receipt — audit-worthy on its own, kept even for ack-only pings.
    receipt_id = await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=adapter.source,
        event_type=receipt_event_type,
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
