"""Applicant hiring smart summary (Module 10a, Task 3), gated on NEXUS_APP_DB_URL.

Mirrors test_lead_summary: offline cases monkeypatch the Anthropic client so the
prompt-assembly + response shape is proven without a network call — the summary
carries {summary, generated_at}, the prompt includes the applicant's name + stage +
a seeded event summary, first GET generates + caches, second GET is a cache hit,
regenerate forces a fresh call, an unset key -> 503, an unknown applicant -> 404.

One gated-live case (ANTHROPIC_API_KEY present) exercises the real fast model end
to end; the applicant_summary span is verified live in Task 6.
"""
import asyncio
import os

import httpx
import pytest

from conftest import NEXUS_APP_DB_URL, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

# Priya Raman — seeded applicant at the 'interview' stage.
PRIYA_APPLICANT = "dddddddd-0000-0000-0000-000000000003"


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
        return _Resp("Priya is a strong interview-stage applicant; schedule the panel.")


class _Client:
    def __init__(self, captured):
        self.messages = _Messages(captured)


async def _offline_scenario(monkeypatch_key, captured):
    import uuid

    from app import db
    from app.config import settings
    from app.main import app
    from app.services.views import summary as summary_mod
    from conftest import DEMO_TENANT

    original_key = settings.anthropic_api_key
    settings.anthropic_api_key = monkeypatch_key or ""
    summary_mod.get_anthropic = lambda: _Client(captured)

    token = uuid.uuid4().hex[:8]
    out = {"token": token}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            # A fresh applicant — its own applicant.created event ("... created
            # manually") is the seeded activity we assert on.
            created = await ac.post("/api/applicants", json={"name": f"Summary Applicant {token}"})
            applicant_id = created.json()["id"]
            out["applicant_id"] = applicant_id

            out["resp"] = await ac.get(f"/api/applicants/{applicant_id}/summary")
            out["calls_after_get1"] = len(captured)
            out["resp2"] = await ac.get(f"/api/applicants/{applicant_id}/summary")
            out["calls_after_get2"] = len(captured)
            out["regen"] = await ac.post(f"/api/applicants/{applicant_id}/summary/regenerate")
            out["calls_after_regen"] = len(captured)
            out["unknown_code"] = (
                await ac.get("/api/applicants/00000000-0000-0000-0000-0000000000aa/summary")
            ).status_code

        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.entity_summaries where entity_id=%s", (applicant_id,)
            )
            await conn.execute("delete from public.applicants where id=%s", (applicant_id,))
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
    assert body["summary"].startswith("Priya")
    assert body["generated_at"]

    # the assembled prompt saw the applicant name + its seeded event summary
    assert out["calls_after_get1"] == 1
    blob = str(captured[0])
    assert f"Summary Applicant {out['token']}" in blob
    assert "created manually" in blob.lower()

    # second GET served from cache (no new call), regenerate forces a fresh one
    assert out["resp2"].status_code == 200
    assert out["resp2"].json()["generated_at"] == body["generated_at"]
    assert out["calls_after_get2"] == 1
    assert out["regen"].status_code == 200
    assert out["calls_after_regen"] == 2

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
                return await ac.post(f"/api/applicants/{PRIYA_APPLICANT}/summary/regenerate")
        finally:
            await db.close_pool()

    r = asyncio.run(_run())
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["summary"], str) and r.json()["summary"].strip()
