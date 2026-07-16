"""CORE tool — `search_documents`. Business-agnostic: retrieval becomes a tool the
model chooses to call, replacing Module 1's always-on per-turn context injection.

Wraps `retrieval.retrieve_chunks`. Citation numbering is turn-global: the chat
loop passes `start_index` (the number of citations already gathered this turn) so
a second search continues [9], [10]… instead of restarting at [1]. `start_index`
is injected by the loop and is deliberately absent from the input schema — the
model never sets it.
"""
from __future__ import annotations

from ..retrieval import retrieve_chunks
from .core import ToolDef, ToolInputError, ToolResult
from .registry import register

SNIPPET_CHARS = 200
MAX_TOP_K = 8


async def _search_documents(conn, args: dict) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolInputError("'query' is required to search documents.")
    try:
        top_k = int(args.get("top_k", MAX_TOP_K))
    except (ValueError, TypeError):
        top_k = MAX_TOP_K
    top_k = max(1, min(top_k, MAX_TOP_K))
    start = int(args.get("start_index", 0) or 0)

    chunks = await retrieve_chunks(conn, query, limit=top_k)
    sources = [
        {
            "n": start + i,
            "document_id": c["document_id"],
            "filename": c["filename"],
            "chunk_id": c["chunk_id"],
            "chunk_index": c["chunk_index"],
            "snippet": c["chunk_text"][:SNIPPET_CHARS],
            "chunk_text": c["chunk_text"],
        }
        for i, c in enumerate(chunks, start=1)
    ]
    if not sources:
        return ToolResult(
            f'No documents matched "{query}".', {"query": query, "sources": []}
        )
    return ToolResult(
        f'Found {len(sources)} document passage(s) for "{query}".',
        {"query": query, "sources": sources},
    )


register(ToolDef(
    name="search_documents",
    description=(
        "Search the uploaded document corpus (notes, policies, files) by semantic "
        "similarity and return the most relevant passages, numbered for citation. "
        "Use for any question answerable from documents rather than structured "
        "records; cite the passages you use with their [n]."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search the documents for."},
            "top_k": {
                "type": "integer",
                "default": MAX_TOP_K,
                "maximum": MAX_TOP_K,
                "description": "Maximum passages to return (1–8).",
            },
        },
        "required": ["query"],
    },
    handler=_search_documents,
))
