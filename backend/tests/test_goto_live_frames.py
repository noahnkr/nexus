"""What a REAL GoTo notification frame looks like (captured 2026-07-22).

Every other GoTo fixture in this suite is derived from GoTo's published Call
Events Report schema or from call-history records. These three are the genuine
article: frames pulled off a live WebSocket channel during two real calls on the
business line, saved verbatim.

They exist to pin down two things the schema-derived fixtures got wrong.

**The envelope is not shaped like the docs.** `data.type` is `"call-state"`; the
`ENDING` everyone talks about is nested at `content.state.type`. `frame_kind`'s
type check (`REPORT_SUMMARY`/`ENDING`/`CALL_ENDED`) therefore never matches a
real frame — it classifies correctly only because `"call-events" in source`. That
is worth knowing before someone "tidies up" the source check.

**`participants` is empty, on every call.** Two independent calls — one 80
seconds, one 20 minutes — both carry `"participants": []` and no phone number
anywhere in the payload. So a call cannot be resolved to a person from its
notification frame alone. `test_a_real_frame_carries_no_counterpart` asserts the
gap deliberately: it documents the current, broken reality so that whatever
fixes it (a follow-up detail fetch, or `REPORT_SUMMARY` under the `cr.v1.read`
scope) has a test that must change when the behaviour changes.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services.connectors import gt_map

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "goto"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


CALL_FRAMES = ["call_ending_short", "call_ending_long"]


# --------------------------------------------------------------------------- #
# envelope
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", CALL_FRAMES)
def test_a_real_call_frame_is_classified_as_a_call(name):
    assert gt_map.frame_kind(load(name)) == "call"


@pytest.mark.parametrize("name", CALL_FRAMES)
def test_the_ending_is_nested_not_on_the_envelope_type(name):
    """`data.type` is "call-state" — the docs' `ENDING` lives two levels down.
    `frame_kind` matches on `source`, which is the only reason it works."""
    frame = load(name)
    assert frame["data"]["type"] == "call-state"
    assert frame["data"]["content"]["state"]["type"] == "ENDING"
    assert frame["data"]["source"] == "call-events"


def test_a_channel_lifecycle_frame_is_ignored_rather_than_ingested():
    """GoTo asks for a refresh at 600s remaining. It is not a call and must not
    be mistaken for one."""
    frame = load("websocket_refresh_required")
    assert frame["data"]["type"] == "WEBSOCKET_REFRESH_REQUIRED"
    assert gt_map.frame_kind(frame) == ""


# --------------------------------------------------------------------------- #
# the gap
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", CALL_FRAMES)
def test_a_real_frame_carries_no_counterpart(name):
    """**Asserts a known defect, on purpose.**

    Both real calls carry `participants: []` and no phone number anywhere, so
    there is nobody to resolve the call to and it can only become an ack-only
    receipt. When this is fixed — by fetching call detail after the frame, or by
    `REPORT_SUMMARY` carrying participants — this test SHOULD fail, and that
    failure is the signal that resolution started working.
    """
    content = gt_map.frame_content(load(name))
    assert content["state"]["participants"] == []
    assert gt_map._numbers_in(content) == [], "no phone number is present to match on"


@pytest.mark.parametrize("name", CALL_FRAMES)
def test_the_metadata_that_does_survive(name):
    """Direction and timing are present and usable — it is only identity that is
    missing, which is why the fix is a lookup rather than a redesign."""
    content = gt_map.frame_content(load(name))
    assert content["metadata"]["direction"] == "INBOUND"
    assert content["metadata"]["callCreated"]
    assert content["metadata"]["conversationSpaceId"]


# --------------------------------------------------------------------------- #
# recordings
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", CALL_FRAMES)
def test_this_account_does_record_calls_with_transcription_enabled(name):
    """v1.2.0 concluded from a failing *search* grammar that this account
    produces no recordings, and shipped calls as metadata-only on that basis.
    Both live calls carry a recording with `transcriptEnabled: true`, and
    `GET /recording/v1/recordings/{id}` answered `200 {"status": "UPLOADED"}`.
    The conclusion was wrong; the transcript fetch path is still unknown."""
    state = gt_map.frame_content(load(name))["state"]
    assert state["recordings"], "the account does produce recordings"
    assert state["recordings"][0]["transcriptEnabled"] is True
    assert state["transcripts"], "a transcript id is offered too"
