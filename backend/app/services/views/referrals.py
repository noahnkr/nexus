"""Referrals view — vertical content seam (Module 17).

Which referral sources send leads that actually convert, and how much won business
(authorized hours/week) each has produced. This is enrichment-by-name over the
free-text `leads.source`: a `referral_partners` row (a hospital, a senior-living
community, a discharge planner) joins to leads by EXACT source-name match — no FK,
no backfill, no connector linking. An untracked source is just a string with no
matching partner row; it still appears here with `partner: null`.

The Referrals dashboard rides the Leads surface (it is NOT a fifth sanctioned
surface): this seam + `routers/referrals.py` are re-templating-seam members
alongside `views/leads.py`, `views/clients.py`, and the entity migration. Core
never imports them. Metrics are deterministic SQL — no LLM near the numbers
(CLAUDE.md) — in the `funnel_metrics` house style (several small queries assembled
in Python; empty tenant -> zeroes/nulls, never a 500).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from psycopg.rows import dict_row

from .leads import LEAD_STAGES

# ---------------------------------------------------------------------------
# category config (mirrored on the frontend in lib/referrals.ts). Keys match the
# migration's CHECK; a null category = an untyped partner.
# ---------------------------------------------------------------------------
PARTNER_CATEGORIES: list[dict] = [
    {"key": "hospital", "label": "Hospital"},
    {"key": "senior_living", "label": "Senior living"},
    {"key": "discharge_planner", "label": "Discharge planner"},
    {"key": "home_health", "label": "Home health"},
    {"key": "community", "label": "Community"},
    {"key": "other", "label": "Other"},
]
CATEGORY_KEYS: list[str] = [c["key"] for c in PARTNER_CATEGORIES]
_CATEGORY_LABELS: dict[str, str] = {c["key"]: c["label"] for c in PARTNER_CATEGORIES}


def is_valid_category(category: str | None) -> bool:
    """None is valid (an untyped partner); a non-null value must be a known key."""
    return category is None or category in _CATEGORY_LABELS


def category_label(category: str | None) -> str:
    """Plain label for a category key. None -> "Untyped"; falls back to the raw
    value so an unrecognized key never crashes a summary or a task title."""
    if category is None:
        return "Untyped"
    return _CATEGORY_LABELS.get(category, category)


# Non-terminal lead statuses: a lead still being worked. Derived from the seam
# config's terminal flags rather than re-listed, so a stage-set change (v1.1.2
# went from five stages to seven) can't silently miss this surface.
_IN_PIPELINE_STATUSES = tuple(
    s["key"] for s in LEAD_STAGES if not s["terminal"]
)

# A source needs at least this many leads before its conversion rate is a signal
# worth headlining ("best converter"). Below it, the rate is noise.
_BEST_CONVERTER_MIN_LEADS = 3


def _month_floor(d: date) -> date:
    return date(d.year, d.month, 1)


def _months_back(d: date, n: int) -> date:
    """First of the month `n` months before the month containing `d`."""
    total = d.year * 12 + (d.month - 1) - n
    return date(total // 12, total % 12 + 1, 1)


def _month_key(value) -> str:
    """'YYYY-MM' bucket key for a date/datetime (the frontend formats the label)."""
    return f"{value.year:04d}-{value.month:02d}"


async def referral_metrics(conn, months: int = 6) -> dict:
    """Per-source conversion + hours-won snapshot for the Referrals dashboard.

    Returns `sources` (one row per distinct non-empty `leads.source` UNION every
    tracked partner name — so a tracked-but-quiet partner still appears) and
    `totals`. Deterministic SQL; empty tenant -> zeroes/nulls, never a 500.

    `hours_won` per source = summed `authorized_hours_per_week` of every client
    whose `lead_id` traces to a lead with that source (all linked clients — a
    discharged client was still won business). `monthly` is `date_trunc('month')`
    lead-count buckets over the last `months` months, zero-filled."""
    months = max(1, min(int(months), 24))
    today = _month_floor(datetime.now(timezone.utc).date())
    month_starts = [_months_back(today, months - 1 - i) for i in range(months)]
    month_keys = [_month_key(m) for m in month_starts]
    window_start = month_starts[0]

    async with conn.cursor(row_factory=dict_row) as cur:
        # --- per-source lead aggregates ---
        await cur.execute(
            f"""select source,
                       count(*) as leads_total,
                       count(*) filter (where status = any(%(pipeline)s)) as in_pipeline,
                       count(*) filter (where status = 'converted') as converted,
                       count(*) filter (where status = 'lost') as lost,
                       max(created_at) as last_lead_at
                  from public.leads
                 where source is not null and source <> ''
                 group by source""",
            {"pipeline": list(_IN_PIPELINE_STATUSES)},
        )
        lead_rows = {r["source"]: r for r in await cur.fetchall()}

        # --- hours won per source (all linked clients) ---
        await cur.execute(
            """select l.source, coalesce(sum(c.authorized_hours_per_week), 0) as hours_won
                 from public.clients c
                 join public.leads l on l.id = c.lead_id
                where l.source is not null and l.source <> ''
                group by l.source"""
        )
        hours_by_source = {r["source"]: float(r["hours_won"]) for r in await cur.fetchall()}

        # --- avg days-to-convert per source (lead.created_at -> stage_changed->converted) ---
        await cur.execute(
            """select l.source,
                      avg(extract(epoch from (e.created_at - l.created_at)) / 86400.0) as d
                 from public.events e
                 join public.leads l on l.id = e.entity_id
                where e.entity_type = 'lead'
                  and e.event_type = 'lead.stage_changed'
                  and e.payload->>'to' = 'converted'
                  and l.source is not null and l.source <> ''
                group by l.source"""
        )
        avg_days_by_source = {r["source"]: r["d"] for r in await cur.fetchall()}

        # --- monthly lead-count buckets (within the window) ---
        await cur.execute(
            """select source, date_trunc('month', created_at) as m, count(*) as n
                 from public.leads
                where source is not null and source <> ''
                  and created_at >= %(start)s
                group by source, date_trunc('month', created_at)""",
            {"start": window_start},
        )
        monthly_map: dict[str, dict[str, int]] = {}
        for r in await cur.fetchall():
            monthly_map.setdefault(r["source"], {})[_month_key(r["m"])] = r["n"]

        # --- tracked partners (enrichment, joined by name) ---
        await cur.execute(
            "select id, name, category, contact_name, phone, email, notes "
            "from public.referral_partners"
        )
        partners = {r["name"]: r for r in await cur.fetchall()}

        # --- leads in the last 30 days (pipeline inflow headline) ---
        await cur.execute(
            "select count(*) as n from public.leads "
            "where created_at >= now() - interval '30 days'"
        )
        leads_last_30_days = (await cur.fetchone())["n"]

        # --- ALL-leads monthly buckets (the page's overall trend row — includes
        # leads with an empty source, unlike the per-source series). ---
        await cur.execute(
            """select date_trunc('month', created_at) as m, count(*) as n
                 from public.leads
                where created_at >= %(start)s
                group by date_trunc('month', created_at)""",
            {"start": window_start},
        )
        all_month_map = {_month_key(r["m"]): r["n"] for r in await cur.fetchall()}

    # Every distinct lead source UNION every tracked partner name (quiet partners
    # still surface — that absence of leads is itself the relationship signal).
    source_keys = set(lead_rows) | set(partners)

    sources: list[dict] = []
    for src in source_keys:
        agg = lead_rows.get(src)
        leads_total = agg["leads_total"] if agg else 0
        converted = agg["converted"] if agg else 0
        partner = partners.get(src)
        buckets = monthly_map.get(src, {})
        d = avg_days_by_source.get(src)
        sources.append({
            "source": src,
            "partner": {
                "id": str(partner["id"]),
                "category": partner["category"],
                "contact_name": partner["contact_name"],
                "phone": partner["phone"],
                "email": partner["email"],
                "notes": partner["notes"],
            } if partner else None,
            "leads_total": leads_total,
            "in_pipeline": agg["in_pipeline"] if agg else 0,
            "converted": converted,
            "lost": agg["lost"] if agg else 0,
            "conversion_rate": round(100.0 * converted / leads_total, 1) if leads_total else 0.0,
            "avg_days_to_convert": round(float(d), 1) if d is not None else None,
            "hours_won": round(hours_by_source.get(src, 0.0), 1),
            "last_lead_at": agg["last_lead_at"] if agg else None,
            "monthly": [{"month": k, "count": buckets.get(k, 0)} for k in month_keys],
        })

    # Default order: most won business first, then most leads, then name — the
    # frontend re-sorts client-side, so this is just a stable, sensible default.
    sources.sort(key=lambda s: (-s["hours_won"], -s["leads_total"], s["source"]))

    # Best converter: highest conversion rate among sources with enough leads to
    # trust the number. Null when nothing clears the bar.
    eligible = [s for s in sources if s["leads_total"] >= _BEST_CONVERTER_MIN_LEADS]
    best = max(eligible, key=lambda s: s["conversion_rate"], default=None)

    return {
        "sources": sources,
        "totals": {
            "tracked_partners": len(partners),
            "leads_last_30_days": leads_last_30_days,
            "total_hours_won": round(sum(hours_by_source.values()), 1),
            "best_converter": (
                {"source": best["source"], "conversion_rate": best["conversion_rate"]}
                if best else None
            ),
        },
        "months": month_keys,
        "monthly": [{"month": k, "count": all_month_map.get(k, 0)} for k in month_keys],
    }
