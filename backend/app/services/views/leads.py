"""Leads view — vertical content seam (Module 9).

The one place the leads pipeline's *meaning* lives on the server: the ordered
stage config (labels, terminal flags), the single stage-writing path, the
smart-summary prompt intro, and (9b) the funnel metrics queries. Core code never
imports this — `routers/leads.py` (itself seam), the `update_lead_status` tool
handler (`services/tools/entities.py`, also seam), and the connector entity
writers (`services/connectors/entity_writers.py`, seam) are the only readers.

Stages are `leads.status` values (entity-seam CHECK: new/contact_attempted/
contacted/visit_scheduled/visit_completed/converted/lost) — there is no stage
table and no new column. This config is the label/order/terminal overlay on those
raw values, mirrored on the frontend in `frontend/src/lib/leads.ts`.

The seven stages reflect WelcomeHome's funnel one-to-one (v1.1.2), so a lead's
position here is exactly what the office sees in the CRM; `wh_map` translates.

`change_stage()` is THE writer of `leads.status` (Module 18a). It was extracted
from two places that had drifted into near-copies — the REST PATCH and the
`update_lead_status` tool handler — when the WelcomeHome sync would have made it
three. One emitter means a coordinator's UI click, a chat/MCP-approved change,
and a CRM stage move are indistinguishable in the timeline and cannot diverge;
it is the `views/clients.change_status` (M15) and `views/caregivers.move_stage`
(M10) pattern, arriving late to the surface that needed it first.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from ..events import log_event

# Ordered funnel: the five worked stages then the two terminal ones. `terminal`
# marks a stage a lead ends at (converted = won, lost = dropped) — used by the
# funnel/metrics (9b) for the "in pipeline" (non-terminal) count. Anything asking
# "is this lead still in play" derives it from these flags; never a fresh list.
LEAD_STAGES: list[dict] = [
    {"key": "new", "label": "New", "terminal": False},
    {"key": "contact_attempted", "label": "Contact Attempted", "terminal": False},
    {"key": "contacted", "label": "Contacted", "terminal": False},
    {"key": "visit_scheduled", "label": "Visit Scheduled", "terminal": False},
    {"key": "visit_completed", "label": "Visit Completed", "terminal": False},
    {"key": "converted", "label": "Converted", "terminal": True},
    {"key": "lost", "label": "Lost", "terminal": True},
]

STAGE_KEYS: list[str] = [s["key"] for s in LEAD_STAGES]
_LABELS: dict[str, str] = {s["key"]: s["label"] for s in LEAD_STAGES}


def is_valid_stage(status: str | None) -> bool:
    return status in _LABELS


def stage_label(status: str | None) -> str:
    """Plain-language label for a stage value ("contacted" -> "Contacted").
    Falls back to the raw value so an unrecognized status never crashes a summary."""
    if status is None:
        return "—"
    return _LABELS.get(status, status)


# --- Smart summary (9a) — the only vertical content the generic helper needs ---
# The prompt intro tells the fast model what a *lead* summary should say; the span
# name makes the trace read `lead_summary`. Everything else is in views/summary.py.
LEAD_SUMMARY_INTRO = (
    "You summarize a prospective home-care client (a 'lead') for the office staff "
    "working the sales pipeline. Say who the lead is, where they came from, what has "
    "happened with them so far, and the likely next step to move them forward. Where "
    "their correspondence shows it, say how they have been to deal with — how they "
    "prefer to be reached and how readily they respond."
)
LEAD_SUMMARY_SPAN = "lead_summary"


class StageChangeError(Exception):
    """A rejected stage move. `not_found` (unknown lead) maps to a router 404;
    otherwise the router uses 422. Tool handlers surface either as a plain
    ToolInputError."""

    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


# ---------------------------------------------------------------------------
# change_stage() — THE single writer of leads.status.
# ---------------------------------------------------------------------------
async def change_stage(
    conn, tenant_id: str, source_system: str, lead_id: str, to_status: str
) -> dict:
    """Move one lead to `to_status`, emitting `lead.stage_changed` and superseding
    in-flight sequences, all in the CALLER's transaction (it must NOT open its own
    tenant_tx — a tool handler already holds one via execute_tool). Returns
    `{changed, from, to, name}`.

    A no-op (target == current) returns `changed=False` and emits nothing — a
    re-submitted form, or a CRM re-sync of an unchanged prospect, must not litter
    the timeline. Raises StageChangeError for an invalid stage or unknown lead.

    Deliberately does NOT promote a `converted` lead to a client. Promotion is a
    connector concern (Module 18a, `entity_writers.write_client`): a coordinator
    dragging a card to Converted in the UI is recording an outcome, not asking the
    system to stand up a client record behind their back.
    """
    if not is_valid_stage(to_status):
        raise StageChangeError(f"invalid status; must be one of: {', '.join(STAGE_KEYS)}")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select name, status from public.leads where id = %s for update", (lead_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise StageChangeError("lead not found", not_found=True)

    name, current = row["name"], row["status"]
    if to_status == current:
        return {"changed": False, "from": current, "to": current, "name": name}

    await conn.execute(
        "update public.leads set status = %s where id = %s", (to_status, lead_id)
    )

    # Advancing a lead ends any in-flight sequence bound to the leads view for it,
    # so it can't receive a colder stage's message after moving on. Lazy import
    # avoids the tools<->automations cycle (the engine imports execute_tool).
    from ..automations import supersede_sequence_runs

    await supersede_sequence_runs(conn, tenant_id, "lead", lead_id, view="leads")
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="lead.stage_changed",
        entity_type="lead",
        entity_id=lead_id,
        payload={
            "summary": (
                f"Lead '{name}' moved from {stage_label(current)} "
                f"to {stage_label(to_status)}"
            ),
            "from": current,
            "to": to_status,
        },
    )
    return {"changed": True, "from": current, "to": to_status, "name": name}


# --- Funnel metrics (9b) — plain SQL over leads + events, no materialized views --
async def funnel_metrics(conn) -> dict:
    """Conversion snapshot for the leads dashboard widgets. Returns per-stage counts
    (all five stages, zero-filled), conversion rate (converted ÷ all leads, %),
    new-this-week, average days-to-convert (from lead.created_at to its
    stage_changed→converted event; null if none observed), and the top sources.
    Empty tenant → zeroes and nulls, never a 500."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select status, count(*) as n from public.leads group by status")
        counts = {r["status"]: r["n"] for r in await cur.fetchall()}

        await cur.execute("select count(*) as n from public.leads")
        total = (await cur.fetchone())["n"]

        await cur.execute(
            "select count(*) as n from public.leads "
            "where created_at >= now() - interval '7 days'"
        )
        new_last_7_days = (await cur.fetchone())["n"]

        # Mean days from a lead's creation to its move-to-converted event.
        await cur.execute(
            """select avg(extract(epoch from (e.created_at - l.created_at)) / 86400.0) as d
                 from public.events e
                 join public.leads l on l.id = e.entity_id
                where e.entity_type = 'lead'
                  and e.event_type = 'lead.stage_changed'
                  and e.payload->>'to' = 'converted'"""
        )
        avg_days = (await cur.fetchone())["d"]

        await cur.execute(
            """select source, count(*) as n from public.leads
                where source is not null and source <> ''
                group by source order by n desc, source limit 5"""
        )
        top_sources = [{"source": r["source"], "count": r["n"]} for r in await cur.fetchall()]

    converted = counts.get("converted", 0)
    conversion_rate = round(100.0 * converted / total, 1) if total else 0.0
    stages = [{"stage": s["key"], "count": counts.get(s["key"], 0)} for s in LEAD_STAGES]
    return {
        "stages": stages,
        "conversion_rate": conversion_rate,
        "new_last_7_days": new_last_7_days,
        "avg_days_to_convert": round(float(avg_days), 1) if avg_days is not None else None,
        "top_sources": top_sources,
    }
