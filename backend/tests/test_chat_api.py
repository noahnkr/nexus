"""Chat SSE + persistence test over the real router/DB path (Module 2 agentic
loop). The Anthropic stream is stubbed (no keys) to script a plain no-tool turn,
which still yields the Module 1 SSE shape. Runs against the real nexus_app DB for
persistence, so it is skipped until NEXUS_APP_DB_URL is set.

Asserts: SSE frame order start -> citations -> text* -> done; user+assistant rows
persisted as content-block jsonb; the second turn sends the full prior history to
the model; the (single) system block and the tools array are cached.
"""
import asyncio
import json

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, bearer_headers

pytestmark = pytest.mark.skipif(
    not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set"
)

DIM = 1024
_capture: dict = {}


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 11
    output_tokens = 7


class _FinalMessage:
    def __init__(self, text):
        self.content = [_Block(text)]
        self.stop_reason = "end_turn"
        self.usage = _Usage()


class _Delta:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Event:
    """A `content_block_delta` frame, shaped like the Anthropic SDK's."""

    def __init__(self, delta):
        self.type = "content_block_delta"
        self.delta = delta


class _FakeStream:
    def __init__(self, deltas):
        self._deltas = deltas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def __aiter__(self):
        for d in self._deltas:
            yield _Event(_Delta("text_delta", text=d))

    @property
    def text_stream(self):
        # Tripwire (v1.1.1) — see test_chat_tools.FakeStream. Consuming
        # `.text_stream` hits the unguarded `run_tree.outputs` deref in
        # LangSmith's Anthropic wrapper and kills live chat turns.
        raise AssertionError("chat must not consume .text_stream (v1.1.1)")

    async def get_final_message(self):
        return _FinalMessage("".join(self._deltas))


class _FakeMessages:
    def stream(self, **kwargs):
        _capture["model"] = kwargs.get("model")
        _capture["system"] = kwargs.get("system")
        _capture["messages"] = list(kwargs.get("messages", []))
        _capture["tools"] = kwargs.get("tools")
        return _FakeStream(["Hello", " there", " [1]"])


class _FakeClient:
    messages = _FakeMessages()


def _patch(monkeypatch):
    from app.services import chat_service, retrieval

    monkeypatch.setattr(chat_service, "get_anthropic", lambda: _FakeClient())

    async def _fake_embed_query(_text):
        return [0.0] * DIM

    monkeypatch.setattr(retrieval, "embed_query", _fake_embed_query)


def _parse_sse(text):
    frames = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        frames.append((event, data))
    return frames


async def _with_app(fn):
    from app import db
    from app.main import app

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            return await fn(ac)
    finally:
        await db.close_pool()


def test_chat_stream_and_persistence(monkeypatch):
    _patch(monkeypatch)

    async def scenario(ac):
        thread = (await ac.post("/api/chat/threads", json={"title": "t"})).json()
        tid = thread["id"]

        r1 = await ac.post(f"/api/chat/threads/{tid}/messages", json={"content": "first question"})
        frames = _parse_sse(r1.text)
        names = [e for e, _ in frames]
        assert names[0] == "start"
        assert names[1] == "citations"
        assert "text" in names
        assert names[-1] == "done"
        # assembled assistant text from deltas
        assistant_text = "".join(
            d["delta"] for e, d in frames if e == "text"
        )
        assert assistant_text == "Hello there [1]"

        # history restore endpoint shows both messages as content-block arrays
        msgs = (await ac.get(f"/api/chat/threads/{tid}/messages")).json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"][0]["type"] == "text"
        assert msgs[0]["content"][0]["text"] == "first question"
        assert msgs[1]["content"][0]["text"] == "Hello there [1]"

        # second turn: the model receives the full prior history + new message
        await ac.post(f"/api/chat/threads/{tid}/messages", json={"content": "second question"})
        sent = _capture["messages"]
        assert [m["role"] for m in sent] == ["user", "assistant", "user"]
        assert sent[-1]["content"][0]["text"] == "second question"

        # system is now a single cached persona block (M2: no per-turn context block)
        assert _capture["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert len(_capture["system"]) == 1
        # the tools array is sent and cached (breakpoint on its last entry)
        assert _capture["tools"] and _capture["tools"][-1]["cache_control"] == {"type": "ephemeral"}

        return tid

    tid = _run_and_cleanup(scenario)
    assert tid


def _run_and_cleanup(scenario):
    tid_holder = {}

    async def wrapped(ac):
        tid = await scenario(ac)
        tid_holder["tid"] = tid
        return tid

    try:
        return asyncio.run(_with_app(wrapped))
    finally:
        import psycopg

        if tid_holder.get("tid"):
            with psycopg.connect(NEXUS_APP_DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "select set_config('request.app.tenant_id', %s, false)",
                        (DEMO_TENANT,),
                    )
                    cur.execute(
                        "delete from public.chat_threads where id=%s", (tid_holder["tid"],)
                    )
                conn.commit()


def test_turn_failure_hides_internals_and_logs_the_traceback(monkeypatch, caplog):
    """A crash mid-turn must reach the user as plain language, never as the raw
    exception — the v1.1.1 regression, where LangSmith's
    `'NoneType' object has no attribute 'outputs'` was rendered into the chat
    window. The traceback belongs in the server log instead."""
    _patch(monkeypatch)

    from app.routers import chat as chat_router

    def _boom(*_a, **_kw):
        async def _gen():
            raise RuntimeError("'NoneType' object has no attribute 'outputs'")
            yield  # pragma: no cover — makes _boom an async generator

        return _gen()

    monkeypatch.setattr(chat_router, "stream_chat_turn", _boom)

    async def scenario(ac):
        thread = (await ac.post("/api/chat/threads", json={"title": "t"})).json()
        tid = thread["id"]
        with caplog.at_level("ERROR", logger="nexus.chat"):
            r = await ac.post(
                f"/api/chat/threads/{tid}/messages", json={"content": "boom?"}
            )
        frames = _parse_sse(r.text)

        assert [e for e, _ in frames] == ["error"]
        message = frames[0][1]["message"]
        assert message == chat_router.GENERIC_ERROR
        # no internals leak to the chat window
        assert "NoneType" not in message
        assert "outputs" not in message
        assert "RuntimeError" not in message

        # ...but the traceback IS logged, which is where it is diagnosable
        records = [r for r in caplog.records if r.name == "nexus.chat"]
        assert records, "the failed turn must be logged"
        assert records[0].exc_info is not None
        assert isinstance(records[0].exc_info[1], RuntimeError)
        assert "outputs" in str(records[0].exc_info[1])

        return tid

    assert _run_and_cleanup(scenario)
