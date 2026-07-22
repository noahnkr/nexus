"""gm_map — Gmail message parsing (v1.3.0, Task 2). Offline, pure.

The fixtures here are the MIME shapes real mail actually arrives in, not the
simplified one the API docs lead with. That matters because the body is not a
field: a plain message, a `multipart/alternative` with a text and an HTML
rendering, and a `multipart/mixed` wrapping that plus attachments are three
different trees, and only the first is easy.
"""
from __future__ import annotations

import base64

import pytest

from app.services.connectors import gm_map


def b64url(text: str) -> str:
    """Encode as Gmail does — URL-safe alphabet, padding stripped."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(*, headers=None, payload=None, labels=None, **kwargs) -> dict:
    base = {
        "id": "msg-1",
        "threadId": "thread-1",
        "labelIds": labels if labels is not None else ["INBOX"],
        "snippet": "a short preview",
        "internalDate": "1784500000000",
        "payload": payload or {
            "headers": headers or [
                {"name": "From", "value": '"Margaret Ellison" <margaret@example.com>'},
                {"name": "To", "value": "office@shsgreaternaperville.com"},
                {"name": "Subject", "value": "Care hours question"},
                {"name": "Date", "value": "Tue, 21 Jul 2026 14:03:00 -0500"},
            ],
            "mimeType": "text/plain",
            "body": {"data": b64url("Could we move Tuesday to 2pm?")},
        },
    }
    base.update(kwargs)
    return base


# --------------------------------------------------------------------------- #
# base64url
# --------------------------------------------------------------------------- #
def test_base64url_decodes_the_alphabet_gmail_actually_uses():
    """Standard base64 fails on any payload containing '-' or '_', which is most
    of them. This is the single most likely silent breakage in the whole parser."""
    raw = b"\xfb\xff\xbe"  # encodes to characters outside the standard alphabet
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert gm_map.b64url(encoded) == raw


def test_base64url_restores_stripped_padding():
    assert gm_map.b64url(b64url("abc")) == b"abc"
    assert gm_map.b64url(b64url("abcd")) == b"abcd"
    assert gm_map.b64url(b64url("abcde")) == b"abcde"


def test_base64url_returns_empty_on_junk_rather_than_raising():
    assert gm_map.b64url("!!!not base64!!!") == b""
    assert gm_map.b64url(None) == b""


# --------------------------------------------------------------------------- #
# headers and addresses
# --------------------------------------------------------------------------- #
def test_headers_are_read_case_insensitively():
    msg = message(headers=[{"name": "subject", "value": "lowercase header"}])
    assert gm_map.header(msg, "Subject") == "lowercase header"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"Margaret Ellison" <margaret@example.com>', "margaret@example.com"),
        ("margaret@example.com", "margaret@example.com"),
        ("<margaret@example.com>", "margaret@example.com"),
        ("Margaret <MARGARET@Example.COM>", "margaret@example.com"),
        ("", ""),
        (None, ""),
        ("not an address", ""),
    ],
)
def test_address_extraction_handles_the_forms_mail_arrives_in(raw, expected):
    assert gm_map.email_address(raw) == expected


def test_addresses_are_lowercased_because_they_are_matched_on():
    """The same person writes `Margaret@` today and `margaret@` next week; if the
    resolver saw two different keys it would raise a review task for a contact it
    already knows."""
    assert gm_map.email_address("Margaret@Example.com") == "margaret@example.com"


def test_display_name_is_extracted_when_present():
    assert gm_map.display_name('"Margaret Ellison" <m@e.com>') == "Margaret Ellison"
    assert gm_map.display_name("m@e.com") == ""


# --------------------------------------------------------------------------- #
# body extraction — the hard part
# --------------------------------------------------------------------------- #
def test_a_simple_plain_text_body_is_read():
    assert gm_map.body_text(message()) == "Could we move Tuesday to 2pm?"


def test_multipart_alternative_prefers_plain_text_over_html():
    """Real mail usually carries both renderings. Taking the HTML when plain text
    exists means stripping markup for no reason and risking a worse result."""
    msg = message(payload={
        "mimeType": "multipart/alternative",
        "headers": [],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64url("the plain version")}},
            {"mimeType": "text/html",
             "body": {"data": b64url("<p>the <b>html</b> version</p>")}},
        ],
    })
    assert gm_map.body_text(msg) == "the plain version"


def test_an_html_only_body_is_stripped_to_readable_text():
    """v1.1.3's lesson applied at the source: strip markup on the way IN, so
    nothing downstream has to guess whether a body is HTML."""
    msg = message(payload={
        "mimeType": "text/html",
        "headers": [],
        "body": {"data": b64url("<p>Hello <b>Margaret</b>,</p><p>Tuesday works.</p>")},
    })
    text = gm_map.body_text(msg)
    assert "<" not in text
    assert "Hello Margaret," in text
    assert "Tuesday works." in text


def test_deeply_nested_multipart_bodies_are_found():
    """multipart/mixed wrapping multipart/alternative is what a message with
    attachments looks like — the body is two levels down."""
    msg = message(payload={
        "mimeType": "multipart/mixed",
        "headers": [],
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": b64url("nested body")}},
            ]},
            {"mimeType": "application/pdf", "filename": "care-plan.pdf",
             "body": {"attachmentId": "att-1", "size": 1024}},
        ],
    })
    assert gm_map.body_text(msg) == "nested body"


def test_an_attached_text_file_is_not_mistaken_for_the_body():
    """A part with a filename is a document, not the email someone wrote."""
    msg = message(payload={
        "mimeType": "multipart/mixed",
        "headers": [],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64url("the real body")}},
            {"mimeType": "text/plain", "filename": "notes.txt",
             "body": {"attachmentId": "att-1", "data": b64url("attached text")}},
        ],
    })
    assert gm_map.body_text(msg) == "the real body"


def test_script_and_style_content_never_reaches_the_text():
    msg = message(payload={
        "mimeType": "text/html", "headers": [],
        "body": {"data": b64url(
            "<style>.x{color:red}</style><script>alert(1)</script><p>Real text</p>"
        )},
    })
    text = gm_map.body_text(msg)
    assert "Real text" in text
    assert "color:red" not in text
    assert "alert" not in text


# --------------------------------------------------------------------------- #
# attachments
# --------------------------------------------------------------------------- #
def test_attachments_are_listed_with_what_the_runner_needs():
    msg = message(payload={
        "mimeType": "multipart/mixed", "headers": [],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64url("hi")}},
            {"mimeType": "application/pdf", "filename": "care-plan.pdf",
             "body": {"attachmentId": "att-9", "size": 2048}},
        ],
    })
    found = gm_map.attachments(msg)
    assert found == [{
        "filename": "care-plan.pdf", "mime_type": "application/pdf",
        "size": 2048, "attachment_id": "att-9",
    }]


def test_inline_images_are_not_attachments():
    """A signature logo has no filename. Ingesting one per email would swamp the
    corpus with the same 4KB image thousands of times."""
    msg = message(payload={
        "mimeType": "multipart/related", "headers": [],
        "parts": [
            {"mimeType": "image/png", "filename": "",
             "body": {"attachmentId": "att-inline", "size": 4096}},
        ],
    })
    assert gm_map.attachments(msg) == []


# --------------------------------------------------------------------------- #
# direction and timing
# --------------------------------------------------------------------------- #
def test_direction_comes_from_the_sent_label():
    """Gmail has no direction field, and comparing From to the mailbox breaks on
    aliases and delegated sending. The label is the reliable signal."""
    assert gm_map.is_outbound(message(labels=["SENT"])) is True
    assert gm_map.is_outbound(message(labels=["INBOX"])) is False


def test_the_date_header_is_preferred_for_when_it_happened():
    when = gm_map.occurred_at(message())
    assert when is not None and when.startswith("2026-07-21")


def test_a_malformed_date_header_falls_back_to_gmails_own_timestamp():
    """Broken Date headers are common enough in real mail that the fallback is
    load-bearing, not defensive decoration."""
    msg = message(headers=[{"name": "Date", "value": "not a date at all"}])
    when = gm_map.occurred_at(msg)
    assert when is not None and when.startswith("2026-")


# --------------------------------------------------------------------------- #
# map_message
# --------------------------------------------------------------------------- #
def test_an_inbound_message_resolves_on_the_sender():
    mapped = gm_map.map_message(message())
    assert mapped is not None
    assert mapped["counterpart"] == "margaret@example.com"
    assert mapped["direction"] == "inbound"
    assert mapped["subject"] == "Care hours question"
    assert mapped["counterpart_name"] == "Margaret Ellison"


def test_an_outbound_message_resolves_on_the_recipient():
    """Same rule as the phone channel: the office's own address is never the
    thing to attribute the message to."""
    msg = message(
        labels=["SENT"],
        headers=[
            {"name": "From", "value": "office@shsgreaternaperville.com"},
            {"name": "To", "value": '"Margaret" <margaret@example.com>'},
            {"name": "Subject", "value": "Re: Care hours"},
        ],
    )
    mapped = gm_map.map_message(msg)
    assert mapped is not None
    assert mapped["counterpart"] == "margaret@example.com"
    assert mapped["direction"] == "outbound"


def test_a_draft_is_not_correspondence():
    assert gm_map.map_message(message(labels=["DRAFT"])) is None


def test_a_message_with_no_usable_address_is_skipped():
    msg = message(headers=[{"name": "Subject", "value": "no sender"}])
    assert gm_map.map_message(msg) is None


def test_a_missing_subject_gets_a_readable_placeholder():
    msg = message(headers=[{"name": "From", "value": "a@b.com"}])
    mapped = gm_map.map_message(msg)
    assert mapped is not None
    assert mapped["subject"] == "(no subject)"


# --------------------------------------------------------------------------- #
# history pages
# --------------------------------------------------------------------------- #
def test_added_ids_are_deduplicated_within_a_page():
    """Gmail repeats a message across history records whenever anything about it
    changes, so the same id commonly appears several times in one page."""
    page = {"history": [
        {"messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}]},
        {"messagesAdded": [{"message": {"id": "m1"}}]},
    ]}
    assert gm_map.added_message_ids(page) == ["m1", "m2"]


def test_an_empty_history_page_yields_nothing():
    assert gm_map.added_message_ids({}) == []
    assert gm_map.added_message_ids({"history": [{"labelsAdded": []}]}) == []
