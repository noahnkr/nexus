"""MCP server: exposes the tool registry to external MCP clients over HTTP.

Streamable HTTP transport mounted at `/mcp` inside the existing FastAPI app
(Module 3a). It adds no tool logic of its own — `list_tools` mirrors the registry
and `call_tool` dispatches every call through the same `execute_tool` seam chat
uses, tagging the audit row `source_system='mcp'`. New tools registered in later
modules appear over MCP with zero MCP-side changes.

Three pieces:
  * a low-level `mcp.server.lowlevel.Server` bound to the registry,
  * a stateless JSON `StreamableHTTPSessionManager` (entered in the app lifespan),
  * a bearer-token ASGI wrapper on the mount only — fail closed when the token is
    unset, and a 401 is a plain HTTP response that never reaches the MCP layer.
"""
from __future__ import annotations

import hmac
import json

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import PlainTextResponse

from ..config import settings
from ..db import tenant_tx
from ..deps import get_tenant_id
from ..llm import traceable

# Importing the tools package runs the registry bootstrap (register() side
# effects) so list_tools has the full set on the first request.
from . import tools  # noqa: F401
from .tools import execute_tool
from .tools.registry import all_tools

server: Server = Server(name="nexus", version="0.1.0")


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    """Mirror the registry. No `cache_control` here — that's an Anthropic-API
    concern, not part of the MCP tool shape."""
    return [
        types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
        for t in all_tools()
    ]


@traceable(run_type="chain", name="mcp_call")
async def _dispatch(name: str, arguments: dict) -> types.CallToolResult:
    """Run one tool through the shared execution seam on a tenant-scoped tx.

    Mirrors the chat contract: never raises for a tool problem — `execute_tool`
    resolves every outcome to a ToolResult (and writes the `events` audit row),
    which we surface as a CallToolResult with `isError` set accordingly.
    """
    tenant_id = get_tenant_id()
    async with tenant_tx(tenant_id) as conn:
        result = await execute_tool(
            conn, tenant_id, name, arguments, source_system="mcp"
        )
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(
                    {"summary": result.summary, "data": result.data}, default=str
                ),
            )
        ],
        isError=result.is_error,
    )


@server.call_tool()
async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
    return await _dispatch(name, arguments)


# Stateless + JSON: tool calls are plain request/response (no server push needed),
# which keeps HTTP clients (Claude Code now, n8n in M7) and tests simple.
session_manager = StreamableHTTPSessionManager(
    app=server, stateless=True, json_response=True
)


def _authorized(scope) -> bool:
    """Constant-time bearer check against the configured token. Fail closed: an
    unset token rejects every request."""
    token = settings.nexus_mcp_token
    if not token:
        return False
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            expected = f"Bearer {token}".encode()
            return hmac.compare_digest(value, expected)
    return False


def build_mcp_asgi_app():
    """The ASGI handler mounted at `/mcp`: bearer-token gate in front of the
    session manager. A rejected request gets a plain 401 and never reaches MCP."""

    async def app(scope, receive, send) -> None:
        if scope["type"] != "http":
            # Streamable HTTP is HTTP-only; reject anything else (e.g. websocket).
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return
        if not _authorized(scope):
            response = PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return
        await session_manager.handle_request(scope, receive, send)

    return app
