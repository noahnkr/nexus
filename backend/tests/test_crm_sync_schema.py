"""CRM-sync schema additions (Module 18a, Task 2), gated on NEXUS_APP_DB_URL.

Covers the `20260730000000_entities_crm_sync.sql` migration:
  * `leads` carries the CRM-fed address / zip / background columns;
  * `lead_contacts` exists with the client_contacts shape, is tenant-isolated,
    and cascades when its lead is deleted;
  * the seeded lead contacts land under the lead the promotion path uses;
  * the Module 11 field catalog picks the new lead columns up from
    information_schema without a code change (the plan says verify, not assume).
"""
import asyncio
import uuid

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

MARGARET = "33333333-0000-0000-0000-000000000001"


async def _schema_scenario():
    from psycopg.rows import dict_row

    from app import db
    from app.services.automations.entities import entity_catalog, entity_field_suggestions

    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select address, zip, background from public.leads where id = %s",
                    (MARGARET,),
                )
                out["seeded_lead"] = await cur.fetchone()

                await cur.execute(
                    "select name, relationship, is_primary, source "
                    "from public.lead_contacts where lead_id = %s order by name",
                    (MARGARET,),
                )
                out["seeded_contacts"] = await cur.fetchall()

            # lead_contacts mirrors client_contacts column-for-column (plus `source`).
            # Promotion copies rows across, so a drift here would break it silently.
            async with conn.cursor() as cur:
                await cur.execute(
                    "select table_name, column_name from information_schema.columns "
                    "where table_schema = 'public' "
                    "and table_name in ('lead_contacts', 'client_contacts')"
                )
                by_table: dict[str, set] = {"lead_contacts": set(), "client_contacts": set()}
                for table, column in await cur.fetchall():
                    by_table[table].add(column)
            out["shape_diff"] = sorted(
                (by_table["client_contacts"] - {"client_id"})
                ^ (by_table["lead_contacts"] - {"lead_id", "source"})
            )

            # Cascade: deleting a lead takes its contacts with it.
            temp_lead = str(uuid.uuid4())
            await conn.execute(
                "insert into public.leads (id, tenant_id, name) "
                "values (%s, app.current_tenant_id(), %s)",
                (temp_lead, f"cascade-probe-{uuid.uuid4().hex[:6]}"),
            )
            await conn.execute(
                "insert into public.lead_contacts (tenant_id, lead_id, name) "
                "values (app.current_tenant_id(), %s, %s)",
                (temp_lead, "Cascade Probe Contact"),
            )
            await conn.execute("delete from public.leads where id = %s", (temp_lead,))
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.lead_contacts where lead_id = %s",
                    (temp_lead,),
                )
                out["after_cascade"] = (await cur.fetchone())[0]

            # A demo-tenant contact row...
            probe_id = str(uuid.uuid4())
            await conn.execute(
                "insert into public.lead_contacts (id, tenant_id, lead_id, name) "
                "values (%s, app.current_tenant_id(), %s, %s)",
                (probe_id, MARGARET, "RLS Probe Contact"),
            )

            out["catalog"] = await entity_catalog(conn)
            out["suggestions"] = await entity_field_suggestions(conn)

        # ...is invisible to the probe tenant.
        async with db.tenant_tx(PROBE_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*) from public.lead_contacts where id = %s", (probe_id,)
                )
                out["probe_sees"] = (await cur.fetchone())[0]

        async with db.tenant_tx(DEMO_TENANT) as conn:
            await conn.execute("delete from public.lead_contacts where id = %s", (probe_id,))
    finally:
        await db.close_pool()
    return out


def test_crm_sync_schema():
    r = asyncio.run(_schema_scenario())

    lead = r["seeded_lead"]
    assert lead["zip"] == "92008"
    assert "Rosewood" in lead["address"]
    assert "fall in the kitchen" in lead["background"]

    contacts = r["seeded_contacts"]
    assert [c["name"] for c in contacts] == ["Claire Ellison-Boyd", "Trevor Ellison"]
    assert [c["relationship"] for c in contacts] == ["daughter", "son"]
    assert [c["is_primary"] for c in contacts] == [True, False]

    # Same columns as client_contacts modulo the FK and lead_contacts' `source`.
    assert r["shape_diff"] == []

    assert r["after_cascade"] == 0
    assert r["probe_sees"] == 0


def test_field_catalog_picks_up_the_new_lead_columns():
    """The M11 catalog reads information_schema, so the migration alone should
    surface the columns — no seam edit required. Verified, not assumed."""
    r = asyncio.run(_schema_scenario())

    lead_fields = {f["path"] for f in r["catalog"]["lead"]["fields"]}
    assert {"entity.address", "entity.zip", "entity.background"} <= lead_fields

    labels = {f["path"]: f["label"] for f in r["catalog"]["lead"]["fields"]}
    assert labels["entity.zip"] == "Zip"
    assert labels["entity.background"] == "Background"

    assert "entity.zip" in r["suggestions"]
