"""Client care smart summary (Module 16a, Task 5), gated on NEXUS_APP_DB_URL.

Mirrors test_applicant_summary: offline cases monkeypatch the Anthropic client so
prompt assembly + response shape are proven without a network call — the summary
carries {summary, generated_at}, first GET generates + caches, second GET is a
cache hit, regenerate forces a fresh call, an unset key -> 503, unknown id -> 404.

The client-specific assertion is that the prompt sees the CARE picture in plain
language: the status LABEL (not the raw `hospital_hold`), the payer label, the
contact with their relationship, and the delivered-of-authorized hours line. That
framing is what makes the summary useful to a coordinator rather than a field dump.

One gated-live case (ANTHROPIC_API_KEY present) exercises the real fast model and
its `client_summary` span end to end.
"""
import asyncio
import os
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

WALTER = "44444444-0000-0000-0000-000000000001"  # seeded active client


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
        return _Resp("Walter is stable on private pay; confirm this week's coverage.")


class _Client:
    def __init__(self, captured):
        self.messages = _Messages(captured)


async def _offline_scenario(key, captured):
    from app import db
    from app.config import settings
    from app.main import app
    from app.services.views import summary as summary_mod

    original_key = settings.anthropic_api_key
    settings.anthropic_api_key = key or ""
    summary_mod.get_anthropic = lambda: _Client(captured)

    token = uuid.uuid4().hex[:8]
    out = {"token": token}
    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            created = await ac.post("/api/clients", json={
                "name": f"Summary Client {token}",
                "payer": "medicaid",
                "authorized_hours_per_week": 24.0,
                "care_summary": "Needs help with morning transfers.",
            })
            client_id = created.json()["id"]
            out["client_id"] = client_id
            await ac.post(f"/api/clients/{client_id}/contacts", json={
                "name": "Nia Fletcher", "relationship": "daughter", "is_primary": True,
            })
            # A hospital hold, so the prompt must show the LABEL not the raw value.
            await ac.patch(f"/api/clients/{client_id}", json={"status": "hospital_hold"})

            out["resp"] = await ac.get(f"/api/clients/{client_id}/summary")
            out["calls_after_get1"] = len(captured)
            out["resp2"] = await ac.get(f"/api/clients/{client_id}/summary")
            out["calls_after_get2"] = len(captured)
            out["regen"] = await ac.post(f"/api/clients/{client_id}/summary/regenerate")
            out["calls_after_regen"] = len(captured)
            out["unknown_code"] = (
                await ac.get("/api/clients/00000000-0000-0000-0000-0000000000aa/summary")
            ).status_code

        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.entity_summaries where entity_id=%s", (client_id,)
            )
            await conn.execute(
                "delete from public.client_contacts where client_id=%s", (client_id,)
            )
            await conn.execute("delete from public.clients where id=%s", (client_id,))
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
    assert body["summary"].startswith("Walter")
    assert body["generated_at"]

    assert out["calls_after_get1"] == 1
    blob = str(captured[0])
    assert f"Summary Client {out['token']}" in blob
    # Plain language, not raw enum values or uuids.
    assert "Hospital hold" in blob
    assert "hospital_hold" not in blob
    assert "Medicaid" in blob
    assert "Nia Fletcher (daughter)" in blob
    assert "of 24.0 authorized" in blob
    # The care-coordination framing, not the hiring one.
    assert "home-care client" in blob

    assert out["resp2"].status_code == 200
    assert out["resp2"].json()["generated_at"] == body["generated_at"]
    assert out["calls_after_get2"] == 1  # cache hit, no second call
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
                return await ac.post(f"/api/clients/{WALTER}/summary/regenerate")
        finally:
            await db.close_pool()

    r = asyncio.run(_run())
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["summary"], str) and r.json()["summary"].strip()
