"""Read-path summary derivation for the Event Log.

Events are immutable, so summaries are NEVER backfilled onto stored rows. The
convention (CLAUDE.md) is that every new writer sets a plain-language
`payload.summary` — tool calls and connector events already do. This function is
the read-time fallback for the older core event types that don't, plus a generic
humanizer for anything unrecognized. Pure and business-agnostic: vertical event
names self-describe through `payload.summary`, so no vertical seam is needed here.
"""
from __future__ import annotations


def _humanize(event_type: str) -> str:
    """`lead.created` -> `Lead created`, `some.custom_thing` -> `Some custom thing`."""
    words = event_type.replace(".", " ").replace("_", " ").split()
    if not words:
        return "Event"
    text = " ".join(words)
    return text[0].upper() + text[1:]


def summarize_event(event_type: str, source_system: str, payload: dict | None) -> str:
    payload = payload or {}

    # 1. An explicit plain-language summary always wins (the writer convention).
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    # 2. Per-type templates for the core types that predate the summary convention.
    filename = payload.get("filename")
    if event_type == "document.uploaded":
        return f"Document '{filename}' uploaded" if filename else "Document uploaded"
    if event_type == "document.processing":
        return f"Document '{filename}' processing" if filename else "Document processing started"
    if event_type == "document.ready":
        return f"Document '{filename}' ready" if filename else "Document processed and ready"
    if event_type == "document.failed":
        return f"Document '{filename}' failed" if filename else "Document processing failed"
    if event_type == "chat.message.completed":
        return "Assistant replied in chat"
    if event_type == "webhook.received":
        return f"Received a webhook from {source_system}"
    # The polled twin of the above (Module 18a): sources with no webhooks are
    # swept by the connector sync loop, and the Event Log should say so.
    if event_type == "connector.received":
        return f"Synced a record from {source_system}"

    # 3. Generic fallback: humanize the event_type.
    return _humanize(event_type)
