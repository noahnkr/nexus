"""CORE tool — `search_communications` (v1.1.0). The communications-tier mirror of
`search_documents`, kept a SEPARATE tool on purpose: the curated document corpus
stays pristine, and the agent picks the tool by whether the question is about
uploaded files or about what was said in past conversations.

Safe/read-only. Wraps `retrieval.retrieve_communications`. Citation numbering is
turn-global via the injected `start_index` (the model never sets it), identical to
`search_documents`.
"""
from __future__ import annotations

from ..retrieval import retrieve_communications
from .core import ToolDef, ToolInputError, ToolResult
from .registry import register

SNIPPET_CHARS = 200
MAX_TOP_K = 8

_CHANNEL_LABELS = {
    "call": "Call",
    "email": "Email",
    "sms": "Text",
    "note": "Note",
    "other": "Message",
}


def _label(channel: str | None, occurred_at) -> str:
    """A plain-language source label — "Call · Jul 3, 2026" — for the citation."""
    name = _CHANNEL_LABELS.get(channel or "", "Message")
    when = ""
    if occurred_at is not None:
        try:
            # Built by hand rather than %-d/%#d — those directives are
            # platform-specific (fail on Windows, where the tests run).
            when = f" · {occurred_at.strftime('%b')} {occurred_at.day}, {occurred_at.year}"
        except (ValueError, TypeError, AttributeError):
            when = ""
    return f"{name}{when}"


async def _search_communications(conn, args: dict) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolInputError("'query' is required to search communications.")
    try:
        top_k = int(args.get("top_k", MAX_TOP_K))
    except (ValueError, TypeError):
        top_k = MAX_TOP_K
    top_k = max(1, min(top_k, MAX_TOP_K))
    start = int(args.get("start_index", 0) or 0)

    chunks = await retrieve_communications(conn, query, limit=top_k)
    sources = [
        {
            "n": start + i,
            "kind": "communication",
            "communication_id": c["communication_id"],
            "label": _label(c.get("channel"), c.get("occurred_at")),
            "channel": c.get("channel"),
            "source": c.get("source"),
            "chunk_id": c["chunk_id"],
            "chunk_index": c["chunk_index"],
            "snippet": c["chunk_text"][:SNIPPET_CHARS],
            "chunk_text": c["chunk_text"],
        }
        for i, c in enumerate(chunks, start=1)
    ]
    if not sources:
        return ToolResult(
            f'No communications matched "{query}".', {"query": query, "sources": []}
        )
    return ToolResult(
        f'Found {len(sources)} communication passage(s) for "{query}".',
        {"query": query, "sources": sources},
    )


register(ToolDef(
    name="search_communications",
    description=(
        "Search past communications (calls, emails, text messages, notes) by "
        "semantic similarity and return the most relevant passages, numbered for "
        "citation. Use for questions about what was said or discussed with someone "
        "— as opposed to search_documents, which searches uploaded files (care "
        "plans, policies). Cite the passages you use with their [n]."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search past communications for.",
            },
            "top_k": {
                "type": "integer",
                "default": MAX_TOP_K,
                "maximum": MAX_TOP_K,
                "description": "Maximum passages to return (1–8).",
            },
        },
        "required": ["query"],
    },
    handler=_search_communications,
))
