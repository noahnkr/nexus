"""Shared OAuth TokenSource + the GoTo consent bootstrap (v1.2.0, Task A1).

Offline: a fake httpx transport plays the token endpoint, so caching, the expiry
slack, refresh-token rotation and plain-language failures are all exercised
without the network. A fake clock drives expiry — waiting an hour to prove a
cache boundary is not a test, it's a nap.

Live (env-gated on the three GOTO_CONNECT_* credentials): the one-time consent
has been done and the refresh token still works. The token itself is asserted
non-empty and never printed.
"""
import asyncio
import os

import httpx
import pytest

from app.services.connectors.oauth import TokenError, TokenSource

TOKEN_URL = "https://identity.test/oauth/token"


class _Clock:
    """Monotonic-shaped fake clock the TokenSource reads instead of time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _transport(responses, calls: list[httpx.Request] | None = None) -> httpx.MockTransport:
    """Serve `responses` (a list of httpx.Response factories or dicts) in order;
    the last one repeats once exhausted."""

    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        spec = responses[i]
        if isinstance(spec, httpx.Response):
            return spec
        return httpx.Response(spec.get("status", 200), json=spec.get("json", {}))

    return httpx.MockTransport(handler)


def _source(transport, clock=None, **kwargs) -> TokenSource:
    return TokenSource(
        TOKEN_URL,
        kwargs.pop("client_id", "cid"),
        kwargs.pop("client_secret", "csecret"),
        kwargs.pop("refresh_token", "rtoken"),
        client=httpx.AsyncClient(transport=transport),
        time_fn=clock or _Clock(),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# caching + expiry
# --------------------------------------------------------------------------- #
def test_token_is_cached_until_the_expiry_slack():
    calls: list[httpx.Request] = []
    clock = _Clock()
    transport = _transport(
        [
            {"json": {"access_token": "first", "expires_in": 3600}},
            {"json": {"access_token": "second", "expires_in": 3600}},
        ],
        calls,
    )

    async def scenario():
        source = _source(transport, clock)
        try:
            first = await source.token()
            cached = await source.token()  # no second HTTP call
            clock.advance(3600 - 30)  # inside the 60s slack ⇒ due for refresh
            refreshed = await source.token()
            return first, cached, refreshed
        finally:
            await source.aclose()

    first, cached, refreshed = asyncio.run(scenario())
    assert first == "first"
    assert cached == "first"
    assert refreshed == "second"
    assert len(calls) == 2  # one initial, one after the slack boundary


def test_refresh_posts_the_grant_with_basic_auth():
    calls: list[httpx.Request] = []
    transport = _transport([{"json": {"access_token": "t", "expires_in": 60}}], calls)

    async def scenario():
        source = _source(transport)
        try:
            await source.token()
        finally:
            await source.aclose()

    asyncio.run(scenario())
    (request,) = calls
    assert str(request.url) == TOKEN_URL
    assert b"grant_type=refresh_token" in request.content
    assert b"refresh_token=rtoken" in request.content
    # Client credentials ride the Basic header, never the body.
    assert request.headers["authorization"].startswith("Basic ")
    assert b"client_secret" not in request.content


def test_invalidate_forces_a_refresh():
    calls: list[httpx.Request] = []
    transport = _transport(
        [
            {"json": {"access_token": "a", "expires_in": 3600}},
            {"json": {"access_token": "b", "expires_in": 3600}},
        ],
        calls,
    )

    async def scenario():
        source = _source(transport)
        try:
            a = await source.token()
            source.invalidate()
            b = await source.token()
            return a, b
        finally:
            await source.aclose()

    a, b = asyncio.run(scenario())
    assert (a, b) == ("a", "b")
    assert len(calls) == 2


def test_concurrent_callers_share_one_refresh():
    calls: list[httpx.Request] = []
    transport = _transport([{"json": {"access_token": "one", "expires_in": 3600}}], calls)

    async def scenario():
        source = _source(transport)
        try:
            return await asyncio.gather(*(source.token() for _ in range(5)))
        finally:
            await source.aclose()

    tokens = asyncio.run(scenario())
    assert tokens == ["one"] * 5
    assert len(calls) == 1  # the lock collapsed the stampede


# --------------------------------------------------------------------------- #
# rotation + failure modes
# --------------------------------------------------------------------------- #
def test_rotated_refresh_token_is_adopted_in_process():
    clock = _Clock()
    transport = _transport(
        [
            {"json": {"access_token": "a", "expires_in": 100, "refresh_token": "rotated"}},
            {"json": {"access_token": "b", "expires_in": 100}},
        ]
    )

    async def scenario():
        source = _source(transport, clock)
        try:
            await source.token()
            adopted = source.refresh_token
            clock.advance(100)
            await source.token()
            return adopted
        finally:
            await source.aclose()

    assert asyncio.run(scenario()) == "rotated"


def test_missing_credentials_fail_plainly():
    transport = _transport([{"json": {"access_token": "never"}}])

    async def scenario():
        source = _source(transport, refresh_token="")
        try:
            assert source.configured() is False
            with pytest.raises(TokenError) as exc:
                await source.token()
            return str(exc.value)
        finally:
            await source.aclose()

    message = asyncio.run(scenario())
    assert "not fully configured" in message


def test_rejected_refresh_surfaces_the_status_and_body():
    transport = _transport([{"status": 401, "json": {"error": "invalid_grant"}}])

    async def scenario():
        source = _source(transport)
        try:
            with pytest.raises(TokenError) as exc:
                await source.token()
            return str(exc.value)
        finally:
            await source.aclose()

    message = asyncio.run(scenario())
    assert "401" in message and "invalid_grant" in message


def test_response_without_access_token_is_an_error():
    transport = _transport([{"json": {"expires_in": 3600}}])

    async def scenario():
        source = _source(transport)
        try:
            with pytest.raises(TokenError):
                await source.token()
        finally:
            await source.aclose()

    asyncio.run(scenario())


def test_network_failure_surfaces_as_token_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    async def scenario():
        source = _source(httpx.MockTransport(handler))
        try:
            with pytest.raises(TokenError) as exc:
                await source.token()
            return str(exc.value)
        finally:
            await source.aclose()

    assert "ConnectError" in asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# the consent URL (pure — no browser, no listener)
# --------------------------------------------------------------------------- #
def test_consent_url_carries_scopes_state_and_redirect():
    import urllib.parse

    from app.scripts.goto_oauth import build_consent_url
    from app.services.connectors.goto_client import AUTHORIZE_URL, SCOPES

    url = build_consent_url("client-123", "http://localhost:8765", "st4te")
    assert url.startswith(AUTHORIZE_URL + "?")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert params["client_id"] == ["client-123"]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == ["http://localhost:8765"]
    assert params["state"] == ["st4te"]
    assert params["scope"][0].split(" ") == list(SCOPES)
    # The transcript-bearing scope is load-bearing for the v1.2.0 gate.
    assert "recording.v1.notifications.manage" in SCOPES


# --------------------------------------------------------------------------- #
# live (env-gated): the consent happened and the refresh token still works
# --------------------------------------------------------------------------- #
_LIVE = all(
    os.getenv(name)
    for name in (
        "GOTO_CONNECT_CLIENT_ID",
        "GOTO_CONNECT_CLIENT_SECRET",
        "GOTO_CONNECT_REFRESH_TOKEN",
    )
)


@pytest.mark.skipif(not _LIVE, reason="GOTO_CONNECT_* credentials not configured")
def test_live_refresh_token_obtains_an_access_token():
    from app.services.connectors.goto_client import token_source

    async def scenario():
        source = token_source()
        try:
            return await source.token()
        finally:
            await source.aclose()

    token = asyncio.run(scenario())
    assert isinstance(token, str) and token  # never printed
