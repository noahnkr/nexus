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

Cancellation (M15a): the client can stop a turn mid-stream. Because history
replays verbatim to the Messages API, a thread must never be left ending on a
`user` message — so an aborted turn persists whatever text streamed (marked
`metadata.stopped`) on a fresh, shielded transaction before the generator dies.
"""
from __future__ import annotations

import asyncio
import json

from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..config import settings
from ..db import tenant_tx
from ..llm import get_anthropic, traceable
from .events import log_event
from .settings import get_settings
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
    "Match the shape of your answer to the request. For a conversational "
    "question, reply in short prose — no headings, no bullets. When the user asks "
    "for something document-like (a care plan, a summary they'll share, a "
    "comparison, a checklist, a breakdown), write it as a structured document in "
    "markdown: a short lead-in, "
    "## headings for sections, bullet lists for items, and GFM tables when you are "
    "presenting the same few attributes across several rows (people, visits, "
    "options). Keep tables to the columns that matter — narrow tables read well, "
    "very wide ones do not. Bold sparingly, for genuine labels.\n\n"
    "Choose the smallest set of tools that answers the question. If earlier "
    "conversation already contains the answer, respond directly without calling "
    "tools. Never expose raw JSON, SQL, or tool payloads in your reply — write "
    "plain language. If the tools return nothing relevant, say so plainly rather "
    "than guessing."
)

# Tenant preferences (M15b) reach the model as a SECOND system block, appended
# after PERSONA — never merged into it. The ordering is the safety property: the
# core persona (which carries the approval-gate rules and the no-raw-JSON rule)
# is always the model's first and highest-priority instruction, and the tenant's
# text is explicitly framed as subordinate to it. An owner can shape how the
# assistant sounds; they cannot talk it out of the gate.
TONE_SENTENCES = {
    "professional": "Keep your tone professional and businesslike.",
    "friendly": "Keep your tone warm and conversational.",
    "concise": "Be brief — short answers, minimal preamble.",
    "balanced": "",  # the default: no tone sentence at all
}

INSTRUCTIONS_PREAMBLE = (
    "The business owner has customized how you should respond. Follow these "
    "preferences where they don't conflict with the rules above:"
)


def build_system(tenant_settings: dict | None) -> list[dict]:
    """The system array for a turn: PERSONA, optionally followed by the tenant's
    preferences. `cache_control` sits on the LAST block so the whole system prefix
    is cached (the API caches up to each breakpoint; two text blocks plus the tools
    array stays well inside the 4-breakpoint budget)."""
    blocks: list[dict] = [{"type": "text", "text": PERSONA}]

    settings_dict = tenant_settings or {}
    instructions = (settings_dict.get("agent_instructions") or "").strip()
    tone = settings_dict.get("agent_tone") or "balanced"
    tone_sentence = TONE_SENTENCES.get(tone, "")

    if instructions or tone_sentence:
        parts = [INSTRUCTIONS_PREAMBLE]
        if tone_sentence:
            parts.append(tone_sentence)
        if instructions:
            parts.append(instructions)
        blocks.append({"type": "text", "text": "\n\n".join(parts)})

    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


# Plain-language progress labels (D8). Args are only used to append a short,
# already-plain string (a search query or a report purpose) — never raw JSON.
# The map lives in the tool layer (services/tools/labels.py) so chat and the
# automations vocabulary endpoint share one source of truth.
from .tools.labels import TOOL_LABELS  # noqa: E402


class ThreadNotFound(Exception):
    pass


# Text persisted for a turn the user stopped before any text streamed. It is NOT
# an empty block on purpose: chat history replays verbatim to the Messages API,
# which rejects empty text blocks in input messages — an empty block would break
# the very replay the stop contract exists to protect (M15a D4).
STOPPED_PLACEHOLDER = "(Response stopped.)"

# Strong references to in-flight stop-persistence tasks. The generator is being
# cancelled when these are spawned, so nothing else holds them; without this the
# loop may garbage-collect a task mid-write.
_stop_persists: set[asyncio.Task] = set()


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


async def _persist_stopped(
    tenant_id: str, thread_id: str, text: str, usage: dict, tool_calls: list[dict]
) -> None:
    """Close out a turn the client aborted mid-stream, on a FRESH transaction.

    The generator's own `tenant_tx` may be mid-rollback when cancellation lands, so
    this opens its own. Persisting here is what keeps the thread replayable: the
    history must never end on a `user` message, or the next turn's verbatim replay
    is rejected for non-alternating roles.
    """
    content = [{"type": "text", "text": text.strip() or STOPPED_PLACEHOLDER}]
    metadata = {
        "usage": usage,
        "model": settings.chat_model,
        "tool_calls": tool_calls,
        "stopped": True,
    }
    async with tenant_tx(tenant_id) as conn:
        message_id = await _persist_message(
            conn, tenant_id, thread_id, "assistant", content, metadata=metadata
        )
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="chat",
            event_type="chat.message.stopped",
            entity_type="chat_thread",
            entity_id=thread_id,
            payload={
                "summary": "Response stopped by the user",
                "assistant_message_id": message_id,
                "usage": usage,
            },
        )


def _spawn_stop_persist(*args) -> asyncio.Task:
    task = asyncio.ensure_future(_persist_stopped(*args))
    _stop_persists.add(task)
    task.add_done_callback(_stop_persists.discard)
    return task


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
        # Preferences are read once per turn, on the transaction we already have
        # open — they can't change mid-turn, and the tool loop shouldn't re-query.
        tenant_settings = await get_settings(conn)

    # 2. Agentic loop.
    client = get_anthropic()
    system = build_system(tenant_settings)
    tool_defs = anthropic_tool_defs()

    agg_sources: list[dict] = []  # turn-global, public shape (UI + persistence)
    tool_calls_meta: list[dict] = []  # for metadata.tool_calls (history reload)
    usage_in = usage_out = 0
    citations_sent = False
    final = None
    # Text streamed so far for the CURRENT model response, reset each iteration: a
    # tool-use iteration's text is persisted with its assistant message at the end
    # of that iteration, so only the in-flight response is at risk on an abort.
    streamed_text = ""

    # The guard opens BEFORE the first yield: the user message is already committed
    # by this point, so an abort anywhere from here on — including on the very first
    # frame — would otherwise leave the thread ending on a `user` message.
    try:
        yield "start", {"thread_id": thread_id, "user_message_id": user_message_id}

        for i in range(MAX_ITERS):
            is_last = i == MAX_ITERS - 1
            streamed_text = ""
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
                    streamed_text += delta
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

            # Nothing partial is written mid-iteration: the assistant tool_use message
            # and its tool_result reply commit together above, so a cancel landing
            # anywhere in this block still leaves an alternating history.
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_result_blocks})
    except (asyncio.CancelledError, GeneratorExit):
        # The client disconnected (Stop button): Starlette either cancels the task
        # running this generator (CancelledError at the suspended yield) or closes
        # the generator (GeneratorExit). Both mean "the turn ended early".
        #
        # Close the turn out on a fresh transaction — the outer one may be
        # mid-rollback — so the thread never ends on a `user` message. The await
        # can itself be cancelled again (anyio re-delivers cancellation at every
        # checkpoint); the shielded task keeps running on the loop either way.
        stop_task = _spawn_stop_persist(
            tenant_id,
            thread_id,
            streamed_text,
            {"input_tokens": usage_in, "output_tokens": usage_out},
            tool_calls_meta,
        )
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            pass
        raise

    # 3. Persist the final assistant answer + audit event.
    # Never an empty text block: input messages replay verbatim and the Messages
    # API rejects empty text, which would break every later turn on this thread.
    final_content = _dump_content(final.content) if final and final.content else [
        {"type": "text", "text": STOPPED_PLACEHOLDER}
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
