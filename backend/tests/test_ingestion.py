"""End-to-end ingestion test over the real nexus_app DB path (RLS-scoped), with
the Voyage embedder and Supabase Storage stubbed. Skipped until NEXUS_APP_DB_URL
is set. Verifies: upload -> row reaches 'ready', chunks present with embeddings,
>=3 lifecycle events; and the failed path sets `error` on unparseable input.

Background tasks run to completion within the ASGITransport call (Starlette awaits
them before the response coroutine returns), so state can be asserted right after.
"""
import asyncio

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, bearer_headers

pytestmark = pytest.mark.skipif(
    not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

EMBED_DIM = 1024


async def _fake_embed_documents(texts):
    return [[0.01] * EMBED_DIM for _ in texts]


async def _noop(*args, **kwargs):
    return None


def _patch(monkeypatch):
    from app.services import ingestion, storage

    monkeypatch.setattr(ingestion, "embed_documents", _fake_embed_documents)
    monkeypatch.setattr(storage, "upload", _noop)
    monkeypatch.setattr(storage, "remove", _noop)


async def _post_file(ac, filename, content, content_type):
    files = {"file": (filename, content, content_type)}
    return await ac.post("/api/documents", files=files)


def _run(coro):
    return asyncio.run(coro)


async def _with_app(fn):
    from app import db
    from app.main import app

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            return await fn(ac)
    finally:
        await db.close_pool()


def test_markdown_upload_reaches_ready(monkeypatch):
    _patch(monkeypatch)
    md = b"# Care Plan\n\n" + (b"The client requires morning visits. " * 100)

    async def scenario(ac):
        resp = await _post_file(ac, "care.md", md, "text/markdown")
        assert resp.status_code == 202
        doc_id = resp.json()["id"]
        assert resp.json()["status"] == "uploaded"

        detail = (await ac.get(f"/api/documents/{doc_id}")).json()
        assert detail["status"] == "ready", detail
        assert detail["chunk_count"] >= 1
        return doc_id

    doc_id = _run(_with_app(scenario))

    # Verify chunks got embeddings and >=3 lifecycle events were written.
    import psycopg

    with psycopg.connect(NEXUS_APP_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select set_config('request.app.tenant_id', %s, false)", (DEMO_TENANT,))
            cur.execute(
                "select count(*), count(embedding) from public.document_chunks where document_id=%s",
                (doc_id,),
            )
            total, embedded = cur.fetchone()
            assert total >= 1 and embedded == total
            cur.execute(
                """select array_agg(event_type order by created_at)
                   from public.events where entity_id=%s and source_system='ingestion'""",
                (doc_id,),
            )
            events = cur.fetchone()[0]
            assert "document.uploaded" in events
            assert "document.processing" in events
            assert "document.ready" in events
            assert len(events) >= 3
            # cleanup
            cur.execute("delete from public.documents where id=%s", (doc_id,))
        conn.commit()


def test_unparseable_pdf_sets_failed(monkeypatch):
    _patch(monkeypatch)
    garbage = b"%PDF-1.4 this is not a real pdf body \x00\x01\x02"

    async def scenario(ac):
        resp = await _post_file(ac, "broken.pdf", garbage, "application/pdf")
        assert resp.status_code == 202
        doc_id = resp.json()["id"]
        detail = (await ac.get(f"/api/documents/{doc_id}")).json()
        assert detail["status"] == "failed"
        assert detail["error"]
        return doc_id

    doc_id = _run(_with_app(scenario))

    import psycopg

    with psycopg.connect(NEXUS_APP_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select set_config('request.app.tenant_id', %s, false)", (DEMO_TENANT,))
            cur.execute(
                "select 1 from public.events where entity_id=%s and event_type='document.failed'",
                (doc_id,),
            )
            assert cur.fetchone() is not None
            cur.execute("delete from public.documents where id=%s", (doc_id,))
        conn.commit()
