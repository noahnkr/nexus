"""run_report read-only text-to-SQL tool (Module 2, Task 4).

Proves the three safety layers end to end against the real DB: the guard shapes
what runs, RLS scopes results to the tenant even through generated SQL, the row
cap truncates, and — most importantly — the Postgres READ ONLY transaction
physically blocks a write even if the guard is bypassed. Skipped until
NEXUS_APP_DB_URL is set.
"""
import asyncio

import psycopg
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(
    not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

MARGARET_LEAD = "33333333-0000-0000-0000-000000000001"


async def _scenario():
    from app import db
    from app.services.tools import get_tool
    from app.services.tools import reporting

    await db.open_pool()
    try:
        run_report = get_tool("run_report")
        async with db.tenant_tx(DEMO_TENANT) as conn:
            by_status = await run_report.handler(
                conn,
                {
                    "sql": "select status, count(*) as n from leads group by status",
                    "purpose": "leads per status",
                },
            )
            total = await run_report.handler(
                conn,
                {"sql": "select count(*) as n from leads", "purpose": "total leads"},
            )
            big = await run_report.handler(
                conn,
                {"sql": "select generate_series(1, 300) as n", "purpose": "row cap check"},
            )

        # Direct proof the READ ONLY transaction holds even bypassing the guard:
        # push an UPDATE straight through the executor internals.
        readonly_error = None
        try:
            await reporting._execute_readonly(
                f"update leads set source = 'HACKED' where id = '{MARGARET_LEAD}'",
                DEMO_TENANT,
            )
        except psycopg.Error as exc:
            readonly_error = exc

        # And the seed row is unchanged.
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select source from public.leads where id=%s", (MARGARET_LEAD,)
                )
                margaret_source = (await cur.fetchone())[0]

        return by_status, total, big, readonly_error, margaret_source
    finally:
        await db.close_pool()


def test_run_report():
    by_status, total, big, readonly_error, margaret_source = asyncio.run(_scenario())

    # lead-count-by-status matches the seed (6 demo leads across 5 statuses).
    counts = {r["status"]: r["n"] for r in by_status.data["rows"]}
    assert counts == {
        "new": 1,
        "contacted": 1,
        "visit_scheduled": 1,
        "converted": 2,
        "lost": 1,
    }
    assert set(by_status.data["columns"]) == {"status", "n"}

    # RLS through text-to-SQL: only the demo tenant's 6 leads are counted, never
    # the probe tenant's lead.
    assert total.data["rows"][0]["n"] == 6

    # row cap: 300 rows truncated to 200 with the flag set.
    assert big.data["row_count"] == 200
    assert big.data["truncated"] is True

    # READ ONLY barrier held (a psycopg read-only-transaction error), and the
    # targeted seed row is untouched.
    assert readonly_error is not None
    assert "read-only" in str(readonly_error).lower()
    assert margaret_source == "website"  # seed value, not 'HACKED'
