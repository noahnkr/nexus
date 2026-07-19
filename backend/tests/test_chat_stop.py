"""Offline test of the stop-stream contract (Module 15a, Task 1).

No DB, no keys — reuses the fake Anthropic client / connection / tool seam from
test_chat_tools.py. The point of the contract is replay validity: chat history is
replayed verbatim to the Messages API, which requires alternating roles and
rejects empty text blocks. So an aborted turn must leave the thread ending on a
non-empty `assistant` message, whether the abort lands mid-text or mid-tool-loop.

Covers:
  * a mid-text abort persists the partial text with metadata.stopped and logs
    chat.message.stopped (not chat.message.completed),
  * an abort before any text still persists a non-empty placeholder,
  * the next turn on the same thread sees a well-formed alternating history,
  * both abort shapes (generator close, task cancellation) take the same path.
"""
import asyncio

import pytest

from app.services import chat_service as cs

from test_chat_tools import FakeBlock, FakeMessage, _install

TXT = FakeBlock("text", text="the full answer")


def _store():
    return {"messages": [], "events": []}


def _events(store, event_type):
    """log_event params are positional: (tenant, source, type, ent_type, ent_id, payload)."""
    return [e for e in store["events"] if e[2] == event_type]


def _payload(event):
    payload = event[5]
    return getattr(payload, "obj", payload)


async def _abort(gen, cancel=False):
    """Abort a turn the way the server does. `aclose()` (GeneratorExit) returns
    normally; `athrow(CancelledError)` re-raises at the caller, as Starlette's task
    cancellation would. Both must run the same close-out path inside the generator.
    Yields control twice afterwards so the shielded persistence task settles."""
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await gen.athrow(asyncio.CancelledError)
    else:
        await gen.aclose()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _drain_then_stop(gen, *, text_events, cancel=False):
    """Consume events until `text_events` text frames have arrived, then abort."""
    seen = 0
    async for name, _data in gen:
        if name == "text":
            seen += 1
            if seen >= text_events:
                break
    await _abort(gen, cancel)


# ---------------------------------------------------------------------------
# Abort mid-text
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cancel", [False, True], ids=["close", "cancel"])
def test_stop_midtext_persists_partial(monkeypatch, cancel):
    store = _store()
    script = [(["Hello ", "there ", "friend"], FakeMessage([TXT], "end_turn"))]
    _install(monkeypatch, store, script, [], [])

    async def scenario():
        gen = cs.stream_chat_turn("t1", "th1", "hi")
        await _drain_then_stop(gen, text_events=2, cancel=cancel)

    asyncio.run(scenario())

    # History: user question, then the partial assistant answer. Alternating.
    roles = [m["role"] for m in store["messages"]]
    assert roles == ["user", "assistant"]

    last = store["messages"][-1]
    assert last["content"] == [{"type": "text", "text": "Hello there"}]

    # chat.message.stopped, not completed.
    assert len(_events(store, "chat.message.stopped")) == 1
    assert _events(store, "chat.message.completed") == []
    assert _payload(_events(store, "chat.message.stopped")[0])["summary"] == (
        "Response stopped by the user"
    )


def test_stop_before_any_text_persists_placeholder(monkeypatch):
    """An empty text block would be rejected on replay — a placeholder is written."""
    store = _store()
    script = [(["first"], FakeMessage([TXT], "end_turn"))]
    _install(monkeypatch, store, script, [], [])

    async def scenario():
        gen = cs.stream_chat_turn("t1", "th1", "hi")
        # Stop at the very first event ("start"), before any text streams.
        async for _name, _data in gen:
            break
        await _abort(gen)

    asyncio.run(scenario())

    last = store["messages"][-1]
    assert last["role"] == "assistant"
    (block,) = last["content"]
    assert block["type"] == "text"
    assert block["text"] == cs.STOPPED_PLACEHOLDER
    assert block["text"].strip(), "an empty text block breaks verbatim replay"


# ---------------------------------------------------------------------------
# The next turn still works — the real reason the contract exists
# ---------------------------------------------------------------------------
def test_next_turn_after_stop_sees_valid_history(monkeypatch):
    store = _store()
    captured: list[dict] = []
    script = [
        (["partial answer here"], FakeMessage([TXT], "end_turn")),  # turn 1 (stopped)
        (["a complete answer"], FakeMessage([TXT], "end_turn")),  # turn 2
    ]
    _install(monkeypatch, store, script, captured, [])

    async def scenario():
        gen = cs.stream_chat_turn("t1", "th1", "first question")
        await _drain_then_stop(gen, text_events=1)
        # A fresh turn on the same thread must complete normally.
        return [(e, d) async for e, d in cs.stream_chat_turn("t1", "th1", "second question")]

    events = asyncio.run(scenario())

    assert events[-1][0] == "done"

    # The second request replayed a well-formed history: strictly alternating
    # roles starting at user, with no empty text blocks.
    replayed = captured[1]["messages"]
    assert [m["role"] for m in replayed] == ["user", "assistant", "user"]
    for m in replayed:
        for block in m["content"]:
            if block.get("type") == "text":
                assert block["text"].strip(), f"empty text block in {m['role']} message"


# ---------------------------------------------------------------------------
# Aborting inside the tool loop
# ---------------------------------------------------------------------------
def test_stop_during_tool_loop_keeps_alternation(monkeypatch):
    """An abort inside a tool iteration rolls that iteration's transaction back —
    the assistant tool_use message and its tool_result reply commit together at the
    end of the iteration, so nothing partial is written. The close-out still appends
    an assistant message, keeping the history alternating."""
    store = _store()
    tool_use = FakeBlock(
        "tool_use", id="tu1", name="search_documents", input={"query": "visits"}
    )
    script = [
        ([], FakeMessage([tool_use], "tool_use")),
        (["answer"], FakeMessage([TXT], "end_turn")),
    ]
    _install(monkeypatch, store, script, [], [])

    async def scenario():
        gen = cs.stream_chat_turn("t1", "th1", "hi")
        # Run until the tool result lands, then abort before the final answer.
        async for name, _data in gen:
            if name == "tool_result":
                break
        await _abort(gen)

    asyncio.run(scenario())

    roles = [m["role"] for m in store["messages"]]
    # The tool iteration's writes rolled back with its transaction, so the thread is
    # the user question plus the close-out assistant message — still alternating.
    assert roles == ["user", "assistant"]
    assert store["messages"][-1]["content"][0]["text"] == cs.STOPPED_PLACEHOLDER
    assert len(_events(store, "chat.message.stopped")) == 1
