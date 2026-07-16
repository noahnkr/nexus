"""Webhook ingress (Module 3b, Tasks 2 & 4).

Offline: the HMAC verify helper and the ingress guards that reject before any DB
work (unknown source 404, bad signature 401, malformed JSON 400). Gated
(NEXUS_APP_DB_URL): a signed POST through a throwaway in-test adapter produces the
receipt event + one row per normalized event and the right counts; an ack-only
payload writes only the receipt.
"""
import asyncio
import json

import httpx

import conftest
from app.config import settings
from app.services.connectors import (
    ConnectorAdapter,
    NormalizedEvent,
    NormalizedResult,
    register_adapter,
    sign,
)
from app.services.connectors.base import SIGNATURE_HEADER

SECRET = "test-webhook-secret"


def _signed_headers(body: bytes, secret: str = SECRET) -> dict:
    old = settings.nexus_webhook_secret
    settings.nexus_webhook_secret = secret
    try:
        signature = sign(body)
    finally:
        settings.nexus_webhook_secret = old
    return {"content-type": "application/json", SIGNATURE_HEADER: signature}


# --------------------------------------------------------------------------- #
# Offline — HMAC helper
# --------------------------------------------------------------------------- #


def test_hmac_verify(monkeypatch):
    monkeypatch.setattr(settings, "nexus_webhook_secret", SECRET)
    adapter = ConnectorAdapter()
    body = b'{"hello":"world"}'
    good = sign(body)

    assert adapter.verify({SIGNATURE_HEADER: good}, body) is True
    assert adapter.verify({SIGNATURE_HEADER: "deadbeef"}, body) is False
    assert adapter.verify({}, body) is False  # header absent

    # Empty secret ⇒ fail closed even with a (now-meaningless) signature.
    monkeypatch.setattr(settings, "nexus_webhook_secret", "")
    assert adapter.verify({SIGNATURE_HEADER: good}, body) is False


# --------------------------------------------------------------------------- #
# Offline — ingress guards (reject before touching the DB)
# --------------------------------------------------------------------------- #


def _offline_post(path: str, body: bytes, headers: dict):
    from app.main import app

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(path, content=body, headers=headers)

    return asyncio.run(go())


def test_unknown_source_404(monkeypatch):
    monkeypatch.setattr(settings, "nexus_webhook_secret", SECRET)
    body = b"{}"
    resp = _offline_post("/api/webhooks/does_not_exist", body, _signed_headers(body))
    assert resp.status_code == 404


def test_bad_signature_401(monkeypatch):
    monkeypatch.setattr(settings, "nexus_webhook_secret", SECRET)
    body = b'{"event":"lead.created"}'
    headers = {"content-type": "application/json", SIGNATURE_HEADER: "wrong"}
    resp = _offline_post("/api/webhooks/welcomehome", body, headers)
    assert resp.status_code == 401


def test_malformed_json_400(monkeypatch):
    monkeypatch.setattr(settings, "nexus_webhook_secret", SECRET)
    body = b"{not valid json"
    resp = _offline_post("/api/webhooks/welcomehome", body, _signed_headers(body))
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Gated — end-to-end through a throwaway adapter
# --------------------------------------------------------------------------- #


class _ThrowawayAdapter(ConnectorAdapter):
    source = "throwaway_test"
    category = "manual"

    async def normalize(self, payload: dict, headers) -> NormalizedResult:
        if payload.get("ack"):
            return NormalizedResult(ack_only=True)
        return NormalizedResult(events=[
            NormalizedEvent(
                event_type="test.reference",
                entity_type="lead",
                external_id=payload.get("external_id", "THROWAWAY-UNKNOWN"),
                summary="throwaway test reference event",
                detail=payload,
            )
        ])


def test_ingress_end_to_end():
    conftest._require("NEXUS_APP_DB_URL")

    async def scenario():
        from app import db
        from app.main import app

        settings.nexus_webhook_secret = SECRET
        settings.nexus_tenant_id = conftest.DEMO_TENANT
        register_adapter(_ThrowawayAdapter())
        await db.open_pool()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                # A reference event → one review task.
                body = json.dumps({"external_id": "THROWAWAY-REF-1"}).encode()
                r1 = await c.post(
                    "/api/webhooks/throwaway_test", content=body, headers=_signed_headers(body)
                )
                # An ack-only payload → only the receipt row.
                ack_body = json.dumps({"ack": True}).encode()
                r2 = await c.post(
                    "/api/webhooks/throwaway_test",
                    content=ack_body,
                    headers=_signed_headers(ack_body),
                )

            async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select count(*) from public.events "
                        "where source_system='throwaway_test' and event_type='webhook.received'"
                    )
                    receipts = (await cur.fetchone())[0]
                    await cur.execute(
                        "select count(*) from public.events "
                        "where source_system='throwaway_test' and event_type='test.reference'"
                    )
                    ref_events = (await cur.fetchone())[0]
                    await cur.execute(
                        "select title, description from public.tasks "
                        "where title like 'Review: throwaway%' order by created_at desc limit 1"
                    )
                    task = await cur.fetchone()
            return r1.json(), r1.status_code, r2.json(), receipts, ref_events, task
        finally:
            await db.close_pool()

    counts, status, ack, receipts, ref_events, task = asyncio.run(scenario())

    assert status == 200
    assert counts == {"received": 1, "matched": 0, "created": 0, "tasks": 1}
    assert ack == {"status": "ack"}
    assert receipts >= 2  # one per POST, including the ack
    assert ref_events >= 1
    assert task is not None
    # Plain-language task, no JSON payload leaked into user-facing fields.
    assert "{" not in task[0] and "{" not in (task[1] or "")
