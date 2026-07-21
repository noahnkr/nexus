"""Chat streaming against the REAL LangSmith-wrapped Anthropic client (v1.1.1).

Every other chat test hand-rolls a fake client, which is exactly why none of them
could catch the bug this version fixes: they fake away the wrapper that crashes.
Here only the HTTP transport is faked — `wrap_anthropic` and `AsyncAnthropic` are
genuine, tracing is genuinely enabled, and canned SSE bodies drive a full
two-iteration tool loop.

The bug: `langsmith/wrappers/_anthropic.py` traces `.text_stream` through a path
ending in an unguarded `run_tree.outputs = ...`, which raises
`'NoneType' object has no attribute 'outputs'` whenever the run tree resolves to
None. `chat_service` therefore iterates raw stream events instead.

NOTE FOR FUTURE READERS: this test passes both before and after the v1.1.1 fix —
the live trigger never reproduced offline. It is a CI guard over the real wrapper
(so a later langsmith bump fails here rather than in a user's chat window), not
the reproduction. The fail-first signal is the `.text_stream` tripwire on
`test_chat_tools.FakeStream`.
"""
import asyncio

import anthropic
import httpx
import pytest
from langsmith import Client as LangSmithClient
from langsmith.wrappers import wrap_anthropic

from app.services import chat_service as cs

from test_chat_tools import _collect, _install

# --- canned SSE ------------------------------------------------------------
# Turn 1: tool_use only, no text block at all (the shape a "look this up" question
# produces, and the shape the two reported failures took).
TOOL_USE_SSE = """event: message_start
data: {"type":"message_start","message":{"id":"msg_tool","type":"message","role":"assistant","model":"claude-test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":120,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tu_live","name":"search_communications","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\": \\"Barbara Noftz\\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":18}}

event: message_stop
data: {"type":"message_stop"}

"""

# Turn 2: the streamed answer.
ANSWER = "Her most recent touch point was a call on 3 July."
TEXT_SSE = """event: message_start
data: {"type":"message_start","message":{"id":"msg_text","type":"message","role":"assistant","model":"claude-test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":200,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Her most recent touch point "}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"was a call on 3 July."}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":22}}

event: message_stop
data: {"type":"message_stop"}

"""


def _wrapped_client(bodies):
    """A real wrap_anthropic'd AsyncAnthropic whose transport replays `bodies` in
    order. The LangSmith client is pinned to an unroutable endpoint with a fake key
    so tracing is genuinely ON (the wrapper's traced path is what we're exercising)
    while no run can ever reach the real project — .env's LANGSMITH_* must not be
    picked up here. Upload failures land on langsmith's background thread and are
    swallowed there."""
    served = iter(bodies)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=next(served).encode(),
        )

    ls_client = LangSmithClient(
        api_url="http://127.0.0.1:9/unroutable", api_key="lsv2_pt_fake"
    )
    return wrap_anthropic(
        anthropic.AsyncAnthropic(
            api_key="sk-ant-fake",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
        tracing_extra={"client": ls_client},
    )


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9/unroutable")


def test_tool_loop_survives_the_real_langsmith_wrapper(traced, monkeypatch):
    """A tool-calling turn completes end to end through the genuine wrapper."""
    store = {"messages": [], "events": []}
    captured, calls = [], []
    # Fakes the DB + tool seams exactly as the other chat tests do...
    _install(monkeypatch, store, [], captured, calls)
    # ...then swaps the fake Anthropic client back out for the real wrapped one.
    client = _wrapped_client([TOOL_USE_SSE, TEXT_SSE])
    monkeypatch.setattr(cs, "get_anthropic", lambda: client)

    events = asyncio.run(_collect("t", "thread-live", "most recent touch point?"))

    assert [e for e, _ in events] == [
        "start", "tool", "tool_result", "citations", "text", "text", "done",
    ]
    assert "".join(d["delta"] for e, d in events if e == "text") == ANSWER

    # The tool_use block was decoded from input_json_delta and executed once.
    assert [c[0] for c in calls] == ["search_communications"]
    assert calls[0][1] == {"query": "Barbara Noftz"}

    # Tool arguments streamed as input_json_delta must never leak into the answer.
    assert "Barbara Noftz" not in "".join(
        d["delta"] for e, d in events if e == "text"
    )

    # Usage is summed across both model calls (120+200 in, 18+22 out).
    done = next(d for e, d in events if e == "done")
    assert done["usage"] == {"input_tokens": 320, "output_tokens": 40}


def test_text_only_turn_survives_the_real_langsmith_wrapper(traced, monkeypatch):
    """The no-tool path (a single end_turn response) streams cleanly too."""
    store = {"messages": [], "events": []}
    captured, calls = [], []
    _install(monkeypatch, store, [], captured, calls)
    client = _wrapped_client([TEXT_SSE])
    monkeypatch.setattr(cs, "get_anthropic", lambda: client)

    events = asyncio.run(_collect("t", "thread-live-2", "hello"))

    assert [e for e, _ in events] == ["start", "citations", "text", "text", "done"]
    assert "".join(d["delta"] for e, d in events if e == "text") == ANSWER
    assert calls == []
    # The final assistant answer is persisted verbatim for replay.
    assert store["messages"][-1]["content"] == [{"type": "text", "text": ANSWER}]
