"""WelcomeHome sync runner (Module 18a, Task 6), gated on NEXUS_APP_DB_URL.

Runs the real runner against a FAKE WelcomeHome client backed by the CSV
fixtures, so the whole path — export pages -> wh_map -> ingest seam -> leads,
contacts, clients, timeline events — is exercised without touching the live CRM.

The two-cycle case is the one that matters: a poller that re-creates rather than
re-matches would quietly double the pipeline within a day.
"""
import asyncio
import csv
import json
import pathlib
import uuid

import pytest

import conftest
from app.services.connectors import sync as sync_mod
from app.services.connectors import wh_runner

pytestmark = pytest.mark.skipif(
    not conftest.NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "wh"
PROSPECT_PAGES = ["prospects_page1.csv", "prospects_page2.csv", "prospects_page3.csv"]

# The fixture prospect ids, namespaced the way wh_map emits them.
FIXTURE_PROSPECTS = [f"wh:prospect:{n}" for n in (9001, 9002, 9003, 9004, 9005, 9006)]


def _csv(name: str) -> list[dict]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _json(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakeWHClient:
    """Serves the fixtures and records the watermark each table was asked for."""

    def __init__(self, calls: list, second_cycle_empty: bool = False):
        self.calls = calls
        self.second_cycle_empty = second_cycle_empty

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def reference(self, name: str) -> list[dict]:
        return _json(f"{name}.json")

    async def export_pages(self, table: str, updated_at_after=None):
        self.calls.append((table, updated_at_after))
        # An incremental sweep on a quiet account returns nothing new.
        if updated_at_after is not None and self.second_cycle_empty:
            return
        if table == "Prospects":
            for page in PROSPECT_PAGES:
                yield _csv(page)
        elif table == "Residents":
            yield _csv("residents.csv")
        elif table == "Influencers":
            yield _csv("influencers.csv")
        elif table == "Activities":
            yield _csv("activities.csv")


async def _leads_by_external(conn, external_ids):
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select x.external_id, l.* from public.leads l
                 join public.external_ids x on x.entity_id = l.id
                where x.entity_type = 'lead' and x.external_id = any(%s)""",
            (list(external_ids),),
        )
        return {r["external_id"]: dict(r) for r in await cur.fetchall()}


async def _cleanup(conn):
    """Remove everything the fixture sweep created, by external id."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select entity_type, entity_id from public.external_ids "
            "where external_id like 'wh:%'"
        )
        rows = await cur.fetchall()
    tables = {
        "lead": "leads", "client": "clients",
        "lead_contact": "lead_contacts", "document": "documents",
    }
    # Clients first: they reference leads.
    order = ["client", "document", "lead_contact", "lead"]
    for etype in order:
        for row in [r for r in rows if r["entity_type"] == etype]:
            if etype == "client":
                await conn.execute(
                    "delete from public.client_contacts where client_id = %s",
                    (row["entity_id"],),
                )
            if etype == "document":
                await conn.execute(
                    "delete from public.document_chunks where document_id = %s",
                    (row["entity_id"],),
                )
            await conn.execute(
                f"delete from public.{tables[etype]} where id = %s", (row["entity_id"],)
            )
    await conn.execute("delete from public.external_ids where external_id like 'wh:%'")
    await conn.execute(
        "delete from public.connector_state where source_system = 'welcomehome'"
    )


async def _two_cycle_scenario(second_cycle_empty: bool):
    from psycopg.rows import dict_row

    from app import db

    calls: list = []
    runner = wh_runner.WelcomeHomeRunner()
    out: dict = {"calls": calls}

    original_client = wh_runner.WelcomeHomeClient
    original_runners = sync_mod._RUNNERS
    wh_runner.WelcomeHomeClient = lambda *a, **k: _FakeWHClient(calls, second_cycle_empty)
    sync_mod._RUNNERS = {"welcomehome": runner}

    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await _cleanup(conn)   # a previous failed run must not skew this one
            async with conn.cursor() as cur:
                await cur.execute("select now()")
                since = (await cur.fetchone())[0]

        out["cycle1"] = await sync_mod.connectors_cycle()
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            out["leads_after_1"] = await _leads_by_external(conn, FIXTURE_PROSPECTS)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select state from public.connector_state "
                    "where source_system = 'welcomehome'"
                )
                out["state"] = (await cur.fetchone())["state"]

        out["cycle2"] = await sync_mod.connectors_cycle()
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            out["leads_after_2"] = await _leads_by_external(conn, FIXTURE_PROSPECTS)
            lead_ids = [r["id"] for r in out["leads_after_2"].values()]

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select count(*) from public.leads where id = any(%s)", (lead_ids,)
                )
                out["lead_count"] = (await cur.fetchone())["count"]

                await cur.execute(
                    "select event_type, count(*) as n from public.events "
                    "where source_system = 'welcomehome' and created_at >= %s "
                    "group by event_type",
                    (since,),
                )
                out["event_counts"] = {
                    r["event_type"]: r["n"] for r in await cur.fetchall()
                }

                await cur.execute(
                    "select payload from public.events "
                    "where event_type = 'lead.activity_logged' and created_at >= %s "
                    "order by created_at",
                    (since,),
                )
                out["activities"] = [r["payload"] for r in await cur.fetchall()]

                await cur.execute(
                    "select c.name, c.lead_id, c.status from public.clients c "
                    "where c.lead_id = any(%s)", (lead_ids,)
                )
                out["clients"] = [dict(r) for r in await cur.fetchall()]

                await cur.execute(
                    "select l.name, count(lc.id) as n from public.leads l "
                    "left join public.lead_contacts lc on lc.lead_id = l.id "
                    "where l.id = any(%s) group by l.name", (lead_ids,)
                )
                out["contact_counts"] = {r["name"]: r["n"] for r in await cur.fetchall()}

            await _cleanup(conn)
        return out
    finally:
        wh_runner.WelcomeHomeClient = original_client
        sync_mod._RUNNERS = original_runners
        await db.close_pool()


def test_a_sweep_imports_prospects_with_their_mapped_stages():
    r = asyncio.run(_two_cycle_scenario(second_cycle_empty=True))

    assert r["cycle1"] == {"welcomehome": True}
    leads = r["leads_after_1"]
    assert set(leads) == set(FIXTURE_PROSPECTS)

    assert leads["wh:prospect:9001"]["name"] == "Margaret Ellison"
    assert leads["wh:prospect:9001"]["status"] == "new"
    assert leads["wh:prospect:9001"]["zip"] == "60540"
    assert leads["wh:prospect:9002"]["status"] == "contacted"
    assert leads["wh:prospect:9003"]["status"] == "qualified"
    assert leads["wh:prospect:9004"]["status"] == "converted"
    assert leads["wh:prospect:9005"]["status"] == "lost"
    # The unmapped stage defaults to 'new' on CREATE (a brand-new lead has to
    # start somewhere) but is never subsequently overwritten by a guess.
    assert leads["wh:prospect:9006"]["status"] == "new"

    # Referral sources land verbatim — the M16 join key.
    assert leads["wh:prospect:9001"]["source"] == "A Place For Mom"
    assert leads["wh:prospect:9004"]["source"] == "Family and Friend Referral"


def test_a_second_cycle_matches_rather_than_duplicating():
    r = asyncio.run(_two_cycle_scenario(second_cycle_empty=False))

    assert r["cycle2"] == {"welcomehome": True}
    # Same lead ids across both cycles, and exactly six leads in total.
    assert r["lead_count"] == 6
    assert {k: v["id"] for k, v in r["leads_after_1"].items()} == {
        k: v["id"] for k, v in r["leads_after_2"].items()
    }
    # Nothing was created on the second pass.
    assert r["event_counts"].get("lead.created", 0) == 0


def test_the_second_cycle_asks_only_for_rows_past_the_cursor():
    r = asyncio.run(_two_cycle_scenario(second_cycle_empty=True))

    cursors = r["state"]["cursors"]
    assert set(cursors) == {"Prospects", "Activities"}

    # First cycle: no watermark on any table. Second: one on the cursored tables.
    first_pass = r["calls"][:4]
    assert all(watermark is None for _, watermark in first_pass)

    second_pass = dict(r["calls"][4:])
    assert second_pass["Prospects"] is not None
    assert second_pass["Activities"] is not None
    # People tables are re-read whole each sweep by design — they're small and a
    # prospect isn't mappable without them.
    assert second_pass["Residents"] is None


def test_activities_land_on_lead_timelines_and_skip_system_noise():
    r = asyncio.run(_two_cycle_scenario(second_cycle_empty=True))

    summaries = [p["summary"] for p in r["activities"]]
    # 5301 Call, 5302 Text, 5303 Email, 5304 Home Visit = 4 real activities.
    # 5306/5307 system, 5308 referrer-scoped, 5309 deleted are all skipped.
    assert len(summaries) == 5   # + 5305 Note on the Start-of-Care prospect
    assert any(s.startswith("Call (inbound): Intake call transcript.") for s in summaries)
    assert not any("Advance Stage" in s for s in summaries)
    assert not any("Prospect Added" in s for s in summaries)

    detail = next(
        p["detail"] for p in r["activities"] if p["summary"].startswith("Call (inbound)")
    )
    assert detail["activity_type"] == "Call"
    assert detail["direction"] == "inbound"
    assert detail["wh_activity_id"] == "wh:activity:5301"


def test_the_sweep_promotes_a_start_of_care_prospect_to_a_client():
    r = asyncio.run(_two_cycle_scenario(second_cycle_empty=False))

    clients = r["clients"]
    assert len(clients) == 1                    # only 9004 reached Start of Care
    assert clients[0]["name"] == "Walter Nkemdi"
    assert clients[0]["status"] == "active"
    assert clients[0]["lead_id"] is not None    # the M16 join key


def test_contacts_are_attached_and_not_duplicated_across_cycles():
    r = asyncio.run(_two_cycle_scenario(second_cycle_empty=False))
    counts = r["contact_counts"]
    # Margaret: a daughter + a son. Harold: his wife Vivian (a second resident).
    assert counts["Margaret Ellison"] == 2
    assert counts["Harold Pryce"] == 1
    assert counts["Walter Nkemdi"] == 1
    assert counts["Estelle Barnhart"] == 0
