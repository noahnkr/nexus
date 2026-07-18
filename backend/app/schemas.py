"""Pydantic response/request models for the API layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


# --- Documents / ingestion ---------------------------------------------------
class DocumentOut(BaseModel):
    id: str
    filename: str
    mime_type: str | None = None
    status: str
    error: str | None = None
    storage_path: str | None = None
    created_at: datetime
    updated_at: datetime


class ChunkPreview(BaseModel):
    chunk_index: int
    chunk_text: str
    metadata: dict[str, Any] = {}


class DocumentDetail(DocumentOut):
    chunk_count: int
    chunks: list[ChunkPreview] = []


# --- Chat --------------------------------------------------------------------
class ThreadOut(BaseModel):
    id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class ThreadCreate(BaseModel):
    title: str | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: list[dict[str, Any]]
    citations: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    created_at: datetime


class MessageCreate(BaseModel):
    content: str


# --- Event Log ---------------------------------------------------------------
class EventOut(BaseModel):
    id: str
    created_at: datetime
    source_system: str
    event_type: str
    entity_type: str | None = None
    entity_id: str | None = None
    summary: str  # derived at read time (services/event_summaries.py)
    payload: dict[str, Any] = {}  # raw jsonb — the sanctioned technical detail


class EventPage(BaseModel):
    events: list[EventOut] = []
    next_cursor: str | None = None


class EventFacets(BaseModel):
    source_systems: list[str] = []
    event_types: list[str] = []


# --- Leads view (Module 9, vertical seam) ------------------------------------
class RegionRef(BaseModel):
    id: str
    name: str


class LeadOut(BaseModel):
    id: str
    name: str
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    status: str  # new | contacted | qualified | converted | lost (leads.status)
    region_id: str | None = None
    region_name: str | None = None  # left-joined from regions
    requirements: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime


class LeadPage(BaseModel):
    leads: list[LeadOut] = []
    total: int = 0  # full count for the filtered set (offset paging in the UI)


class LeadFacets(BaseModel):
    sources: list[str] = []  # distinct non-null lead sources
    regions: list[RegionRef] = []  # for the create/edit selector + source filter


class LeadCreate(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    region_id: str | None = None


class LeadPatch(BaseModel):
    """Partial update. Only fields present in the request body are written (the
    router reads `model_fields_set`), so region_id can be explicitly cleared to
    null while an omitted field is left untouched. A `status` change emits
    lead.stage_changed; other field changes emit one lead.updated."""
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    region_id: str | None = None
    status: str | None = None


class LeadSummaryOut(BaseModel):
    """On-demand AI smart summary — generated per profile open, never persisted."""
    summary: str
    generated_at: datetime


class StageCount(BaseModel):
    stage: str
    count: int


class SourceCount(BaseModel):
    source: str
    count: int


class LeadMetrics(BaseModel):
    """Funnel dashboard widgets (9b). All five stages, zero-filled; conversion rate
    as a percent; avg_days_to_convert null when none observed."""
    stages: list[StageCount] = []
    conversion_rate: float = 0.0
    new_last_7_days: int = 0
    avg_days_to_convert: float | None = None
    top_sources: list[SourceCount] = []


# --- Caregivers view (Module 10, vertical seam) ------------------------------
class QualificationRef(BaseModel):
    id: str
    name: str


class ApplicantOut(BaseModel):
    id: str
    name: str
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    stage: str  # applied|screening|interview|offer|hired|rejected (applicants.stage)
    qualification_ids: list[str] = []
    region_ids: list[str] = []
    qualification_names: list[str] = []  # resolved from qualifications
    region_names: list[str] = []  # resolved from regions
    availability: dict[str, Any] = {}
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    # Set only on the PATCH response that moved an applicant to `hired` and created
    # a caregiver — the profile's hired banner names it. Null on every read.
    promoted_resource_id: str | None = None
    promoted_resource_name: str | None = None


class ApplicantPage(BaseModel):
    applicants: list[ApplicantOut] = []
    total: int = 0


class ApplicantFacets(BaseModel):
    sources: list[str] = []  # distinct non-null applicant sources
    regions: list[RegionRef] = []  # create/edit selector
    qualifications: list[QualificationRef] = []  # create/edit selector


class ApplicantCreate(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    qualification_ids: list[str] = []
    region_ids: list[str] = []


class ApplicantPatch(BaseModel):
    """Partial update. Only fields present in the request body are written (the
    router reads `model_fields_set`). A `stage` change routes through
    views/caregivers.move_stage() (emits applicant.stage_changed + hired-promotion);
    other field changes emit one applicant.updated."""
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    notes: str | None = None
    qualification_ids: list[str] | None = None
    region_ids: list[str] | None = None
    stage: str | None = None


class ApplicantSummaryOut(BaseModel):
    """On-demand AI hiring summary — generated per profile open, never persisted."""
    summary: str
    generated_at: datetime


class ApplicantMetrics(BaseModel):
    """Hiring funnel dashboard widgets (10b). All six stages, zero-filled; hire rate
    as a percent; avg_days_to_hire null when none observed."""
    stages: list[StageCount] = []
    hire_rate: float = 0.0
    new_last_7_days: int = 0
    avg_days_to_hire: float | None = None
    top_sources: list[SourceCount] = []


# --- Tasks & approvals -------------------------------------------------------
class PendingActionOut(BaseModel):
    id: str
    tool_name: str
    tool_input: dict[str, Any] = {}  # UI expandable technical detail only
    status: str  # pending | approved | rejected | executed | failed
    source_system: str
    result: dict[str, Any] | None = None  # {summary, error?} once resolved
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class TaskOut(BaseModel):
    id: str
    title: str
    description: str | None = None
    status: str  # pending | in_progress | done | cancelled
    priority: str  # low | normal | high | urgent
    originating_event_id: str | None = None
    assigned_to: str | None = None
    due_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    pending_actions: list[PendingActionOut] = []


class TaskPage(BaseModel):
    tasks: list[TaskOut] = []
    next_cursor: str | None = None


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    priority: str = "normal"
    due_at: datetime | None = None


class TaskPatch(BaseModel):
    status: str


class RejectBody(BaseModel):
    note: str | None = None


class ActionResolution(BaseModel):
    action: PendingActionOut
    task: TaskOut


# --- Home summary ------------------------------------------------------------
class DocumentCounts(BaseModel):
    ready: int = 0
    processing: int = 0
    failed: int = 0


class AutomationHomeCounts(BaseModel):
    """Home widget counts for automations (Module 8a). `failed_today` drives the
    card's warning tone client-side."""
    active: int = 0
    runs_today: int = 0
    failed_today: int = 0


class HomeSummary(BaseModel):
    """At-a-glance counts for the Home landing widgets. Read-only, business-agnostic
    (core tables only), RLS-scoped."""
    open_tasks: int = 0
    pending_approvals: int = 0
    documents: DocumentCounts = DocumentCounts()
    events_today: int = 0
    automations: AutomationHomeCounts = AutomationHomeCounts()


# --- Automations -------------------------------------------------------------
# trigger/conditions/steps ride as raw JSON: the engine's `validate_recipe` is the
# schema authority (plain-language 422s), and M8's builder renders the recipe shape.
class AutomationCreate(BaseModel):
    name: str
    description: str | None = None
    trigger: dict[str, Any]
    conditions: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    # Optional generic view-binding (Module 9b): {"view": …, "stage": …}. Validated
    # for shape only server-side; a duplicate (view, stage) is a 409.
    binding: dict[str, Any] | None = None


class AutomationPatch(BaseModel):
    """Partial update. A present trigger/conditions/steps triggers revalidation of
    the merged recipe; `status` flips active/paused. `binding` is
    model_fields_set-gated by the router: omit to leave unchanged, send null to
    clear, send an object to (re)bind."""
    name: str | None = None
    description: str | None = None
    status: str | None = None
    trigger: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] | None = None
    binding: dict[str, Any] | None = None


class LastRun(BaseModel):
    """The most recent run's at-a-glance state for the grid card (Module 8a)."""
    status: str
    at: datetime


class AutomationOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str  # active | paused
    trigger: dict[str, Any]
    conditions: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    next_fire_at: datetime | None = None
    created_by: str | None = None
    active_runs: int = 0  # runs currently running/waiting/waiting_approval
    last_run: LastRun | None = None  # newest run's status + time (grid card)
    requires_approval: bool = False  # any step calls a gated (unsafe) tool
    binding: dict[str, Any] | None = None  # generic view-binding (9b), null = unbound
    created_at: datetime
    updated_at: datetime


class RunOut(BaseModel):
    id: str
    automation_id: str
    status: str  # running | waiting | waiting_approval | completed | failed | cancelled
    trigger_event_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    context: dict[str, Any] = {}
    step_index: int = 0
    step_log: list[dict[str, Any]] = []  # per-step plain-language trail (M8 timeline)
    wake_at: datetime | None = None
    error: str | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RunNow(BaseModel):
    entity_type: str | None = None
    entity_id: str | None = None


# --- Automations builder vocabulary + drafting (Module 8b) --------------------
class VocabTool(BaseModel):
    name: str
    label: str  # plain-language name (shared with chat's activity labels)
    description: str
    input_schema: dict[str, Any]
    safe: bool  # False -> gated (requires approval); the builder shows the amber chip


class VocabFunction(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class VocabTriggers(BaseModel):
    event_types: list[str] = []
    source_systems: list[str] = []


# --- Field catalog (Module 11a) — trigger-aware, plain-language field knowledge --
class FieldRef(BaseModel):
    """One template-able field: the `{{path}}` and its plain-language label."""
    path: str
    label: str


class EntityFields(BaseModel):
    """A canonical entity's plain-language name + its `entity.*` fields."""
    label: str  # "Lead", "Applicant", … (from the vertical seam)
    fields: list[FieldRef] = []


class FieldCatalog(BaseModel):
    """Everything the builder needs to offer the RIGHT fields for the selected
    trigger, in plain language. `payload_by_event` + `event_entity` let 11b filter to
    the chosen trigger; `entities` keyed by type keeps a lead.created session from
    seeing applicant columns."""
    trigger_fields: list[FieldRef] = []  # 5 core trigger fields, static + labeled
    payload_by_event: dict[str, list[FieldRef]] = {}  # observed payload keys per event type
    entities: dict[str, EntityFields] = {}  # entity.* fields per entity type
    event_entity: dict[str, str] = {}  # event type -> the entity a run on it is about


class Vocabulary(BaseModel):
    """Everything the builder renders from — so new tools/functions/event types
    appear with zero frontend changes (the M9/M10 seam)."""
    triggers: VocabTriggers
    tools: list[VocabTool] = []
    functions: list[VocabFunction] = []
    operators: list[str] = []
    generate_models: list[str] = []
    field_roots: list[str] = []
    # Concrete `entity.*` / `trigger.*` dotted paths for the builder's field
    # autocomplete (WS2). Suggestions only — any path is still allowed.
    field_suggestions: list[str] = []
    # Trigger-aware, plain-language field knowledge (Module 11) — the structured
    # replacement 11b's token picker renders from. field_suggestions stays for the
    # interim FieldCombobox until 11b upgrades it.
    field_catalog: FieldCatalog = FieldCatalog()


class DraftRequest(BaseModel):
    description: str


class AutomationDraft(BaseModel):
    """An agent-drafted, UNSAVED recipe returned for human review in the builder.
    Never persisted by the agent (CLAUDE.md) — the standard create path saves it."""
    name: str
    description: str | None = None
    trigger: dict[str, Any]
    conditions: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    explanation: str
