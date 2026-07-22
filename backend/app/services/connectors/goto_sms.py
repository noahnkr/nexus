"""Outbound SMS through GoTo Connect (v1.2.0, Task 8).

Called by the gated `send_sms` tool handler *after* approval. Everything about
the gate is unchanged — name, schema, `safe=False`, `gate_describe`,
`editable_fields` — this module only replaces what happens once a human has said
yes. `execute_tool` remains the single seam; nothing here is callable from chat
directly.

WHY THE TOOL HANDLER DOESN'T JUST CALL THE CLIENT: an outbound text is
correspondence, so it belongs in the communications tier alongside the inbound
half of the same conversation. Sending and recording are one operation from the
office's point of view and it would be easy for a future edit to keep one and
lose the other, so they live together here.

FAILURE IS LOUD. The old placeholder returned `delivered: false` and a cheerful
summary. A real send that fails must say so: the approver believes a message went
out, and a silent failure means a client is waiting on a reply that will never
come. Unconfigured credentials are equally loud — "I can't send" is useful,
"[placeholder] Would send" pretending to be success is not.
"""
from __future__ import annotations

import logging

from ...config import settings
from .goto_client import GoToClient, GoToError, credentials_configured
from .gt_map import e164

log = logging.getLogger("nexus.connectors.goto.sms")


class SmsError(RuntimeError):
    """A send that did not happen. The message is shown to the approver, so it is
    written in plain language with no internals in it."""


async def send_sms(to: str, body: str, *, client_factory=GoToClient) -> dict:
    """Send one SMS from the business line. Returns the provider's response.

    Raises `SmsError` — never a bare provider exception — so the tool layer has
    one thing to catch and the user sees one kind of message.
    """
    if not credentials_configured():
        raise SmsError(
            "Texting isn't connected yet — the GoTo Connect credentials are not "
            "configured, so the message was not sent."
        )

    owner = e164(settings.goto_business_number)
    if not owner:
        raise SmsError(
            "No business phone number is configured (GOTO_BUSINESS_NUMBER), so "
            "there is no line to send the message from."
        )

    recipient = e164(to)
    if not recipient:
        raise SmsError(f"'{to}' is not a phone number that can be texted.")

    try:
        async with client_factory() as goto:
            return await goto.send_sms(owner, recipient, body)
    except GoToError as exc:
        # The provider's message can carry ids and payload fragments; keep it in
        # the log and give the approver something they can act on.
        log.warning("goto sms send failed: %s", exc)
        raise SmsError(
            f"The text to {recipient} could not be sent — GoTo rejected it. "
            "It has not been delivered; please try again or call instead."
        ) from exc


__all__ = ["send_sms", "SmsError"]
