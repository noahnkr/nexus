"""Embeddings tests. Batching is verified with a stubbed Voyage client (no key);
a single live smoke test runs only when VOYAGE_API_KEY is set.
"""
import asyncio

import pytest

from app.services import embeddings
from app.services.embeddings import EMBED_DIM, embed_documents, embed_query, to_pgvector


class _FakeResp:
    def __init__(self, n):
        self.embeddings = [[0.0] * EMBED_DIM for _ in range(n)]


class _FakeVoyage:
    def __init__(self):
        self.batch_sizes = []

    async def embed(self, texts, model, input_type):
        self.batch_sizes.append(len(texts))
        assert input_type in ("document", "query")
        return _FakeResp(len(texts))


def test_batches_capped_at_128(monkeypatch):
    fake = _FakeVoyage()
    monkeypatch.setattr(embeddings, "get_voyage", lambda: fake)
    texts = [f"chunk {i}" for i in range(300)]
    result = asyncio.run(embed_documents(texts))
    assert len(result) == 300
    assert fake.batch_sizes == [128, 128, 44]
    assert all(len(v) == EMBED_DIM for v in result)


def test_empty_documents_no_call(monkeypatch):
    fake = _FakeVoyage()
    monkeypatch.setattr(embeddings, "get_voyage", lambda: fake)
    assert asyncio.run(embed_documents([])) == []
    assert fake.batch_sizes == []


def test_embed_query_single(monkeypatch):
    fake = _FakeVoyage()
    monkeypatch.setattr(embeddings, "get_voyage", lambda: fake)
    vec = asyncio.run(embed_query("hello"))
    assert len(vec) == EMBED_DIM
    assert fake.batch_sizes == [1]


def test_to_pgvector_format():
    lit = to_pgvector([1.0, 2.5, -3.0])
    assert lit == "[1.0,2.5,-3.0]"


def test_dim_mismatch_raises(monkeypatch):
    class _BadResp:
        embeddings = [[0.0] * 10]

    class _BadVoyage:
        async def embed(self, *a, **k):
            return _BadResp()

    monkeypatch.setattr(embeddings, "get_voyage", lambda: _BadVoyage())
    with pytest.raises(ValueError):
        asyncio.run(embed_query("x"))


@pytest.mark.live
def test_live_embedding_is_1024_dim():
    import os

    if not os.getenv("VOYAGE_API_KEY"):
        pytest.skip("VOYAGE_API_KEY not set")
    vec = asyncio.run(embed_query("in-home senior care scheduling"))
    assert len(vec) == EMBED_DIM
