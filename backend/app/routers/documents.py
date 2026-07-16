"""Documents / ingestion API.

POST   /api/documents        multipart upload -> 202, kicks off background pipeline
GET    /api/documents        list documents (newest first)
GET    /api/documents/{id}   document + chunk previews
DELETE /api/documents/{id}   remove row (chunks cascade) + Storage object
"""
from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from psycopg.rows import dict_row

from ..db import tenant_tx
from ..deps import get_tenant_id
from ..schemas import ChunkPreview, DocumentDetail, DocumentOut
from ..services import storage
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.post("", status_code=202, response_model=DocumentOut)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
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
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """insert into public.documents (tenant_id, filename, mime_type, status)
                   values (%s, %s, %s, 'uploaded')
                   returning *""",
                (tenant_id, filename, file.content_type),
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

    # 4. Process in the background (parse -> chunk -> embed -> ready).
    background.add_task(process_document, document_id, tenant_id, filename, data)
    return _document_out(row)


@router.get("", response_model=list[DocumentOut])
async def list_documents(tenant_id: str = Depends(get_tenant_id)):
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select * from public.documents order by created_at desc"
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
