"""Automations REST API (Module 7a, Task 4) — CRUD over recipes + a manual
run-now trigger that makes the whole engine curl-testable before any background
machinery (7b) exists.

Every write goes through `validate_recipe` (the single recipe gate): a bad recipe
is a 422 whose detail is the plain-language `RecipeError` message (M8 renders it
inline). Reads/writes are tenant-scoped via the standard `tenant_conn` dependency
— RLS does all filtering, so no query mentions tenant_id.

`POST /{id}/run` executes synchronously in-request: `start_run` commits, then
`advance_run` drives the run (in its own per-step transactions) until it completes,
parks (`waiting`/`waiting_approval`), or fails — then the refreshed run is returned.
A no-delay recipe finishes in well under a second at this scale (approvals set the
synchronous-execution precedent).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..config import settings
from ..db import tenant_conn, tenant_tx
from ..deps import get_current_user, get_tenant_id
from ..schemas import (
    AutomationCreate,
    AutomationDraft,
    AutomationOut,
    AutomationPatch,
    DraftRequest,
    EntityFields,
    FieldCatalog,
    FieldRef,
    LastRun,
    RunNow,
    RunOut,
    Vocabulary,
    VocabFunction,
    VocabTool,
    VocabTriggers,
)
from ..services.approvals import ActionAlreadyResolved, reject_action
from ..services.automations import (
    RecipeError,
    advance_run,
    cancel_run,
    get_run,
    start_run,
    validate_recipe,
)
from ..services.automations.draft import DraftError, draft_recipe
from ..services.automations.entities import (
    ENTITY_TABLES,
    EVENT_ENTITY_TYPES,
    EVENT_PAYLOAD_FIELDS,
    EVENT_PREFIX_ENTITY_TYPES,
    entity_catalog,
    entity_field_suggestions,
    humanize,
)
from ..services.automations.functions import all_functions
from ..services.automations.recipe import OPERATORS
from ..services.automations.scheduler import next_fire
from ..services.tools import all_tools, get_tool
from ..services.tools.labels import tool_label

# Core event types the builder should always offer, even before any have been
# observed in the events table (the vocabulary unions these with observed facets).
_KNOWN_EVENT_TYPES = [
    "lead.created", "lead.updated", "client.created", "client.updated",
    "client.status_changed",
    "schedule.created", "schedule.assigned", "schedule.called_out",
    "schedule.cancelled", "schedule.updated",
    "schedule.checked_in", "schedule.checked_out",
    "applicant.created", "applicant.stage_changed",
    "referral_partner.created", "referral_partner.updated",
    "referral_partner.deleted",
    "resource.created", "resource.updated", "resource.status_changed",
    "credential.added", "credential.updated", "credential.removed",
]

# The five core trigger fields every event carries, with plain-language labels
# (Module 11). Static — they're the shape of `_trigger_scope`, not observed data.
_CORE_TRIGGER_FIELDS: list[tuple[str, str]] = [
    ("trigger.event_type", "Event type"),
    ("trigger.source_system", "Source system"),
    ("trigger.entity_type", "Record type"),
    ("trigger.entity_id", "Record id"),
    ("trigger.created_at", "When it happened"),
]

router = APIRouter(prefix="/api", tags=["automations"])

# Tools the builder must not offer as recipe steps (M15c). See the vocabulary
# endpoint for why.
_STEP_EXCLUDED_TOOLS = {"run_automation"}

_ACTIVE_STATES = ("running", "waiting", "waiting_approval")
_TERMINAL_STATES = ("completed", "failed", "cancelled")
_MAX_RUN_LIMIT = 100

# Plain message surfaced when the one-sequence-per-(view,stage) unique index fires.
_BINDING_CONFLICT = "That stage already has a sequence — edit it instead."


def _validate_binding(binding: dict | None) -> None:
    """Validate binding SHAPE generically (core never interprets vertical stage
    names): a non-empty object with string values, containing a 'view' key, at most
    4 keys. Meaning is enforced by the unique index + the vertical builder. A null
    binding (unbound) is always valid."""
    if binding is None:
        return
    if not isinstance(binding, dict) or not binding:
        raise HTTPException(status_code=422, detail="binding must be a non-empty object")
    if len(binding) > 4:
        raise HTTPException(status_code=422, detail="binding has too many keys")
    if "view" not in binding:
        raise HTTPException(status_code=422, detail="binding must include a 'view'")
    for key, value in binding.items():
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=422, detail=f"binding value for '{key}' must be a non-empty string"
            )


def _requires_approval(steps: list) -> bool:
    """True if any tool step calls a gated (unsafe) tool — the recipe will pause
    for approval. Computed server-side against the tool registry so the grid can
    warn office staff before a run parks."""
    for step in steps or []:
        if isinstance(step, dict) and step.get("type") == "tool":
            tool = get_tool(step.get("tool", ""))
            if tool is not None and not tool.safe:
                return True
    return False


def _valid_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{what} must be a valid id")


def _automation_out(
    row: dict, active_runs: int = 0, last_run: LastRun | None = None
) -> AutomationOut:
    steps = row["steps"] or []
    return AutomationOut(
        id=str(row["id"]),
        name=row["name"],
        description=row["description"],
        status=row["status"],
        trigger=row["trigger"] or {},
        conditions=row["conditions"] or [],
        steps=steps,
        next_fire_at=row["next_fire_at"],
        created_by=row["created_by"],
        active_runs=active_runs,
        last_run=last_run,
        requires_approval=_requires_approval(steps),
        binding=row.get("binding"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_out(row: dict) -> RunOut:
    return RunOut(
        id=str(row["id"]),
        automation_id=str(row["automation_id"]),
        status=row["status"],
        trigger_event_id=str(row["trigger_event_id"]) if row["trigger_event_id"] else None,
        entity_type=row["entity_type"],
        entity_id=str(row["entity_id"]) if row["entity_id"] else None,
        context=row["context"] or {},
        step_index=row["step_index"],
        step_log=row["step_log"] or [],
        wake_at=row["wake_at"],
        error=row["error"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _active_run_counts(conn, automation_ids: list[str]) -> dict[str, int]:
    if not automation_ids:
        return {}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select automation_id, count(*) as n
                 from public.automation_runs
                where automation_id = any(%s) and status = any(%s)
                group by automation_id""",
            (automation_ids, list(_ACTIVE_STATES)),
        )
        return {str(r["automation_id"]): r["n"] for r in await cur.fetchall()}


async def _last_runs(conn, automation_ids: list[str]) -> dict[str, LastRun]:
    """Newest run per automation (status + finished_at or created_at) for the grid
    card — one DISTINCT ON query instead of N lateral joins."""
    if not automation_ids:
        return {}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select distinct on (automation_id)
                      automation_id, status, coalesce(finished_at, created_at) as at
                 from public.automation_runs
                where automation_id = any(%s)
                order by automation_id, created_at desc""",
            (automation_ids,),
        )
        return {
            str(r["automation_id"]): LastRun(status=r["status"], at=r["at"])
            for r in await cur.fetchall()
        }


async def _load_row(conn, automation_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.automations where id = %s", (automation_id,))
        return await cur.fetchone()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("/automations", response_model=list[AutomationOut])
async def list_automations(
    conn=Depends(tenant_conn),
    status: str | None = None,
    view: str | None = None,
):
    clauses: list[str] = []
    params: list = []
    if status:
        if status not in ("active", "paused"):
            raise HTTPException(status_code=422, detail="status must be 'active' or 'paused'")
        clauses.append("status = %s")
        params.append(status)
    if view:
        # Bound-sequence lookup for a pipeline view (9b): the Center's binding chips
        # and the funnel strip filter to one view's sequences.
        clauses.append("binding->>'view' = %s")
        params.append(view)
    where = (" where " + " and ".join(clauses)) if clauses else ""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select * from public.automations{where} order by created_at desc", params
        )
        rows = await cur.fetchall()
    ids = [str(r["id"]) for r in rows]
    counts = await _active_run_counts(conn, ids)
    last = await _last_runs(conn, ids)
    return [
        _automation_out(r, counts.get(str(r["id"]), 0), last.get(str(r["id"])))
        for r in rows
    ]


@router.post("/automations", response_model=AutomationOut, status_code=201)
async def create_automation(
    body: AutomationCreate,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
    user: dict = Depends(get_current_user),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    _validate_binding(body.binding)
    recipe = {"trigger": body.trigger, "conditions": body.conditions, "steps": body.steps}
    try:
        validate_recipe(recipe)
    except RecipeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    binding = Json(body.binding) if body.binding is not None else None
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """insert into public.automations
                     (tenant_id, name, description, status, trigger, conditions,
                      steps, created_by, binding)
                   values (%s, %s, %s, 'paused', %s, %s, %s, %s, %s)
                   returning *""",
                (tenant_id, name, body.description, Json(body.trigger),
                 Json(body.conditions), Json(body.steps), user.get("email"), binding),
            )
            row = await cur.fetchone()
    except UniqueViolation:
        raise HTTPException(status_code=409, detail=_BINDING_CONFLICT)
    return _automation_out(row, 0)


# ---------------------------------------------------------------------------
# builder vocabulary + cron preview + agent drafting (Module 8b)
# NOTE: these literal paths are registered BEFORE /automations/{automation_id} so
# FastAPI doesn't capture "vocabulary"/"cron-preview"/"draft" as an id.
# ---------------------------------------------------------------------------
async def _build_field_catalog(conn, event_types: list[str]) -> FieldCatalog:
    """The trigger-aware, plain-language field catalog (Module 11a). Lets the builder
    offer the RIGHT fields for the selected trigger — payload fields grouped per
    event type, entity fields per entity type, and an event→entity map — all in
    plain language. Payload fields and the entity map are the union of the seam's
    DECLARATIONS (EVENT_PAYLOAD_FIELDS / EVENT_ENTITY_TYPES — correct on a tenant
    with no event history) and what's actually been observed; declared wins on
    conflict (curated labels; stray test events can't mislabel a mapping). Every
    event's writer sets payload.summary by convention, so `summary` is offered for
    every event type. No caching; runs per vocabulary fetch (fine at this scale)."""
    # Observed payload keys, grouped per event type (the pooled facet query + a
    # group-by on event_type). Automation-sourced excluded, mirroring the facets.
    observed_keys: dict[str, list[str]] = {}
    async with conn.cursor() as cur:
        await cur.execute(
            "select event_type, jsonb_object_keys(payload) as k from public.events "
            "where payload is not null and jsonb_typeof(payload) = 'object' "
            "and source_system <> 'automation' group by 1, 2 order by 1, 2"
        )
        for event_type, key in await cur.fetchall():
            observed_keys.setdefault(event_type, []).append(key)

    payload_by_event: dict[str, list[FieldRef]] = {}
    for event_type in set(event_types) | set(observed_keys) | set(EVENT_PAYLOAD_FIELDS):
        refs: list[FieldRef] = [
            FieldRef(path=f"trigger.payload.{key}", label=label)
            for key, label in EVENT_PAYLOAD_FIELDS.get(event_type, [])
        ]
        seen = {r.path for r in refs}
        for key in [*observed_keys.get(event_type, []), "summary"]:
            path = f"trigger.payload.{key}"
            if path not in seen:
                seen.add(path)
                refs.append(FieldRef(path=path, label=humanize(key)))
        payload_by_event[event_type] = refs

    # Entity fields per type (vertical seam supplies labels).
    entities = {
        etype: EntityFields(
            label=info["label"],
            fields=[FieldRef(**f) for f in info["fields"]],
        )
        for etype, info in (await entity_catalog(conn)).items()
    }

    # event -> entity: declared first, observed most-frequent fills unknowns, then
    # the prefix heuristics cover anything left — the seam's prefix map
    # (`credential.*` -> `resource`) before the plain `lead.created` -> `lead` rule.
    event_entity: dict[str, str] = dict(EVENT_ENTITY_TYPES)
    async with conn.cursor() as cur:
        await cur.execute(
            "select event_type, entity_type from public.events "
            "where entity_type is not null and source_system <> 'automation' "
            "group by 1, 2 order by event_type, count(*) desc"
        )
        for event_type, ent_type in await cur.fetchall():
            event_entity.setdefault(event_type, ent_type)  # first = most frequent
    for event_type in event_types:
        if event_type not in event_entity:
            prefix = event_type.split(".")[0]
            mapped = EVENT_PREFIX_ENTITY_TYPES.get(prefix)
            if mapped is not None:
                event_entity[event_type] = mapped
            elif prefix in ENTITY_TABLES:
                event_entity[event_type] = prefix

    return FieldCatalog(
        trigger_fields=[FieldRef(path=p, label=lbl) for p, lbl in _CORE_TRIGGER_FIELDS],
        payload_by_event=payload_by_event,
        entities=entities,
        event_entity=event_entity,
    )


async def _build_vocabulary(conn) -> Vocabulary:
    """Everything the builder renders from. Event types union a core-known list with
    what's actually been observed (automation-sourced excluded); tools/functions
    come straight from the registries so new ones appear with no frontend change."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select distinct event_type from public.events "
            "where source_system <> 'automation' order by event_type"
        )
        observed_types = [r["event_type"] for r in await cur.fetchall()]
        await cur.execute(
            "select distinct source_system from public.events "
            "where source_system <> 'automation' order by source_system"
        )
        sources = [r["source_system"] for r in await cur.fetchall()]

    # Core-known ∪ seam-declared ∪ observed: a declared connector event (e.g.
    # sms.received) is offerable as a trigger before one has ever arrived.
    event_types = sorted(
        set(_KNOWN_EVENT_TYPES) | set(EVENT_ENTITY_TYPES) | set(observed_types)
    )

    # Field autocomplete suggestions (WS2): core trigger paths + observed
    # trigger.payload.* keys + the vertical's entity.<col> paths (from the seam).
    field_suggestions: set[str] = {
        "trigger.event_type", "trigger.source_system", "trigger.entity_type",
        "trigger.entity_id", "trigger.created_at",
    }
    async with conn.cursor() as cur:
        await cur.execute(
            "select distinct jsonb_object_keys(payload) as k from public.events "
            "where payload is not null and jsonb_typeof(payload) = 'object' "
            "and source_system <> 'automation'"
        )
        for (key,) in await cur.fetchall():
            field_suggestions.add(f"trigger.payload.{key}")
    field_suggestions.update(await entity_field_suggestions(conn))

    # `run_automation` is deliberately absent from the step palette: it refuses
    # every call made with source_system='automation' (automations must not start
    # automations), so offering it in the builder would only ever produce a step
    # that fails at run time. Chat and MCP still see it via the registry.
    tools = [
        VocabTool(name=t.name, label=tool_label(t.name), description=t.description,
                  input_schema=t.input_schema, safe=t.safe)
        for t in all_tools()
        if t.name not in _STEP_EXCLUDED_TOOLS
    ]
    functions = [
        VocabFunction(name=f.name, description=f.description, input_schema=f.input_schema)
        for f in all_functions()
    ]
    return Vocabulary(
        triggers=VocabTriggers(event_types=event_types, source_systems=sources),
        tools=tools,
        functions=functions,
        operators=list(OPERATORS),
        generate_models=["default", "fast"],
        field_roots=["trigger", "entity", "context"],
        field_suggestions=sorted(field_suggestions),
        field_catalog=await _build_field_catalog(conn, event_types),
    )


@router.get("/automations/vocabulary", response_model=Vocabulary)
async def get_vocabulary(conn=Depends(tenant_conn)):
    return await _build_vocabulary(conn)




@router.post("/automations/draft", response_model=AutomationDraft)
async def draft_automation(body: DraftRequest, conn=Depends(tenant_conn)):
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="drafting requires an Anthropic API key")
    description = (body.description or "").strip()
    if not description:
        raise HTTPException(status_code=422, detail="describe what you want to automate")
    vocab = await _build_vocabulary(conn)
    try:
        return await draft_recipe(description, vocab)
    except DraftError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "technical": exc.detail},
        )


@router.get("/automations/{automation_id}", response_model=AutomationOut)
async def get_automation(automation_id: str, conn=Depends(tenant_conn)):
    automation_id = _valid_uuid(automation_id, "automation_id")
    row = await _load_row(conn, automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="automation not found")
    counts = await _active_run_counts(conn, [automation_id])
    last = await _last_runs(conn, [automation_id])
    return _automation_out(row, counts.get(automation_id, 0), last.get(automation_id))


@router.patch("/automations/{automation_id}", response_model=AutomationOut)
async def patch_automation(
    automation_id: str,
    body: AutomationPatch,
    conn=Depends(tenant_conn),
):
    automation_id = _valid_uuid(automation_id, "automation_id")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.automations where id = %s for update", (automation_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="automation not found")

    # Merge provided fields onto the current recipe, then revalidate if the recipe
    # shape changed (a bad edit is a 422 and leaves the row untouched).
    trigger = body.trigger if body.trigger is not None else row["trigger"]
    conditions = body.conditions if body.conditions is not None else row["conditions"]
    steps = body.steps if body.steps is not None else row["steps"]
    definition_changed = (
        body.trigger is not None or body.conditions is not None or body.steps is not None
    )
    if definition_changed:
        # Edit guard (8a): the engine reads steps from the row at each advance, so a
        # definition edit while runs are in flight would change them mid-run. Block
        # it with a plain message; name/description/status edits are always allowed.
        counts = await _active_run_counts(conn, [automation_id])
        in_flight = counts.get(automation_id, 0)
        if in_flight:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{in_flight} run{'s' if in_flight != 1 else ''} in flight — "
                    "cancel them or let them finish before editing the recipe."
                ),
            )
        try:
            validate_recipe({"trigger": trigger, "conditions": conditions, "steps": steps})
        except RecipeError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    status = row["status"]
    if body.status is not None:
        if body.status not in ("active", "paused"):
            raise HTTPException(status_code=422, detail="status must be 'active' or 'paused'")
        status = body.status
    name = body.name.strip() if body.name is not None else row["name"]
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    description = body.description if body.description is not None else row["description"]

    # binding is model_fields_set-gated: omit to leave unchanged, send null to clear,
    # send an object to (re)bind. Validate SHAPE only; a duplicate (view, stage) is a
    # 409 from the unique index below.
    binding_provided = "binding" in body.model_fields_set
    binding = body.binding if binding_provided else row["binding"]
    if binding_provided:
        _validate_binding(binding)

    # Cron bookkeeping (7b): an active cron automation needs `next_fire_at` armed;
    # recompute on (re)activation or an expression change, and clear it otherwise so
    # a paused/non-cron automation never sits with a stale schedule.
    next_fire_at = row["next_fire_at"]
    if trigger.get("type") == "cron" and status == "active":
        reactivated = row["status"] != "active"
        expr_changed = body.trigger is not None
        if next_fire_at is None or reactivated or expr_changed:
            next_fire_at = next_fire(trigger["expression"])
    else:
        next_fire_at = None

    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """update public.automations
                      set name=%s, description=%s, status=%s, trigger=%s,
                          conditions=%s, steps=%s, next_fire_at=%s, binding=%s
                    where id=%s
                    returning *""",
                (name, description, status, Json(trigger), Json(conditions), Json(steps),
                 next_fire_at, Json(binding) if binding is not None else None,
                 automation_id),
            )
            updated = await cur.fetchone()
    except UniqueViolation:
        raise HTTPException(status_code=409, detail=_BINDING_CONFLICT)
    counts = await _active_run_counts(conn, [automation_id])
    last = await _last_runs(conn, [automation_id])
    return _automation_out(updated, counts.get(automation_id, 0), last.get(automation_id))


@router.delete("/automations/{automation_id}", status_code=204)
async def delete_automation(automation_id: str, conn=Depends(tenant_conn)):
    automation_id = _valid_uuid(automation_id, "automation_id")
    async with conn.cursor() as cur:
        await cur.execute(
            "delete from public.automations where id = %s returning id", (automation_id,)
        )
        deleted = await cur.fetchone()
    if deleted is None:
        raise HTTPException(status_code=404, detail="automation not found")
    return None


# ---------------------------------------------------------------------------
# manual run + run history
# ---------------------------------------------------------------------------
@router.post("/automations/{automation_id}/run", response_model=RunOut)
async def run_now(
    automation_id: str,
    body: RunNow | None = None,
    tenant_id: str = Depends(get_tenant_id),
):
    automation_id = _valid_uuid(automation_id, "automation_id")
    entity_type = body.entity_type if body else None
    entity_id = _valid_uuid(body.entity_id, "entity_id") if body and body.entity_id else None

    # Own transaction so start_run commits before advance_run (which opens its own
    # per-step transactions and must see the committed run row).
    async with tenant_tx(tenant_id) as conn:
        automation = await _load_row(conn, automation_id)
        if automation is None:
            raise HTTPException(status_code=404, detail="automation not found")
        # Manual run is an explicit override: force the run regardless of entry
        # conditions, so a None return means only the concurrency guard fired.
        run_id = await start_run(
            conn, tenant_id, automation,
            entity_type=entity_type, entity_id=entity_id, skip_conditions=True,
        )
    if run_id is None:
        raise HTTPException(
            status_code=409,
            detail="an active run already exists for this automation and record",
        )

    await advance_run(tenant_id, run_id)

    async with tenant_tx(tenant_id) as conn:
        run = await get_run(conn, run_id)
    assert run is not None
    return _run_out(run)


@router.get("/automations/{automation_id}/runs", response_model=list[RunOut])
async def list_runs(
    automation_id: str,
    conn=Depends(tenant_conn),
    limit: int = Query(50, ge=1),
):
    automation_id = _valid_uuid(automation_id, "automation_id")
    limit = min(limit, _MAX_RUN_LIMIT)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select * from public.automation_runs
                where automation_id = %s
                order by created_at desc
                limit %s""",
            (automation_id, limit),
        )
        rows = await cur.fetchall()
    return [_run_out(r) for r in rows]


@router.get("/automation-runs/{run_id}", response_model=RunOut)
async def get_run_detail(run_id: str, conn=Depends(tenant_conn)):
    run_id = _valid_uuid(run_id, "run_id")
    row = await get_run(conn, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_out(row)


@router.post("/automation-runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run_endpoint(
    run_id: str,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
    user: dict = Depends(get_current_user),
):
    """Cancel an active run. A `waiting_approval` run with a still-pending action is
    cancelled *through* `approvals.reject_action` (the sanctioned seam — resolving
    action + task + run together); otherwise the run is flipped directly. 409 on a
    terminal run, 404 unknown."""
    run_id = _valid_uuid(run_id, "run_id")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, status from public.automation_runs where id = %s for update",
            (run_id,),
        )
        run = await cur.fetchone()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run["status"] in _TERMINAL_STATES:
        raise HTTPException(
            status_code=409, detail=f"this run is already {run['status']}"
        )

    # waiting_approval with a live pending action -> go through the approvals seam so
    # the action + task + run all resolve in one place (its 7b hook cancels the run).
    action_id = None
    if run["status"] == "waiting_approval":
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id from public.pending_actions "
                "where automation_run_id = %s and status = 'pending' limit 1",
                (run_id,),
            )
            pa = await cur.fetchone()
        action_id = str(pa["id"]) if pa else None

    if action_id is not None:
        try:
            await reject_action(
                conn, tenant_id, action_id,
                resolved_by=user.get("email"), note="Automation run cancelled by user",
            )
        except ActionAlreadyResolved:
            # Raced to resolved between our lock and reject — fall back to direct cancel.
            await cancel_run(conn, tenant_id, run_id, resolved_by=user.get("email"))
    else:
        await cancel_run(conn, tenant_id, run_id, resolved_by=user.get("email"))

    row = await get_run(conn, run_id)
    assert row is not None
    return _run_out(row)
