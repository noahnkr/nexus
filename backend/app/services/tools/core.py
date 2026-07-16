"""Tool execution core: the ToolDef/ToolResult contract and the single
`execute_tool` seam.

`execute_tool` is the ONLY way a tool runs — chat now, the MCP server in Module
3, n8n nodes in Module 7 all go through it. It:

  * refuses unsafe (state-changing) tools until Module 5's approval gate exists,
  * runs the handler inside a savepoint so a handler error can't poison the
    outer transaction that still has to write the audit row,
  * catches handler errors into a structured `is_error` result, and
  * ALWAYS writes exactly one immutable `events` row per call (CLAUDE.md audit
    rule).

The plain-language `summary` is what reaches user-facing surfaces and the audit
line; raw args/data stay in the `events` payload and LangSmith only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

from ...llm import traceable
from ..events import log_event
from .registry import get_tool


class ToolInputError(Exception):
    """Raised by a handler when the model supplied invalid arguments. Its message
    is surfaced as the tool's plain-language summary (a clean refusal, not a
    stack trace)."""


@dataclass
class ToolResult:
    summary: str  # plain-language, user-facing + audit line
    data: Any  # JSON-serializable; becomes the tool_result block the model reads
    is_error: bool = False


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[Any, dict], Awaitable[ToolResult]]
    safe: bool = True


GATED_MESSAGE = (
    "This action needs human approval and can't run yet — the approval gate "
    "arrives in Module 5."
)


def _jsonable(value: Any) -> Any:
    """Coerce psycopg row values (uuid, datetime, Decimal, arrays, jsonb) into
    JSON-serializable form. Shared by every tool that returns DB rows."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


async def _audit(
    conn, tenant_id: str, source_system: str, name: str, args: Any, result: ToolResult
) -> ToolResult:
    payload: dict = {"tool_name": name, "summary": result.summary, "input": args}
    if result.is_error and isinstance(result.data, dict) and "error" in result.data:
        payload["error"] = result.data["error"]
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="tool.called",
        payload=payload,
    )
    return result


@traceable(run_type="tool")
async def execute_tool(
    conn, tenant_id: str, name: str, args: Any, *, source_system: str = "chat"
) -> ToolResult:
    """Validate, gate, run, and audit a single tool call. Never raises for a tool
    problem — every outcome (unknown tool, bad args, gated, handler error,
    success) resolves to a ToolResult and writes one `events` row."""
    tool = get_tool(name)
    if tool is None:
        return await _audit(
            conn,
            tenant_id,
            source_system,
            name,
            args if isinstance(args, dict) else {},
            ToolResult(f"Unknown tool '{name}'.", {"error": f"unknown tool: {name}"}, True),
        )
    if not isinstance(args, dict):
        return await _audit(
            conn,
            tenant_id,
            source_system,
            name,
            {},
            ToolResult(
                f"'{name}' received invalid arguments.",
                {"error": "arguments must be a JSON object"},
                True,
            ),
        )
    if not tool.safe:
        return await _audit(
            conn,
            tenant_id,
            source_system,
            name,
            args,
            ToolResult(
                GATED_MESSAGE,
                {"error": "gated tool refused: no approval gate until Module 5"},
                True,
            ),
        )
    try:
        # Savepoint: a handler SQL error rolls back to here, leaving the outer
        # transaction alive to write the audit row below.
        async with conn.transaction():
            result = await tool.handler(conn, args)
    except ToolInputError as exc:
        result = ToolResult(str(exc), {"error": str(exc)}, True)
    except Exception as exc:  # noqa: BLE001 — any handler failure becomes an audited error
        result = ToolResult(
            f"'{name}' could not be completed.", {"error": str(exc)}, True
        )
    return await _audit(conn, tenant_id, source_system, name, args, result)
