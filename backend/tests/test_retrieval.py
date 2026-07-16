"""Retrieval test over the nexus_app RLS path. Inserts basis-vector chunks (as in
test_vector.py) for the demo tenant and a competing chunk for the probe tenant,
then confirms retrieve_chunks returns the demo nearest neighbour and never the
probe tenant's chunk — RLS does the filtering. embed_query is stubbed so no key
is needed. Skipped until NEXUS_APP_DB_URL is set.
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
            "insert into public.documents (tenant_id, filename, status) values (%s,%s,'ready') returning id",
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

    # Stub the query embedding to the basis vector at position 1.
    async def fake_embed_query(_text):
        return _basis(1)

    retrieval.embed_query = fake_embed_query  # module-level rebind for this test

    demo_doc = probe_doc = None
    conn = await psycopg.AsyncConnection.connect(NEXUS_APP_DB_URL, autocommit=True)
    try:
        demo_doc = await _seed(conn, DEMO_TENANT, "demo-retrieval.txt", [0, 1, 2])
        probe_doc = await _seed(conn, PROBE_TENANT, "probe-retrieval.txt", [1])

        # Query as the demo tenant.
        async with conn.cursor() as cur:
            await cur.execute(
                "select set_config('request.app.tenant_id', %s, false)", (DEMO_TENANT,)
            )
        results = await retrieval.retrieve_chunks(conn, "anything", limit=8)

        filenames = {r["filename"] for r in results}
        assert "probe-retrieval.txt" not in filenames, "RLS leak: probe chunk returned"
        assert results, "expected demo chunks"
        assert results[0]["chunk_index"] == 1, "nearest neighbour should be basis(1)"
        assert all(r["document_id"] == str(demo_doc) for r in results)
    finally:
        async with conn.cursor() as cur:
            for tid, doc in ((DEMO_TENANT, demo_doc), (PROBE_TENANT, probe_doc)):
                if doc is not None:
                    await cur.execute(
                        "select set_config('request.app.tenant_id', %s, false)", (tid,)
                    )
                    await cur.execute("delete from public.documents where id=%s", (doc,))
        await conn.close()


def test_retrieve_scopes_to_tenant():
    asyncio.run(_scenario())
