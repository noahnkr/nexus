"""Matching engine (Module 12a, Task 3), gated on NEXUS_APP_DB_URL.

Ranks a purpose-built roster against one visit and asserts the disqualifiers,
the geography tiering, the continuity cap, the overtime penalty, plain-language
reasons/warnings, and deterministic order. All fixtures are created inside one
tenant transaction and deleted before it commits (matching is read-only, so it
writes no events of its own).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

# A fixed Monday far from the seeded relative times, so nothing collides.
SHIFT_START = datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)   # Mon 09:00
SHIFT_END = datetime(2027, 3, 1, 13, 0, tzinfo=timezone.utc)    # Mon 13:00
MON_AVAIL = {"mon": ["08:00-16:00"]}  # covers the 09:00–13:00 window


async def _scenario():
    from psycopg.types.json import Json

    from app import db
    from app.services.views.matching import rank_candidates

    sfx = uuid.uuid4().hex[:6]
    ids = {k: str(uuid.uuid4()) for k in
           ("qual", "region", "client", "A", "B", "C", "D", "E", "F", "G", "J")}
    sched_ids: list[str] = []
    out: dict = {"ids": ids}

    await db.open_pool()
    try:
        async with db.tenant_tx(DEMO_TENANT) as conn:
            async def sched(rid, start, end, status, quals=None):
                sid = str(uuid.uuid4())
                sched_ids.append(sid)
                await conn.execute(
                    """insert into public.schedules
                         (id, tenant_id, resource_id, client_id, start_time, end_time,
                          status, required_qualification_ids)
                       values (%s, app.current_tenant_id(), %s, %s, %s, %s, %s, %s)""",
                    (sid, rid, ids["client"], start, end, status, quals or []),
                )
                return sid

            async def resource(key, *, quals, regions, avail, zip_, langs, traits):
                await conn.execute(
                    """insert into public.resources
                         (id, tenant_id, name, qualification_ids, region_ids,
                          availability, zip, languages, traits)
                       values (%s, app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s)""",
                    (ids[key], f"cand-{key}-{sfx}", quals, regions, Json(avail),
                     zip_, langs, traits),
                )

            # Reference rows: one required qualification, one region covering 90001.
            await conn.execute(
                "insert into public.qualifications (id, tenant_id, name) "
                "values (%s, app.current_tenant_id(), %s)",
                (ids["qual"], f"TestQual-{sfx}"),
            )
            await conn.execute(
                "insert into public.regions (id, tenant_id, name, zip_codes) "
                "values (%s, app.current_tenant_id(), %s, %s)",
                (ids["region"], f"TestRegion-{sfx}", ["90001", "90002"]),
            )
            # Client at zip 90001, speaks en/es, wants a female caregiver.
            await conn.execute(
                """insert into public.clients
                     (id, tenant_id, name, status, zip, languages, preferences)
                   values (%s, app.current_tenant_id(), %s, 'active', %s, %s, %s)""",
                (ids["client"], f"cli-{sfx}", "90001", ["en", "es"], ["female caregiver"]),
            )
            Q = [ids["qual"]]

            # A: same zip. 30 avail + 20 zip + 10 lang + 5 light = 65
            await resource("A", quals=Q, regions=[], avail=MON_AVAIL, zip_="90001",
                           langs=["en"], traits=[])
            # B: region-covered zip. 30 + 12 + 10 + 5 = 57
            await resource("B", quals=Q, regions=[ids["region"]], avail=MON_AVAIL,
                           zip_="90002", langs=["en"], traits=[])
            # C: no geography. 30 + 0 + 10 + 5 = 45 (+ service-area warning)
            await resource("C", quals=Q, regions=[], avail=MON_AVAIL, zip_="90003",
                           langs=["en"], traits=[])
            # D: missing the required qualification -> disqualified.
            await resource("D", quals=[], regions=[ids["region"]], avail=MON_AVAIL,
                           zip_="90001", langs=["en"], traits=[])
            # E: qualified but a hard time conflict on the window -> disqualified.
            await resource("E", quals=Q, regions=[], avail=MON_AVAIL, zip_="90001",
                           langs=["en"], traits=[])
            await sched(ids["E"], SHIFT_START, SHIFT_END, "scheduled")
            # F: the caregiver being replaced (called out of a different-time visit).
            await resource("F", quals=Q, regions=[], avail=MON_AVAIL, zip_="90001",
                           langs=["en"], traits=[])
            replaced_id = await sched(
                ids["F"], SHIFT_START + timedelta(days=1), SHIFT_END + timedelta(days=1),
                "called_out",
            )
            # G: qualified, no other signal, already 40h this week -> -15 + overtime.
            await resource("G", quals=Q, regions=[], avail={}, zip_="90009",
                           langs=[], traits=[])
            await sched(ids["G"], datetime(2027, 3, 3, 0, 0, tzinfo=timezone.utc),
                        datetime(2027, 3, 4, 16, 0, tzinfo=timezone.utc), "scheduled")
            # J: continuity only. 6 completed visits (capped at +20) + 5 light = 25.
            await resource("J", quals=Q, regions=[], avail={}, zip_="90009",
                           langs=[], traits=[])
            for i in range(6):
                past = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc) + timedelta(days=i)
                await sched(ids["J"], past, past + timedelta(hours=2), "completed")

            base_row = {
                "client_id": ids["client"],
                "start_time": SHIFT_START,
                "end_time": SHIFT_END,
                "required_qualification_ids": Q,
            }
            run1 = await rank_candidates(conn, base_row)
            run2 = await rank_candidates(conn, base_row)

            # Replacement row: F (the called-out caregiver) must be excluded.
            repl_row = {**base_row, "replaces_schedule_id": replaced_id}
            run_repl = await rank_candidates(conn, repl_row)

            # M18: deactivating a caregiver removes them from the candidate pool
            # outright — no score, no warning, just absent (they can't be staffed).
            await conn.execute(
                "update public.resources set status = 'inactive' where id = %s",
                (ids["C"],),
            )
            run_inactive = await rank_candidates(conn, base_row)

            out["run1"] = run1
            out["run2"] = run2
            out["run_repl"] = run_repl
            out["run_inactive"] = run_inactive

            # cleanup (schedules first for the FK), then resources + reference rows.
            for sid in sched_ids:
                await conn.execute("delete from public.schedules where id = %s", (sid,))
            for key in ("A", "B", "C", "D", "E", "F", "G", "J"):
                await conn.execute("delete from public.resources where id = %s", (ids[key],))
            await conn.execute("delete from public.clients where id = %s", (ids["client"],))
            await conn.execute("delete from public.regions where id = %s", (ids["region"],))
            await conn.execute("delete from public.qualifications where id = %s", (ids["qual"],))
        return out
    finally:
        await db.close_pool()


def test_matching():
    out = asyncio.run(_scenario())
    ids = out["ids"]
    run1 = out["run1"]
    by_id = {c["resource_id"]: c for c in run1}
    order = [c["resource_id"] for c in run1]

    # Disqualifiers: missing qual (D) and time conflict (E) are absent.
    assert ids["D"] not in by_id
    assert ids["E"] not in by_id
    # The called-out caregiver (F) is absent from their replacement's candidates.
    assert ids["F"] not in {c["resource_id"] for c in out["run_repl"]}

    # Geography tiering: same-zip (A) > region-covered (B) > no-geography (C).
    assert order.index(ids["A"]) < order.index(ids["B"]) < order.index(ids["C"])
    assert by_id[ids["A"]]["score"] == 65
    assert by_id[ids["B"]]["score"] == 57
    assert by_id[ids["C"]]["score"] == 45
    assert "Lives in the client's ZIP code" in by_id[ids["A"]]["reasons"]
    assert "Serves the client's area" in by_id[ids["B"]]["reasons"]
    assert "Not in the client's service area" in by_id[ids["C"]]["warnings"]

    # Continuity cap: 6 completed visits earns +20, not +30 -> score 25.
    j = by_id[ids["J"]]
    assert j["score"] == 25
    assert any("6 past visits" in r for r in j["reasons"])

    # Overtime penalty + its plain warning.
    g = by_id[ids["G"]]
    assert g["score"] == -15
    assert any("over 40 hours" in w for w in g["warnings"])

    # Reasons/warnings are plain sentences — no raw field names leak through.
    for c in run1:
        for text in c["reasons"] + c["warnings"]:
            lowered = text.lower()
            assert "qualification_ids" not in lowered
            assert "region_ids" not in lowered
            assert "_id" not in lowered

    # Deterministic order across two runs.
    assert [c["resource_id"] for c in out["run2"]] == order


def test_inactive_caregivers_are_not_candidates():
    """M18: an inactive caregiver disappears from ranking entirely — the rest of
    the ordering is untouched, so this is an exclusion, not a scoring penalty."""
    out = asyncio.run(_scenario())
    ids = out["ids"]
    before = [c["resource_id"] for c in out["run1"]]
    after = [c["resource_id"] for c in out["run_inactive"]]

    assert ids["C"] in before
    assert ids["C"] not in after
    assert after == [rid for rid in before if rid != ids["C"]]
