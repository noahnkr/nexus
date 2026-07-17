"""Module 6a auth: the JWT verification matrix + machine-path exemptions +
`resolved_by` identity. Gated on NEXUS_APP_DB_URL (the 200/RLS/identity paths need
the pool + DB); the 401/403 paths short-circuit in `get_tenant_id` before any DB
access but run inside the same opened-pool scenario for simplicity.

Tokens are HS256, minted locally with SUPABASE_JWT_SECRET — the offline path
`get_tenant_id` accepts alongside the ES256 tokens Supabase Auth issues. No network.
"""
import asyncio
import time
import uuid

import httpx
import jwt
import pytest

from conftest import (
    DEMO_TENANT,
    NEXUS_APP_DB_URL,
    PROBE_TENANT,
    SUPABASE_JWT_SECRET,
    bearer_headers,
)

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")


def _mint(*, aud="authenticated", tenant=DEMO_TENANT, email=None, exp_delta=3600):
    now = int(time.time())
    payload = {
        "role": "authenticated",
        "aud": aud,
        "sub": "00000000-0000-0000-0000-0000000000ff",
        "iat": now,
        "exp": now + exp_delta,
    }
    if tenant is not None:
        payload["app_metadata"] = {"tenant_id": tenant}
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, SUPABASE_JWT_SECRET, algorithm="HS256")


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _scenario():
    from app import db
    from app.main import app
    from app.services.tools import execute_tool

    token = uuid.uuid4().hex[:8]
    out = {"created_task_ids": []}

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            # --- verification matrix on a protected route (GET /api/tasks) ---
            out["no_header"] = (await ac.get("/api/tasks")).status_code
            out["garbage"] = (
                await ac.get("/api/tasks", headers=_h("not.a.jwt"))
            ).status_code
            out["expired"] = (
                await ac.get("/api/tasks", headers=_h(_mint(exp_delta=-10)))
            ).status_code
            out["wrong_aud"] = (
                await ac.get("/api/tasks", headers=_h(_mint(aud="anon")))
            ).status_code
            out["no_tenant"] = (
                await ac.get("/api/tasks", headers=_h(_mint(tenant=None)))
            ).status_code

            # --- valid demo token: 200, and it sees a demo-tenant row ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "insert into public.tasks (tenant_id, title, status, priority) "
                        "values (%s, %s, 'pending', 'normal') returning id",
                        (DEMO_TENANT, f"authtest {token}"),
                    )
                    demo_task_id = str((await cur.fetchone())[0])
            out["created_task_ids"].append(demo_task_id)

            demo = await ac.get(
                "/api/tasks", params={"limit": 100}, headers=bearer_headers(DEMO_TENANT)
            )
            out["demo_code"] = demo.status_code
            out["demo_sees_own"] = demo_task_id in {t["id"] for t in demo.json()["tasks"]}

            # probe token: RLS hides the demo row (verified through the JWT claim)
            probe = await ac.get(
                "/api/tasks", params={"limit": 100}, headers=bearer_headers(PROBE_TENANT)
            )
            out["probe_code"] = probe.status_code
            out["probe_blind"] = demo_task_id not in {t["id"] for t in probe.json()["tasks"]}

            # --- machine paths never require a user JWT ---
            out["healthz"] = (await ac.get("/healthz")).status_code
            # unknown webhook source -> 404 (not a missing-bearer 401): the ingress
            # authenticates by signature, so get_tenant_id doesn't gate it.
            out["webhook_unauth"] = (
                await ac.post("/api/webhooks/does_not_exist", json={})
            ).status_code
            # the retired realtime-token dev seam is gone
            out["realtime_token"] = (
                await ac.get("/api/auth/realtime-token", headers=bearer_headers())
            ).status_code

            # --- Task 3: resolved_by comes from the verified user ---
            async with db.tenant_tx(DEMO_TENANT) as conn:
                q1 = await execute_tool(
                    conn, DEMO_TENANT, "send_sms", {"to": "+16195550123", "body": "hi"},
                )
            out["created_task_ids"].append(q1.data["task_id"])
            appr = await ac.post(
                f"/api/pending-actions/{q1.data['pending_action_id']}/approve",
                headers=bearer_headers(DEMO_TENANT, email="office@example.com"),
            )
            out["approve_code"] = appr.status_code
            out["approve_resolved_by"] = appr.json()["action"]["resolved_by"]

            async with db.tenant_tx(DEMO_TENANT) as conn:
                q2 = await execute_tool(
                    conn, DEMO_TENANT, "send_email",
                    {"to": "x@example.com", "subject": "s", "body": "b"},
                )
            out["created_task_ids"].append(q2.data["task_id"])
            rej = await ac.post(
                f"/api/pending-actions/{q2.data['pending_action_id']}/reject",
                json={"note": "no"},
                headers=bearer_headers(DEMO_TENANT, email="office@example.com"),
            )
            out["reject_resolved_by"] = rej.json()["action"]["resolved_by"]

        # cleanup (events are immutable; delete only the tasks/actions we seeded)
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for tid in out["created_task_ids"]:
                await conn.execute("delete from public.pending_actions where task_id=%s", (tid,))
                await conn.execute("delete from public.tasks where id=%s", (tid,))
        return out
    finally:
        await db.close_pool()


def test_auth_matrix_and_identity():
    out = asyncio.run(_scenario())

    assert out["no_header"] == 401
    assert out["garbage"] == 401
    assert out["expired"] == 401
    assert out["wrong_aud"] == 401
    assert out["no_tenant"] == 403  # valid token, no tenant claim

    assert out["demo_code"] == 200
    assert out["demo_sees_own"]
    assert out["probe_code"] == 200
    assert out["probe_blind"]  # RLS isolation through the verified claim

    assert out["healthz"] == 200
    assert out["webhook_unauth"] == 404  # machine path, not JWT-gated
    assert out["realtime_token"] == 404  # dev seam retired

    assert out["approve_code"] == 200
    assert out["approve_resolved_by"] == "office@example.com"
    assert out["reject_resolved_by"] == "office@example.com"
