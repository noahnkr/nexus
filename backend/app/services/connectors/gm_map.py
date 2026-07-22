"""Pure translation of Gmail message payloads (v1.3.0).

The `wh_map`/`gt_map` pattern: no I/O, no database, no settings mutation. The
runner fetches, this translates, the adapter turns the result into canonical
events.

WHAT A GMAIL MESSAGE LOOKS LIKE, and why parsing it needs real care: the body is
not a field. It is a recursive MIME tree under `payload`, where a plain email is
`{mimeType: "text/plain", body: {data}}` but a typical real one is
`multipart/alternative` holding both a text and an HTML rendering, and one with
attachments is `multipart/mixed` wrapping that alternative plus the files. So the
text has to be walked for, preferring `text/plain` and falling back to stripped
`text/html` — which is v1.1.3's lesson applied at the source rather than at the
timeline: strip markup on the way IN, so nothing downstream has to guess.

Base64URL, not base64. Gmail encodes body and attachment data with the URL-safe
alphabet and usually omits padding. Decoding with the standard alphabet fails on
any message containing a `-` or `_`, which is most of them.

DIRECTION. Gmail has no direction field; it has labels. `SENT` on a message means
we sent it. That is the only reliable signal — comparing the From address to the
mailbox breaks on aliases and delegated sending.
"""
from __future__ import annotations

import base64
import binascii
import re
from email.utils import parsedate_to_datetime

# Gmail's own labels. `SENT` is the outbound marker; `DRAFT` is not mail at all.
SENT_LABEL = "SENT"
DRAFT_LABEL = "DRAFT"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")
_ANGLE_ADDRESS_RE = re.compile(r"<([^>]+)>")


def b64url(data: str | None) -> bytes:
    """Decode Gmail's base64url payload data. Returns b'' on anything unparseable.

    Padding is re-added because Gmail strips it; without this, roughly three in
    four messages fail to decode.
    """
    if not data:
        return b""
    s = str(data).replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s)
    except (binascii.Error, ValueError):
        return b""


def header(message: dict, name: str) -> str:
    """One header value from a Gmail message, case-insensitively. '' when absent."""
    payload = message.get("payload") or {}
    wanted = name.lower()
    for entry in payload.get("headers") or []:
        if str(entry.get("name", "")).lower() == wanted:
            return str(entry.get("value") or "").strip()
    return ""


def email_address(raw: str | None) -> str:
    """The bare address out of a `From`/`To` header.

    `"Margaret Ellison" <margaret@example.com>` → `margaret@example.com`.
    Lower-cased, because mail addresses are matched, and a person who writes
    `Margaret@` today wrote `margaret@` last week.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    match = _ANGLE_ADDRESS_RE.search(text)
    if match:
        text = match.group(1)
    text = text.strip().strip("<>").strip()
    return text.lower() if "@" in text else ""


def display_name(raw: str | None) -> str:
    """The human name out of a `From` header, or '' when it is bare."""
    if not raw:
        return ""
    text = str(raw).strip()
    if "<" not in text:
        return ""
    name = text.split("<", 1)[0].strip().strip('"').strip()
    return name


def html_to_text(html: str) -> str:
    """Readable text out of an HTML body part.

    Deliberately small rather than a parser dependency: block tags become line
    breaks, everything else is dropped, entities that actually appear in mail are
    unescaped. The goal is legible text for a timeline and for embedding, not
    faithful rendering.
    """
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>", "\n", text)
    text = _TAG_RE.sub("", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
    ):
        text = text.replace(entity, char)
    text = _WS_RE.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _walk(part: dict):
    """Every node of the MIME tree, depth-first including the root."""
    if not isinstance(part, dict):
        return
    yield part
    for child in part.get("parts") or []:
        yield from _walk(child)


def body_text(message: dict) -> str:
    """The message body as plain text.

    `text/plain` wins when present; otherwise the `text/html` part is stripped.
    Parts that are attachments (they carry a filename) are never treated as body
    — an attached .txt is a document, not the email someone wrote.
    """
    payload = message.get("payload") or {}
    plain: list[str] = []
    html: list[str] = []

    for part in _walk(payload):
        if part.get("filename"):
            continue
        mime = str(part.get("mimeType") or "").lower()
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        if mime == "text/plain":
            plain.append(b64url(data).decode("utf-8", "replace"))
        elif mime == "text/html":
            html.append(b64url(data).decode("utf-8", "replace"))

    if plain:
        return "\n".join(p.strip() for p in plain if p.strip()).strip()
    if html:
        return html_to_text("\n".join(html))
    return ""


def attachments(message: dict) -> list[dict]:
    """Every attachment as `{filename, mime_type, size, attachment_id}`.

    Inline images and other parts with no filename are excluded: a signature logo
    is not a document, and ingesting one per email would swamp the corpus.
    """
    found: list[dict] = []
    for part in _walk(message.get("payload") or {}):
        filename = str(part.get("filename") or "").strip()
        if not filename:
            continue
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        if not attachment_id:
            continue
        found.append({
            "filename": filename,
            "mime_type": str(part.get("mimeType") or "").lower(),
            "size": int(body.get("size") or 0),
            "attachment_id": str(attachment_id),
        })
    return found


def occurred_at(message: dict) -> str | None:
    """When the message was sent, as ISO 8601.

    Prefers the `Date` header (what the sender's client claims) and falls back to
    `internalDate` (when Gmail received it, epoch milliseconds). A malformed Date
    header is common enough in real mail that the fallback is load-bearing.
    """
    raw = header(message, "Date")
    if raw:
        try:
            return parsedate_to_datetime(raw).isoformat()
        except (TypeError, ValueError, IndexError):
            pass
    internal = message.get("internalDate")
    if internal:
        try:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(
                int(internal) / 1000, tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError, OSError):
            pass
    return None


def is_outbound(message: dict) -> bool:
    """True when we sent it (Gmail's `SENT` label)."""
    return SENT_LABEL in (message.get("labelIds") or [])


def is_draft(message: dict) -> bool:
    """Drafts are not correspondence — nobody has received them."""
    return DRAFT_LABEL in (message.get("labelIds") or [])


def map_message(message: dict) -> dict | None:
    """A Gmail message → the decoded shape the adapter consumes.

    Returns None for anything that is not correspondence: a draft, or a message
    with no counterpart address to attribute it to.

    The COUNTERPART is the other party — the sender on inbound mail, the first
    recipient on outbound. Same rule as the phone channel: the office's own
    address is never the thing to resolve against.
    """
    if is_draft(message):
        return None

    outbound = is_outbound(message)
    from_header = header(message, "From")
    to_header = header(message, "To")

    counterpart_header = to_header if outbound else from_header
    counterpart = email_address(counterpart_header)
    if not counterpart:
        return None

    subject = header(message, "Subject") or "(no subject)"
    body = body_text(message)
    name = display_name(counterpart_header) or counterpart

    return {
        "message_id": str(message.get("id") or ""),
        "thread_id": str(message.get("threadId") or ""),
        "from": email_address(from_header),
        "to": email_address(to_header),
        "counterpart": counterpart,
        "counterpart_name": name,
        "direction": "outbound" if outbound else "inbound",
        "subject": subject,
        "body": body,
        "snippet": str(message.get("snippet") or "").strip(),
        "occurred_at": occurred_at(message),
        "attachments": attachments(message),
    }


def added_message_ids(history_page: dict) -> list[str]:
    """Message ids added in one `history.list` page, in order and de-duplicated.

    Gmail repeats a message across history records whenever anything about it
    changes, so the same id commonly appears several times in one page.
    """
    seen: set[str] = set()
    ids: list[str] = []
    for record in history_page.get("history") or []:
        for added in record.get("messagesAdded") or []:
            message = added.get("message") or {}
            message_id = str(message.get("id") or "")
            if message_id and message_id not in seen:
                seen.add(message_id)
                ids.append(message_id)
    return ids


__all__ = [
    "b64url",
    "header",
    "email_address",
    "display_name",
    "html_to_text",
    "body_text",
    "attachments",
    "occurred_at",
    "is_outbound",
    "is_draft",
    "map_message",
    "added_message_ids",
]
