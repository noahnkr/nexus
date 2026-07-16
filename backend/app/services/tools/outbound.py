"""CORE tools — outbound messaging (`send_sms`, `send_email`), gated + placeholder.

Both are `safe=False`, so `execute_tool` queues them for human approval. On
approval the handler runs — but this phase it performs NO external call: it
validates inputs and returns a `[placeholder]` summary with
`data={"delivered": false, "placeholder": true}`. This proves the gate on
genuinely external actions (mirrors Module 3's placeholder-adapter approach)
while real delivery waits on Module 7 credentials.

Wiring real delivery later replaces ONLY the marked handler internals:
  * send_sms  -> GoTo Connect messaging API: `POST /messaging/v1/messages`
                 (`messaging.v1.send`), body `{ownerPhoneNumber, contactPhoneNumbers,
                 body}`, bearer OAuth token. Map `to`->contact, `body`->body.
  * send_email -> Gmail API: `users.messages.send` with a base64url-encoded RFC-2822
                 message (`To`, `Subject`, body), OAuth2 on the sending mailbox.
Everything else (gating, audit, task/approval lifecycle) already works and stays.
"""
from __future__ import annotations

from .core import ToolDef, ToolInputError, ToolResult
from .registry import register


def _require_text(args: dict, key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ToolInputError(f"'{key}' is required.")
    return val.strip()


# --- send_sms ---
async def _describe_send_sms(conn, args: dict) -> str:
    to = str(args.get("to", "")).strip() or "a recipient"
    return f"Send an SMS to {to}"


async def _send_sms(conn, args: dict) -> ToolResult:
    to = _require_text(args, "to")
    body = _require_text(args, "body")
    # --- placeholder execution (no external call until Module 7) ---
    return ToolResult(
        f"[placeholder] Would send SMS to {to}: “{body[:60]}”",
        {"delivered": False, "placeholder": True, "to": to},
    )


# --- send_email ---
async def _describe_send_email(conn, args: dict) -> str:
    to = str(args.get("to", "")).strip() or "a recipient"
    subject = str(args.get("subject", "")).strip()
    tail = f": “{subject}”" if subject else ""
    return f"Send an email to {to}{tail}"


async def _send_email(conn, args: dict) -> ToolResult:
    to = _require_text(args, "to")
    subject = _require_text(args, "subject")
    body = _require_text(args, "body")
    # --- placeholder execution (no external call until Module 7) ---
    return ToolResult(
        f"[placeholder] Would send email to {to}: “{subject}”",
        {"delivered": False, "placeholder": True, "to": to, "subject": subject},
    )


register(ToolDef(
    name="send_sms",
    description=(
        "Send a text message (SMS) to a phone number. This sends a message outside "
        "the system and requires human approval before it is sent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient phone number (E.164, e.g. +16195550101)."},
            "body": {"type": "string", "description": "Message text."},
        },
        "required": ["to", "body"],
    },
    handler=_send_sms,
    safe=False,
    gate_describe=_describe_send_sms,
))

register(ToolDef(
    name="send_email",
    description=(
        "Send an email to an address. This sends a message outside the system and "
        "requires human approval before it is sent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject line."},
            "body": {"type": "string", "description": "Email body text."},
        },
        "required": ["to", "subject", "body"],
    },
    handler=_send_email,
    safe=False,
    gate_describe=_describe_send_email,
))
