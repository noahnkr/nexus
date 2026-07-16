"""events: the immutable audit trail. Every tool call, webhook, and gated-action
resolution writes a row here (CLAUDE.md). This module is the single writer helper.

The insert runs on a tenant-scoped connection; RLS enforces that tenant_id matches
app.current_tenant_id(). tenant_id is passed explicitly so the value in the row is
never ambiguous.
"""
from __future__ import annotations

from psycopg.types.json import Json


async def log_event(
    conn,
    *,
    tenant_id: str,
    source_system: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
) -> None:
    await conn.execute(
        """insert into public.events
             (tenant_id, source_system, event_type, entity_type, entity_id, payload)
           values (%s, %s, %s, %s, %s, %s)""",
        (tenant_id, source_system, event_type, entity_type, entity_id, Json(payload or {})),
    )
