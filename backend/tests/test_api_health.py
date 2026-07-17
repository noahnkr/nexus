"""Boot-level check: the FastAPI app imports and /healthz answers.

Uses httpx ASGITransport against the app object directly. The lifespan (which
opens the psycopg pool, needing NEXUS_APP_DB_URL) is NOT triggered here — we call
the ASGI app without the lifespan context so this stays a pure import+route smoke
test that runs with no DB or keys.
"""
import asyncio

import httpx


def test_healthz():
    from app.main import app

    async def _call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.get("/healthz")

    resp = asyncio.run(_call())
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_app_has_expected_routes():
    from app.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/healthz" in paths
    # The realtime-token dev seam was retired in Module 6 (Realtime rides the
    # real Supabase session token now).
    assert "/api/auth/realtime-token" not in paths
