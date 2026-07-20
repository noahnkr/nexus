"""Deterministic caregiver-matching engine — Module 12a vertical content seam.

`rank_candidates(conn, schedule_row)` scores the whole roster against one visit in a
single pass (fine at this scale — low tens of caregivers) and returns the top
candidates with plain-language reasons and warnings. No LLM anywhere in the ranking:
the office user must be able to read *why* a caregiver ranked where they did, and a
re-run must produce the identical order. Weights are module-level constants, not
config — one client, explainability over tunability; re-templating swaps this seam
file wholesale.

This is a re-templating-seam member alongside views/schedule.py (the transition
seam that calls the shared availability/qual helpers here), the entity migration,
tools/entities.py, and the connector writers.
"""
from __future__ import annotations

from datetime import date, datetime

from psycopg.rows import dict_row

# Score weights (points). Named so the reasons read as sentences and a future
# re-template can see every lever in one place. Load balance is the only penalty.
WEIGHTS: dict[str, int] = {
    "availability_fit": 30,
    "geography_same_zip": 20,
    "geography_region": 12,
    "continuity_per_visit": 5,
    "continuity_cap": 20,
    "language_overlap": 10,
    "trait_match_each": 5,
    "trait_match_cap": 10,
    "load_under_20": 5,
    "load_over_40_penalty": -15,
}

FULL_WEEK_HOURS = 40
LIGHT_WEEK_HOURS = 20
TOP_N = 10

# ISO-week scheduled hours for a caregiver (date_trunc('week', …) buckets Monday-
# first). One definition shared by the matcher's load-balance component and the
# roster payload's `hours_this_week` (routers/schedule.py imports week_hours). Counts
# committed/worked visits only (open shifts hold no one, so they never inflate load).
_WEEK_HOURS_ALL_SQL = """
select resource_id,
       coalesce(sum(extract(epoch from (end_time - start_time)) / 3600.0), 0) as hours
  from public.schedules
 where status in ('scheduled','called_out','completed')
   and resource_id is not null
   and date_trunc('week', start_time) = date_trunc('week', %(ref)s::timestamptz)
 group by resource_id
"""

_WEEK_HOURS_ONE_SQL = """
select coalesce(sum(extract(epoch from (end_time - start_time)) / 3600.0), 0) as hours
  from public.schedules
 where resource_id = %(resource_id)s
   and status in ('scheduled','called_out','completed')
   and date_trunc('week', start_time) = date_trunc('week', %(ref)s::timestamptz)
"""


async def week_hours(conn, resource_id: str, ref: datetime | str) -> float:
    """Scheduled hours for one caregiver in the ISO week containing `ref`. Shared
    with the roster payload so the board and the matcher agree on the number."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_WEEK_HOURS_ONE_SQL, {"resource_id": resource_id, "ref": ref})
        return float((await cur.fetchone())["hours"])


async def week_hours_map(conn, ref: date | datetime | str) -> dict[str, float]:
    """{resource_id: scheduled_hours} for the ISO week containing `ref`, one query.
    The board's `hours_this_week` uses this; the matcher uses the same buckets."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_WEEK_HOURS_ALL_SQL, {"ref": ref})
        return {str(r["resource_id"]): float(r["hours"]) for r in await cur.fetchall()}


# ---------------------------------------------------------------------------
# Shared field helpers (views/schedule.py reuses these for its assign() warnings,
# so the "qualification gap" / "outside availability" language is defined once).
# ---------------------------------------------------------------------------
def _hm(text: str) -> int | None:
    """'08:30' -> minutes-since-midnight, or None if unparseable."""
    try:
        h, m = text.strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def availability_covers(availability: dict | None, start: datetime, end: datetime) -> bool:
    """True iff the visit window falls entirely inside one declared range for its
    weekday. `availability` is the resources.availability jsonb
    ({"mon": ["08:00-16:00"], …}). Times are compared in the visit's own clock —
    this vertical does no timezone/geo math (deferred to Future Plans)."""
    if not availability:
        return False
    day = start.strftime("%a").lower()[:3]  # 'mon', 'tue', …
    ranges = availability.get(day) or []
    v_start = start.hour * 60 + start.minute
    v_end = end.hour * 60 + end.minute
    for r in ranges:
        parts = str(r).split("-")
        if len(parts) != 2:
            continue
        lo, hi = _hm(parts[0]), _hm(parts[1])
        if lo is None or hi is None:
            continue
        if lo <= v_start and v_end <= hi:
            return True
    return False


async def missing_qualification_names(
    conn, required_ids, resource_qual_ids
) -> list[str]:
    """Plain qualification names the caregiver is missing for a visit (empty if none).
    Used by both the matcher's disqualifier and assign()'s soft warning."""
    required = {str(x) for x in (required_ids or [])}
    held = {str(x) for x in (resource_qual_ids or [])}
    gap = required - held
    if not gap:
        return []
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, name from public.qualifications where id = any(%s)",
            (list(gap),),
        )
        names = {str(r["id"]): r["name"] for r in await cur.fetchall()}
    return [names.get(g, "a required qualification") for g in gap]


# ---------------------------------------------------------------------------
# rank_candidates — the single ranking pass.
# ---------------------------------------------------------------------------
def _as_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def rank_candidates(conn, schedule_row: dict) -> list[dict]:
    """Rank the roster for one visit. `schedule_row` needs client_id, start_time,
    end_time, required_qualification_ids; optionally id (the shift being filled, so a
    reassign doesn't self-conflict) and replaces_schedule_id (its caregiver is
    disqualified — they just called out). Returns up to TOP_N
    {resource_id, name, phone, score, reasons[], warnings[]}, score-desc then
    name-asc so the order is stable across runs."""
    start = _as_dt(schedule_row["start_time"])
    end = _as_dt(schedule_row["end_time"])
    required = {str(x) for x in (schedule_row.get("required_qualification_ids") or [])}
    client_id = str(schedule_row["client_id"])
    self_id = schedule_row.get("id")
    self_id = str(self_id) if self_id else None
    visit_hours = (end - start).total_seconds() / 3600.0

    async with conn.cursor(row_factory=dict_row) as cur:
        # Client (geography / language / preferences the roster scores against).
        await cur.execute(
            "select zip, languages, preferences from public.clients where id = %s",
            (client_id,),
        )
        client = await cur.fetchone()
        if client is None:
            return []
        client_zip = client["zip"]
        client_langs = {str(x) for x in (client["languages"] or [])}
        client_prefs = {str(x) for x in (client["preferences"] or [])}

        # Roster. Inactive caregivers (M18) are excluded outright — they are off
        # the schedule board too, so they can never be ranked for a shift. Their
        # past visits are untouched; only future staffing ignores them.
        await cur.execute(
            """select id, name, phone, zip, languages, traits, region_ids,
                      qualification_ids, availability
                 from public.resources
                where status = 'active'"""
        )
        roster = await cur.fetchall()

        # Region zip_codes (for the region-covered geography tier).
        await cur.execute("select id, zip_codes from public.regions")
        region_zips = {str(r["id"]): set(r["zip_codes"] or []) for r in await cur.fetchall()}

        # Caregivers with a hard time conflict on this window (disqualifier).
        await cur.execute(
            """select distinct resource_id from public.schedules
                where status in ('scheduled','called_out')
                  and resource_id is not null
                  and start_time < %(end)s and end_time > %(start)s
                  and (%(self_id)s::uuid is null or id <> %(self_id)s::uuid)""",
            {"start": start, "end": end, "self_id": self_id},
        )
        conflicted = {str(r["resource_id"]) for r in await cur.fetchall()}

        # Continuity: completed past visits with this client, per caregiver.
        await cur.execute(
            """select resource_id, count(*) as n from public.schedules
                where client_id = %s and status = 'completed' and resource_id is not null
                group by resource_id""",
            (client_id,),
        )
        completed_counts = {str(r["resource_id"]): r["n"] for r in await cur.fetchall()}

        # Load: scheduled hours this ISO week (the visit's week), per caregiver.
        await cur.execute(_WEEK_HOURS_ALL_SQL, {"ref": start})
        week_load = {str(r["resource_id"]): float(r["hours"]) for r in await cur.fetchall()}

        # The caregiver being replaced (called out of the visit this shift covers)
        # is disqualified from their own replacement.
        replaced_resource: str | None = None
        replaces_id = schedule_row.get("replaces_schedule_id")
        if replaces_id:
            await cur.execute(
                "select resource_id from public.schedules where id = %s", (str(replaces_id),)
            )
            rep = await cur.fetchone()
            if rep and rep["resource_id"] is not None:
                replaced_resource = str(rep["resource_id"])

    candidates: list[dict] = []
    for r in roster:
        rid = str(r["id"])
        held_quals = {str(x) for x in (r["qualification_ids"] or [])}

        # --- disqualifiers (excluded outright) ---
        if required - held_quals:
            continue
        if rid in conflicted:
            continue
        if replaced_resource is not None and rid == replaced_resource:
            continue

        score = 0
        reasons: list[str] = []
        warnings: list[str] = []

        # availability fit
        if availability_covers(r["availability"], start, end):
            score += WEIGHTS["availability_fit"]
            reasons.append("Available during this visit's window")
        else:
            warnings.append("Outside their declared availability for this shift")

        # geography
        r_zip = r["zip"]
        r_region_zips: set[str] = set()
        for gid in (r["region_ids"] or []):
            r_region_zips |= region_zips.get(str(gid), set())
        if client_zip and r_zip and r_zip == client_zip:
            score += WEIGHTS["geography_same_zip"]
            reasons.append("Lives in the client's ZIP code")
        elif client_zip and client_zip in r_region_zips:
            score += WEIGHTS["geography_region"]
            reasons.append("Serves the client's area")
        else:
            warnings.append("Not in the client's service area")

        # continuity (capped)
        past = completed_counts.get(rid, 0)
        if past:
            pts = min(past * WEIGHTS["continuity_per_visit"], WEIGHTS["continuity_cap"])
            score += pts
            visit_word = "visit" if past == 1 else "visits"
            reasons.append(f"Has completed {past} past {visit_word} with this client")

        # language overlap
        if client_langs and client_langs & {str(x) for x in (r["languages"] or [])}:
            score += WEIGHTS["language_overlap"]
            reasons.append("Shares a language with the client")

        # trait ∩ preference (capped)
        matched = client_prefs & {str(x) for x in (r["traits"] or [])}
        if matched:
            pts = min(len(matched) * WEIGHTS["trait_match_each"], WEIGHTS["trait_match_cap"])
            score += pts
            reasons.append("Matches client preferences: " + ", ".join(sorted(matched)))

        # load balance
        current = week_load.get(rid, 0.0)
        if current + visit_hours > FULL_WEEK_HOURS:
            score += WEIGHTS["load_over_40_penalty"]
            warnings.append("Would push this caregiver over 40 hours this week")
        elif current < LIGHT_WEEK_HOURS:
            score += WEIGHTS["load_under_20"]
            reasons.append("Has a light schedule this week")

        candidates.append({
            "resource_id": rid,
            "name": r["name"],
            "phone": r["phone"],
            "score": score,
            "reasons": reasons,
            "warnings": warnings,
        })

    candidates.sort(key=lambda c: (-c["score"], c["name"]))
    return candidates[:TOP_N]
