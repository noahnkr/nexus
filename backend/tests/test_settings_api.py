"""Tenant settings seam + API (Module 15b, Task 1), gated on NEXUS_APP_DB_URL.

Drives the real router through the ASGI transport. The load-bearing properties:
defaults for a tenant that has never saved anything, partial updates that don't
clobber siblings, validation that rejects before writing, RLS isolation between
tenants, and — the one that matters for privacy — a `settings.updated` audit event
that names the changed KEYS and never their values.

Rows are deleted afterwards (events are immutable, so event assertions scope to the
unique marker text written into workspace_name).
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


async def _clear(db, tenant):
    async with db.tenant_tx(tenant) as conn:
        await conn.execute("delete from public.tenant_settings")


async def _scenario():
    from app import db
    from app.main import app

    marker = f"Acme Care {uuid.uuid4().hex[:8]}"
    out: dict = {"marker": marker}

    await db.open_pool()
    try:
        await _clear(db, DEMO_TENANT)
        await _clear(db, PROBE_TENANT)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=bearer_headers(DEMO_TENANT)
        ) as ac:
            # A tenant that has never saved settings still gets a full object.
            out["defaults"] = (await ac.get("/api/settings")).json()

            # Partial update: one key in, the others keep their defaults.
            out["patch_one"] = (
                await ac.patch("/api/settings", json={"workspace_name": marker})
            ).json()

            # A second partial update must not clobber the first key.
            out["patch_two"] = (
                await ac.patch(
                    "/api/settings",
                    json={"agent_instructions": "Always sign off as The Nexus Team.",
                          "agent_tone": "friendly"},
                )
            ).json()

            # Re-read proves it persisted rather than just echoing back.
            out["reread"] = (await ac.get("/api/settings")).json()

            # --- validation: each rejection leaves the stored row untouched ---
            out["unknown_key"] = (
                await ac.patch("/api/settings", json={"nope": "x"})
            ).status_code
            out["bad_tone"] = (
                await ac.patch("/api/settings", json={"agent_tone": "sassy"})
            ).status_code
            out["long_instructions"] = (
                await ac.patch("/api/settings", json={"agent_instructions": "x" * 4001})
            ).status_code
            out["long_name"] = (
                await ac.patch("/api/settings", json={"workspace_name": "x" * 81})
            ).status_code
            out["after_rejections"] = (await ac.get("/api/settings")).json()

        # RLS: a second tenant sees its own defaults, not the demo tenant's values.
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=bearer_headers(PROBE_TENANT)
        ) as ac2:
            out["probe"] = (await ac2.get("/api/settings")).json()

        # No token at all -> 401 (these are user preferences; no machine path).
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
            out["anon_get"] = (await anon.get("/api/settings")).status_code
            out["anon_patch"] = (
                await anon.patch("/api/settings", json={"workspace_name": "x"})
            ).status_code

        # The audit trail names keys, never values.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            from psycopg.rows import dict_row

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select payload from public.events "
                    "where event_type='settings.updated' order by created_at desc limit 5"
                )
                out["events"] = [r["payload"] for r in await cur.fetchall()]

        await _clear(db, DEMO_TENANT)
        await _clear(db, PROBE_TENANT)
        return out
    finally:
        await db.close_pool()


def test_settings_api():
    out = asyncio.run(_scenario())
    marker = out["marker"]

    # Defaults for a fresh tenant.
    assert out["defaults"] == {
        "workspace_name": "",
        "agent_instructions": "",
        "agent_tone": "balanced",
    }

    # Partial updates merge rather than replace.
    assert out["patch_one"]["workspace_name"] == marker
    assert out["patch_one"]["agent_tone"] == "balanced"
    assert out["patch_two"]["workspace_name"] == marker  # not clobbered
    assert out["patch_two"]["agent_tone"] == "friendly"
    assert out["reread"] == out["patch_two"]

    # Every invalid patch is a 422 and changes nothing.
    assert out["unknown_key"] == 422
    assert out["bad_tone"] == 422
    assert out["long_instructions"] == 422
    assert out["long_name"] == 422
    assert out["after_rejections"] == out["reread"]

    # RLS: the probe tenant is unaffected by the demo tenant's writes.
    assert out["probe"]["workspace_name"] == ""
    assert out["probe"]["agent_tone"] == "balanced"

    # Preferences are user-JWT only.
    assert out["anon_get"] == 401
    assert out["anon_patch"] == 401

    # The audit event names the changed keys...
    assert out["events"], "expected settings.updated events"
    latest = out["events"][0]
    assert set(latest["keys"]) == {"agent_instructions", "agent_tone"}
    assert "agent instructions" in latest["summary"] and "tone" in latest["summary"]

    # ...and never leaks a value into the log.
    blob = " ".join(str(e) for e in out["events"])
    assert marker not in blob
    assert "The Nexus Team" not in blob
