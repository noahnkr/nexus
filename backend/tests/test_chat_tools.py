"""Offline test of the Module 2 agentic chat loop (Task 5).

No DB, no keys: the Anthropic client, the tenant transaction, and execute_tool
are all faked. A tiny in-memory message store stands in for chat_messages so
history replay across turns can be asserted.

Covers:
  * SSE order for a two-tool turn: start -> tool -> tool_result (x2) -> citations
    -> text* -> done.
  * turn-global citation numbering / start_index injection.
  * verbatim persistence of tool_use + tool_result blocks, and that the next
    model call replays the full block history.
  * the no-tool turn still yields the M1 sequence start -> citations(empty) ->
    text -> done.
"""
import asyncio
from contextlib import asynccontextmanager

import pytest

from app.services import chat_service as cs
from app.services.tools.core import ToolResult


# --- fake DB ----------------------------------------------------------------
def _unwrap(v):
    return getattr(v, "obj", v)


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        if "from public.chat_threads where id" in s:
            self._rows = [{"id": params[0]}]  # thread exists
        elif "insert into public.chat_messages" in s:
            if "'user'" in s:  # initial user insert: role hardcoded in SQL
                role, content = "user", _unwrap(params[2])
            else:  # _persist_message: (tenant, thread, role, content, cite, meta)
                role, content = params[2], _unwrap(params[3])
            mid = f"msg-{len(self.store['messages'])}"
            self.store["messages"].append({"id": mid, "role": role, "content": content})
            self._rows = [{"id": mid}]
        elif "select role, content from public.chat_messages" in s:
            self._rows = [
                {"role": m["role"], "content": m["content"]}
                for m in self.store["messages"]
            ]
        else:
            self._rows = []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self, row_factory=None):
        return FakeCursor(self.store)

    async def execute(self, sql, params=None):
        if "insert into public.events" in sql.lower():
            self.store["events"].append(params)


# --- fake Anthropic ---------------------------------------------------------
class FakeBlock:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class FakeUsage:
    input_tokens = 10
    output_tokens = 5


class FakeMessage:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


class FakeStream:
    def __init__(self, deltas, final):
        self._deltas = deltas
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def gen():
            for d in self._deltas:
                yield d

        return gen()

    async def get_final_message(self):
        return self._final


class FakeMessages:
    def __init__(self, script, captured):
        self.script = script
        self.captured = captured
        self.i = 0

    def stream(self, **kwargs):
        # Snapshot messages — the loop reuses one growing list object across calls.
        snap = dict(kwargs)
        snap["messages"] = list(kwargs.get("messages", []))
        self.captured.append(snap)
        deltas, final = self.script[self.i]
        self.i += 1
        return FakeStream(deltas, final)


class FakeClient:
    def __init__(self, script, captured):
        self.messages = FakeMessages(script, captured)


# --- harness ----------------------------------------------------------------
def _install(monkeypatch, store, script, captured, calls):
    monkeypatch.setattr(cs, "get_anthropic", lambda: FakeClient(script, captured))

    @asynccontextmanager
    async def fake_tx(tenant_id):
        yield FakeConn(store)

    monkeypatch.setattr(cs, "tenant_tx", fake_tx)

    async def fake_execute_tool(conn, tenant_id, name, args, *, source_system="chat"):
        calls.append((name, dict(args)))
        if name == "search_documents":
            start = int(args.get("start_index", 0))
            srcs = [{
                "n": start + 1,
                "document_id": "d1",
                "filename": "notes.pdf",
                "chunk_id": "c1",
                "chunk_index": 0,
                "snippet": "snip",
                "chunk_text": "full passage text",
            }]
            return ToolResult("Found 1 document passage(s).", {"query": args.get("query"), "sources": srcs})
        if name == "list_leads":
            return ToolResult("Found 1 lead(s).", {"leads": [{"name": "Margaret Ellison"}], "count": 1})
        return ToolResult("ok", {})

    monkeypatch.setattr(cs, "execute_tool", fake_execute_tool)


async def _collect(tenant, thread, text):
    return [(e, d) async for e, d in cs.stream_chat_turn(tenant, thread, text)]


TU1 = FakeBlock("tool_use", id="tu1", name="search_documents", input={"query": "morning visits"})
TU2 = FakeBlock("tool_use", id="tu2", name="list_leads", input={"status": "new"})


def test_two_tool_turn_sse_order_and_replay(monkeypatch):
    store = {"messages": [], "events": []}
    captured, calls = [], []
    script = [
        ([], FakeMessage([TU1], "tool_use")),
        ([], FakeMessage([TU2], "tool_use")),
        (["Carmen ", "and Evelyn."], FakeMessage([FakeBlock("text", text="Carmen and Evelyn.")], "end_turn")),
    ]
    _install(monkeypatch, store, script, captured, calls)

    events = asyncio.run(_collect("t", "thread-1", "who can do dementia care?"))
    names = [e for e, _ in events]
    assert names == [
        "start", "tool", "tool_result", "tool", "tool_result",
        "citations", "text", "text", "done",
    ]

    # tools were executed in order, with turn-global start_index injected.
    assert [c[0] for c in calls] == ["search_documents", "list_leads"]
    assert calls[0][1]["start_index"] == 0

    # citations aggregate the search passages (turn-global n), no chunk_text leak.
    citations = next(d for e, d in events if e == "citations")
    assert [s["n"] for s in citations["sources"]] == [1]
    assert "chunk_text" not in citations["sources"][0]

    # streamed answer text.
    assert "".join(d["delta"] for e, d in events if e == "text") == "Carmen and Evelyn."

    # verbatim persistence: each tool-use iteration persists its own
    # assistant(tool_use) + user(tool_result) pair, then the final assistant answer.
    roles = [m["role"] for m in store["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert store["messages"][1]["content"][0]["type"] == "tool_use"
    assert store["messages"][2]["content"][0]["type"] == "tool_result"
    assert store["messages"][2]["content"][0]["tool_use_id"] == "tu1"
    assert store["messages"][3]["content"][0]["name"] == "list_leads"
    assert store["messages"][4]["content"][0]["tool_use_id"] == "tu2"
    assert store["messages"][5]["content"][0]["text"] == "Carmen and Evelyn."

    # the 2nd model call replays the tool_use/tool_result block history.
    second_call_messages = captured[1]["messages"]
    assert [m["role"] for m in second_call_messages] == ["user", "assistant", "user"]
    assert second_call_messages[1]["content"][0]["type"] == "tool_use"
    assert second_call_messages[2]["content"][0]["type"] == "tool_result"

    # tools array cached (breakpoint on last), system block cached.
    assert captured[0]["tools"][-1].get("cache_control") == {"type": "ephemeral"}
    assert captured[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_second_turn_replays_prior_history(monkeypatch):
    store = {"messages": [], "events": []}
    captured, calls = [], []

    # Turn 1: one tool then an answer.
    script1 = [
        ([], FakeMessage([TU1], "tool_use")),
        (["Answer one."], FakeMessage([FakeBlock("text", text="Answer one.")], "end_turn")),
    ]
    _install(monkeypatch, store, script1, captured, calls)
    asyncio.run(_collect("t", "thread-1", "first question"))

    # Turn 2: a fresh script; the loaded history must include turn 1's blocks.
    captured2 = []
    script2 = [(["Answer two."], FakeMessage([FakeBlock("text", text="Answer two.")], "end_turn"))]
    monkeypatch.setattr(cs, "get_anthropic", lambda: FakeClient(script2, captured2))
    asyncio.run(_collect("t", "thread-1", "second question"))

    replayed = captured2[0]["messages"]
    roles = [m["role"] for m in replayed]
    # user q1, assistant(tool_use), user(tool_result), assistant a1, user q2
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert any(
        b.get("type") == "tool_use" for m in replayed for b in m["content"]
    )
    assert replayed[-1]["content"][0]["text"] == "second question"


def test_no_tool_turn_matches_m1_sequence(monkeypatch):
    store = {"messages": [], "events": []}
    captured, calls = [], []
    script = [(["Hi ", "there."], FakeMessage([FakeBlock("text", text="Hi there.")], "end_turn"))]
    _install(monkeypatch, store, script, captured, calls)

    events = asyncio.run(_collect("t", "thread-1", "hello"))
    names = [e for e, _ in events]
    assert names[0] == "start"
    assert names[1] == "citations"
    assert names[-1] == "done"
    assert "text" in names
    # no tools called; citations empty.
    assert calls == []
    citations = next(d for e, d in events if e == "citations")
    assert citations["sources"] == []
