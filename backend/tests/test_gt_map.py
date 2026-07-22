"""gt_map — pure payload translation for GoTo Connect (v1.2.0, Task 3).

Offline by construction: `gt_map` does no I/O, so every case here is a literal
payload in, a dict out. The fixtures are not invented — `HISTORY_CALL` is a real
record from this account's `/call-history/v1/calls` (numbers changed, shape
byte-for-byte), and `REPORT_FRAME` follows the Call Events Report schema the
bridge subscribes to.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services.connectors import gt_map

BUSINESS = "+16303602784"
BRIDGE = "+13312811588"  # WelcomeHome's provisional number — plumbing, not a person
CLIENT = "+16304615622"


@pytest.fixture(autouse=True)
def _goto_settings(monkeypatch):
    """The office's own identity and the guard list, as production sets them."""
    monkeypatch.setattr(settings, "goto_business_number", BUSINESS)
    monkeypatch.setattr(settings, "goto_ignored_numbers", BRIDGE)


# A real call-history record's shape (verified against 100 live records).
HISTORY_CALL = {
    "legId": "2cc8557c-3e60-4b21-bd9a-84b4e13a4f44",
    "originatorId": "3798ba83-a4f4-45ff-9a7a-7ef393ca870f",
    "caller": {"name": "Margaret Ellison", "number": CLIENT},
    "callee": {"name": "Brennen Roberts", "number": "1000"},
    "direction": "INBOUND",
    "startTime": "2026-07-21T22:47:39.151Z",
    "answerTime": "2026-07-21T22:47:44.574Z",
    "duration": 77836,
    "hangupCause": 16,
    "ownerPhoneNumber": BUSINESS,
}

# The Call Events Report notification the WebSocket bridge receives.
REPORT_FRAME = {
    "data": {
        "source": "call-events-report",
        "type": "REPORT_SUMMARY",
        "timestamp": "2026-07-21T18:55:35.993Z",
        "content": {
            "conversationSpaceId": "29a1c77a-e52a-3764-8606-8b02b1277be8",
            "callCreated": "2026-07-21T18:42:43.633Z",
            "callEnded": "2026-07-21T18:43:00.983Z",
            "direction": "OUTBOUND",
            "accountKey": "6327799820468129299",
            "ownerPhoneNumber": BUSINESS,
            "participants": [
                {
                    "participantId": "e26feaf0-d390-419f-8f20-c4c83e9f74fe",
                    "legId": "602d0871-f21a-4f3c-815c-9f2c21f82cee",
                    "type": {"name": "Brennen Roberts", "value": "LINE",
                             "extensionNumber": "1000"},
                },
                {
                    "participantId": "aa11bb22-0000-4444-8888-cccccccccccc",
                    "legId": "77ab0871-f21a-4f3c-815c-9f2c21f82999",
                    "type": {"name": "External", "value": "EXTERNAL",
                             "number": CLIENT},
                },
            ],
        },
    }
}


# --------------------------------------------------------------------------- #
# e164
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+1 (630) 461-5622", "+16304615622"),
        ("6304615622", "+16304615622"),
        ("16304615622", "+16304615622"),
        ("(630) 461-5622", "+16304615622"),
        ("", ""),
        (None, ""),
        ("not a number", ""),
    ],
)
def test_e164_normalizes_the_shapes_goto_actually_sends(raw, expected):
    assert gt_map.e164(raw) == expected


def test_e164_refuses_to_coin_a_number_from_an_extension():
    """An extension is an internal identifier, not a phone number.

    This is the guard that keeps the office's own line from resolving as a
    person: '1000' must not become '+1000' and then match something.
    """
    assert gt_map.e164("1000") == ""
    assert gt_map.e164("1001") == ""


# --------------------------------------------------------------------------- #
# the counterpart rule
# --------------------------------------------------------------------------- #
def test_inbound_history_call_resolves_on_the_caller():
    number, direction = gt_map.counterpart_and_direction(HISTORY_CALL)
    assert number == CLIENT
    assert direction == "inbound"


def test_outbound_history_call_resolves_on_the_callee():
    record = {
        **HISTORY_CALL,
        "direction": "OUTBOUND",
        "caller": {"name": "Brennen Roberts", "number": "1000"},
        "callee": {"name": "Margaret Ellison", "number": CLIENT},
    }
    number, direction = gt_map.counterpart_and_direction(record)
    assert number == CLIENT
    assert direction == "outbound"


def test_the_office_line_is_never_the_counterpart():
    """Both sides carry real numbers and one of them is us — pick the other."""
    record = {
        "direction": "INBOUND",
        "caller": {"name": "Someone", "number": CLIENT},
        "callee": {"name": "Office", "number": BUSINESS},
        "ownerPhoneNumber": BUSINESS,
    }
    assert gt_map.counterpart_and_direction(record)[0] == CLIENT


def test_report_frame_counterpart_comes_from_the_external_participant():
    content = gt_map.frame_content(REPORT_FRAME)
    number, direction = gt_map.counterpart_and_direction(content)
    assert number == CLIENT
    assert direction == "outbound"


# --------------------------------------------------------------------------- #
# map_call
# --------------------------------------------------------------------------- #
def test_map_call_reads_milliseconds_as_milliseconds():
    """`duration` is ms in call history: 77836 is 1m 17s, not 21 hours."""
    mapped = gt_map.map_call(HISTORY_CALL)
    assert mapped is not None
    assert mapped["duration_seconds"] == 77
    assert mapped["summary"] == "Call from Margaret Ellison (1m 17s)"


def test_map_call_reads_duration_seconds_as_seconds():
    mapped = gt_map.map_call({**HISTORY_CALL, "duration": None, "durationSeconds": 42})
    assert mapped is not None
    assert mapped["duration_seconds"] == 42


def test_map_call_carries_identity_and_timing():
    mapped = gt_map.map_call(HISTORY_CALL)
    assert mapped is not None
    assert mapped["counterpart"] == CLIENT
    assert mapped["direction"] == "inbound"
    assert mapped["occurred_at"] == "2026-07-21T22:47:39.151Z"
    assert mapped["external_call_id"] == HISTORY_CALL["legId"]


def test_map_call_falls_back_to_the_number_when_there_is_no_name():
    mapped = gt_map.map_call({**HISTORY_CALL, "caller": {"number": CLIENT}})
    assert mapped is not None
    assert CLIENT in mapped["summary"]


def test_map_call_unwraps_a_notification_envelope():
    mapped = gt_map.map_call(REPORT_FRAME["data"])
    assert mapped is not None
    assert mapped["counterpart"] == CLIENT
    assert mapped["external_call_id"] == "29a1c77a-e52a-3764-8606-8b02b1277be8"


# --------------------------------------------------------------------------- #
# the known-numbers guard
# --------------------------------------------------------------------------- #
def test_the_bridge_number_is_guarded_out():
    """A WelcomeHome-initiated call dials the office first. That leg's counterpart
    is the bridge, not a person, and ingesting it would attach a meaningless call
    to whoever the bridge number matched."""
    record = {
        **HISTORY_CALL,
        "caller": {"name": "SNRSHELPINGSNRS", "number": BRIDGE},
    }
    assert gt_map.map_call(record) is None


def test_the_guard_accepts_any_written_form_of_the_number():
    """Operators paste numbers however their source shows them; the guard
    normalizes before comparing so '(331) 281-1588' still matches."""
    import app.config as config_module

    object.__setattr__(config_module.settings, "goto_ignored_numbers", "(331) 281-1588")
    try:
        assert BRIDGE in gt_map.ignored_numbers()
    finally:
        object.__setattr__(config_module.settings, "goto_ignored_numbers", BRIDGE)


def test_guard_list_parses_multiple_numbers_and_ignores_blanks(monkeypatch):
    monkeypatch.setattr(
        settings, "goto_ignored_numbers", f"{BRIDGE}, , 6304615622,"
    )
    assert gt_map.ignored_numbers() == {BRIDGE, CLIENT}


def test_an_internal_extension_to_extension_call_is_not_correspondence():
    record = {
        "direction": "INBOUND",
        "caller": {"name": "Dan Drews", "number": "1001"},
        "callee": {"name": "Brennen Roberts", "number": "1000"},
        "ownerPhoneNumber": BUSINESS,
    }
    assert gt_map.map_call(record) is None


# --------------------------------------------------------------------------- #
# map_sms
# --------------------------------------------------------------------------- #
def test_map_sms_keeps_the_body_and_names_the_sender():
    mapped = gt_map.map_sms(
        {"from": CLIENT, "ownerPhoneNumber": BUSINESS,
         "body": "Can we move Tuesday's visit to 2pm?",
         "timestamp": "2026-07-21T18:00:00Z", "id": "msg-1"}
    )
    assert mapped is not None
    assert mapped["counterpart"] == CLIENT
    assert mapped["direction"] == "inbound"
    assert mapped["body"] == "Can we move Tuesday's visit to 2pm?"
    assert mapped["external_message_id"] == "msg-1"


def test_map_sms_truncates_only_the_summary_never_the_body():
    """The summary is a glance; the body is the record. v1.1.3's lesson."""
    long_body = "y" * 500
    mapped = gt_map.map_sms({"from": CLIENT, "body": long_body})
    assert mapped is not None
    assert mapped["body"] == long_body
    assert len(mapped["summary"]) < 120


def test_map_sms_guards_the_bridge_number_too():
    assert gt_map.map_sms({"from": BRIDGE, "body": "hello"}) is None


# --------------------------------------------------------------------------- #
# frame discrimination
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "frame,expected",
    [
        (REPORT_FRAME, "call"),
        ({"data": {"source": "messaging", "type": "INCOMING_MESSAGE"}}, "sms"),
        ({"data": {"source": "presence", "type": "SESSION_MANAGEMENT"}}, ""),
        ({}, ""),
    ],
)
def test_frame_kind_keys_off_the_envelope_not_the_content(frame, expected):
    assert gt_map.frame_kind(frame) == expected


def test_frame_content_unwraps_and_passes_through():
    assert gt_map.frame_content(REPORT_FRAME)["direction"] == "OUTBOUND"
    # Already-unwrapped input is returned as-is, so callers need not care.
    assert gt_map.frame_content(HISTORY_CALL) is HISTORY_CALL
