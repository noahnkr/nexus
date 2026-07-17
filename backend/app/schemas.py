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


class AutomationPatch(BaseModel):
    """Partial update. A present trigger/conditions/steps triggers revalidation of
    the merged recipe; `status` flips active/paused."""
    name: str | None = None
    description: str | None = None
    status: str | None = None
    trigger: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] | None = None


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


class Vocabulary(BaseModel):
    """Everything the builder renders from — so new tools/functions/event types
    appear with zero frontend changes (the M9/M10 seam)."""
    triggers: VocabTriggers
    tools: list[VocabTool] = []
    functions: list[VocabFunction] = []
    operators: list[str] = []
    generate_models: list[str] = []
    field_roots: list[str] = []


class CronPreview(BaseModel):
    next: list[datetime] = []


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
