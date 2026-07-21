"""Communications tier (v1.1.0): the store for messages, calls, and emails.

`ingest_communication` is the ONE entry every message source writes through — the
WelcomeHome CRM activity path today, the GoTo/Gmail messaging connectors next.
Two principles, both enforced here (CLAUDE.md "Knowledge tiers"):

  * STORE-ALL, EMBED-SELECTIVELY. Every message is stored in `communications`
    (timeline-linked, durable). Only long-form correspondence is chunked into
    `communication_chunks` and embedded — `should_embed` decides. A short SMS is
    stored but never embedded (it is a message, not a retrievable document).
  * EVENT-AS-SPINE + DEDUP. A communication links to its originating `events` row
    via `source_event_id`; `content_hash` deduplicates the same message arriving
    from two sources (or a re-run without an `external_id`).

Business-agnostic: no vertical concepts. Follows the ingestion discipline —
chunk/embed round-trips happen with no transaction held open, and a failure raises
(the caller logs + skips; a bad message must never cost a sync its cursor).
"""
from __future__ import annotations

import hashlib
import logging

from psycopg.types.json import Json

from ..db import tenant_tx
from ..llm import traceable
from .chunking import chunk_text
from .embeddings import embed_documents, to_pgvector

log = logging.getLogger("nexus.communications")

# Below this, a message is a one-liner (a text, a "left a voicemail" note) —
# store it, but embedding it buys nothing. Carried over from WelcomeHome's
# empirically-set narrative threshold; the single home for the number now.
EMBED_MIN_CHARS = 500

# Channels that are never embedded regardless of length — a short-message medium
# is a message, not a document, even on the rare long one.
_NEVER_EMBED_CHANNELS = frozenset({"sms"})


def should_embed(channel: str, body: str) -> bool:
    """The embed-selectively policy. Long-form prose on a non-message channel is
    worth retrieving; everything else is store-only."""
    if channel in _NEVER_EMBED_CHANNELS:
        return False
    return len((body or "").strip()) >= EMBED_MIN_CHARS


def content_hash(channel: str, direction: str | None, occurred_at, body: str) -> str:
    """Stable dedup key over the message's identity. Same tuple from two sources
    (or a re-run without an external id) hashes the same, so we store it once."""
    parts = f"{channel}|{direction or ''}|{occurred_at}|{(body or '').strip()}"
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


@traceable(run_type="chain", name="ingest_communication")
async def ingest_communication(
    tenant_id: str,
    *,
    channel: str,
    direction: str | None,
    occurred_at,
    body: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    subject: str | None = None,
    source: str,
    external_id: str | None = None,
    source_event_id: str | None = None,
    embed: bool | None = None,
) -> str | None:
    """Store one message (store-all), embedding it when the policy says so. Returns
    the communication id, or None when there was nothing to store.

    Idempotent: a re-offered `(source, external_id)` finds the existing row; a
    message with the same `content_hash` for the same entity is deduplicated even
    across sources. `embed` overrides the policy (the backfill's structured pass
    passes `embed=False` to defer embedding to its batched pass).
    """
    body = (body or "").strip()
    if not body:
        return None

    hashed = content_hash(channel, direction, occurred_at, body)

    async with tenant_tx(tenant_id) as conn:
        # Idempotency by connector id.
        if external_id is not None:
            row = await (
                await conn.execute(
                    "select id from public.communications "
                    "where source = %s and external_id = %s",
                    (source, external_id),
                )
            ).fetchone()
            if row is not None:
                return str(row[0])

        # Cross-source (and no-external-id replay) dedup by content + entity.
        row = await (
            await conn.execute(
                "select id from public.communications "
                "where content_hash = %s "
                "  and entity_type is not distinct from %s "
                "  and entity_id is not distinct from %s "
                "limit 1",
                (hashed, entity_type, entity_id),
            )
        ).fetchone()
        if row is not None:
            return str(row[0])

        row = await (
            await conn.execute(
                """insert into public.communications
                     (tenant_id, channel, direction, occurred_at, subject, body,
                      entity_type, entity_id, source, external_id, content_hash,
                      source_event_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   returning id""",
                (tenant_id, channel, direction, occurred_at, subject, body,
                 entity_type, entity_id, source, external_id, hashed, source_event_id),
            )
        ).fetchone()
        communication_id = str(row[0])

    if embed is None:
        embed = should_embed(channel, body)
    if embed:
        await embed_communication(
            tenant_id, communication_id, body=body,
            entity_type=entity_type, entity_id=entity_id, source=source,
        )
    return communication_id


@traceable(run_type="chain", name="embed_communication")
async def embed_communication(
    tenant_id: str,
    communication_id: str,
    *,
    body: str,
    entity_type: str | None,
    entity_id: str | None,
    source: str,
) -> bool:
    """Chunk + embed one already-stored communication and mark it embedded. Idempotent
    (a re-embed clears prior chunks first), so the backfill's batched-embed pass can
    re-run safely. Returns True when it embedded, False when there was nothing to
    chunk. Embedding round-trips happen with no transaction held open."""
    chunks = chunk_text(body or "")
    if not chunks:
        return False

    async with tenant_tx(tenant_id) as conn:
        await conn.execute(
            "delete from public.communication_chunks where communication_id = %s",
            (communication_id,),
        )
        chunk_ids: list[str] = []
        for c in chunks:
            row = await (
                await conn.execute(
                    """insert into public.communication_chunks
                         (tenant_id, communication_id, chunk_index, chunk_text,
                          metadata, entity_type, entity_id, source)
                       values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                    (tenant_id, communication_id, c.index, c.text, Json(c.metadata),
                     entity_type, entity_id, source),
                )
            ).fetchone()
            chunk_ids.append(row[0])

    embeddings = await embed_documents([c.text for c in chunks])

    async with tenant_tx(tenant_id) as conn:
        for chunk_id, embedding in zip(chunk_ids, embeddings):
            await conn.execute(
                "update public.communication_chunks set embedding = %s::vector "
                "where id = %s",
                (to_pgvector(embedding), chunk_id),
            )
        await conn.execute(
            "update public.communications set embedded = true where id = %s",
            (communication_id,),
        )
    return True
