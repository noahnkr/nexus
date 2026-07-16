"""Supabase Storage access for original uploads.

Uses the service-role key — a documented, Storage-only exception to the "never
use the service role for data access" rule (per-tenant Storage RLS is deferred to
Module 6). Objects live at {tenant_id}/{document_id}/{filename} in the private
`documents` bucket. The storage3 client is synchronous, so calls are pushed to a
threadpool to avoid blocking the event loop.
"""
from __future__ import annotations

from fastapi.concurrency import run_in_threadpool

from ..config import settings

BUCKET = "documents"
_client = None


def _storage():
    global _client
    if _client is None:
        from supabase import create_client

        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        _client = client.storage
    return _client


def object_path(tenant_id: str, document_id: str, filename: str) -> str:
    return f"{tenant_id}/{document_id}/{filename}"


async def upload(path: str, data: bytes, content_type: str | None) -> None:
    def _do():
        _storage().from_(BUCKET).upload(
            path,
            data,
            {"content-type": content_type or "application/octet-stream", "upsert": "true"},
        )

    await run_in_threadpool(_do)


async def remove(path: str) -> None:
    def _do():
        _storage().from_(BUCKET).remove([path])

    await run_in_threadpool(_do)
