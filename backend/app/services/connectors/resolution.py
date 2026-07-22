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
from .entity_writers import UPDATERS, WRITERS, resolve_by_email, resolve_by_phone

# `resolve_by` value -> the vertical seam hook that answers "whose is this?".
# Both domains share one code path because they share one problem: the identifier
# does not carry an entity type, so the match has to decide it.
_CONTACT_DOMAINS = {
    "phone": resolve_by_phone,
    "email": resolve_by_email,
}


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


async def _find_mapping_any_type(conn, category: str, external_id: str) -> dict | None:
    """A mapping lookup with NO entity-type filter — the phone-domain case.

    A phone number does not announce whose it is, so the stored mapping's
    `entity_type` is the answer rather than a filter. Deliberately a separate
    function from `_find_mapping`: the id-domain lookup must keep its type scope,
    since two systems can legitimately use the same record id for different
    kinds of thing.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, entity_id, entity_type from public.external_ids
                where source_system = %s and external_id = %s
                order by last_synced_at desc nulls last
                limit 1""",
            (category, external_id),
        )
        return await cur.fetchone()


async def _resolve_contact(conn, tenant_id: str, adapter, ev, originating_event_id: str):
    """The contact-domain path (`resolve_by` of `"phone"` or `"email"`).

    Three outcomes, and the middle one is the interesting one:
      * already mapped   → behave exactly like the id path (handled by the caller);
      * exactly one match → register the mapping so every later event from this
        number or address short-circuits, then proceed as `matched`;
      * none, or several  → a review task. Ambiguity NAMES the candidates, because
        "this belongs to two people, here they are" is something an office user
        can resolve in seconds, whereas a silent guess is a wrong record that
        nobody ever notices.

    Returns `("matched", entity_id, entity_type)`, or `("task", task_id, None)`
    when the identifier was ambiguous or unknown. The task is written HERE, once,
    so an ambiguous identifier can carry its candidate list — the caller must not
    write a second one.
    """
    lookup = _CONTACT_DOMAINS[ev.resolve_by]
    noun = "number" if ev.resolve_by == "phone" else "address"
    matches = await lookup(conn, tenant_id, ev.external_id)

    if len(matches) == 1:
        match = matches[0]
        await conn.execute(
            """insert into public.external_ids
                 (tenant_id, entity_type, entity_id, source_system, external_id)
               values (%s, %s, %s, %s, %s)
               on conflict (tenant_id, source_system, external_id) do nothing""",
            (tenant_id, match["entity_type"], match["entity_id"],
             adapter.category, ev.external_id),
        )
        return "matched", match["entity_id"], match["entity_type"]

    if len(matches) > 1:
        who = "; ".join(f"{m['name']} ({m['via']})" for m in matches)
        task_id = await _create_review_task(
            conn, tenant_id, ev, originating_event_id,
            title=f"Review: {ev.summary} — shared {noun}",
            description=(
                f"{ev.external_id} belongs to more than one record, so this "
                f"{ev.event_type} was not attached to anyone: {who}. "
                f"Open the right record and confirm whose {noun} this is."
            ),
        )
        return "task", task_id, None

    return "task", await _create_review_task(conn, tenant_id, ev, originating_event_id), None


async def route_normalized_event(
    conn, tenant_id: str, adapter, ev, originating_event_id: str
) -> RouteOutcome:
    category = adapter.category
    # Contact-domain events (a phone number, an email address) carry no entity
    # type — the match decides it — so their lookup is untyped and falls through
    # to the vertical seam. Every other event resolves by the source's record id.
    by_contact = getattr(ev, "resolve_by", "id") in _CONTACT_DOMAINS

    if by_contact:
        mapping = await _find_mapping_any_type(conn, category, ev.external_id)
        if mapping is None:
            outcome, value, entity_type = await _resolve_contact(
                conn, tenant_id, adapter, ev, originating_event_id
            )
            if outcome == "matched":
                # The seam decided the type; the adapter's was only a fallback.
                ev.entity_type = str(entity_type)
                event_id = await _log(conn, tenant_id, adapter, ev, "matched", value)
                return RouteOutcome("matched", event_id, entity_id=value)
            event_id = await _log(conn, tenant_id, adapter, ev, "task", None)
            return RouteOutcome("task", event_id, task_id=value)
        ev.entity_type = str(mapping["entity_type"])
    else:
        mapping = await _find_mapping(conn, category, ev.entity_type, ev.external_id)
    if mapping is not None:
        entity_id = str(mapping["entity_id"])
        await conn.execute(
            "update public.external_ids set last_synced_at = now() where id = %s",
            (mapping["id"],),
        )
        # A polled source re-sends the whole record; apply the changes before
        # logging so the event and the row it describes agree. An entity type with
        # no registered updater keeps the original log-only behavior.
        updater = UPDATERS.get(ev.entity_type) if ev.updates_entity else None
        if updater is not None:
            await updater(conn, tenant_id, entity_id, ev.attributes, adapter.source)
        event_id = await _log(conn, tenant_id, adapter, ev, "matched", entity_id)
        return RouteOutcome("matched", event_id, entity_id=entity_id)

    writer = WRITERS.get(ev.entity_type) if ev.creates_entity else None
    if writer is not None:
        entity_id = await writer(conn, tenant_id, ev.attributes, adapter.source)
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


async def _create_review_task(
    conn,
    tenant_id: str,
    ev,
    originating_event_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """The unresolvable-event review task.

    `title`/`description` are overridable so a caller with more to say can say it
    — the ambiguous-phone case names the candidate records — without forking a
    second task-creation path.
    """
    title = title or f"Review: {ev.summary}"
    description = description or (
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
