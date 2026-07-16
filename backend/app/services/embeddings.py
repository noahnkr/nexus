"""Voyage embeddings. voyage-3.5 -> 1024 dims, matching the document_chunks column.

Documents are embedded in batches of <=128 with input_type="document"; queries
use input_type="query". Both spans are traced. `to_pgvector` renders a Python
float list as the '[...]' literal pgvector's `::vector` cast accepts.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..config import settings
from ..llm import get_voyage, traceable

EMBED_DIM = 1024
_BATCH_SIZE = 128


def _assert_dims(vectors: Sequence[Sequence[float]]) -> None:
    for v in vectors:
        if len(v) != EMBED_DIM:
            raise ValueError(f"Expected {EMBED_DIM}-dim embedding, got {len(v)}")


@traceable(run_type="embedding", name="embed_documents")
async def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = get_voyage()
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        resp = await client.embed(
            batch, model=settings.embedding_model, input_type="document"
        )
        out.extend(resp.embeddings)
    _assert_dims(out)
    return out


@traceable(run_type="embedding", name="embed_query")
async def embed_query(text: str) -> list[float]:
    client = get_voyage()
    resp = await client.embed(
        [text], model=settings.embedding_model, input_type="query"
    )
    embedding = resp.embeddings[0]
    _assert_dims([embedding])
    return embedding


def to_pgvector(embedding: Sequence[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"
