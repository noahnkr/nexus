"""`search_communications` tool (v1.1.0). Offline: input validation + registry
shape. Gated (DB + Voyage): a real search returns kind-labeled communication
sources with turn-global citation numbering.
"""
import asyncio
import os
import uuid

import pytest

import conftest
from app.services.tools import get_tool
from app.services.tools.core import ToolInputError


# --------------------------------------------------------------------------- #
# Offline
# --------------------------------------------------------------------------- #

def test_tool_is_registered_safe_and_read_only():
    tool = get_tool("search_communications")
    assert tool is not None
    assert tool.safe is True  # read-only: never gated
    assert "query" in tool.input_schema["properties"]


def test_blank_query_raises_input_error():
    tool = get_tool("search_communications")

    async def go():
        return await tool.handler(None, {"query": "   "})

    with pytest.raises(ToolInputError):
        asyncio.run(go())


def test_empty_result_reports_no_match_plainly(monkeypatch):
    """When retrieval finds nothing (an empty index), the tool returns a plain
    message, not an error. Offline — retrieval is stubbed to return no rows."""
    from app.services.tools import communications as comms_tool

    async def _empty(conn, query, *, limit=8):
        return []

    monkeypatch.setattr(comms_tool, "retrieve_communications", _empty)
    tool = get_tool("search_communications")

    async def go():
        return await tool.handler(None, {"query": "anything"})

    res = asyncio.run(go())
    assert res.is_error is False
    assert res.data["sources"] == []
    assert "No communications matched" in res.summary


# --------------------------------------------------------------------------- #
# Gated
# --------------------------------------------------------------------------- #

_gated = pytest.mark.skipif(
    not (conftest.NEXUS_APP_DB_URL and os.getenv("VOYAGE_API_KEY")),
    reason="NEXUS_APP_DB_URL and VOYAGE_API_KEY required",
)

MARGARET = "33333333-0000-0000-0000-000000000001"

CALL = (
    "Assessment call. The client uses a walker and needs help transferring in and out "
    "of the shower. She is allergic to latex gloves, so caregivers must use nitrile. "
    "Her son handles all billing and wants invoices emailed monthly. She keeps a small "
    "dog and asked whether caregivers are comfortable with pets. Mornings between eight "
    "and eleven work best, and she would like the same caregiver each visit for "
    "continuity. " * 2
)


async def _scenario():
    from app import db
    from app.services.communications import ingest_communication

    tool = get_tool("search_communications")
    tag = uuid.uuid4().hex[:8]
    out: dict = {"tag": tag}
    await db.open_pool()
    try:
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            await conn.execute(
                "delete from public.communications where source = 'test-tool'"
            )
        out["communication_id"] = await ingest_communication(
            conftest.DEMO_TENANT, channel="call", direction="inbound",
            occurred_at="2026-07-05T10:00:00Z", body=f"{CALL} [ref {tag}]",
            entity_type="lead", entity_id=MARGARET, source="test-tool",
            external_id=f"tool:{tag}:call",
        )
        async with db.tenant_tx(conftest.DEMO_TENANT) as conn:
            out["hit"] = await tool.handler(
                conn, {"query": "is she allergic to latex", "start_index": 8}
            )
            await conn.execute(
                "delete from public.communications where source = 'test-tool'"
            )
        return out
    finally:
        await db.close_pool()


@pytest.fixture(scope="module")
def result():
    return asyncio.run(_scenario())


@_gated
def test_tool_returns_kind_labeled_communication_sources(result):
    sources = result["hit"].data["sources"]
    assert sources, "expected at least one source"
    # The demo tenant is shared across the suite and may hold other embedded
    # communications, so assert on OUR row (matched by communication id) rather
    # than on whichever chunk happens to rank first.
    ours = [s for s in sources if s["communication_id"] == result["communication_id"]]
    assert ours, "the ingested call should be among the matches"
    assert ours[0]["kind"] == "communication"
    assert ours[0]["label"].startswith("Call")  # channel label
    assert sources[0]["n"] == 9  # start_index=8 -> first citation is [9]
