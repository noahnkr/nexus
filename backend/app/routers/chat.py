"""Chat API: threads CRUD + SSE streaming with RAG.

POST   /api/chat/threads                 create a thread
GET    /api/chat/threads                 list threads (newest activity first)
DELETE /api/chat/threads/{id}            delete a thread (messages cascade)
GET    /api/chat/threads/{id}/messages   full message history
POST   /api/chat/threads/{id}/messages   stream an assistant turn (text/event-stream)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row

from ..db import tenant_tx
from ..deps import get_tenant_id
from ..schemas import MessageCreate, MessageOut, ThreadCreate, ThreadOut
from ..services.chat_service import ThreadNotFound, stream_chat_turn

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _thread_out(row: dict) -> ThreadOut:
    return ThreadOut(
        id=str(row["id"]),
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/threads", response_model=ThreadOut, status_code=201)
async def create_thread(body: ThreadCreate, tenant_id: str = Depends(get_tenant_id)):
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "insert into public.chat_threads (tenant_id, title) values (%s,%s) returning *",
                (tenant_id, body.title),
            )
            row = await cur.fetchone()
    return _thread_out(row)


@router.get("/threads", response_model=list[ThreadOut])
async def list_threads(tenant_id: str = Depends(get_tenant_id)):
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select * from public.chat_threads order by updated_at desc"
            )
            rows = await cur.fetchall()
    return [_thread_out(r) for r in rows]


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, tenant_id: str = Depends(get_tenant_id)):
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "delete from public.chat_threads where id=%s returning id", (thread_id,)
            )
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Thread not found")
    return None


@router.get("/threads/{thread_id}/messages", response_model=list[MessageOut])
async def list_messages(thread_id: str, tenant_id: str = Depends(get_tenant_id)):
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id from public.chat_threads where id=%s", (thread_id,)
            )
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Thread not found")
            await cur.execute(
                """select id, role, content, citations, metadata, created_at
                   from public.chat_messages where thread_id=%s order by seq""",
                (thread_id,),
            )
            rows = await cur.fetchall()
    return [
        MessageOut(
            id=str(r["id"]),
            role=r["role"],
            content=r["content"],
            citations=r["citations"],
            metadata=r["metadata"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/threads/{thread_id}/messages")
async def post_message(
    thread_id: str, body: MessageCreate, tenant_id: str = Depends(get_tenant_id)
):
    async def event_stream():
        try:
            async for event, data in stream_chat_turn(tenant_id, thread_id, body.content):
                yield _sse(event, data)
        except ThreadNotFound:
            yield _sse("error", {"message": "Thread not found"})
        except Exception as exc:  # noqa: BLE001 — surface any failure to the client
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
