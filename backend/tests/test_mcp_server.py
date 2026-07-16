"""MCP server tests (Module 3a).

Offline tests drive the mounted MCP ASGI app over httpx's ASGITransport with no
DB: the bearer gate (401), the JSON-RPC `initialize` handshake, and `tools/list`
mirroring the registry. Gated tests (NEXUS_APP_DB_URL) prove a real `tools/call`
runs through `execute_tool` against seed data and writes a `source_system='mcp'`
audit row.

The module-level `session_manager` in `app.services.mcp_server` is consumed by
the app lifespan in production and its `run()` is once-per-instance, so each test
builds a FRESH session manager around the shared low-level `server` (only the
manager is single-use; the server and its registered handlers are reusable).
"""
import asyncio
import json

import httpx
from starlette.responses import PlainTextResponse

import conftest
from app.config import settings
from app.services import mcp_server
from app.services.tools.registry import all_tools
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

TOKEN = "test-mcp-token"
BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
AUTH_HEADERS = {**BASE_HEADERS, "Authorization": f"Bearer {TOKEN}"}


def _fresh_app():
    """A test ASGI app: the real bearer gate in front of a fresh (single-use)
    session manager bound to the shared server."""
    manager = StreamableHTTPSessionManager(
        app=mcp_server.server, stateless=True, json_response=True
    )

    async def app(scope, receive, send):
        if scope["type"] != "http":
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        if not mcp_server._authorized(scope):
            await PlainTextResponse("Unauthorized", status_code=401)(
                scope, receive, send
            )
            return
        await manager.handle_request(scope, receive, send)

    return manager, app


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://mcp-test"
    )


async def _rpc(client, method, params, req_id, headers):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    resp = await client.post("/", json=body, headers=headers)
    return resp


async def _initialize(client, headers=AUTH_HEADERS):
    resp = await _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
        1,
        headers,
    )
    pv = resp.headers.get("mcp-protocol-version", "2025-06-18")
    return resp, {**headers, "MCP-Protocol-Version": pv}


# --------------------------------------------------------------------------- #
# Offline (Task 1) — no DB required
# --------------------------------------------------------------------------- #


def test_missing_token_401(monkeypatch):
    monkeypatch.setattr(settings, "nexus_mcp_token", TOKEN)

    async def go():
        _, app = _fresh_app()
        async with _client(app) as client:
            resp = await _rpc(client, "tools/list", {}, 1, BASE_HEADERS)  # no auth
            return resp

    resp = asyncio.run(go())
    assert resp.status_code == 401


def test_wrong_token_401(monkeypatch):
    monkeypatch.setattr(settings, "nexus_mcp_token", TOKEN)

    async def go():
        _, app = _fresh_app()
        headers = {**BASE_HEADERS, "Authorization": "Bearer not-the-token"}
        async with _client(app) as client:
            return await _rpc(client, "tools/list", {}, 1, headers)

    assert asyncio.run(go()).status_code == 401


def test_unset_token_fails_closed(monkeypatch):
    """With no configured token, even a Bearer-carrying request is rejected."""
    monkeypatch.setattr(settings, "nexus_mcp_token", "")

    async def go():
        _, app = _fresh_app()
        async with _client(app) as client:
            return await _rpc(client, "tools/list", {}, 1, AUTH_HEADERS)

    assert asyncio.run(go()).status_code == 401


def test_initialize_handshake(monkeypatch):
    monkeypatch.setattr(settings, "nexus_mcp_token", TOKEN)

    async def go():
        manager, app = _fresh_app()
        async with manager.run(), _client(app) as client:
            resp, _ = await _initialize(client)
            return resp.status_code, resp.json()

    status, body = asyncio.run(go())
    assert status == 200
    assert body["result"]["serverInfo"]["name"] == "nexus"
    assert "tools" in body["result"]["capabilities"]


def test_tools_list_mirrors_registry(monkeypatch):
    monkeypatch.setattr(settings, "nexus_mcp_token", TOKEN)

    async def go():
        manager, app = _fresh_app()
        async with manager.run(), _client(app) as client:
            _, headers = await _initialize(client)
            resp = await _rpc(client, "tools/list", {}, 2, headers)
            return resp.json()

    body = asyncio.run(go())
    tools = body["result"]["tools"]
    got = {t["name"] for t in tools}
    expected = {t.name for t in all_tools()}
    assert got == expected
    assert expected  # registry is non-empty
    for t in tools:
        assert isinstance(t.get("inputSchema"), dict)
        assert "cache_control" not in t  # MCP shape carries no Anthropic caching hint


# --------------------------------------------------------------------------- #
# Gated (Task 2) — real DB, seed data, audit trail
# --------------------------------------------------------------------------- #


def _tool_result(body):
    """Unwrap a JSON-RPC tools/call response into (payload_dict, is_error)."""
    result = body["result"]
    text = result["content"][0]["text"]
    return json.loads(text), result.get("isError", False)


def test_tools_call_list_leads_and_audit(monkeypatch):
    conftest._require("NEXUS_APP_DB_URL")
    monkeypatch.setattr(settings, "nexus_mcp_token", TOKEN)
    monkeypatch.setattr(settings, "nexus_app_db_url", conftest.NEXUS_APP_DB_URL)
    monkeypatch.setattr(settings, "nexus_tenant_id", conftest.DEMO_TENANT)

    from app.db import close_pool, open_pool, tenant_tx

    async def go():
        await open_pool()
        try:
            manager, app = _fresh_app()
            async with manager.run(), _client(app) as client:
                _, headers = await _initialize(client)
                resp = await _rpc(
                    client,
                    "tools/call",
                    {"name": "list_leads", "arguments": {"status": "new"}},
                    2,
                    headers,
                )
                payload, is_error = _tool_result(resp.json())

                # The most recent tool.called audit row for this call.
                async with tenant_tx(conftest.DEMO_TENANT) as conn:
                    row = await (
                        await conn.execute(
                            "select source_system, payload->>'tool_name' "
                            "from events where event_type='tool.called' "
                            "and payload->>'tool_name'='list_leads' "
                            "order by created_at desc limit 1"
                        )
                    ).fetchone()
                return payload, is_error, row
        finally:
            await close_pool()

    payload, is_error, row = asyncio.run(go())
    assert is_error is False
    blob = json.dumps(payload)
    assert "Margaret Ellison" in blob
    assert row is not None
    assert row[0] == "mcp"
    assert row[1] == "list_leads"


def test_tools_call_unknown_tool_is_error(monkeypatch):
    conftest._require("NEXUS_APP_DB_URL")
    monkeypatch.setattr(settings, "nexus_mcp_token", TOKEN)
    monkeypatch.setattr(settings, "nexus_app_db_url", conftest.NEXUS_APP_DB_URL)
    monkeypatch.setattr(settings, "nexus_tenant_id", conftest.DEMO_TENANT)

    from app.db import close_pool, open_pool

    async def go():
        await open_pool()
        try:
            manager, app = _fresh_app()
            async with manager.run(), _client(app) as client:
                _, headers = await _initialize(client)
                resp = await _rpc(
                    client,
                    "tools/call",
                    {"name": "no_such_tool", "arguments": {}},
                    2,
                    headers,
                )
                return _tool_result(resp.json())
        finally:
            await close_pool()

    payload, is_error = asyncio.run(go())
    assert is_error is True
    assert "error" in payload["data"]
