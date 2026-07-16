"""Ingestion pipeline: parse -> chunk -> embed -> ready.

process_document runs as a FastAPI BackgroundTask after the upload response. It
advances documents.status through processing -> ready (or failed on any error),
writing an events row at each transition. There is no worker/queue by design:
single tenant, human-paced uploads (CLAUDE.md scale discipline). The frontend
observes documents rows via Supabase Realtime.
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


@traceable(run_type="chain", name="process_document")
async def process_document(
    document_id: str, tenant_id: str, filename: str, data: bytes
) -> None:
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
                             (tenant_id, document_id, chunk_index, chunk_text, metadata)
                           values (%s, %s, %s, %s, %s) returning id""",
                        (tenant_id, document_id, c.index, c.text, Json(c.metadata)),
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
