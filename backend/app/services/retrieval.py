"""Retrieval: basic RAG over pgvector cosine distance, top-8.

Runs on the tenant-scoped connection, so RLS does the tenant filtering (this
proves the nexus_app + GUC path end to end). No threshold, no hybrid, no rerank —
those are Module 10. Only chunks with a non-null embedding are considered.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from ..llm import traceable
from .embeddings import embed_query, to_pgvector

TOP_K = 8


@traceable(run_type="retriever", name="retrieve_chunks")
async def retrieve_chunks(conn, query: str, *, limit: int = TOP_K) -> list[dict]:
    query_vec = to_pgvector(await embed_query(query))
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select c.id            as chunk_id,
                      c.document_id,
                      c.chunk_index,
                      c.chunk_text,
                      d.filename
               from public.document_chunks c
               join public.documents d on d.id = c.document_id
               where c.embedding is not null
               order by c.embedding <=> %s::vector
               limit %s""",
            (query_vec, limit),
        )
        rows = await cur.fetchall()
    return [
        {
            "chunk_id": str(r["chunk_id"]),
            "document_id": str(r["document_id"]),
            "chunk_index": r["chunk_index"],
            "chunk_text": r["chunk_text"],
            "filename": r["filename"],
        }
        for r in rows
    ]
