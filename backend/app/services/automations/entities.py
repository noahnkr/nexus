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
    "applicant": "applicants",
}

# Plain-language name for each entity type — vertical content the field catalog
# (Module 11) shows the office user ("The Lead", "The Applicant"). Core only
# humanizes column names; the human name of a *record* is business language.
ENTITY_LABELS: dict[str, str] = {
    "lead": "Lead",
    "client": "Client",
    "resource": "Caregiver",
    "schedule": "Visit",
    "applicant": "Applicant",
}


def humanize(name: str) -> str:
    """snake_case column/key -> sentence-case label ("hours_per_week" -> "Hours per
    week"). Core fallback labeling for the field catalog (Module 11); lives here so
    entity_catalog and the vocabulary builder share one implementation. The seam
    could override specific column labels later if a real need appears."""
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name


# --- Declared event knowledge (Module 11) -------------------------------------
# The field registry must be right for a tenant with NO event history: known event
# types DECLARE the entity a run on them is about and the payload fields their
# writers actually produce; the vocabulary UNIONS these with observed keys.
# Declared beats observed (curated labels, and stray test events can't mislabel a
# mapping). Paths are relative to `trigger.payload.` and may be nested — connector
# events carry the raw source body under `detail` (see resolution.py).
EVENT_ENTITY_TYPES: dict[str, str] = {
    "lead.created": "lead",
    "lead.updated": "lead",
    "lead.stage_changed": "lead",
    "lead.converted": "client",
    "client.created": "client",
    "client.updated": "client",
    "client.status_changed": "client",
    "schedule.created": "schedule",
    "schedule.assigned": "schedule",
    "schedule.called_out": "schedule",
    "schedule.cancelled": "schedule",
    "schedule.updated": "schedule",
    "schedule.no_show": "schedule",
    "schedule.checked_in": "schedule",
    "schedule.checked_out": "schedule",
    "resource.created": "resource",
    "resource.updated": "resource",
    "applicant.created": "applicant",
    "applicant.updated": "applicant",
    "applicant.stage_changed": "applicant",
    "tour.scheduled": "lead",
    "call.received": "lead",
    "call.completed": "lead",
    "sms.received": "lead",
    "email.received": "lead",
    "message.received": "lead",
    "calendar.event.updated": "schedule",
}

_STAGE_CHANGE_FIELDS: list[tuple[str, str]] = [
    ("from", "Previous stage"),
    ("to", "New stage"),
]

EVENT_PAYLOAD_FIELDS: dict[str, list[tuple[str, str]]] = {
    "lead.created": [
        ("detail.prospect.name", "Prospect name (CRM)"),
        ("detail.prospect.phone", "Prospect phone (CRM)"),
        ("detail.prospect.email", "Prospect email (CRM)"),
        ("detail.prospect.source", "Lead source (CRM)"),
    ],
    "lead.updated": [("fields", "Changed fields")],
    "lead.stage_changed": _STAGE_CHANGE_FIELDS,
    "applicant.stage_changed": _STAGE_CHANGE_FIELDS,
    "schedule.called_out": [("replacement_schedule_id", "Replacement shift id")],
    "client.status_changed": [("from", "Previous status"), ("to", "New status")],
    "schedule.checked_out": [("actual_hours", "Actual hours worked")],
    "sms.received": [
        ("detail.message.from", "Sender number"),
        ("detail.message.text", "Message text"),
    ],
    "call.received": [("detail.call.from", "Caller number")],
    "call.completed": [
        ("detail.call.from", "Caller number"),
        ("detail.call.durationSeconds", "Call length (seconds)"),
    ],
    "email.received": [
        ("detail.message.from", "Sender address"),
        ("detail.message.subject", "Subject line"),
    ],
    "calendar.event.updated": [("detail.summary", "Calendar event title")],
}


async def _entity_columns(conn) -> dict[str, list[str]]:
    """{entity_type: [column, …]} for this vertical's entity tables, in table
    definition order. Skips `tenant_id` (plumbing); keeps `id` (it feeds tool inputs
    like `lead_id`). The single column-query path so the field catalog and the flat
    suggestion list can never drift."""
    type_by_table = {table: etype for etype, table in ENTITY_TABLES.items()}
    cols: dict[str, list[str]] = {etype: [] for etype in ENTITY_TABLES}
    async with conn.cursor() as cur:
        await cur.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = any(%s) "
            "order by table_name, ordinal_position",
            (list(ENTITY_TABLES.values()),),
        )
        for table, column in await cur.fetchall():
            if column == "tenant_id":
                continue
            etype = type_by_table.get(table)
            if etype is not None:
                cols[etype].append(column)
    return cols


async def entity_catalog(conn) -> dict:
    """Per-entity field catalog for the builder (Module 11): `{type: {label,
    fields:[{path,label}]}}`. Entity names from ENTITY_LABELS (vertical); column
    labels humanized (core). 11b filters to the run's entity type so a lead.created
    session never sees applicant columns."""
    columns = await _entity_columns(conn)
    return {
        etype: {
            "label": ENTITY_LABELS.get(etype, humanize(etype)),
            "fields": [{"path": f"entity.{c}", "label": humanize(c)} for c in cols],
        }
        for etype, cols in columns.items()
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
    """`entity.<col>` field paths for the builder's autocomplete (WS2) — derived from
    the same column query as `entity_catalog` (one path, no drift). Plumbing columns
    are dropped."""
    columns = await _entity_columns(conn)
    paths = {f"entity.{c}" for cols in columns.values() for c in cols}
    return sorted(paths)
