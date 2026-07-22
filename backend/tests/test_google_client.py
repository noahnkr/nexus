"""Google Workspace client + OAuth bootstrap (v1.3.0, Task 1).

Offline against `httpx.MockTransport`, plus one env-gated live check that the
configured refresh token really yields an access token and a Gmail profile.

`TokenSource` itself is not re-tested here — it is the same implementation
v1.2.0 built and `test_goto_oauth.py` covers, which is the point of having
written it once. What IS tested is the Google-specific surface: the consent
URL's offline-access parameters, the endpoint contracts, and the two cursor
expiry semantics that are easy to mistake for outages.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from app.config import settings
from app.services.connectors.google_client import (
    GoogleClient,
    GoogleError,
    HistoryGone,
    SCOPES,
    SyncTokenExpired,
    credentials_configured,
)


def _client(handler, **kwargs) -> GoogleClient:
    """A client whose HTTP goes to `handler` and whose token is already valid."""
    class _Tokens:
        async def token(self):
            return "access-token"

        def invalidate(self):
            pass

        async def aclose(self):
            pass

    return GoogleClient(
        tokens=_Tokens(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# consent URL
# --------------------------------------------------------------------------- #
def test_consent_url_asks_for_offline_access():
    """Without `access_type=offline` Google issues no refresh token at all, and
    the whole unattended integration is impossible."""
    from app.scripts.google_oauth import build_consent_url

    url = build_consent_url("client-123", "http://localhost:8766", "state-abc")
    assert "access_type=offline" in url
    assert "client_id=client-123" in url
    assert "state=state-abc" in url
    assert "response_type=code" in url


def test_consent_url_forces_the_prompt_so_a_rerun_still_yields_a_refresh_token():
    """Google returns a refresh token only on the first consent per client+account.
    Without `prompt=consent`, re-running the bootstrap silently produces no
    refresh token and looks like a broken script."""
    from app.scripts.google_oauth import build_consent_url

    assert "prompt=consent" in build_consent_url("c", "http://localhost:8766", "s")


def test_consent_url_requests_exactly_the_declared_scopes():
    from app.scripts.google_oauth import build_consent_url
    import urllib.parse

    url = build_consent_url("c", "http://localhost:8766", "s")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["scope"][0].split(" ") == list(SCOPES)


def test_scopes_are_least_privilege():
    """`gmail.readonly` not `gmail.modify`: this integration mirrors the mailbox,
    it never changes it. A widened scope here should be a deliberate decision,
    so the test states the intent."""
    assert any(s.endswith("gmail.readonly") for s in SCOPES)
    assert not any("gmail.modify" in s for s in SCOPES)


# --------------------------------------------------------------------------- #
# endpoint contracts
# --------------------------------------------------------------------------- #
def test_gmail_profile_hits_the_users_me_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"emailAddress": "a@b.com", "historyId": "9911"})

    profile = _run(_client(handler).gmail_profile())
    assert profile["historyId"] == "9911"
    assert seen["url"].endswith("/gmail/v1/users/me/profile")


def test_history_asks_only_for_added_messages():
    """Label changes and deletions are not this integration's business; asking
    for them would mean filtering them out again on every cycle."""
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"history": []})

    _run(_client(handler).gmail_history("500"))
    assert seen["params"]["historyTypes"] == "messageAdded"
    assert seen["params"]["startHistoryId"] == "500"


def test_history_passes_the_page_token_through():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={})

    _run(_client(handler).gmail_history("500", page_token="page-2"))
    assert seen["params"]["pageToken"] == "page-2"


def test_calendar_list_sends_sync_token_or_window_but_never_both():
    """Google rejects `syncToken` together with `timeMin` with a 400. Sending
    both would break every incremental sync in a way that only shows up live."""
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": []})

    client = _client(handler)
    _run(client.calendar_events(sync_token="tok-1", time_min="2026-07-01T00:00:00Z"))
    assert seen["params"].get("syncToken") == "tok-1"
    assert "timeMin" not in seen["params"]

    _run(client.calendar_events(time_min="2026-07-01T00:00:00Z"))
    assert seen["params"].get("timeMin") == "2026-07-01T00:00:00Z"
    assert "syncToken" not in seen["params"]


def test_calendar_insert_posts_the_event_body():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["body"] = request.content
        return httpx.Response(200, json={"id": "evt-1"})

    result = _run(_client(handler).calendar_insert({"summary": "Tour"}))
    assert result["id"] == "evt-1"
    assert seen["method"] == "POST"
    assert b"Tour" in seen["body"]


# --------------------------------------------------------------------------- #
# cursor lifecycle — 404 and 410 are normal, not outages
# --------------------------------------------------------------------------- #
def test_an_expired_history_cursor_raises_its_own_type():
    """Gmail's historyId goes stale after about a week and answers 404. That is a
    cursor lifecycle event with a specific recovery (re-bootstrap), not a failure
    — conflating it with a generic error would stall the sync permanently."""
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "Not Found"}})

    with pytest.raises(HistoryGone):
        _run(_client(handler).gmail_history("1"))


def test_an_expired_sync_token_raises_its_own_type():
    def handler(request):
        return httpx.Response(410, json={"error": {"message": "Sync token is no longer valid"}})

    with pytest.raises(SyncTokenExpired):
        _run(_client(handler).calendar_events(sync_token="stale"))


def test_a_404_somewhere_else_is_still_an_ordinary_error():
    """Only the history endpoint's 404 means "cursor expired". A 404 on a message
    means the message is gone, which is a different thing."""
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "Not Found"}})

    with pytest.raises(GoogleError) as exc:
        _run(_client(handler).gmail_message("missing"))
    assert not isinstance(exc.value, HistoryGone)


def test_a_401_refreshes_the_token_once_and_retries():
    calls = {"n": 0}
    invalidated = {"n": 0}

    class _Tokens:
        async def token(self):
            return "t"

        def invalidate(self):
            invalidated["n"] += 1

        async def aclose(self):
            pass

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"ok": True})

    client = GoogleClient(
        tokens=_Tokens(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert _run(client.gmail_profile()) == {"ok": True}
    assert invalidated["n"] == 1


def test_a_client_error_is_not_retried():
    """Retrying a 403 just burns quota against a permission that will not change
    within the retry window."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, json={"error": {"message": "insufficient scope"}})

    with pytest.raises(GoogleError):
        _run(_client(handler).gmail_profile())
    assert calls["n"] == 1


def test_a_non_json_body_surfaces_plainly():
    def handler(request):
        return httpx.Response(200, content=b"<html>proxy error</html>")

    with pytest.raises(GoogleError) as exc:
        _run(_client(handler).gmail_profile())
    assert "non-JSON" in str(exc.value)


# --------------------------------------------------------------------------- #
# activation
# --------------------------------------------------------------------------- #
def test_credentials_are_required_in_full(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_refresh_token", "")
    assert credentials_configured() is False
    monkeypatch.setattr(settings, "google_refresh_token", "refresh")
    assert credentials_configured() is True


# --------------------------------------------------------------------------- #
# live — gated on real credentials
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not (
        os.getenv("GOOGLE_CLIENT_ID")
        and os.getenv("GOOGLE_CLIENT_SECRET")
        and os.getenv("GOOGLE_REFRESH_TOKEN")
    ),
    reason="GOOGLE_* credentials not configured — run app.scripts.google_oauth first",
)
def test_live_refresh_token_reaches_the_gmail_profile():
    """The one check that proves the consent actually worked end to end.

    Asserts presence, never contents: the mailbox address and historyId are not
    printed, because a test log is not a place for either.
    """
    async def scenario():
        async with GoogleClient() as google:
            return await google.gmail_profile()

    profile = asyncio.run(scenario())
    assert profile.get("emailAddress"), "the profile should name the mailbox"
    assert profile.get("historyId"), "the profile should carry a historyId to start from"
