"""Chat turn orchestrator — an agentic tool loop (Module 2).

The turn is now a bounded loop over the Anthropic Messages API with `tools`:

    persist user message -> load history
    loop (<= MAX_ITERS):
        stream a model response
        if it ends in tool_use:
            persist the assistant message (content blocks verbatim)
            run each tool through execute_tool (audited), emit SSE tool events
            persist one user message carrying the tool_result blocks
            continue
        else (end_turn): stream the final answer text, persist, finish

Retrieval is no longer injected per turn — it's the `search_documents` tool the
model routes to. The system prompt is a single static block (persona + routing
guidance) cached with `cache_control`; the tools array is cached too (breakpoint
on its last entry). Messages are stored as Anthropic content-block JSON verbatim,
so `tool_use`/`tool_result` blocks replay on later turns with no schema change.

SSE contract (additive over Module 1):
    start -> (tool, tool_result)* -> citations -> text* -> done   (or error)
`citations` is emitted once, right before the final text stream, aggregating all
search_documents passages this turn (turn-global [n] numbering). No raw JSON,
SQL, or tool payloads ever reach a user-facing field — only plain-language
`summary`/`label` strings do.
"""
from __future__ import annotations

import json

from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..config import settings
from ..db import tenant_tx
from ..llm import get_anthropic, traceable
from .events import log_event
from .tools import anthropic_tool_defs, execute_tool

MAX_TOKENS = 2048
MAX_ITERS = 5

PERSONA = (
    "You are Nexus, the operational assistant for a business control center. You "
    "help non-technical staff by answering questions about their business data.\n\n"
    "You have four kinds of tools:\n"
    "- Structured lookup tools for specific records and filtered lists — use these "
    "for questions about particular entities or lists.\n"
    "- search_documents for questions answerable from uploaded documents; cite the "
    "passages it returns inline with their bracketed numbers like [1], and cite "
    "only the ones that actually support your statement.\n"
    "- run_report for aggregate or analytical questions (counts, breakdowns, "
    "group-bys) — it runs a single read-only SQL query.\n"
    "- Action tools that change records or send messages (update a lead/client "
    "status, create or cancel a scheduled visit, send an SMS or email), plus "
    "create_task for internal to-dos.\n\n"
    "Action tools that change records or send messages DO NOT run immediately: "
    "they are queued for a human to approve first. When you call one, the result "
    "tells you a task was created and is awaiting approval — report that plainly to "
    "the user (e.g. \"I've queued that for approval\"). NEVER tell the user the "
    "action already happened, was sent, or is done — it hasn't run yet. create_task "
    "is the exception: it takes effect immediately (an internal note, no outside "
    "effect).\n\n"
    "Choose the smallest set of tools that answers the question. If earlier "
    "conversation already contains the answer, respond directly without calling "
    "tools. Never expose raw JSON, SQL, or tool payloads in your reply — write "
    "plain language. If the tools return nothing relevant, say so plainly rather "
    "than guessing."
)

# Plain-language progress labels (D8). Args are only used to append a short,
# already-plain string (a search query or a report purpose) — never raw JSON.
TOOL_LABELS = {
    "search_documents": "Searching documents",
    "list_leads": "Looking up leads",
    "get_lead": "Looking up a lead",
    "list_clients": "Looking up clients",
    "get_client": "Looking up a client",
    "list_resources": "Looking up caregivers",
    "get_resource_availability": "Checking caregiver availability",
    "list_schedules": "Looking up schedules",
    "run_report": "Running a report",
    "update_lead_status": "Updating a lead",
    "update_client_status": "Updating a client",
    "create_schedule": "Scheduling a visit",
    "cancel_schedule": "Cancelling a visit",
    "create_task": "Creating a task",
    "send_sms": "Sending a text message",
    "send_email": "Sending an email",
}


class ThreadNotFound(Exception):
    pass


def _label(name: str, args: dict) -> str:
    base = TOOL_LABELS.get(name, f"Running {name}")
    if name == "search_documents" and isinstance(args.get("query"), str):
        return f"{base} for “{args['query'][:60]}”…"
    if name == "run_report" and isinstance(args.get("purpose"), str):
        return f"{base}: {args['purpose'][:80]}"
    return base + "…"


def _public_source(s: dict) -> dict:
    """M1 Source shape for the citations event / persistence — drops chunk_text
    (which is only for the model's tool_result), keeps the snippet for the UI."""
    return {
        "n": s["n"],
        "document_id": s["document_id"],
        "filename": s["filename"],
        "chunk_id": s["chunk_id"],
        "chunk_index": s["chunk_index"],
        "snippet": s["snippet"],
    }


def _dump_content(blocks) -> list[dict]:
    """SDK content blocks -> input-valid dicts for verbatim persistence + replay.

    Thinking / redacted_thinking blocks (emitted before tool_use by reasoning
    models) MUST be preserved with their signature and kept ahead of the tool_use
    block, or the next request in the tool loop is rejected. Each known block type
    is mapped to its exact input schema; unknown types are dropped rather than
    replayed malformed."""
    out: list[dict] = []
    for b in blocks:
        btype = getattr(b, "type", None)
        if btype == "text":
            out.append({"type": "text", "text": b.text})
        elif btype == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif btype == "thinking":
            out.append({"type": "thinking", "thinking": b.thinking, "signature": b.signature})
        elif btype == "redacted_thinking":
            out.append({"type": "redacted_thinking", "data": b.data})
    return out


async def _persist_message(
    conn, tenant_id, thread_id, role, content, *, citations=None, metadata=None
) -> str:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.chat_messages
                 (tenant_id, thread_id, role, content, citations, metadata)
               values (%s,%s,%s,%s,%s,%s) returning id""",
            (
                tenant_id,
                thread_id,
                role,
                Json(content),
                Json(citations if citations is not None else []),
                Json(metadata or {}),
            ),
        )
        return str((await cur.fetchone())["id"])


@traceable(run_type="chain", name="chat_turn")
async def stream_chat_turn(tenant_id: str, thread_id: str, user_text: str):
    """Async generator yielding (event_name, data_dict) tuples for the router."""
    # 1. Verify thread, persist the user message, load full history.
    async with tenant_tx(tenant_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id from public.chat_threads where id=%s", (thread_id,)
            )
            if await cur.fetchone() is None:
                raise ThreadNotFound(thread_id)

            await cur.execute(
                """insert into public.chat_messages (tenant_id, thread_id, role, content)
                   values (%s,%s,'user',%s) returning id""",
                (tenant_id, thread_id, Json([{"type": "text", "text": user_text}])),
            )
            user_message_id = str((await cur.fetchone())["id"])

            await cur.execute(
                """select role, content from public.chat_messages
                   where thread_id=%s order by seq""",
                (thread_id,),
            )
            messages = [
                {"role": r["role"], "content": r["content"]} for r in await cur.fetchall()
            ]
        await conn.execute(
            "update public.chat_threads set updated_at=now() where id=%s", (thread_id,)
        )

    yield "start", {"thread_id": thread_id, "user_message_id": user_message_id}

    # 2. Agentic loop.
    client = get_anthropic()
    system = [{"type": "text", "text": PERSONA, "cache_control": {"type": "ephemeral"}}]
    tool_defs = anthropic_tool_defs()

    agg_sources: list[dict] = []  # turn-global, public shape (UI + persistence)
    tool_calls_meta: list[dict] = []  # for metadata.tool_calls (history reload)
    usage_in = usage_out = 0
    citations_sent = False
    final = None

    for i in range(MAX_ITERS):
        is_last = i == MAX_ITERS - 1
        # Force an answer (no more tool calls) on the last allowed step. Extended
        # thinking is disabled: this module only needs tool routing, and streamed
        # thinking blocks are fragile to persist and replay verbatim across the
        # tool loop (empty-block / signature edge cases).
        tool_choice = {"type": "none"} if is_last else {"type": "auto"}
        async with client.messages.stream(
            model=settings.chat_model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tool_defs,
            tool_choice=tool_choice,
            messages=messages,
            # Disabled via extra_body: the pinned SDK (0.44) predates the native
            # `thinking` param, but these models think by default and the old SDK
            # drops streamed thinking content, breaking verbatim replay.
            extra_body={"thinking": {"type": "disabled"}},
        ) as stream:
            async for delta in stream.text_stream:
                if not citations_sent:
                    yield "citations", {"sources": agg_sources}
                    citations_sent = True
                yield "text", {"delta": delta}
            final = await stream.get_final_message()

        usage_in += final.usage.input_tokens
        usage_out += final.usage.output_tokens

        if final.stop_reason != "tool_use":
            break

        # --- tool-use turn: run tools, persist assistant + tool_result messages ---
        tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
        tool_result_blocks: list[dict] = []
        async with tenant_tx(tenant_id) as tconn:
            for b in tool_uses:
                model_input = b.input if isinstance(b.input, dict) else {}
                exec_args = dict(model_input)
                if b.name == "search_documents":
                    exec_args["start_index"] = len(agg_sources)

                yield "tool", {
                    "name": b.name,
                    "label": _label(b.name, model_input),
                    "tool_use_id": b.id,
                }

                result = await execute_tool(
                    tconn, tenant_id, b.name, exec_args, source_system="chat"
                )

                ev_sources = None
                if b.name == "search_documents" and not result.is_error:
                    pub = [_public_source(s) for s in result.data.get("sources", [])]
                    agg_sources.extend(pub)
                    ev_sources = pub

                # A gated call returns a non-error "queued" result; flag it so the
                # UI can render the chip distinctly (additive SSE field, and stored
                # on metadata.tool_calls so it survives a history reload).
                queued = (
                    isinstance(result.data, dict)
                    and result.data.get("status") == "queued"
                )

                tool_calls_meta.append({
                    "name": b.name,
                    "summary": result.summary,
                    "is_error": result.is_error,
                    "queued": queued,
                })
                yield "tool_result", {
                    "tool_use_id": b.id,
                    "summary": result.summary,
                    "is_error": result.is_error,
                    "sources": ev_sources,
                    "queued": queued,
                }

                block = {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": json.dumps(result.data),
                }
                if result.is_error:
                    block["is_error"] = True
                tool_result_blocks.append(block)

            assistant_content = _dump_content(final.content)
            await _persist_message(tconn, tenant_id, thread_id, "assistant", assistant_content)
            await _persist_message(tconn, tenant_id, thread_id, "user", tool_result_blocks)

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_result_blocks})

    # 3. Persist the final assistant answer + audit event.
    final_content = _dump_content(final.content) if final and final.content else [
        {"type": "text", "text": ""}
    ]
    usage = {"input_tokens": usage_in, "output_tokens": usage_out}
    metadata = {"usage": usage, "model": settings.chat_model, "tool_calls": tool_calls_meta}
    async with tenant_tx(tenant_id) as conn:
        assistant_message_id = await _persist_message(
            conn, tenant_id, thread_id, "assistant", final_content,
            citations=agg_sources, metadata=metadata,
        )
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="chat",
            event_type="chat.message.completed",
            entity_type="chat_thread",
            entity_id=thread_id,
            payload={
                "assistant_message_id": assistant_message_id,
                "usage": usage,
                "tool_calls": [
                    {"name": t["name"], "summary": t["summary"]} for t in tool_calls_meta
                ],
            },
        )

    if not citations_sent:
        yield "citations", {"sources": agg_sources}
        citations_sent = True
    yield "done", {"assistant_message_id": assistant_message_id, "usage": usage}
