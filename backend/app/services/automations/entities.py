"""VERTICAL SEAM — entity_type -> table map + raw-row lookup for `entity.*`.

A recipe's conditions and templates can reference `entity.<field>` — the canonical
row the run is about (the lead a webhook created, the client a schedule concerns).
Read *tools* return tool-shaped summaries; conditions need the raw row, so this
seam does a plain single-row select and `_jsonable`-coerces it.

This is the one file a new vertical replaces for entity lookups — it mirrors
`connectors/entity_writers.py` and `tools/entities.py`, and re-templates the same
way. Core engine code never references a vertical concept; it only calls
`get_entity`. An unknown `entity_type` yields an empty scope, so a condition on
`entity.*` then simply fails its `exists` check (never a crash).
"""
from __future__ import annotations

import uuid

from psycopg.rows import dict_row

from ..tools.core import _jsonable

# This instantiation (senior care). A new vertical swaps this map.
ENTITY_TABLES: dict[str, str] = {
    "lead": "leads",
    "client": "clients",
    "resource": "resources",
    "schedule": "schedules",
}


async def get_entity(conn, entity_type: str | None, entity_id: str | None) -> dict | None:
    """The raw canonical row (JSON-coerced) for `entity.*` scope, or None if the
    type is unknown, the id is missing/malformed, or no row matches."""
    if not entity_type or not entity_id:
        return None
    table = ENTITY_TABLES.get(entity_type)
    if table is None:
        return None
    try:
        eid = str(uuid.UUID(str(entity_id)))
    except (ValueError, AttributeError, TypeError):
        return None
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"select * from public.{table} where id = %s", (eid,))
        row = await cur.fetchone()
    return _jsonable(dict(row)) if row else None


async def entity_field_suggestions(conn) -> list[str]:
    """`entity.<col>` field paths for the builder's autocomplete (WS2) — the columns
    of this vertical's entity tables. Vertical (reads ENTITY_TABLES); core just
    concatenates it into the vocabulary. Plumbing columns are dropped."""
    skip = {"tenant_id"}
    cols: set[str] = set()
    async with conn.cursor() as cur:
        await cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = any(%s)",
            (list(ENTITY_TABLES.values()),),
        )
        for (name,) in await cur.fetchall():
            if name not in skip:
                cols.add(name)
    return sorted(f"entity.{c}" for c in cols)
