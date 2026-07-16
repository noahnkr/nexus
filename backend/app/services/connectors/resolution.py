"""Entity resolution — the core, business-agnostic rule that every inbound
connector event resolves to a canonical entity via `external_ids` before
anything else is written (CLAUDE.md).

`route_normalized_event` has exactly three outcomes:
  * matched  — the external id is already mapped; bump last_synced_at.
  * created  — a `creates_entity` event with a registered writer; insert the
               canonical row + the external_ids mapping.
  * task     — anything unresolvable (a reference to an unknown entity, or a
               creates_entity type with no writer): a plain-language review task
               linked to the webhook receipt. No business-table write.

Every outcome writes one `events` row (source_system = connector name), linked to
the resolved/created entity when there is one.
"""
from __future__ import annotations

from dataclasses import dataclass

from psycopg.rows import dict_row

from ..events import log_event
from .entity_writers import WRITERS


@dataclass
class RouteOutcome:
    resolution: str  # "matched" | "created" | "task"
    event_id: str
    entity_id: str | None = None
    task_id: str | None = None


async def _find_mapping(conn, category: str, entity_type: str, external_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, entity_id from public.external_ids
                where source_system = %s and entity_type = %s and external_id = %s
                limit 1""",
            (category, entity_type, external_id),
        )
        return await cur.fetchone()


async def route_normalized_event(
    conn, tenant_id: str, adapter, ev, originating_event_id: str
) -> RouteOutcome:
    category = adapter.category

    mapping = await _find_mapping(conn, category, ev.entity_type, ev.external_id)
    if mapping is not None:
        entity_id = str(mapping["entity_id"])
        await conn.execute(
            "update public.external_ids set last_synced_at = now() where id = %s",
            (mapping["id"],),
        )
        event_id = await _log(conn, tenant_id, adapter, ev, "matched", entity_id)
        return RouteOutcome("matched", event_id, entity_id=entity_id)

    writer = WRITERS.get(ev.entity_type) if ev.creates_entity else None
    if writer is not None:
        entity_id = await writer(conn, tenant_id, ev.attributes)
        await conn.execute(
            """insert into public.external_ids
                 (tenant_id, entity_type, entity_id, source_system, external_id)
               values (%s, %s, %s, %s, %s)""",
            (tenant_id, ev.entity_type, entity_id, category, ev.external_id),
        )
        event_id = await _log(conn, tenant_id, adapter, ev, "created", entity_id)
        return RouteOutcome("created", event_id, entity_id=entity_id)

    # Unresolvable (reference to an unknown entity, or creates_entity with no
    # writer): a plain-language review task linked to the receipt. No business write.
    task_id = await _create_review_task(conn, tenant_id, ev, originating_event_id)
    event_id = await _log(conn, tenant_id, adapter, ev, "task", None)
    return RouteOutcome("task", event_id, task_id=task_id)


async def _create_review_task(conn, tenant_id: str, ev, originating_event_id: str) -> str:
    title = f"Review: {ev.summary}"
    description = (
        f"An inbound {ev.event_type} event couldn't be matched to a known "
        f"{ev.entity_type}. Please review and link or create the record."
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.tasks
                 (tenant_id, title, description, priority, originating_event_id)
               values (%s, %s, %s, 'normal', %s)
               returning id""",
            (tenant_id, title, description, originating_event_id),
        )
        row = await cur.fetchone()
    return str(row["id"])


async def _log(conn, tenant_id: str, adapter, ev, resolution: str, entity_id: str | None) -> str:
    return await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=adapter.source,
        event_type=ev.event_type,
        entity_type=ev.entity_type if entity_id else None,
        entity_id=entity_id,
        payload={
            "summary": ev.summary,
            "external_id": ev.external_id,
            "resolution": resolution,
            "detail": ev.detail,
        },
    )
