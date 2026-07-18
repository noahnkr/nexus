"""Lead smart summary (Module 9a, Task 2), gated on NEXUS_APP_DB_URL.

Offline cases monkeypatch the Anthropic client (the test_automation_draft pattern)
so the prompt-assembly + response shape is proven without a network call: the
summary carries {summary, generated_at}, the prompt includes the lead's name and a
seeded event summary, an unset key -> 503 plain message, and an unknown lead -> 404.

One gated-live case (ANTHROPIC_API_KEY present) exercises the real fast model end
to end; the lead_summary span is verified live in Task 5.
"""
import asyncio
import os

import httpx
import pytest

from conftest import NEXUS_APP_DB_URL, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

# Margaret Ellison — seeded lead with a seeded lead.created event
# ("New website inquiry from Margaret Ellison").
MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"


class _TextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_TextBlock(text)]


class _Messages:
    def __init__(self, captured):
        self.captured = captured

    async def create(self, **kwargs):
        self.captured.append(kwargs)
        return _Resp("Margaret is a promising new website lead; call her to qualify.")


class _Client:
    def __init__(self, captured):
        self.messages = _Messages(captured)


async def _offline_scenario(monkeypatch_key: str | None, captured):
    import uuid

    from app import db
    from app.main import app
    from app.services.views import summary as summary_mod
    from app.config import settings
    from conftest import DEMO_TENANT

    # Force the key on/off to exercise the 503 path deterministically.
    original_key = settings.anthropic_api_key
    settings.anthropic_api_key = monkeypatch_key or ""
    summary_mod.get_anthropic = lambda: _Client(captured)  # bypass the real client

    token = uuid.uuid4().hex[:8]
    out = {"token": token}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # A fresh lead — its own lead.created event ("... created manually") is
            # the seeded activity we assert on, unpolluted by other tests' events on
            # the shared demo leads.
            created = await ac.post("/api/leads", json={"name": f"Summary Lead {token}"})
            lead_id = created.json()["id"]
            out["lead_id"] = lead_id

            # first GET generates + caches; second GET serves the cache (no new call);
            # regenerate forces a fresh generation. Snapshot the LLM call count after
            # each so we can tell cache hits from generations.
            out["resp"] = await ac.get(f"/api/leads/{lead_id}/summary")
            out["calls_after_get1"] = len(captured)
            out["resp2"] = await ac.get(f"/api/leads/{lead_id}/summary")
            out["calls_after_get2"] = len(captured)
            out["regen"] = await ac.post(f"/api/leads/{lead_id}/summary/regenerate")
            out["calls_after_regen"] = len(captured)
            out["unknown_code"] = (
                await ac.get("/api/leads/00000000-0000-0000-0000-0000000000aa/summary")
            ).status_code

        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.entity_summaries where entity_id=%s", (lead_id,)
            )
            await conn.execute("delete from public.leads where id=%s", (lead_id,))
        return out
    finally:
        settings.anthropic_api_key = original_key
        await db.close_pool()


def test_summary_offline_with_key():
    from app.services.views import summary as summary_mod

    captured: list = []
    original = summary_mod.get_anthropic
    try:
        out = asyncio.run(_offline_scenario("sk-test-key", captured))
    finally:
        summary_mod.get_anthropic = original

    r = out["resp"]
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"].startswith("Margaret")
    assert body["generated_at"]

    # the assembled prompt saw the lead name and its (seeded) event summary
    assert out["calls_after_get1"] == 1  # first GET generated once
    blob = str(captured[0])
    assert f"Summary Lead {out['token']}" in blob
    assert "created manually" in blob.lower()

    # second GET is served from cache: same generated_at, NO new LLM call
    r2 = out["resp2"]
    assert r2.status_code == 200
    assert r2.json()["generated_at"] == body["generated_at"]
    assert out["calls_after_get2"] == 1  # still one call across two GETs

    # regenerate forces a fresh generation (a new LLM call, new timestamp)
    regen = out["regen"]
    assert regen.status_code == 200
    assert out["calls_after_regen"] == 2
    assert regen.json()["generated_at"] >= body["generated_at"]

    # unknown lead -> 404 (even with a key)
    assert out["unknown_code"] == 404


def test_summary_503_without_key():
    from app.services.views import summary as summary_mod

    original = summary_mod.get_anthropic
    try:
        out = asyncio.run(_offline_scenario(None, []))
    finally:
        summary_mod.get_anthropic = original

    assert out["resp"].status_code == 503
    assert "key" in out["resp"].json()["detail"].lower()


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_summary_live():
    from app import db
    from app.main import app

    async def _run():
        await db.open_pool()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t", headers=bearer_headers()
            ) as ac:
                # Regenerate forces a real model call (a plain GET may hit a cache
                # left by a prior run) and reaches the `lead_summary` LangSmith span.
                return await ac.post(f"/api/leads/{MARGARET_LEAD}/summary/regenerate")
        finally:
            await db.close_pool()

    r = asyncio.run(_run())
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["summary"], str) and body["summary"].strip()
