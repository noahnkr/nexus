"""Pure translation of GoTo Connect payloads into canonical shapes (v1.2.0).

The `wh_map.py` pattern: no I/O, no database, no settings mutation — every
function here takes a payload and returns data. `adapters/goto.py` calls it,
`goto_bridge.py` feeds it WebSocket frames, and the tests drive it directly.

TWO INPUT SHAPES, one output. Both are real, and both were captured from this
account rather than imagined:

1. **Call-event notification frames** (what the bridge receives). The envelope is
   the notification-channel standard and the content is the Call Events Report
   schema (`eventTypes: ["REPORT_SUMMARY"]`)::

       {"data": {"source": "call-events-report", "type": "REPORT_SUMMARY",
                 "timestamp": "...", "content": {
                     "conversationSpaceId": "...", "callCreated": "...",
                     "callEnded": "...", "direction": "OUTBOUND|INBOUND",
                     "accountKey": "...", "participants": [...],
                     "callStates": [...]}}}

2. **Call-history records** (`GET /call-history/v1/calls`). Verified 2026-07-21
   against 100 real calls on this account; every record carries exactly::

       {"legId", "originatorId", "caller": {"name", "number"},
        "callee": {"name", "number"}, "direction", "startTime",
        "answerTime", "duration", "hangupCause", "ownerPhoneNumber"}

   `duration` is MILLISECONDS (a 77836 on a ~78-second call).

THE COUNTERPART RULE (plan decision 5). A call has two sides and only one of them
identifies a person we care about. The *counterpart* — the outside number — is the
resolution key; the office line never is. Inbound: counterpart = caller. Outbound:
counterpart = callee. Participant shapes vary more than the docs admit, so
`_counterpart_from_participants` scans defensively for a dialable number rather
than trusting one field name, and treats anything extension-length or flagged as
a LINE as the office side.

NO TRANSCRIPTS. The A2 probe (2026-07-21) established that this account produces
no call recordings: 100 historical calls carry zero recording fields, the
recording search endpoint rejects every query grammar, and the account's own
subscription list has no recording event types. Calls therefore normalize to
metadata only — who, when, how long, which way — with no body text. That is a
deliberate, operator-approved relaxation of the plan's original transcript gate,
not an oversight; if recording is later enabled, transcript enrichment is additive
and nothing here changes.
"""
from __future__ import annotations

import re

from ...config import settings

# Extensions are short (this account uses 1000, 1001…). Anything at or below this
# many digits is an internal identifier, never a dialable counterpart.
_MAX_EXTENSION_DIGITS = 6

# Keys a participant/leg may carry a dialable number under. GoTo is not
# consistent across schemas, so look at all of them.
_NUMBER_KEYS = ("number", "phoneNumber", "phone", "callerId", "dialedNumber")


def e164(raw: str | None) -> str:
    """Best-effort E.164 normalization: a leading '+' and digits only.

    US-centric by deployment, matching the business it serves — a bare 10-digit
    number gains '+1', an 11-digit number starting with 1 gains '+'. Anything
    already carrying '+' is trusted as written. Extension-length input returns
    '' rather than a nonsense '+1000': an extension is not a phone number, and
    silently coining one would let the office's own line resolve as a person.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return ""
    if s.startswith("+"):
        return "+" + digits
    if len(digits) <= _MAX_EXTENSION_DIGITS:
        return ""
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def ignored_numbers() -> set[str]:
    """`GOTO_IGNORED_NUMBERS` as a normalized set — the known-numbers guard.

    The motivating case: WelcomeHome's messaging centre places calls through a
    provisional bridge number. It dials the office first, then the client, so the
    office sees a call whose counterpart is the bridge, not a person. That leg is
    plumbing, not correspondence, and ingesting it would attach a meaningless
    call to whoever the bridge number happens to match.
    """
    return {
        n for n in (e164(part) for part in settings.goto_ignored_numbers.split(","))
        if n
    }


def _numbers_in(obj) -> list[str]:
    """Every dialable number reachable in a participant/leg object.

    Deliberately structural rather than schema-bound: the participant shape
    differs between the call-events and call-events-report schemas, and a scan
    that finds a number wherever it is beats a lookup that finds nothing when
    GoTo renames a field.
    """
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _NUMBER_KEYS and isinstance(value, (str, int)):
                normalized = e164(str(value))
                if normalized:
                    found.append(normalized)
            elif isinstance(value, (dict, list)):
                found.extend(_numbers_in(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_numbers_in(value))
    return found


def _envelope(frame: dict) -> dict:
    """The notification envelope's `data` object, or the frame itself when the
    caller already unwrapped it."""
    data = frame.get("data")
    return data if isinstance(data, dict) else frame


def _content(record: dict) -> dict:
    """The payload to read fields from: a notification envelope's `content`, or
    the record itself when it is already unwrapped (a call-history row)."""
    inner = record.get("content")
    return inner if isinstance(inner, dict) else record


def _office_numbers(record: dict | None = None) -> set[str]:
    """Numbers that are us.

    `GOTO_BUSINESS_NUMBER` is the configured answer. A payload's own
    `ownerPhoneNumber` is added when present — call history stamps every record
    with the line that owns it, so the office side is self-identifying even
    before the setting is filled in.
    """
    numbers = {e164(settings.goto_business_number)}
    if record is not None:
        numbers.add(e164(record.get("ownerPhoneNumber")))
    return {n for n in numbers if n}


def _counterpart_from_participants(content: dict) -> str:
    """The outside number in a call-events-report `content`."""
    office = _office_numbers(content)
    for number in _numbers_in(content.get("participants") or []):
        if number not in office:
            return number
    return ""


def counterpart_and_direction(record: dict) -> tuple[str, str]:
    """`(counterpart_e164, direction)` for either input shape.

    `direction` is lower-cased to the canonical `"inbound" | "outbound"` the
    communications tier uses. An unrecognised direction yields `""`, which the
    caller treats as un-normalizable rather than guessing a side.
    """
    direction = str(record.get("direction") or "").strip().lower()
    if direction not in ("inbound", "outbound"):
        direction = ""

    office = _office_numbers(record)

    caller = record.get("caller")
    callee = record.get("callee")
    if isinstance(caller, dict) or isinstance(callee, dict):
        # Call-history shape: the two sides are named, so honour the direction.
        near, far = (caller, callee) if direction == "inbound" else (callee, caller)
        for side in (near, far):
            if isinstance(side, dict):
                number = e164(side.get("number"))
                if number and number not in office:
                    return number, direction
        return "", direction

    from_participants = _counterpart_from_participants(record)
    if from_participants:
        return from_participants, direction

    # Flat shape: a bare `from`/`to` on the record itself. The placeholder
    # payloads use this, and so does any hand-built fixture — worth supporting
    # rather than making every caller wrap two levels of envelope.
    for key in ("from", "to", "fromNumber", "toNumber"):
        number = e164(record.get(key))
        if number and number not in office:
            return number, direction
    return "", direction


def _display(record: dict, counterpart: str) -> str:
    """The counterpart's name if the payload carries one, else their number."""
    for side in ("caller", "callee"):
        value = record.get(side)
        if isinstance(value, dict) and e164(value.get("number")) == counterpart:
            name = str(value.get("name") or "").strip()
            if name and not name.isdigit():
                return name
    return counterpart


def _duration_seconds(record: dict) -> int | None:
    """Call length in seconds.

    Unit-safety matters here: call history reports `duration` in MILLISECONDS
    (a real 78-second call arrives as 77836), while a `durationSeconds` field
    means what it says. Reading one as the other is a 1000x error on a number
    that reaches a user-facing summary, so the two names are handled separately
    rather than merged.
    """
    seconds = record.get("durationSeconds")
    if isinstance(seconds, (int, float)) and seconds >= 0:
        return int(seconds)
    millis = record.get("duration")
    if isinstance(millis, (int, float)) and millis >= 0:
        return int(millis / 1000)
    return None


def map_call(record: dict) -> dict | None:
    """A completed call → the canonical attributes an adapter event carries.

    Returns None when the record cannot be resolved to an outside party — an
    internal extension-to-extension call, a malformed frame, or a counterpart on
    the ignored list. None means "log the receipt and stop", never an error.
    """
    content = _content(record)
    counterpart, direction = counterpart_and_direction(content)
    if not counterpart or counterpart in ignored_numbers():
        return None

    who = _display(content, counterpart)
    seconds = _duration_seconds(content)
    length = f" ({seconds // 60}m {seconds % 60}s)" if seconds is not None else ""
    verb = "Call from" if direction == "inbound" else "Call to"

    return {
        "counterpart": counterpart,
        "direction": direction or "inbound",
        "summary": f"{verb} {who}{length}",
        "occurred_at": (
            content.get("startTime") or content.get("callCreated") or None
        ),
        "duration_seconds": seconds,
        "external_call_id": (
            content.get("legId") or content.get("conversationSpaceId") or None
        ),
    }


def map_sms(record: dict) -> dict | None:
    """An inbound/outbound SMS → canonical attributes, or None to ack-only.

    The messaging notification carries the body directly, so unlike calls an SMS
    is genuinely correspondence and reaches the communications tier with text.
    """
    content = _content(record)
    body = str(
        content.get("body") or content.get("text") or content.get("message") or ""
    ).strip()

    direction = str(content.get("direction") or "").strip().lower()
    if direction not in ("inbound", "outbound"):
        # A message with a sender we are not is inbound by construction.
        direction = "inbound"

    counterpart = ""
    for key in ("from", "fromNumber", "contactPhoneNumber", "ownerPhoneNumber"):
        candidate = e164(content.get(key))
        if candidate and candidate not in _office_numbers():
            counterpart = candidate
            break
    if not counterpart:
        for number in _numbers_in(content):
            if number not in _office_numbers():
                counterpart = number
                break

    if not counterpart or counterpart in ignored_numbers():
        return None

    preview = body if len(body) <= 60 else body[:57] + "…"
    verb = "Text from" if direction == "inbound" else "Text to"
    return {
        "counterpart": counterpart,
        "direction": direction,
        "body": body,
        "summary": f"{verb} {counterpart}" + (f": {preview}" if preview else ""),
        "occurred_at": content.get("timestamp") or content.get("createdAt") or None,
        "external_message_id": content.get("id") or content.get("messageId") or None,
    }


def frame_kind(frame: dict) -> str:
    """What a raw WebSocket frame is: `"call"`, `"sms"`, or `""` (ignore).

    Keyed off the envelope's `source`/`type` discriminators rather than guessing
    from content, so an unrecognised publisher is ignored quietly instead of
    being mis-ingested as a call.
    """
    data = _envelope(frame)
    source = str(data.get("source") or "").lower()
    kind = str(data.get("type") or "").upper()

    if "call-events" in source or kind in ("REPORT_SUMMARY", "ENDING", "CALL_ENDED"):
        return "call"
    if "message" in source or "MESSAGE" in kind:
        return "sms"
    return ""


def frame_content(frame: dict) -> dict:
    """The `content` object inside a notification envelope (or the frame itself
    when it is already unwrapped)."""
    data = _envelope(frame)
    content = data.get("content")
    return content if isinstance(content, dict) else data


__all__ = [
    "e164",
    "ignored_numbers",
    "counterpart_and_direction",
    "map_call",
    "map_sms",
    "frame_kind",
    "frame_content",
]
