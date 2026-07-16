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
