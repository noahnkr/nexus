"""Read-path event summary derivation (Module 4, Task 2). Offline, pure function."""
import pytest

from app.services.event_summaries import summarize_event


@pytest.mark.parametrize(
    "event_type, source_system, payload, expected",
    [
        # 1. Explicit payload.summary always wins (tool + connector shapes).
        ("tool.called", "chat", {"summary": "Looked up leads", "tool_name": "list_leads"},
         "Looked up leads"),
        ("lead.created", "welcomehome", {"summary": "New lead Beatrice from WelcomeHome"},
         "New lead Beatrice from WelcomeHome"),
        ("call.completed", "goto", {"summary": "Completed call from +16195559100"},
         "Completed call from +16195559100"),
        # A blank/whitespace summary is ignored — falls through to templates.
        ("document.uploaded", "ingestion", {"summary": "   ", "filename": "care.pdf"},
         "Document 'care.pdf' uploaded"),
        # 2. Document templates, with and without a filename in the payload.
        ("document.uploaded", "ingestion", {"filename": "intake.pdf"},
         "Document 'intake.pdf' uploaded"),
        ("document.processing", "ingestion", {}, "Document processing started"),
        ("document.ready", "ingestion", {"chunk_count": 12}, "Document processed and ready"),
        ("document.failed", "ingestion", {"error": "no text"}, "Document processing failed"),
        ("chat.message.completed", "chat", {"usage": {}}, "Assistant replied in chat"),
        ("webhook.received", "gmail", {"source": "gmail"}, "Received a webhook from gmail"),
        # 3. Generic humanize fallback for anything unrecognized.
        ("lead.created", "welcomehome", {}, "Lead created"),
        ("some.custom_thing", "x", {}, "Some custom thing"),
    ],
)
def test_summarize_event(event_type, source_system, payload, expected):
    assert summarize_event(event_type, source_system, payload) == expected


def test_empty_or_missing_payload_never_raises():
    assert summarize_event("document.uploaded", "ingestion", None) == "Document uploaded"
    assert summarize_event("lead.created", "welcomehome", {}) == "Lead created"
    assert summarize_event("", "x", None) == "Event"
