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


class _FakeStream:
    def __init__(self, deltas):
        self._deltas = deltas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def _gen():
            for d in self._deltas:
                yield d

        return _gen()

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
