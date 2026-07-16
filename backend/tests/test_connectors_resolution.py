"""Connector schema + entity resolution (Module 3b, Tasks 1 & 3).

Schema (Task 1, sync via the RLS-subject app_db fixture): connector_state is
tenant-isolated and unique per (tenant, source_system); external_ids accepts the
new 'calendar' category and still rejects bogus ones. Rollback cleans up.

Resolution (Task 3, async against the live DB): route_normalized_event's three
outcomes — matched / created / task — plus the no-writer and cross-tenant
guards. Seeded rows are cleaned up so reruns stay green (events are immutable by
design and remain).
"""
import asyncio
import uuid

import psycopg
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, set_tenant

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"
SEED_SCHEDULE = "66666666-0000-0000-0000-000000000001"


# --------------------------------------------------------------------------- #
# Task 1 — schema (sync, app_db fixture; rollback auto-cleans)
# --------------------------------------------------------------------------- #


def test_connector_state_tenant_isolation(app_db):
    set_tenant(app_db, DEMO_TENANT)
    with app_db.cursor() as cur:
        cur.execute(
            "insert into public.connector_state (tenant_id, source_system, state) "
            "values (%s, 'gcal', '{\"syncToken\":\"abc\"}') returning id",
            (DEMO_TENANT,),
        )
        row_id = cur.fetchone()[0]
        cur.execute("select count(*) from public.connector_state where id=%s", (row_id,))
        assert cur.fetchone()[0] == 1  # visible to its own tenant

    # Invisible to another tenant (RLS), even within the same uncommitted tx.
    set_tenant(app_db, PROBE_TENANT)
    with app_db.cursor() as cur:
        cur.execute("select count(*) from public.connector_state where id=%s", (row_id,))
        assert cur.fetchone()[0] == 0


def test_connector_state_unique_per_source(app_db):
    set_tenant(app_db, DEMO_TENANT)
    with app_db.cursor() as cur:
        cur.execute(
            "insert into public.connector_state (tenant_id, source_system) values (%s, 'gmail')",
            (DEMO_TENANT,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "insert into public.connector_state (tenant_id, source_system) values (%s, 'gmail')",
                (DEMO_TENANT,),
            )


def test_external_ids_calendar_category(app_db):
    set_tenant(app_db, DEMO_TENANT)
    with app_db.cursor() as cur:
        cur.execute(
            """insert into public.external_ids
                 (tenant_id, entity_type, entity_id, source_system, external_id)
               values (%s, 'schedule', %s, 'calendar', 'CAL-CHECK-1') returning id""",
            (DEMO_TENANT, SEED_SCHEDULE),
        )
        assert cur.fetchone()[0] is not None


def test_external_ids_rejects_bogus_category(app_db):
    set_tenant(app_db, DEMO_TENANT)
    with app_db.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """insert into public.external_ids
                     (tenant_id, entity_type, entity_id, source_system, external_id)
                   values (%s, 'schedule', %s, 'bogus', 'X-1')""",
                (DEMO_TENANT, SEED_SCHEDULE),
            )


# --------------------------------------------------------------------------- #
# Task 3 — resolution (async, live DB)
# --------------------------------------------------------------------------- #


class _Adapter:
    source = "test_resolution"
    category = "crm"


def _ev(entity_type, external_id, *, summary, creates_entity=False, attributes=None):
    from app.services.connectors import NormalizedEvent

    return NormalizedEvent(
        event_type=f"{entity_type}.test",
        entity_type=entity_type,
        external_id=external_id,
        summary=summary,
        attributes=attributes or {},
        creates_entity=creates_entity,
    )


async def _resolution_scenario():
    from app import db
    from app.services.connectors.resolution import route_normalized_event
    from app.services.events import log_event

    adapter = _Adapter()
    created_lead_id = None
    await db.open_pool()
    try:
        # --- matched: seed a mapping to Margaret, then route to it. -----------
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                """insert into public.external_ids
                     (tenant_id, entity_type, entity_id, source_system, external_id, last_synced_at)
                   values (%s, 'lead', %s, 'crm', 'TESTRES-MATCH', null)""",
                (DEMO_TENANT, MARGARET_LEAD),
            )
        async with db.tenant_tx(DEMO_TENANT) as conn:
            receipt = await log_event(
                conn, tenant_id=DEMO_TENANT, source_system="test_resolution",
                event_type="webhook.received", payload={},
            )
            matched = await route_normalized_event(
                conn, DEMO_TENANT, adapter,
                _ev("lead", "TESTRES-MATCH", summary="TESTRES match"), receipt,
            )
            # last_synced_at bumped from null.
            async with conn.cursor() as cur:
                await cur.execute(
                    "select last_synced_at from public.external_ids where external_id='TESTRES-MATCH'"
                )
                synced = (await cur.fetchone())[0]

        # --- created: creates_entity + attributes → new lead + mapping. -------
        async with db.tenant_tx(DEMO_TENANT) as conn:
            receipt = await log_event(
                conn, tenant_id=DEMO_TENANT, source_system="test_resolution",
                event_type="webhook.received", payload={},
            )
            created = await route_normalized_event(
                conn, DEMO_TENANT, adapter,
                _ev("lead", "TESTRES-CREATE", summary="TESTRES create",
                    creates_entity=True,
                    attributes={"name": "TESTRES Created Lead", "email": "tr@example.com"}),
                receipt,
            )
            created_lead_id = created.entity_id
            async with conn.cursor() as cur:
                await cur.execute("select name from public.leads where id=%s", (created_lead_id,))
                created_name = (await cur.fetchone())[0]
                await cur.execute(
                    "select entity_id from public.external_ids where external_id='TESTRES-CREATE'"
                )
                mapping_entity = str((await cur.fetchone())[0])

        # --- task: reference to an unknown id → review task, no business write. -
        async with db.tenant_tx(DEMO_TENANT) as conn:
            receipt = await log_event(
                conn, tenant_id=DEMO_TENANT, source_system="test_resolution",
                event_type="webhook.received", payload={},
            )
            task_out = await route_normalized_event(
                conn, DEMO_TENANT, adapter,
                _ev("lead", "TESTRES-UNKNOWN", summary="TESTRES unknown ref"), receipt,
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    "select title, description, originating_event_id from public.tasks where id=%s",
                    (task_out.task_id,),
                )
                task_row = await cur.fetchone()
                await cur.execute(
                    "select count(*) from public.external_ids where external_id='TESTRES-UNKNOWN'"
                )
                unknown_mapped = (await cur.fetchone())[0]

        # --- no writer: creates_entity for a type without a writer → task. ----
        async with db.tenant_tx(DEMO_TENANT) as conn:
            receipt = await log_event(
                conn, tenant_id=DEMO_TENANT, source_system="test_resolution",
                event_type="webhook.received", payload={},
            )
            nowriter = await route_normalized_event(
                conn, DEMO_TENANT, adapter,
                _ev("resource", "TESTRES-NOWRITER", summary="TESTRES no writer",
                    creates_entity=True, attributes={"name": "x"}),
                receipt,
            )

        # --- cross-tenant: probe's mapping must not match under demo. ---------
        async with db.tenant_tx(PROBE_TENANT) as conn:
            # Probe tenant needs its own entity to point at; reuse a random uuid —
            # resolution only reads the mapping, never dereferences entity_id here.
            await conn.execute(
                """insert into public.external_ids
                     (tenant_id, entity_type, entity_id, source_system, external_id)
                   values (%s, 'lead', %s, 'crm', 'TESTRES-PROBE')""",
                (PROBE_TENANT, str(uuid.uuid4())),
            )
        async with db.tenant_tx(DEMO_TENANT) as conn:
            receipt = await log_event(
                conn, tenant_id=DEMO_TENANT, source_system="test_resolution",
                event_type="webhook.received", payload={},
            )
            probe_out = await route_normalized_event(
                conn, DEMO_TENANT, adapter,
                _ev("lead", "TESTRES-PROBE", summary="TESTRES probe"), receipt,
            )

        return {
            "matched": matched, "synced": synced,
            "created": created, "created_name": created_name, "mapping_entity": mapping_entity,
            "task_out": task_out, "task_row": task_row, "unknown_mapped": unknown_mapped,
            "nowriter": nowriter, "probe_out": probe_out,
        }
    finally:
        # Cleanup (events are immutable and remain by design).
        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.external_ids where external_id in "
                "('TESTRES-MATCH','TESTRES-CREATE')"
            )
            if created_lead_id:
                await conn.execute("delete from public.leads where id=%s", (created_lead_id,))
            await conn.execute("delete from public.tasks where title like 'Review: TESTRES%'")
        async with db.tenant_tx(PROBE_TENANT) as conn:
            await conn.execute("delete from public.external_ids where external_id='TESTRES-PROBE'")
        await db.close_pool()


def test_resolution_outcomes():
    r = asyncio.run(_resolution_scenario())

    # matched
    assert r["matched"].resolution == "matched"
    assert r["matched"].entity_id == MARGARET_LEAD
    assert r["synced"] is not None  # last_synced_at bumped from null

    # created
    assert r["created"].resolution == "created"
    assert r["created_name"] == "TESTRES Created Lead"
    assert r["mapping_entity"] == r["created"].entity_id

    # task (reference to unknown)
    assert r["task_out"].resolution == "task"
    title, description, originating = r["task_row"]
    assert title.startswith("Review: ")
    assert originating is not None  # linked to the webhook receipt
    assert "{" not in title and "{" not in description  # plain language, no JSON
    assert r["unknown_mapped"] == 0  # no business/mapping row for the unknown ref

    # creates_entity with no writer → task, never an exception
    assert r["nowriter"].resolution == "task"

    # cross-tenant: probe's mapping never matched under demo
    assert r["probe_out"].resolution != "matched"
