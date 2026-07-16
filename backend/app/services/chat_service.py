"""Chat turn orchestrator: persist user message -> retrieve -> generate (streamed)
-> persist assistant message. Yields structured events the router renders as SSE.

System prompt is two blocks: a static persona (cache_control ephemeral, so it is
cached as history grows) and a per-turn retrieved-context block (never cached, it
changes every turn). Message history is sent verbatim as stored (content-block
arrays), forward-compatible with Module 2 tool blocks.
"""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..config import settings
from ..llm import get_anthropic, traceable
from .events import log_event
from .retrieval import retrieve_chunks

MAX_TOKENS = 2048
SNIPPET_CHARS = 200

PERSONA = (
    "You are Nexus, the operational assistant for a business control center. "
    "You answer questions using the retrieved context provided with each turn. "
    "The context is a numbered list of sources like [1], [2]. When a statement is "
    "supported by a source, cite it inline with its bracketed number, e.g. "
    "\"morning visits are scheduled [2]\". Cite only sources that actually support "
    "the claim. If the retrieved context does not contain the answer, say so plainly "
    "rather than guessing. Be concise and factual."
)


class ThreadNotFound(Exception):
    pass


def _context_block(chunks: list[dict]) -> str:
    if not chunks:
        return "Retrieved context: (none found for this query)."
    parts = [f"[{i}] {c['filename']}\n{c['chunk_text']}" for i, c in enumerate(chunks, 1)]
    return "Retrieved context for this turn:\n\n" + "\n\n".join(parts)


def _sources(chunks: list[dict]) -> list[dict]:
    return [
        {
            "n": i,
            "document_id": c["document_id"],
            "filename": c["filename"],
            "chunk_id": c["chunk_id"],
            "chunk_index": c["chunk_index"],
            "snippet": c["chunk_text"][:SNIPPET_CHARS],
        }
        for i, c in enumerate(chunks, 1)
    ]


@traceable(run_type="chain", name="chat_turn")
async def stream_chat_turn(tenant_id: str, thread_id: str, user_text: str):
    """Async generator yielding (event_name, data_dict) tuples."""
    from ..db import tenant_tx

    # 1. Verify thread, persist the user message, load full history.
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id from public.chat_threads where id=%s", (thread_id,)
            )
            if await cur.fetchone() is None:
                raise ThreadNotFound(thread_id)

            user_content = [{"type": "text", "text": user_text}]
            await cur.execute(
                """insert into public.chat_messages (tenant_id, thread_id, role, content)
                   values (%s,%s,'user',%s) returning id""",
                (tenant_id, thread_id, Json(user_content)),
            )
            user_message_id = str((await cur.fetchone())["id"])

            await cur.execute(
                """select role, content from public.chat_messages
                   where thread_id=%s order by seq""",
                (thread_id,),
            )
            history = [
                {"role": r["role"], "content": r["content"]} for r in await cur.fetchall()
            ]
        # Touch the thread so it sorts to the top of the list.
        await conn.execute(
            "update public.chat_threads set updated_at=now() where id=%s", (thread_id,)
        )

    yield "start", {"thread_id": thread_id, "user_message_id": user_message_id}

    # 2. Retrieve context (RLS-scoped).
    async with tenant_tx(tenant_id) as conn:
        chunks = await retrieve_chunks(conn, user_text)
    sources = _sources(chunks)
    yield "citations", {"sources": sources}

    # 3. Generate, streaming tokens.
    system = [
        {"type": "text", "text": PERSONA, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _context_block(chunks)},
    ]
    client = get_anthropic()
    assistant_text = ""
    usage: dict = {}
    async with client.messages.stream(
        model=settings.chat_model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=history,
    ) as stream:
        async for delta in stream.text_stream:
            assistant_text += delta
            yield "text", {"delta": delta}
        final = await stream.get_final_message()
        usage = {
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
        }

    # 4. Persist the assistant message + audit event.
    assistant_content = [{"type": "text", "text": assistant_text}]
    metadata = {"usage": usage, "model": settings.chat_model}
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """insert into public.chat_messages
                     (tenant_id, thread_id, role, content, citations, metadata)
                   values (%s,%s,'assistant',%s,%s,%s) returning id""",
                (
                    tenant_id,
                    thread_id,
                    Json(assistant_content),
                    Json(sources),
                    Json(metadata),
                ),
            )
            assistant_message_id = str((await cur.fetchone())["id"])
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="chat",
            event_type="chat.message.completed",
            entity_type="chat_thread",
            entity_id=thread_id,
            payload={"assistant_message_id": assistant_message_id, "usage": usage},
        )

    yield "done", {"assistant_message_id": assistant_message_id, "usage": usage}
