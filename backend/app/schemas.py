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
