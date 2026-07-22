"""Outbound email through Gmail (v1.3.0, Task 3).

Sibling of `goto_sms.py`, and deliberately shaped the same way: the gated
`send_email` tool handler calls this only after a human has approved. Nothing
about the gate changes — name, schema, `safe=False`, `gate_describe`,
`editable_fields` are all untouched; this replaces only what happens once
approval is given.

NO `email.sent` EVENT IS WRITTEN HERE, and that is a deliberate departure from
the original plan. The Gmail poll runner already ingests the mailbox in BOTH
directions — a message we send carries Gmail's `SENT` label and comes back on the
next cycle as an outbound `email.sent` with its real Gmail message id. Writing an
event here too would put every approved email on the timeline twice, once with a
synthetic id and once with the real one, and no dedup key would join them. So the
mailbox stays the single source of truth for what was sent, exactly as CLAUDE.md
says external platforms should be. The audit trail is not weakened: `execute_tool`
writes the `tool.executed` row, and `action.queued`/`action.approved` bracket it.
The only cost is that the timeline entry appears one poll cycle later.

FAILURE IS LOUD, for the same reason it is in `goto_sms`: an approver believes the
message went out. A silent failure means someone is waiting on a reply that is
never coming.
"""
from __future__ import annotations

import base64
import logging
from email.message import EmailMessage

from .google_client import GoogleClient, GoogleError, credentials_configured

log = logging.getLogger("nexus.connectors.gmail.send")


class EmailError(RuntimeError):
    """A send that did not happen. Shown to the approver, so it is written in
    plain language with no internals in it."""


def build_message(to: str, subject: str, body: str, sender: str | None = None) -> str:
    """An RFC 2822 message, base64url-encoded the way Gmail's `raw` field wants.

    `EmailMessage` rather than string concatenation: it handles header folding
    and non-ASCII encoding correctly, and a subject line with an accent in it
    should not be the thing that breaks outbound mail.

    `From` is normally omitted — Gmail fills in the authenticated account, which
    is the only address it will let us send as anyway. Passing one is supported
    for tests and for a future send-as alias.
    """
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if sender:
        message["From"] = sender
    message.set_content(body)
    # base64URL, not base64 — the standard alphabet's '+' and '/' are rejected.
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


async def send_email(
    to: str, subject: str, body: str, *, client_factory=GoogleClient
) -> dict:
    """Send one email from the connected mailbox. Returns Gmail's response.

    Raises `EmailError` — never a bare provider exception — so the tool layer has
    one thing to catch and the user sees one kind of message.
    """
    if not credentials_configured():
        raise EmailError(
            "Email isn't connected yet — the Google credentials are not "
            "configured, so the message was not sent."
        )

    recipient = (to or "").strip()
    if "@" not in recipient:
        raise EmailError(f"'{to}' is not an email address that can be written to.")

    raw = build_message(recipient, subject, body)
    try:
        async with client_factory() as google:
            return await google.gmail_send(raw)
    except GoogleError as exc:
        log.warning("gmail send failed: %s", exc)
        raise EmailError(
            f"The email to {recipient} could not be sent — Google rejected it. "
            "It has not been delivered; please try again or call instead."
        ) from exc


__all__ = ["send_email", "build_message", "EmailError"]
