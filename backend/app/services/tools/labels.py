"""Plain-language tool labels — the single map shared by chat's activity display
and the automations vocabulary endpoint (Module 8b). Lifted out of chat_service so
the two surfaces never drift apart. A tool with no entry falls back to a humanized
form of its name, so a newly registered tool is still legible everywhere."""
from __future__ import annotations

TOOL_LABELS: dict[str, str] = {
    "search_documents": "Searching documents",
    "list_leads": "Looking up leads",
    "get_lead": "Looking up a lead",
    "list_clients": "Looking up clients",
    "get_client": "Looking up a client",
    "list_resources": "Looking up caregivers",
    "get_resource_availability": "Checking caregiver availability",
    "list_applicants": "Looking up applicants",
    "get_applicant": "Looking up an applicant",
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


def tool_label(name: str) -> str:
    """Plain label for a tool name (humanized fallback for unmapped tools)."""
    return TOOL_LABELS.get(name, name.replace("_", " ").capitalize())
