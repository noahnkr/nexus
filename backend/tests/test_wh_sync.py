"""WelcomeHome sync write paths (Module 18a, Task 5), gated on NEXUS_APP_DB_URL.

Drives real prospect payloads through the ingest seam and asserts what lands in
the database. Four things are load-bearing and each has a test here:

  * CREATE then UPDATE — a poller re-sends the whole record every sweep, so the
    second sight of a prospect must patch the lead, not fork it.
  * `leads.source` SURVIVES (Module 16 contract) — an unrelated edit must never
    blank or rewrite the referral key.
  * STAGE MOVES go through the single writer, so `lead.stage_changed` lands with
    `source_system='welcomehome'` and sequences see it.
  * START-OF-CARE PROMOTION creates exactly one client, with contacts copied, and
    is idempotent under replay.

Rows created here are cleaned up; events are immutable and left in place.
"""
import asyncio
import uuid

import pytest

import conftest
from app.services.connectors.ingest import SYNC_RECEIPT, ingest_payload

pytestmark = pytest.mark.skipif(
    not conftest.NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)


def _prospect(external_id: str, **over) -> dict:
    """A mapped prospect payload, the shape wh_map.map_prospect returns."""
    base = {
        "external_id": external_id,
        "name": "Margaret Rosewood",
        "source": "A Place For Mom",
        "phone": "(630) 555-0142",
        "email": "m.rosewood@example.com",
        "address": "412 Rosewood Lane, Naperville, IL",
        "zip": "60540",
        "background": "Daughter called after a fall in the kitchen.",
        "status": "new",
        "stage_name": "Inquiry",
        "contacts": [],
        "client_external_id": None,
    }
    base.update(over)
    return base


async def _sync(conn, prospect: dict) -> dict:
    return await ingest_payload(
        "welcomehome",
        {"event": "prospect.synced", "prospect": prospect},
        tenant_id=conftest.DEMO_TENANT,
        receipt_event_type=SYNC_RECEIPT,
        conn=conn,
    )


async def _lead_for(conn, external_id: str):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select l.* from public.leads l
                 join public.external_ids x on x.entity_id = l.id
                where x.entity_type = 'lead' and x.external_id = %s""",
            (external_id,),
        )
        return await cur.fetchone()


async def _events_for(conn, entity_id, event_type=None):
    from psycopg.rows import dict_row

    sql = ("select event_type, source_system, payload from public.events "
           "where entity_id = %s")
    params: list = [entity_id]
    if event_type:
        sql += " and event_type = %s"
        params.append(event_type)
    sql += " order by created_at"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def _cleanup(conn, external_ids: list[str]):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select entity_type, entity_id from public.external_ids where external_id = any(%s)",
            (external_ids,),
        )
        rows = await cur.fetchall()
    for row in rows:
        table = {"lead": "leads", "client": "clients", "lead_contact": "lead_contacts"}
        name = table.get(row["entity_type"])
        if name:
            await conn.execute(f"delete from public.{name} where id = %s", (row["entity_id"],))
    await conn.execute(
        "delete from public.external_ids where external_id = any(%s)", (external_ids,)
    )


# ---------------------------------------------------------------------------
# create -> update -> stage move
# ---------------------------------------------------------------------------
async def _lifecycle_scenario():
    from app import db

    tag = uuid.uuid4().hex[:8]
    pid = f"wh:prospect:test-{tag}"
    cid = f"wh:influencer:test-{tag}"
    out: dict = {}

    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            # 1. First sight -> creates.
            out["create_counts"] = await _sync(conn, _prospect(
                pid,
                contacts=[{
                    "external_id": cid,
                    "name": "Claire Rosewood-Boyd",
                    "relationship": "Daughter",
                    "phone": "(630) 555-0233",
                    "email": "claire.rb@example.com",
                    "is_primary": True,
                }],
            ))
            out["after_create"] = dict(await _lead_for(conn, pid))
            lead_id = out["after_create"]["id"]

            # 2. Re-sync with an edited phone and NO source field (the source
            # didn't mention it this sweep) -> patches, keeps the source.
            out["update_counts"] = await _sync(conn, _prospect(
                pid,
                phone="(630) 555-9999",
                source=None,
                status="contacted",
                stage_name="Contact Made",
            ))
            out["after_update"] = dict(await _lead_for(conn, pid))
            out["events"] = await _events_for(conn, lead_id)

            # 3. Contacts are upserted by external id, not duplicated.
            out["update_counts_2"] = await _sync(conn, _prospect(
                pid,
                contacts=[{
                    "external_id": cid,
                    "name": "Claire Rosewood-Boyd",
                    "relationship": "Daughter",
                    "phone": "(630) 555-0234",
                    "is_primary": True,
                }],
            ))
            from psycopg.rows import dict_row
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select name, relationship, phone, source from public.lead_contacts "
                    "where lead_id = %s", (lead_id,)
                )
                out["contacts"] = await cur.fetchall()

            await _cleanup(conn, [pid, cid])
        return out
    finally:
        await db.close_pool()


def test_a_resynced_prospect_updates_rather_than_forking():
    r = asyncio.run(_lifecycle_scenario())

    assert r["create_counts"] == {"received": 1, "matched": 0, "created": 1, "tasks": 0}
    assert r["update_counts"] == {"received": 1, "matched": 1, "created": 0, "tasks": 0}
    assert r["after_create"]["id"] == r["after_update"]["id"]

    created = r["after_create"]
    assert created["name"] == "Margaret Rosewood"
    assert created["zip"] == "60540"
    assert created["background"].startswith("Daughter called")
    assert created["status"] == "new"

    updated = r["after_update"]
    assert updated["phone"] == "(630) 555-9999"
    assert updated["status"] == "contacted"


def test_an_update_never_blanks_the_referral_source():
    """Module 16's conversion metrics join on this exact string. A sweep that
    didn't mention `source` must leave it exactly as it was."""
    r = asyncio.run(_lifecycle_scenario())
    assert r["after_create"]["source"] == "A Place For Mom"
    assert r["after_update"]["source"] == "A Place For Mom"


def test_a_crm_stage_move_lands_the_shared_stage_event():
    r = asyncio.run(_lifecycle_scenario())

    stage_events = [e for e in r["events"] if e["event_type"] == "lead.stage_changed"]
    assert len(stage_events) == 1
    ev = stage_events[0]
    # Attributed to the connector, so automations can trigger on it and the Event
    # Log shows where it came from.
    assert ev["source_system"] == "welcomehome"
    assert ev["payload"]["from"] == "new"
    assert ev["payload"]["to"] == "contacted"
    # Same plain-language summary the REST PATCH produces — one writer, one voice.
    assert ev["payload"]["summary"] == (
        "Lead 'Margaret Rosewood' moved from New to Contacted"
    )


def test_contacts_are_upserted_by_external_id():
    r = asyncio.run(_lifecycle_scenario())
    contacts = r["contacts"]
    assert len(contacts) == 1                       # not duplicated across 3 syncs
    assert contacts[0]["name"] == "Claire Rosewood-Boyd"
    assert contacts[0]["phone"] == "(630) 555-0234"  # patched in place
    assert contacts[0]["source"] == "welcomehome"


# ---------------------------------------------------------------------------
# Start-of-Care promotion
# ---------------------------------------------------------------------------
async def _promotion_scenario():
    from psycopg.rows import dict_row

    from app import db

    tag = uuid.uuid4().hex[:8]
    pid = f"wh:prospect:test-{tag}"
    contact_id = f"wh:influencer:test-{tag}"
    resident_id = f"wh:resident:test-{tag}"
    out: dict = {}

    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            payload = _prospect(
                pid,
                name="Walter Prairie",
                source="Family and Friend Referral",
                client_external_id=resident_id,
                contacts=[{
                    "external_id": contact_id,
                    "name": "Adaeze Prairie",
                    "relationship": "Daughter",
                    "phone": "(312) 555-0299",
                    "is_primary": True,
                }],
            )
            await _sync(conn, payload)
            lead = await _lead_for(conn, pid)
            lead_id = lead["id"]

            # Move to Start of Care.
            await _sync(conn, {**payload, "status": "converted",
                               "stage_name": "Start of Care"})

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select * from public.clients where lead_id = %s", (lead_id,)
                )
                clients = await cur.fetchall()
            out["clients"] = [dict(c) for c in clients]
            client_id = clients[0]["id"] if clients else None

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select name, relationship, phone, is_primary "
                    "from public.client_contacts where client_id = %s", (client_id,)
                )
                out["client_contacts"] = await cur.fetchall()

                await cur.execute(
                    "select entity_type from public.external_ids where external_id = %s",
                    (resident_id,),
                )
                out["resident_mapping"] = await cur.fetchall()

            out["client_events"] = await _events_for(conn, client_id, "client.created")

            # Replay the same Start-of-Care row — must not create a second client.
            await _sync(conn, {**payload, "status": "converted",
                               "stage_name": "Start of Care"})
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select count(*) from public.clients where lead_id = %s", (lead_id,)
                )
                out["clients_after_replay"] = (await cur.fetchone())["count"]

            out["lead_source"] = lead["source"]

            if client_id:
                await conn.execute(
                    "delete from public.client_contacts where client_id = %s", (client_id,)
                )
                await conn.execute("delete from public.clients where id = %s", (client_id,))
            await _cleanup(conn, [pid, contact_id, resident_id])
        return out
    finally:
        await db.close_pool()


def test_start_of_care_promotes_the_lead_to_a_client():
    r = asyncio.run(_promotion_scenario())

    assert len(r["clients"]) == 1
    client = r["clients"][0]
    assert client["name"] == "Walter Prairie"
    assert client["status"] == "active"
    assert client["zip"] == "60540"
    # The M16 join key: nothing else in the app writes clients.lead_id.
    assert client["lead_id"] is not None
    # WelcomeHome doesn't know these; the office completes intake.
    assert client["payer"] is None
    assert client["authorized_hours_per_week"] is None


def test_promotion_copies_the_family_contacts_across():
    r = asyncio.run(_promotion_scenario())
    contacts = r["client_contacts"]
    assert len(contacts) == 1
    assert contacts[0]["name"] == "Adaeze Prairie"
    assert contacts[0]["relationship"] == "Daughter"
    assert contacts[0]["is_primary"] is True


def test_promotion_emits_a_plain_language_client_created_event():
    r = asyncio.run(_promotion_scenario())
    events = r["client_events"]
    assert len(events) == 1
    assert events[0]["source_system"] == "welcomehome"
    assert events[0]["payload"]["summary"] == (
        "Client 'Walter Prairie' created from welcomehome start of care"
    )


def test_promotion_is_idempotent_under_replay():
    """A re-synced move_in row, or a re-run backfill, must not create a second
    client — the whole import is replay-safe or it is not usable."""
    r = asyncio.run(_promotion_scenario())
    assert r["clients_after_replay"] == 1


def test_the_care_recipient_is_registered_against_the_client():
    """So later changes to that person resolve to the client and flow through
    UPDATERS, rather than looking like a brand-new record."""
    r = asyncio.run(_promotion_scenario())
    assert [row["entity_type"] for row in r["resident_mapping"]] == ["client"]


# ---------------------------------------------------------------------------
# degradation
# ---------------------------------------------------------------------------
async def _unmapped_stage_scenario():
    from app import db

    tag = uuid.uuid4().hex[:8]
    pid = f"wh:prospect:test-{tag}"
    out: dict = {}
    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await _sync(conn, _prospect(pid))
            lead = await _lead_for(conn, pid)
            # A stage wh_map couldn't map arrives as status None.
            await _sync(conn, _prospect(pid, status=None, stage_name="Pilot Track"))
            out["lead"] = dict(await _lead_for(conn, pid))

            # A stage that maps to something the leads CHECK rejects.
            await _sync(conn, _prospect(pid, status="nonsense"))
            out["lead_after_bad"] = dict(await _lead_for(conn, pid))
            out["events"] = await _events_for(conn, lead["id"])
            await _cleanup(conn, [pid])
        return out
    finally:
        await db.close_pool()


def test_an_unmapped_stage_leaves_the_status_alone_and_warns():
    """Unknown WelcomeHome shapes degrade to a warning event, never a 500 and
    never a guessed status."""
    r = asyncio.run(_unmapped_stage_scenario())

    assert r["lead"]["status"] == "new"            # untouched by the None status
    assert r["lead_after_bad"]["status"] == "new"  # untouched by the bad status

    warnings = [e for e in r["events"] if e["event_type"] == "connector.sync_failed"]
    assert len(warnings) == 1
    assert warnings[0]["payload"]["detail"]["unmapped_status"] == "nonsense"
    assert "left unchanged" in warnings[0]["payload"]["summary"]
