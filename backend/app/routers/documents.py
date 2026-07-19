"""Documents / ingestion API.

POST   /api/documents        multipart upload -> 202, kicks off background pipeline
GET    /api/documents        list documents (newest first; optional entity filter)
GET    /api/documents/{id}   document + chunk previews
DELETE /api/documents/{id}   remove row (chunks cascade) + Storage object

An upload may carry an OPTIONAL canonical-entity tag (`entity_type` + `entity_id`
form fields, M16a) — the one sanctioned way to associate a document with a record,
e.g. a client's care plan. The tag is validated against the vertical's entity map
(`services/automations/entities.ENTITY_TABLES`) and the row must actually exist
under the tenant's RLS, so a typo is a 422 rather than a dangling reference.
Chunks inherit the tag, so retrieval and the profile's document list agree.
Untagged uploads keep today's behavior exactly — tenant-general knowledge.
"""
from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from psycopg.rows import dict_row

from ..db import tenant_tx
from ..deps import get_tenant_id
from ..schemas import ChunkPreview, DocumentDetail, DocumentOut
from ..services import storage
from ..services.automations.entities import ENTITY_TABLES
from ..services.events import log_event
from ..services.ingestion import process_document
from ..services.parsing import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/api/documents", tags=["documents"])

_PREVIEW_CHARS = 280


def _document_out(row: dict) -> DocumentOut:
    return DocumentOut(
        id=str(row["id"]),
        filename=row["filename"],
        mime_type=row["mime_type"],
        status=row["status"],
        error=row["error"],
        storage_path=row["storage_path"],
        entity_type=row.get("entity_type"),
        entity_id=str(row["entity_id"]) if row.get("entity_id") else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def _validate_entity_tag(
    conn, entity_type: str | None, entity_id: str | None
) -> tuple[str | None, str | None]:
    """Validate an optional entity tag. Both fields or neither; the type must be a
    key of the vertical's entity map and the row must exist for this tenant (the
    select is RLS-scoped, so another tenant's id reads as 'not found'). Returns the
    normalized pair, or (None, None) for an untagged upload."""
    if not entity_type and not entity_id:
        return None, None
    if not entity_type or not entity_id:
        raise HTTPException(
            status_code=422,
            detail="entity_type and entity_id must be provided together",
        )
    table = ENTITY_TABLES.get(entity_type)
    if table is None:
        raise HTTPException(
            status_code=422,
            detail=f"entity_type must be one of: {', '.join(sorted(ENTITY_TABLES))}",
        )
    try:
        eid = str(uuid.UUID(str(entity_id)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail="entity_id must be a valid id")
    async with conn.cursor() as cur:
        await cur.execute(f"select 1 from public.{table} where id = %s", (eid,))
        if await cur.fetchone() is None:
            raise HTTPException(status_code=422, detail=f"{entity_type} not found")
    return entity_type, eid


@router.post("", status_code=202, response_model=DocumentOut)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    entity_type: str | None = Form(None),
    entity_id: str | None = Form(None),
    tenant_id: str = Depends(get_tenant_id),
):
    filename = file.filename or "upload"
    if _extension(filename) not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    # 1. Create the document row (status 'uploaded'), committed before the upload.
    #    The entity tag is validated first, so a bad tag never leaves a half-made
    #    document or an orphaned Storage object behind.
    async with tenant_tx(tenant_id) as conn:
        tag_type, tag_id = await _validate_entity_tag(conn, entity_type, entity_id)
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """insert into public.documents
                     (tenant_id, filename, mime_type, status, entity_type, entity_id)
                   values (%s, %s, %s, 'uploaded', %s, %s)
                   returning *""",
                (tenant_id, filename, file.content_type, tag_type, tag_id),
            )
            row = await cur.fetchone()
    document_id = str(row["id"])
    path = storage.object_path(tenant_id, document_id, filename)

    # 2. Upload original bytes to private Storage (service-role, documented).
    try:
        await storage.upload(path, data, file.content_type)
    except Exception as exc:  # noqa: BLE001
        async with tenant_tx(tenant_id) as conn:
            await conn.execute(
                "update public.documents set status='failed', error=%s where id=%s",
                (f"storage upload failed: {exc}", document_id),
            )
        raise HTTPException(status_code=502, detail="Storage upload failed") from exc

    # 3. Record storage_path + the uploaded event.
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "update public.documents set storage_path=%s where id=%s returning *",
                (path, document_id),
            )
            row = await cur.fetchone()
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="ingestion",
            event_type="document.uploaded",
            entity_type="document",
            entity_id=document_id,
            payload={"filename": filename, "bytes": len(data)},
        )

    # 4. Process in the background (parse -> chunk -> embed -> ready). The entity
    #    tag rides along so every chunk inherits it.
    background.add_task(
        process_document, document_id, tenant_id, filename, data,
        entity_type=tag_type, entity_id=tag_id,
    )
    return _document_out(row)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    tenant_id: str = Depends(get_tenant_id),
    entity_type: str | None = None,
    entity_id: str | None = None,
):
    """All documents, newest first. Pass `entity_type` + `entity_id` to list only
    the documents tagged to one record (the client profile's documents card)."""
    where, params = "", {}
    if entity_type and entity_id:
        try:
            eid = str(uuid.UUID(str(entity_id)))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=422, detail="entity_id must be a valid id")
        where = " where entity_type = %(etype)s and entity_id = %(eid)s::uuid"
        params = {"etype": entity_type, "eid": eid}
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"select * from public.documents{where} order by created_at desc",
                params,
            )
            rows = await cur.fetchall()
    return [_document_out(r) for r in rows]


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, tenant_id: str = Depends(get_tenant_id)):
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select * from public.documents where id=%s", (document_id,)
            )
            doc = await cur.fetchone()
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await cur.execute(
                """select chunk_index, chunk_text, metadata
                   from public.document_chunks
                   where document_id=%s order by chunk_index""",
                (document_id,),
            )
            chunk_rows = await cur.fetchall()

    chunks = [
        ChunkPreview(
            chunk_index=c["chunk_index"],
            chunk_text=(c["chunk_text"][:_PREVIEW_CHARS]),
            metadata=c["metadata"],
        )
        for c in chunk_rows
    ]
    base = _document_out(doc)
    return DocumentDetail(
        **base.model_dump(), chunk_count=len(chunk_rows), chunks=chunks
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: str, tenant_id: str = Depends(get_tenant_id)):
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select storage_path from public.documents where id=%s", (document_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await conn.execute("delete from public.documents where id=%s", (document_id,))
    # Remove the Storage object after the row is gone (best-effort).
    if row["storage_path"]:
        try:
            await storage.remove(row["storage_path"])
        except Exception:  # noqa: BLE001 — object may already be absent
            pass
    return None
