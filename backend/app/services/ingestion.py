"""Ingestion pipeline: parse -> chunk -> embed -> ready.

process_document runs as a FastAPI BackgroundTask after the upload response. It
advances documents.status through processing -> ready (or failed on any error),
writing an events row at each transition. There is no worker/queue by design:
single tenant, human-paced uploads (CLAUDE.md scale discipline). The frontend
observes documents rows via Supabase Realtime.

`ingest_text` (Module 18a) is the same pipeline minus file parsing, for text that
arrives already extracted — a call transcript on a CRM activity, say. It reuses
the M15 entity tag rather than inventing a second association mechanism, so a
transcript ingested against a lead is retrievable exactly like an uploaded care
plan is. The document row carries a NULL `storage_path`: there is no original
file to keep, and the text itself lives in the chunks.
"""
from __future__ import annotations

import logging

from psycopg.types.json import Json

from ..db import tenant_tx
from ..llm import traceable
from .chunking import chunk_text
from .embeddings import embed_documents, to_pgvector
from .events import log_event
from .parsing import parse_document

log = logging.getLogger("nexus.ingestion")


async def _set_status(conn, document_id: str, status: str, error: str | None = None) -> None:
    await conn.execute(
        "update public.documents set status = %s, error = %s where id = %s",
        (status, error, document_id),
    )


@traceable(run_type="chain", name="ingest_text")
async def ingest_text(
    tenant_id: str,
    title: str,
    text: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    source: str = "ingestion",
    external_id: str | None = None,
) -> str | None:
    """Chunk + embed already-extracted text as a document. Returns the document id,
    or None when there was nothing worth ingesting.

    `external_id` makes this idempotent, which a re-runnable connector backfill
    needs: the same transcript re-offered on a later sweep finds its existing
    document and returns without duplicating chunks. It is stored on the document
    row's `filename`-adjacent metadata via `external_ids`, the same mechanism every
    other connector-owned record uses.

    Unlike `process_document` this raises on failure rather than marking a document
    `failed`. There is no user watching an upload progress bar here — the caller is
    a sync runner, and a failure is its `connector.sync_failed`, not a broken row
    left in someone's document list.
    """
    text = (text or "").strip()
    if not text:
        return None

    async with tenant_tx(tenant_id) as conn:
        if external_id is not None:
            row = await (
                await conn.execute(
                    "select entity_id from public.external_ids "
                    "where entity_type = 'document' and external_id = %s",
                    (external_id,),
                )
            ).fetchone()
            if row is not None:
                return str(row[0])

        row = await (
            await conn.execute(
                """insert into public.documents
                     (tenant_id, filename, mime_type, status, storage_path,
                      entity_type, entity_id)
                   values (%s, %s, 'text/plain', 'processing', null, %s, %s)
                   returning id""",
                (tenant_id, title, entity_type, entity_id),
            )
        ).fetchone()
        document_id = str(row[0])

        if external_id is not None:
            await conn.execute(
                """insert into public.external_ids
                     (tenant_id, entity_type, entity_id, source_system, external_id)
                   values (%s, 'document', %s, 'crm', %s)
                   on conflict (tenant_id, source_system, external_id) do nothing""",
                (tenant_id, document_id, external_id),
            )

    chunks = chunk_text(text)
    if not chunks:
        async with tenant_tx(tenant_id) as conn:
            await _set_status(conn, document_id, "failed", error="no chunkable text")
        return None

    async with tenant_tx(tenant_id) as conn:
        chunk_ids: list[str] = []
        for c in chunks:
            row = await (
                await conn.execute(
                    """insert into public.document_chunks
                         (tenant_id, document_id, chunk_index, chunk_text, metadata,
                          entity_type, entity_id)
                       values (%s, %s, %s, %s, %s, %s, %s) returning id""",
                    (tenant_id, document_id, c.index, c.text, Json(c.metadata),
                     entity_type, entity_id),
                )
            ).fetchone()
            chunk_ids.append(row[0])

    embeddings = await embed_documents([c.text for c in chunks])

    async with tenant_tx(tenant_id) as conn:
        for chunk_id, embedding in zip(chunk_ids, embeddings):
            await conn.execute(
                "update public.document_chunks set embedding = %s::vector where id = %s",
                (to_pgvector(embedding), chunk_id),
            )
        await _set_status(conn, document_id, "ready")
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system=source,
            event_type="document.ready",
            entity_type="document",
            entity_id=document_id,
            payload={
                "summary": f"'{title}' ingested and ready to search",
                "chunk_count": len(chunks),
            },
        )
    return document_id


@traceable(run_type="chain", name="process_document")
async def process_document(
    document_id: str,
    tenant_id: str,
    filename: str,
    data: bytes,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> None:
    """Parse -> chunk -> embed -> ready for one uploaded document.

    `entity_type`/`entity_id` carry the upload's optional canonical-entity tag
    (M16a). When present, every chunk is stamped with it so retrieval can scope to
    "this client's documents". When absent the chunk columns stay NULL, exactly as
    before this module — an untagged upload is tenant-general knowledge.
    """
    try:
        async with tenant_tx(tenant_id) as conn:
            await _set_status(conn, document_id, "processing")
            await log_event(
                conn,
                tenant_id=tenant_id,
                source_system="ingestion",
                event_type="document.processing",
                entity_type="document",
                entity_id=document_id,
            )

        parsed = parse_document(filename, data)
        chunks = chunk_text(parsed.text)
        if not chunks:
            raise ValueError("no extractable text in document")

        # Insert chunks first with NULL embeddings (chunks exist before embedding).
        async with tenant_tx(tenant_id) as conn:
            chunk_ids: list[str] = []
            for c in chunks:
                row = await (
                    await conn.execute(
                        """insert into public.document_chunks
                             (tenant_id, document_id, chunk_index, chunk_text, metadata,
                              entity_type, entity_id)
                           values (%s, %s, %s, %s, %s, %s, %s) returning id""",
                        (tenant_id, document_id, c.index, c.text, Json(c.metadata),
                         entity_type, entity_id),
                    )
                ).fetchone()
                chunk_ids.append(row[0])

        # Embed (batched inside embed_documents), then write vectors back.
        embeddings = await embed_documents([c.text for c in chunks])

        async with tenant_tx(tenant_id) as conn:
            for chunk_id, embedding in zip(chunk_ids, embeddings):
                await conn.execute(
                    "update public.document_chunks set embedding = %s::vector where id = %s",
                    (to_pgvector(embedding), chunk_id),
                )
            await _set_status(conn, document_id, "ready")
            await log_event(
                conn,
                tenant_id=tenant_id,
                source_system="ingestion",
                event_type="document.ready",
                entity_type="document",
                entity_id=document_id,
                payload={"chunk_count": len(chunks)},
            )

    except Exception as exc:  # noqa: BLE001 — any failure marks the document failed
        log.exception("ingestion failed for document %s", document_id)
        async with tenant_tx(tenant_id) as conn:
            await _set_status(conn, document_id, "failed", error=str(exc))
            await log_event(
                conn,
                tenant_id=tenant_id,
                source_system="ingestion",
                event_type="document.failed",
                entity_type="document",
                entity_id=document_id,
                payload={"error": str(exc)},
            )
