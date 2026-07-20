"""WelcomeHome HTTP client (Module 18a, Task 1).

Offline: a fake httpx transport serves the CSV fixtures with a `Link` chain, so
the pager's cursor-following and CSV parsing are exercised without the network.
The cursor pacing is dialed to zero here — the real 21s interval is a rate-limit
concession, not behavior worth waiting for in a test.

Live (env-gated on WELCOMEHOME_API_KEY): the credential smoke test.
"""
import asyncio
import os
import pathlib

import httpx
import pytest

from app.services.connectors.wh_client import (
    WelcomeHomeClient,
    WelcomeHomeError,
    parse_csv_rows,
    parse_next_link,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "wh"

BASE = "https://wh.test"
PAGES = ["prospects_page1.csv", "prospects_page2.csv", "prospects_page3.csv"]


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _paged_transport(calls: list[str] | None = None) -> httpx.MockTransport:
    """Serve the three prospect fixture pages as a Link-header chain.

    Page N returns `rel="next"` pointing at page N+1; the last page returns no
    Link header at all, which is how the walk terminates.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        page = int(request.url.params.get("page", "1"))
        headers = {"content-type": "text/csv"}
        if page < len(PAGES):
            headers["Link"] = f'<{BASE}/next?page={page + 1}>; rel="next"'
        return httpx.Response(200, text=_fixture(PAGES[page - 1]), headers=headers)

    return httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport) -> WelcomeHomeClient:
    return WelcomeHomeClient(
        api_key="test-key",
        base_url=BASE,
        community_id="65648",
        client=httpx.AsyncClient(transport=transport),
        cursor_interval=0,
    )


# --- pure parsers ---------------------------------------------------------
def test_parse_next_link_reads_the_next_relation():
    header = '<https://wh.test/a?page=2>; rel="next", <https://wh.test/a?page=1>; rel="prev"'
    assert parse_next_link(header) == "https://wh.test/a?page=2"


def test_parse_next_link_without_a_next_relation_ends_the_walk():
    assert parse_next_link(None) is None
    assert parse_next_link("") is None
    assert parse_next_link('<https://wh.test/a?page=1>; rel="prev"') is None


def test_parse_csv_rows_handles_a_header_only_body():
    """The normal "nothing changed since the cursor" response — not an error."""
    assert parse_csv_rows("prospects.id,stages.name\n") == []
    assert parse_csv_rows("") == []


def test_parse_csv_rows_strips_keys_and_values():
    rows = parse_csv_rows(" prospects.id , stages.name \n 9001 , Inquiry \n")
    assert rows == [{"prospects.id": "9001", "stages.name": "Inquiry"}]


# --- export pager ---------------------------------------------------------
def test_export_pages_follows_the_link_chain_across_three_pages():
    calls: list[str] = []

    async def scenario():
        async with _client(_paged_transport(calls)) as wh:
            return [page async for page in wh.export_pages("Prospects")]

    pages = asyncio.run(scenario())

    assert len(pages) == 3
    ids = [row["prospects.id"] for page in pages for row in page]
    assert ids == ["9001", "9002", "9003", "9004", "9005", "9006"]

    # Real column names survive the parse — wh_map looks these up verbatim.
    first = pages[0][0]
    assert first["stages.name"] == "Inquiry"
    assert first["lead_sources.name"] == "A Place For Mom"
    assert first["prospects.story"].startswith("Daughter called after a fall")

    # The first request carries the table URL + params; the follow-ups use the
    # cursor URL from the Link header and must NOT re-send the filter params.
    assert calls[0].startswith(f"{BASE}/api/exports/community/65648/table/Prospects")
    assert calls[1] == f"{BASE}/next?page=2"
    assert calls[2] == f"{BASE}/next?page=3"


def test_export_pages_sends_the_incremental_watermark():
    calls: list[str] = []

    async def scenario():
        async with _client(_paged_transport(calls)) as wh:
            async for _ in wh.export_pages("Prospects", "2026-06-01T00:00:00Z"):
                break

    asyncio.run(scenario())
    assert "updated_at_after" in calls[0]


def test_export_pages_stops_cleanly_on_an_empty_table():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="prospects.id\n")
    )

    async def scenario():
        async with _client(transport) as wh:
            return [page async for page in wh.export_pages("Prospects")]

    assert asyncio.run(scenario()) == []


# --- reference endpoints --------------------------------------------------
def test_reference_unwraps_an_enveloped_payload():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"stages": [{"id": 1, "name": "Inquiry"}]})
    )

    async def scenario():
        async with _client(transport) as wh:
            return await wh.reference("stages")

    assert asyncio.run(scenario()) == [{"id": 1, "name": "Inquiry"}]


def test_reference_rejects_an_endpoint_outside_the_allowlist():
    async def scenario():
        async with _client(httpx.MockTransport(lambda r: httpx.Response(200))) as wh:
            await wh.reference("prospects")

    with pytest.raises(WelcomeHomeError, match="unknown reference endpoint"):
        asyncio.run(scenario())


# --- failure handling -----------------------------------------------------
def test_a_client_error_raises_without_retrying():
    """A 401 must fail fast — retrying a bad credential just burns the rate limit."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(401, text="unauthorized")

    async def scenario():
        async with _client(httpx.MockTransport(handler)) as wh:
            await wh.ping()

    with pytest.raises(WelcomeHomeError, match="HTTP 401"):
        asyncio.run(scenario())
    assert len(calls) == 1


def test_a_missing_api_key_fails_before_any_request():
    async def scenario():
        async with WelcomeHomeClient(api_key="", base_url=BASE) as wh:
            await wh.ping()

    with pytest.raises(WelcomeHomeError, match="not configured"):
        asyncio.run(scenario())


# --- live smoke -----------------------------------------------------------
@pytest.mark.skipif(
    not os.getenv("WELCOMEHOME_API_KEY"), reason="WELCOMEHOME_API_KEY not configured"
)
def test_wh_live_ping():
    """Credential smoke test against the real account (read-only)."""

    async def scenario():
        async with WelcomeHomeClient() as wh:
            return await wh.ping()

    assert asyncio.run(scenario())["account_id"] == 18754


@pytest.mark.skipif(
    not os.getenv("WELCOMEHOME_API_KEY"), reason="WELCOMEHOME_API_KEY not configured"
)
def test_wh_live_stage_vocabulary_still_matches_the_map():
    """The stage map is keyed on `system_type`/position (wh_map). If WelcomeHome
    ever drops those anchors this test fails loudly rather than the sync silently
    leaving every lead's status unchanged."""

    async def scenario():
        async with WelcomeHomeClient() as wh:
            return await wh.reference("stages")

    stages = asyncio.run(scenario())
    system_types = {s.get("system_type") for s in stages}
    assert {"new_lead", "visit", "move_in"} <= system_types
