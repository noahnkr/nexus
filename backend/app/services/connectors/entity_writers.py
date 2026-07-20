"""VERTICAL SEAM — the writers inbound connector events use to stand up and
maintain canonical entities.

Sibling to `tools/entities.py`, `views/*.py`, and the entity migration: a new
vertical replaces this file alongside those. Core resolution (resolution.py)
never references a concrete entity type — it looks the type up in `WRITERS` /
`UPDATERS` and falls back to a review task (create) or log-only (update) when
there's no writer, so an unknown entity type is never a 500.

Two registries, both keyed by entity type:

  * `WRITERS`  — `creates_entity` events with no existing mapping. Insert the row.
  * `UPDATERS` — `updates_entity` events whose external id ALREADY maps. Patch the
    row from the non-null attributes. This exists because polled sources re-send
    the whole record on every sweep: "already known" is the normal case, and
    without an update path a CRM edit would be recorded and then thrown away.

Both take the already-tenant-scoped connection and insert with `tenant_id`
supplied explicitly, exactly like the other services — RLS still checks it
against the GUC.

PATCH SEMANTICS (the rule the whole update path rests on): only attributes that
arrive NON-NULL are written. A field the source didn't send is a field the source
has no opinion about, not an instruction to blank ours. This is what keeps the
Module 16 referral contract intact — `leads.source` is the exact-match key that
joins a lead to its referral partner, and a sync that nulled it on an unrelated
edit would silently break every conversion metric.
"""
from __future__ import annotations

import uuid
from typing import Awaitable, Callable

from psycopg.rows import dict_row

from ..events import log_event
from ..views.leads import StageChangeError, change_stage

LEAD_STATUSES = ("new", "contacted", "qualified", "converted", "lost")

# Lead columns a connector may patch. Deliberately excludes `status` (that goes
# through views/leads.change_stage, the single stage-writer) and `region_id`
# (territory is an internal assignment, not something a CRM knows).
LEAD_PATCH_FIELDS = ("name", "phone", "email", "source", "address", "zip", "background")

# Client columns a connector may patch. Contact + location only: status, payer,
# and authorized hours are the M15 seam's business, and a sales CRM has no
# authority over a client's care lifecycle.
CLIENT_PATCH_FIELDS = ("name", "phone", "email", "address", "zip")


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


def _patch(attributes: dict, fields) -> dict:
    """The non-null subset of `attributes` restricted to `fields` (see PATCH
    SEMANTICS above)."""
    out = {}
    for field in fields:
        value = _clean(attributes.get(field))
        if value is not None:
            out[field] = value
    return out


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------
async def write_lead(conn, tenant_id: str, attributes: dict, source_system: str) -> str:
    """Insert a lead from canonical attributes. `name` is required; contact,
    source, status, location and region are optional with safe defaults (status →
    'new', an unrecognised status is coerced to 'new'; region omitted → null).

    A lead that arrives ALREADY converted — the normal case when backfilling a CRM
    whose prospect has long since started care — is promoted to a client here, the
    same as if it had crossed the stage line while we were watching."""
    name = _clean(attributes.get("name"))
    if name is None:
        raise ValueError("lead requires a name")

    status = _clean(attributes.get("status")) or "new"
    if status not in LEAD_STATUSES:
        status = "new"

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.leads
                 (tenant_id, name, phone, email, source, status, region_id,
                  address, zip, background)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               returning id""",
            (
                tenant_id,
                name,
                _clean(attributes.get("phone")),
                _clean(attributes.get("email")),
                _clean(attributes.get("source")),
                status,
                _maybe_uuid(attributes.get("region_id")),
                _clean(attributes.get("address")),
                _clean(attributes.get("zip")),
                _clean(attributes.get("background")),
            ),
        )
        row = await cur.fetchone()
    lead_id = str(row["id"])

    await _sync_lead_contacts(conn, tenant_id, lead_id, attributes, source_system)
    if status == "converted":
        await _promote_to_client(conn, tenant_id, lead_id, attributes, source_system)
    return lead_id


async def update_lead(
    conn, tenant_id: str, lead_id: str, attributes: dict, source_system: str
) -> None:
    """Patch an existing lead from a re-synced source record.

    Order matters: field changes land BEFORE the stage move, so a stage event that
    quotes the lead's name quotes the current one. The stage move itself goes
    through `views/leads.change_stage` — the single writer — so a CRM-driven move
    fires the same sequences and lands the same timeline event as a coordinator
    dragging the card."""
    patch = _patch(attributes, LEAD_PATCH_FIELDS)
    if patch:
        set_parts = ", ".join(f"{field} = %s" for field in patch)
        await conn.execute(
            f"update public.leads set {set_parts} where id = %s",
            (*patch.values(), lead_id),
        )
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system=source_system,
            event_type="lead.updated",
            entity_type="lead",
            entity_id=lead_id,
            payload={
                "summary": (
                    f"Lead '{patch.get('name', '')}' updated from "
                    f"{source_system} ({', '.join(sorted(patch))})"
                ).replace("Lead '' ", "Lead "),
                "fields": sorted(patch),
            },
        )

    await _sync_lead_contacts(conn, tenant_id, lead_id, attributes, source_system)

    status = _clean(attributes.get("status"))
    if status is None:
        return
    if status not in LEAD_STATUSES:
        # An unmapped source stage (a new stage the office invented after this
        # shipped). Leave the status alone and say so — never crash, never guess.
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system=source_system,
            event_type="connector.sync_failed",
            entity_type="lead",
            entity_id=lead_id,
            payload={
                "summary": (
                    f"Could not map a {source_system} stage for this lead — "
                    "its status was left unchanged"
                ),
                "detail": {"unmapped_status": status},
            },
        )
        return

    try:
        result = await change_stage(conn, tenant_id, source_system, lead_id, status)
    except StageChangeError:
        return
    if result["changed"] and status == "converted":
        await _promote_to_client(conn, tenant_id, lead_id, attributes, source_system)


# ---------------------------------------------------------------------------
# lead contacts (family / decision-makers behind the inquiry)
# ---------------------------------------------------------------------------
async def _sync_lead_contacts(
    conn, tenant_id: str, lead_id: str, attributes: dict, source_system: str
) -> None:
    """Upsert `attributes["contacts"]` against `lead_contacts`, keyed by each
    contact's own external id in `external_ids`.

    Keyed by external id rather than by name because people get renamed (a
    marriage, a typo fixed) and matching on name would quietly fork the row. No
    deletes: a contact removed upstream stays, because the office may have added
    notes to it and losing those to an upstream tidy-up is worse than keeping a
    stale row."""
    contacts = attributes.get("contacts")
    if not isinstance(contacts, list):
        return

    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        name = _clean(contact.get("name"))
        external_id = _clean(contact.get("external_id"))
        if name is None or external_id is None:
            continue

        fields = {
            "name": name,
            "relationship": _clean(contact.get("relationship")),
            "phone": _clean(contact.get("phone")),
            "email": _clean(contact.get("email")),
            "is_primary": bool(contact.get("is_primary")),
            "source": source_system,
        }

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select entity_id from public.external_ids "
                "where entity_type = 'lead_contact' and external_id = %s limit 1",
                (external_id,),
            )
            mapping = await cur.fetchone()

        if mapping is not None:
            set_parts = ", ".join(f"{f} = %s" for f in fields)
            await conn.execute(
                f"update public.lead_contacts set {set_parts} where id = %s",
                (*fields.values(), mapping["entity_id"]),
            )
            continue

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """insert into public.lead_contacts
                     (tenant_id, lead_id, name, relationship, phone, email,
                      is_primary, source)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)
                   returning id""",
                (tenant_id, lead_id, *fields.values()),
            )
            new_id = (await cur.fetchone())["id"]
        await conn.execute(
            """insert into public.external_ids
                 (tenant_id, entity_type, entity_id, source_system, external_id)
               values (%s, 'lead_contact', %s, 'crm', %s)
               on conflict (tenant_id, source_system, external_id) do nothing""",
            (tenant_id, new_id, external_id),
        )


# ---------------------------------------------------------------------------
# Start-of-Care promotion (lead -> client)
# ---------------------------------------------------------------------------
async def _promote_to_client(
    conn, tenant_id: str, lead_id: str, attributes: dict, source_system: str
) -> str | None:
    """Stand up the `clients` row for a lead that has reached start of care.

    This closes a real gap rather than adding a nicety: NOTHING in the app wrote
    `clients.lead_id` before this (the manual create route omits it), so the M16
    referrals dashboard — which joins `clients.lead_id -> leads.source` to answer
    "which partners send business that converts" — could only ever see seed data,
    and the M15 census had no way to gain a client from the pipeline.

    Deliberately partial: `payer`, `authorized_hours_per_week`, `care_summary` and
    `region_id` are left null. A sales CRM does not know them, and the census
    buckets a null payer as "unknown" by design — the office completes intake on
    the client profile. Client STATUS lifecycle (hospital hold, discharge) stays
    with the M15 seam; this only ever creates.

    Idempotent by `clients.lead_id`: a replayed stage row or a re-run backfill
    finds the existing client and does nothing. Returns the client id, or None
    when one already existed.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select id from public.clients where lead_id = %s", (lead_id,))
        if await cur.fetchone() is not None:
            return None

        await cur.execute(
            "select name, phone, email, address, zip from public.leads where id = %s",
            (lead_id,),
        )
        lead = await cur.fetchone()
    if lead is None:
        return None

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.clients
                 (tenant_id, lead_id, name, phone, email, address, zip, status)
               values (%s, %s, %s, %s, %s, %s, %s, 'active')
               returning id""",
            (
                tenant_id,
                lead_id,
                lead["name"],
                lead["phone"],
                lead["email"],
                lead["address"],
                lead["zip"],
            ),
        )
        client_id = str((await cur.fetchone())["id"])

    # The family contacts move with the client — same shape by construction
    # (the 18a migration mirrors client_contacts), so this is a copy, not a
    # translation.
    await conn.execute(
        """insert into public.client_contacts
             (tenant_id, client_id, name, relationship, phone, email, is_primary, notes)
           select tenant_id, %s, name, relationship, phone, email, is_primary, notes
             from public.lead_contacts where lead_id = %s""",
        (client_id, lead_id),
    )

    # Register the source's id for the CARE RECIPIENT against the client, so later
    # changes to that person resolve to the client and flow through UPDATERS.
    client_external_id = _clean(attributes.get("client_external_id"))
    if client_external_id:
        await conn.execute(
            """insert into public.external_ids
                 (tenant_id, entity_type, entity_id, source_system, external_id)
               values (%s, 'client', %s, 'crm', %s)
               on conflict (tenant_id, source_system, external_id) do nothing""",
            (tenant_id, client_id, client_external_id),
        )

    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="client.created",
        entity_type="client",
        entity_id=client_id,
        payload={
            "summary": (
                f"Client '{lead['name']}' created from {source_system} start of care"
            ),
            "lead_id": lead_id,
        },
    )
    return client_id


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------
async def update_client(
    conn, tenant_id: str, client_id: str, attributes: dict, source_system: str
) -> None:
    """Patch contact/location fields on an existing client from a re-synced source
    record. Never status, payer, or authorized hours — see CLIENT_PATCH_FIELDS."""
    patch = _patch(attributes, CLIENT_PATCH_FIELDS)
    if not patch:
        return
    set_parts = ", ".join(f"{field} = %s" for field in patch)
    await conn.execute(
        f"update public.clients set {set_parts} where id = %s",
        (*patch.values(), client_id),
    )
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="client.updated",
        entity_type="client",
        entity_id=client_id,
        payload={
            "summary": f"Client details updated from {source_system} "
                       f"({', '.join(sorted(patch))})",
            "fields": sorted(patch),
        },
    )


# entity_type -> auto-create writer. A type absent here that arrives with
# creates_entity=True falls back to the task outcome in resolution.py.
WRITERS: dict[str, Callable[..., Awaitable[str]]] = {
    "lead": write_lead,
}

# entity_type -> updater for an already-mapped entity. A type absent here that
# arrives with updates_entity=True keeps the log-only behavior.
UPDATERS: dict[str, Callable[..., Awaitable[None]]] = {
    "lead": update_lead,
    "client": update_client,
}
