"""Caregivers view — vertical content seam (Module 10).

The one place the hiring pipeline's *meaning* lives on the server: the ordered
stage config (labels, terminal flags), the single stage-moving path with atomic
hired→caregiver promotion, the smart-summary prompt intro, and (10b) the hiring
metrics queries. Core code never imports this — `routers/applicants.py` (itself
seam) and the `update_applicant_stage` tool handler (`services/tools/entities.py`,
also seam) are the only readers.

Stages live in `applicants.stage` (M10 migration CHECK: applied/screening/
interview/offer/hired/rejected) — no stage table. This config is the label/order/
terminal overlay on those raw values, mirrored on the frontend in
`frontend/src/lib/caregivers.ts`. It is the caregivers instance of M9's seam
convention (`services/views/leads.py` is the 1:1 template).
"""
from __future__ import annotations

from psycopg.rows import dict_row

from ..events import log_event

# Ordered hiring funnel: five worked stages then the terminal drop-off. `terminal`
# marks a stage an applicant ends at (hired = won, rejected = dropped) — used by
# the funnel/metrics (10b) for the "in pipeline" (non-terminal) count. `rejected`
# mirrors leads' `lost`, but unlike leads it carries a sequence chip (the PRD's
# automated denied email) — that divergence is frontend view config, not here.
CAREGIVER_STAGES: list[dict] = [
    {"key": "applied", "label": "Applied", "terminal": False},
    {"key": "screening", "label": "Screening", "terminal": False},
    {"key": "interview", "label": "Interview", "terminal": False},
    {"key": "offer", "label": "Offer", "terminal": False},
    {"key": "hired", "label": "Hired", "terminal": True},
    {"key": "rejected", "label": "Rejected", "terminal": True},
]

STAGE_KEYS: list[str] = [s["key"] for s in CAREGIVER_STAGES]
_LABELS: dict[str, str] = {s["key"]: s["label"] for s in CAREGIVER_STAGES}


def is_valid_stage(stage: str | None) -> bool:
    return stage in _LABELS


def stage_label(stage: str | None) -> str:
    """Plain-language label for a stage value ("interview" -> "Interview").
    Falls back to the raw value so an unrecognized stage never crashes a summary."""
    if stage is None:
        return "—"
    return _LABELS.get(stage, stage)


# --- Smart summary (10a) — the only vertical content the generic helper needs ---
# The intro tells the fast model what an *applicant* summary should say; the span
# name makes the trace read `applicant_summary`. Everything else is in
# views/summary.py (shared with leads).
APPLICANT_SUMMARY_INTRO = (
    "You summarize a caregiver job applicant for the office staff running the "
    "hiring pipeline. In 2-4 sentences say who the applicant is, where they came "
    "from, what qualifications and availability they bring, where they are in the "
    "hiring process, and the likely next step to move them forward."
)
APPLICANT_SUMMARY_SPAN = "applicant_summary"


# ---------------------------------------------------------------------------
# move_stage() — THE single stage-moving path. Both the REST PATCH and the gated
# `update_applicant_stage` tool handler delegate here, on the caller's
# tenant-scoped connection *inside the caller's transaction* (it must NOT open its
# own tenant_tx — the tool handler already holds one via execute_tool). One event
# emitter, one promotion path: a UI move and a chat/MCP-approved move are
# indistinguishable in the timeline and can't diverge.
# ---------------------------------------------------------------------------
class MoveStageError(Exception):
    """A bad move_stage request. `not_found` distinguishes an unknown applicant
    (router -> 404) from an invalid target stage (router -> 422); the tool handler
    surfaces either as a plain ToolInputError."""

    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


async def _promote_to_resource(
    conn, tenant_id: str, source_system: str, applicant_id: str, name: str
) -> dict | None:
    """Create the caregiver (`resources`) row for a hired applicant, in the caller's
    transaction — atomic with the stage move. Idempotent: iff no `resources` row
    already carries this `applicant_id` (so re-entering `hired` after moving out
    never duplicates the caregiver). Copies name/contact/quals/regions/availability
    verbatim via an in-DB select, and emits `resource.created`. Returns the new
    caregiver `{resource_id, name}` or None when promotion was skipped."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id from public.resources where applicant_id = %s", (applicant_id,)
        )
        if await cur.fetchone() is not None:
            return None  # already promoted — re-hire is a no-op for the roster

        await cur.execute(
            """insert into public.resources
                 (tenant_id, name, phone, email, qualification_ids, region_ids,
                  availability, applicant_id)
               select tenant_id, name, phone, email, qualification_ids, region_ids,
                      availability, id
                 from public.applicants where id = %s
               returning id, name""",
            (applicant_id,),
        )
        res = await cur.fetchone()

    resource_id = str(res["id"])
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="resource.created",
        entity_type="resource",
        entity_id=resource_id,
        payload={"summary": f"Applicant '{name}' hired — caregiver record created"},
    )
    return {"resource_id": resource_id, "name": res["name"]}


async def move_stage(
    conn, tenant_id: str, source_system: str, applicant_id: str, target_stage: str
) -> dict:
    """Move one applicant to `target_stage`, emitting the stage event and performing
    hired-promotion — all in the caller's transaction. Returns
    `{changed, from, to, promoted}` where `promoted` is the new caregiver
    `{resource_id, name}` (only when a hire created one) or None.

    A no-op (target == current) returns `changed=False` and emits nothing.
    Raises MoveStageError for an invalid stage or an unknown applicant."""
    if not is_valid_stage(target_stage):
        raise MoveStageError(
            f"invalid stage; must be one of: {', '.join(STAGE_KEYS)}"
        )

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select name, stage from public.applicants where id = %s for update",
            (applicant_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise MoveStageError("applicant not found", not_found=True)

    name, current = row["name"], row["stage"]
    if target_stage == current:
        return {"changed": False, "from": current, "to": current, "promoted": None}

    await conn.execute(
        "update public.applicants set stage = %s where id = %s",
        (target_stage, applicant_id),
    )

    # Advancing an applicant ends any in-flight sequence bound to the caregivers
    # view for it, so it can't receive a colder stage's message after moving on
    # (the leads precedent). Lazy import avoids the tools<->automations cycle.
    from ..automations import supersede_sequence_runs

    await supersede_sequence_runs(
        conn, tenant_id, "applicant", applicant_id, view="caregivers"
    )
    await log_event(
        conn,
        tenant_id=tenant_id,
        source_system=source_system,
        event_type="applicant.stage_changed",
        entity_type="applicant",
        entity_id=applicant_id,
        payload={
            "summary": (
                f"Applicant '{name}' moved from {stage_label(current)} "
                f"to {stage_label(target_stage)}"
            ),
            "from": current,
            "to": target_stage,
        },
    )

    promoted = None
    if target_stage == "hired":
        promoted = await _promote_to_resource(
            conn, tenant_id, source_system, applicant_id, name
        )
    return {"changed": True, "from": current, "to": target_stage, "promoted": promoted}


# --- Hiring metrics (10b) — plain SQL over applicants + events -----------------
async def hiring_metrics(conn) -> dict:
    """Hiring-funnel snapshot for the caregivers dashboard widgets. Returns per-stage
    counts (all six stages, zero-filled), hire rate (hired ÷ all applicants, %),
    new-this-week, average days-to-hire (from applicant.created_at to its
    stage_changed→hired event; null if none observed), and the top sources.
    Empty tenant → zeroes and nulls, never a 500. Mirrors leads.funnel_metrics."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select stage, count(*) as n from public.applicants group by stage")
        counts = {r["stage"]: r["n"] for r in await cur.fetchall()}

        await cur.execute("select count(*) as n from public.applicants")
        total = (await cur.fetchone())["n"]

        await cur.execute(
            "select count(*) as n from public.applicants "
            "where created_at >= now() - interval '7 days'"
        )
        new_last_7_days = (await cur.fetchone())["n"]

        # Mean days from an applicant's creation to its move-to-hired event.
        await cur.execute(
            """select avg(extract(epoch from (e.created_at - a.created_at)) / 86400.0) as d
                 from public.events e
                 join public.applicants a on a.id = e.entity_id
                where e.entity_type = 'applicant'
                  and e.event_type = 'applicant.stage_changed'
                  and e.payload->>'to' = 'hired'"""
        )
        avg_days = (await cur.fetchone())["d"]

        await cur.execute(
            """select source, count(*) as n from public.applicants
                where source is not null and source <> ''
                group by source order by n desc, source limit 5"""
        )
        top_sources = [{"source": r["source"], "count": r["n"]} for r in await cur.fetchall()]

    hired = counts.get("hired", 0)
    hire_rate = round(100.0 * hired / total, 1) if total else 0.0
    stages = [{"stage": s["key"], "count": counts.get(s["key"], 0)} for s in CAREGIVER_STAGES]
    return {
        "stages": stages,
        "hire_rate": hire_rate,
        "new_last_7_days": new_last_7_days,
        "avg_days_to_hire": round(float(avg_days), 1) if avg_days is not None else None,
        "top_sources": top_sources,
    }
