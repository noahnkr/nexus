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
async def _read_cache(conn, entity_type: str, entity_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select summary, generated_at from public.entity_summaries "
            "where entity_type = %s and entity_id = %s",
            (entity_type, entity_id),
        )
        row = await cur.fetchone()
    return {"summary": row["summary"], "generated_at": row["generated_at"]} if row else None


async def _write_cache(
    conn, tenant_id: str, entity_type: str, entity_id: str, result: dict
) -> None:
    await conn.execute(
        """insert into public.entity_summaries
             (tenant_id, entity_type, entity_id, summary, model, generated_at)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (tenant_id, entity_type, entity_id) do update
             set summary = excluded.summary,
                 model = excluded.model,
                 generated_at = excluded.generated_at""",
        (tenant_id, entity_type, entity_id, result["summary"],
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
