"""VERTICAL SEAM — auto-create writers for inbound events that stand up a new
canonical entity.

Sibling to `tools/entities.py` and the entity migration: a new vertical replaces
this file alongside those. Core resolution (resolution.py) never references a
concrete entity type — it looks the type up in `WRITERS` and falls back to a
review task when there's no writer, so an unknown `creates_entity` type is never
a 500.

This instantiation ships `lead` only (the one lifecycle event a placeholder
connector auto-creates). Writers take the already-tenant-scoped connection and
insert with `tenant_id` supplied explicitly, exactly like the other services —
RLS still checks it against the GUC.
"""
from __future__ import annotations

import uuid
from typing import Awaitable, Callable

from psycopg.rows import dict_row

LEAD_STATUSES = ("new", "contacted", "qualified", "converted", "lost")


def _clean(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _maybe_uuid(value) -> str | None:
    v = _clean(value)
    if v is None:
        return None
    try:
        return str(uuid.UUID(v))
    except (ValueError, AttributeError, TypeError):
        return None


async def write_lead(conn, tenant_id: str, attributes: dict) -> str:
    """Insert a lead from canonical attributes. `name` is required; contact,
    source, status and region are optional with safe defaults (status → 'new',
    an unrecognised status is coerced to 'new'; region omitted → null)."""
    name = _clean(attributes.get("name"))
    if name is None:
        raise ValueError("lead requires a name")

    status = _clean(attributes.get("status")) or "new"
    if status not in LEAD_STATUSES:
        status = "new"

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.leads
                 (tenant_id, name, phone, email, source, status, region_id)
               values (%s, %s, %s, %s, %s, %s, %s)
               returning id""",
            (
                tenant_id,
                name,
                _clean(attributes.get("phone")),
                _clean(attributes.get("email")),
                _clean(attributes.get("source")),
                status,
                _maybe_uuid(attributes.get("region_id")),
            ),
        )
        row = await cur.fetchone()
    return str(row["id"])


# entity_type -> auto-create writer. A type absent here that arrives with
# creates_entity=True falls back to the task outcome in resolution.py.
WRITERS: dict[str, Callable[[object, str, dict], Awaitable[str]]] = {
    "lead": write_lead,
}
