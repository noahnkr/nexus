"""search_documents tool (Module 2, Task 3).

Reuses test_retrieval.py's basis-vector pattern with a stubbed embed_query, so no
Voyage key is needed. Proves turn-global numbering (start_index offsets the [n]),
the top_k cap at 8, and tenant isolation through the tool. Skipped until
NEXUS_APP_DB_URL is set.
"""
import asyncio

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT

pytestmark = pytest.mark.skipif(
    not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

DIM = 1024


def _basis(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


async def _seed(conn, tenant_id, filename, indices):
    async with conn.cursor() as cur:
        await cur.execute(
            "select set_config('request.app.tenant_id', %s, false)", (tenant_id,)
        )
        await cur.execute(
            "insert into public.documents (tenant_id, filename, status) "
            "values (%s,%s,'ready') returning id",
            (tenant_id, filename),
        )
        doc_id = (await cur.fetchone())[0]
        for idx in indices:
            vec = "[" + ",".join(str(x) for x in _basis(idx)) + "]"
            await cur.execute(
                """insert into public.document_chunks
                     (tenant_id, document_id, chunk_index, chunk_text, embedding)
                   values (%s,%s,%s,%s,%s::vector)""",
                (tenant_id, doc_id, idx, f"{filename} chunk {idx}", vec),
            )
    return doc_id


async def _scenario():
    import psycopg

    from app.services import retrieval
    from app.services.tools import get_tool

    async def fake_embed_query(_text):
        return _basis(1)

    retrieval.embed_query = fake_embed_query

    demo_doc = probe_doc = None
    conn = await psycopg.AsyncConnection.connect(NEXUS_APP_DB_URL, autocommit=True)
    try:
        demo_doc = await _seed(conn, DEMO_TENANT, "demo-search.txt", list(range(10)))
        probe_doc = await _seed(conn, PROBE_TENANT, "probe-search.txt", [1])

        async with conn.cursor() as cur:
            await cur.execute(
                "select set_config('request.app.tenant_id', %s, false)", (DEMO_TENANT,)
            )

        search = get_tool("search_documents")
        # top_k above the cap must clamp to 8; start_index=3 offsets numbering.
        result = await search.handler(conn, {"query": "x", "top_k": 100, "start_index": 3})
        # a second call with a small top_k, continuing numbering.
        result2 = await search.handler(conn, {"query": "x", "top_k": 2, "start_index": 11})
        return result, result2
    finally:
        async with conn.cursor() as cur:
            for tid, doc in ((DEMO_TENANT, demo_doc), (PROBE_TENANT, probe_doc)):
                if doc is not None:
                    await cur.execute(
                        "select set_config('request.app.tenant_id', %s, false)", (tid,)
                    )
                    await cur.execute("delete from public.documents where id=%s", (doc,))
        await conn.close()


def test_search_documents_tool():
    result, result2 = asyncio.run(_scenario())

    sources = result.data["sources"]
    # top_k=100 capped at 8.
    assert len(sources) == 8
    # start_index=3 -> numbering begins at [4] and is contiguous.
    assert [s["n"] for s in sources] == list(range(4, 12))
    # no probe-tenant passage leaked in.
    assert all("probe-search" not in s["filename"] for s in sources)
    # the model-facing payload keeps chunk_text; each source has a snippet too.
    assert "chunk_text" in sources[0] and "snippet" in sources[0]

    # second search: top_k=2 respected, numbering continues from start_index.
    assert len(result2.data["sources"]) == 2
    assert [s["n"] for s in result2.data["sources"]] == [12, 13]
