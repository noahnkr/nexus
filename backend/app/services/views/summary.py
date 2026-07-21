"""View-agnostic smart-summary helper (Module 9a; M10 reuses it for applicants).

One `settings.fast_model` call over an entity row + its recent timeline, producing
a few sentences of plain prose for a profile's "at a glance" card. On-demand only:
NOTHING here persists — the router generates fresh on each profile open (a
user-locked decision: no cache columns, no invalidation machinery).

The vertical caller supplies the two things that carry meaning — a `prompt_intro`
(what the summary should say for this entity kind) and a `span_name` (so the trace
reads `lead_summary`, not a generic name). Everything else — event loading, the
plain-language derivation, the model call, the chain span — is generic here.

Prose output, not a structured Pydantic call: the CLAUDE.md structured-output rule
governs machine-read JSON, and this is human-facing text (draft.py precedent for
the 503-without-key shape).
"""
from __future__ import annotations

from datetime import datetime, timezone

from psycopg.rows import dict_row

from ...config import settings
from ...llm import get_anthropic, traceable
from ..event_summaries import summarize_event

_MAX_EVENTS = 20
_MAX_TOKENS = 400


class SummaryUnavailable(Exception):
    """No Anthropic key configured — the router maps this to a 503 with a plain
    message so a profile still renders (the summary card shows a quiet notice)."""


def _stringify(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _record_block(entity_row: dict) -> str:
    """Flatten the entity row into readable `field: value` lines. Skips plumbing
    columns and empty values so the model sees only substantive facts."""
    skip = {"tenant_id", "id", "region_id"}
    lines = []
    for key, value in entity_row.items():
        if key in skip or value in (None, "", {}, []):
            continue
        lines.append(f"- {key}: {_stringify(value)}")
    return "\n".join(lines) if lines else "(no fields)"


async def _recent_activity(conn, entity_type: str, entity_id: str) -> list[str]:
    """The entity's last N events as plain-language lines (oldest first), each via
    the same read-time derivation the Event Log uses — plain language in, plain
    language out, no raw payloads reach the prompt."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select event_type, source_system, payload, created_at
                 from public.events
                where entity_type = %s and entity_id = %s
                order by created_at desc
                limit %s""",
            (entity_type, entity_id, _MAX_EVENTS),
        )
        rows = await cur.fetchall()
    rows.reverse()  # chronological reads better for a "what has happened" narrative
    return [
        f"{r['created_at'].date()}: "
        f"{summarize_event(r['event_type'], r['source_system'], r['payload'])}"
        for r in rows
    ]


async def generate_entity_summary(
    conn,
    *,
    entity_row: dict,
    entity_type: str,
    entity_id: str,
    prompt_intro: str,
    span_name: str,
) -> dict:
    """Generate an on-demand smart summary for one entity. Returns
    `{"summary": str, "generated_at": datetime}`. Raises SummaryUnavailable when no
    Anthropic key is configured."""
    if not settings.anthropic_api_key:
        raise SummaryUnavailable("summaries require an Anthropic API key")

    activity = await _recent_activity(conn, entity_type, entity_id)
    activity_block = "\n".join(activity) if activity else "(no recorded activity yet)"
    user_content = (
        f"Record:\n{_record_block(entity_row)}\n\n"
        f"Recent activity (oldest first):\n{activity_block}"
    )
    system = (
        f"{prompt_intro}\n\n"
        "Write 2-4 short sentences of plain prose for a busy office coordinator. "
        "No preamble, no bullet points, no headings — just the summary. Base it only "
        "on the facts provided; do not invent details."
    )

    @traceable(run_type="chain", name=span_name)
    async def _run() -> str:
        client = get_anthropic()
        response = await client.messages.create(
            model=settings.fast_model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        parts = [
            getattr(b, "text", "")
            for b in response.content
            if getattr(b, "type", None) == "text"
        ]
        return "".join(parts).strip()

    summary = await _run()
    return {"summary": summary, "generated_at": datetime.now(timezone.utc)}


# ---------------------------------------------------------------------------
# cache (WS7) — the summary is generated once and persisted; later reads serve the
# cached row, and a manual regenerate refreshes it. Generic (keyed by entity), so
# M10 reuses. Tenant scoping is by RLS on the connection; the upsert passes
# tenant_id explicitly so the stored value is never ambiguous (events.py pattern).
# ---------------------------------------------------------------------------
async def _read_cache(
    conn, entity_type: str, entity_id: str, kind: str = "smart_summary"
) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select summary, generated_at from public.entity_summaries "
            "where entity_type = %s and entity_id = %s and kind = %s",
            (entity_type, entity_id, kind),
        )
        row = await cur.fetchone()
    return {"summary": row["summary"], "generated_at": row["generated_at"]} if row else None


async def _write_cache(
    conn, tenant_id: str, entity_type: str, entity_id: str, result: dict,
    kind: str = "smart_summary",
) -> None:
    await conn.execute(
        """insert into public.entity_summaries
             (tenant_id, entity_type, entity_id, kind, summary, model, generated_at)
           values (%s, %s, %s, %s, %s, %s, %s)
           on conflict (tenant_id, entity_type, entity_id, kind) do update
             set summary = excluded.summary,
                 model = excluded.model,
                 generated_at = excluded.generated_at""",
        (tenant_id, entity_type, entity_id, kind, result["summary"],
         settings.fast_model, result["generated_at"]),
    )


async def get_or_generate_entity_summary(
    conn,
    tenant_id: str,
    *,
    entity_row: dict,
    entity_type: str,
    entity_id: str,
    prompt_intro: str,
    span_name: str,
) -> dict:
    """Return the cached summary if present; otherwise generate once, cache it, and
    return it. Cheap on repeat opens (no LLM call). Raises SummaryUnavailable only
    when nothing is cached AND no key is configured."""
    cached = await _read_cache(conn, entity_type, entity_id)
    if cached is not None:
        return cached
    result = await generate_entity_summary(
        conn, entity_row=entity_row, entity_type=entity_type, entity_id=entity_id,
        prompt_intro=prompt_intro, span_name=span_name,
    )
    await _write_cache(conn, tenant_id, entity_type, entity_id, result)
    return result


async def regenerate_entity_summary(
    conn,
    tenant_id: str,
    *,
    entity_row: dict,
    entity_type: str,
    entity_id: str,
    prompt_intro: str,
    span_name: str,
) -> dict:
    """Always generate a fresh summary and overwrite the cache (the manual Regenerate
    path). Raises SummaryUnavailable when no key is configured."""
    result = await generate_entity_summary(
        conn, entity_row=entity_row, entity_type=entity_type, entity_id=entity_id,
        prompt_intro=prompt_intro, span_name=span_name,
    )
    await _write_cache(conn, tenant_id, entity_type, entity_id, result)
    return result


# ===========================================================================
# Communication profile (v1.1.0) — tier-3 DERIVED KNOWLEDGE. A per-entity read of
# how someone communicates (tone, responsiveness, preferred channel, recurring
# topics), generated from their communications history and cached under the
# `comm_profile` kind alongside the smart summary. Tone/style is a summary
# problem, not a retrieval one — so this reads whole messages, it does not embed.
# ===========================================================================
COMM_PROFILE_KIND = "comm_profile"
_MAX_COMMS = 30

COMM_PROFILE_INTRO = (
    "You are profiling how one person communicates, for a coordinator who is about "
    "to reach out to them. From the messages below, describe their tone, how "
    "responsive they are, which channel they seem to prefer, and any recurring "
    "topics or requests. Base it only on the messages provided; do not invent "
    "details or give scheduling advice."
)


async def _recent_communications(conn, entity_type: str, entity_id: str) -> list[str]:
    """The entity's recent communications as plain lines (oldest first). Bodies are
    trimmed to a snippet so a long transcript can't dominate the prompt."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select channel, direction, occurred_at, body
                 from public.communications
                where entity_type = %s and entity_id = %s
                order by occurred_at desc
                limit %s""",
            (entity_type, entity_id, _MAX_COMMS),
        )
        rows = await cur.fetchall()
    rows.reverse()  # chronological reads better for a "how do they communicate" view
    lines = []
    for r in rows:
        body = " ".join((r["body"] or "").split())
        if len(body) > 300:
            body = body[:297].rstrip() + "…"
        direction = f" {r['direction']}" if r["direction"] else ""
        lines.append(f"{r['occurred_at'].date()} [{r['channel']}{direction}]: {body}")
    return lines


async def generate_comm_profile(
    conn, *, entity_type: str, entity_id: str, span_name: str = "comm_profile",
) -> dict:
    """Generate a communication profile from the entity's messages. Returns
    `{"summary": str, "generated_at": datetime}`. With no messages, returns a plain
    placeholder without calling the model. Raises SummaryUnavailable when messages
    exist but no Anthropic key is configured."""
    comms = await _recent_communications(conn, entity_type, entity_id)
    now = datetime.now(timezone.utc)
    if not comms:
        return {
            "summary": "No communications on record yet for this contact.",
            "generated_at": now,
        }
    if not settings.anthropic_api_key:
        raise SummaryUnavailable("communication profiles require an Anthropic API key")

    user_content = "Messages (oldest first):\n" + "\n".join(comms)
    system = (
        f"{COMM_PROFILE_INTRO}\n\n"
        "Write 2-4 short sentences of plain prose for a busy office coordinator. "
        "No preamble, no bullet points, no headings — just the profile."
    )

    @traceable(run_type="chain", name=span_name)
    async def _run() -> str:
        client = get_anthropic()
        response = await client.messages.create(
            model=settings.fast_model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        parts = [
            getattr(b, "text", "")
            for b in response.content
            if getattr(b, "type", None) == "text"
        ]
        return "".join(parts).strip()

    return {"summary": await _run(), "generated_at": now}


async def get_or_generate_comm_profile(
    conn, tenant_id: str, *, entity_type: str, entity_id: str,
) -> dict:
    """Cached comm profile, or generate + cache once. Cheap on repeat opens."""
    cached = await _read_cache(conn, entity_type, entity_id, COMM_PROFILE_KIND)
    if cached is not None:
        return cached
    result = await generate_comm_profile(
        conn, entity_type=entity_type, entity_id=entity_id,
    )
    await _write_cache(conn, tenant_id, entity_type, entity_id, result, COMM_PROFILE_KIND)
    return result


async def regenerate_comm_profile(
    conn, tenant_id: str, *, entity_type: str, entity_id: str,
) -> dict:
    """Always regenerate the comm profile and overwrite its cache row."""
    result = await generate_comm_profile(
        conn, entity_type=entity_type, entity_id=entity_id,
    )
    await _write_cache(conn, tenant_id, entity_type, entity_id, result, COMM_PROFILE_KIND)
    return result
