"""Google Workspace HTTP client (v1.3.0) — the only place that speaks HTTP to Google.

`gmail_runner.py` and `gcal_runner.py` drive it; `gmail_send.py` uses its send
method. Nothing here writes to the database.

No `google-api-python-client`. That library brings a large dependency tree, its
own auth stack and its own retry semantics, to wrap REST endpoints we use six of.
`httpx` + the shared `TokenSource` (written for GoTo in v1.2.0, reused verbatim
here — one implementation, two configs) is smaller and behaves like the rest of
the connector layer.

ENDPOINTS (all relative to their service base):
  * Gmail    `GET  /gmail/v1/users/me/profile`            — historyId bootstrap
             `GET  /gmail/v1/users/me/history`            — incremental changes
             `GET  /gmail/v1/users/me/messages/{id}`      — one message
             `GET  /gmail/v1/users/me/messages/{id}/attachments/{aid}`
             `POST /gmail/v1/users/me/messages/send`
  * Calendar `GET  /calendar/v3/calendars/primary/events` — list (syncToken)
             `POST /calendar/v3/calendars/primary/events` — insert

TWO EXPIRY SEMANTICS worth keeping straight, because they fail differently:
  * Gmail's `historyId` goes stale after roughly a week of no polling and answers
    **404**. There is no way to recover the gap, so the cursor is re-bootstrapped
    from the current profile and the missed window is simply not imported.
  * Calendar's `syncToken` expires and answers **410**. The recovery is a full
    re-list over a bounded window, which does re-deliver events — harmless,
    because ingestion is idempotent by external id.

Failures raise `GoogleError`, which a runner turns into `connector.sync_failed`.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ...config import settings
from .oauth import TokenError, TokenSource

log = logging.getLogger("nexus.connectors.google")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com"

# Least privilege. `gmail.readonly` rather than `gmail.modify`: this integration
# mirrors the mailbox, it never changes it (no marking read, no labelling, no
# deleting). `gmail.send` is send-only and cannot read. Calendar needs read+write
# because `create_calendar_event` is a real gated tool.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
)

_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


class GoogleError(RuntimeError):
    """Any failure talking to Google — auth, network, bad status, bad body."""


class HistoryGone(GoogleError):
    """Gmail's stored `historyId` is too old (404). The gap is unrecoverable;
    the caller re-bootstraps from the current profile."""


class SyncTokenExpired(GoogleError):
    """Calendar's `syncToken` expired (410). The caller drops it and re-lists."""


def token_source(*, client: httpx.AsyncClient | None = None) -> TokenSource:
    """A `TokenSource` bound to the configured Google OAuth client."""
    return TokenSource(
        TOKEN_URL,
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_refresh_token,
        client=client,
    )


def credentials_configured() -> bool:
    """All three credential pieces present — the runner/tool activation check."""
    return bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_refresh_token
    )


class GoogleClient:
    """Async Gmail + Calendar client. One instance per sync cycle (or per call).

        async with GoogleClient() as google:
            profile = await google.gmail_profile()
    """

    def __init__(
        self,
        *,
        tokens: TokenSource | None = None,
        client: httpx.AsyncClient | None = None,
        base_url: str = API_BASE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._tokens = tokens if tokens is not None else token_source(client=client)
        self._owns_tokens = tokens is None

    # -- lifecycle ---------------------------------------------------------
    async def __aenter__(self) -> "GoogleClient":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_tokens:
            await self._tokens.aclose()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    async def _headers(self) -> dict:
        try:
            token = await self._tokens.token()
        except TokenError as exc:
            raise GoogleError(str(exc)) from exc
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # -- request plumbing --------------------------------------------------
    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One authed request with bounded retries on transient statuses.

        404 and 410 are raised as their own types rather than generic failures,
        because both are NORMAL cursor lifecycle events with specific recoveries
        — treating them as errors would make a routine token expiry look like an
        outage and stall the sync.
        """
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        last_error = "no attempt made"
        refreshed = False
        for attempt in range(_MAX_RETRIES):
            headers = {**(await self._headers()), **(kwargs.pop("headers", None) or {})}
            try:
                resp = await self._http().request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code < 400:
                    return resp
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code == 410:
                    raise SyncTokenExpired(last_error)
                if resp.status_code == 404 and "/history" in url:
                    raise HistoryGone(last_error)
                if resp.status_code == 401 and not refreshed:
                    self._tokens.invalidate()
                    refreshed = True
                    continue
                if resp.status_code not in _RETRY_STATUSES:
                    raise GoogleError(f"{method} {url} failed — {last_error}")
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
        raise GoogleError(f"{method} {url} failed after {_MAX_RETRIES} attempts — {last_error}")

    async def get_json(self, path: str, params: dict | None = None) -> dict:
        return _json(await self.request("GET", path, params=params), path)

    async def post_json(self, path: str, body: dict, params: dict | None = None) -> dict:
        return _json(await self.request("POST", path, json=body, params=params), path)

    # -- Gmail -------------------------------------------------------------
    async def gmail_profile(self) -> dict:
        """`users.getProfile` — the credential smoke test, and the source of the
        `historyId` a first-ever run starts from."""
        return await self.get_json("/gmail/v1/users/me/profile")

    async def gmail_history(self, start_history_id: str, page_token: str | None = None) -> dict:
        """`users.history.list` from a cursor. `messageAdded` only — this
        integration cares about mail arriving, not about labels being moved or
        messages being deleted."""
        params: dict = {
            "startHistoryId": start_history_id,
            "historyTypes": "messageAdded",
            "maxResults": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        return await self.get_json("/gmail/v1/users/me/history", params)

    async def gmail_message(self, message_id: str) -> dict:
        """One full message, including the body parts."""
        return await self.get_json(
            f"/gmail/v1/users/me/messages/{message_id}", {"format": "full"}
        )

    async def gmail_attachment(self, message_id: str, attachment_id: str) -> dict:
        """One attachment's base64url `data`."""
        return await self.get_json(
            f"/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}"
        )

    async def gmail_send(self, raw_base64url: str) -> dict:
        """`users.messages.send` with an already-encoded RFC 2822 message."""
        return await self.post_json(
            "/gmail/v1/users/me/messages/send", {"raw": raw_base64url}
        )

    # -- Calendar ----------------------------------------------------------
    async def calendar_events(
        self,
        *,
        sync_token: str | None = None,
        time_min: str | None = None,
        page_token: str | None = None,
        time_max: str | None = None,
        max_results: int = 250,
    ) -> dict:
        """`events.list`. With a `syncToken` this returns only what changed since;
        without one it returns a window and a fresh token to continue from.

        `syncToken` and `timeMin` are mutually exclusive in Google's API — sending
        both is a 400 — so the caller picks one and this passes through what it
        was given.
        """
        params: dict = {"maxResults": max_results, "singleEvents": True}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            if time_min:
                params["timeMin"] = time_min
            if time_max:
                params["timeMax"] = time_max
        if page_token:
            params["pageToken"] = page_token
        return await self.get_json("/calendar/v3/calendars/primary/events", params)

    async def calendar_insert(self, event: dict) -> dict:
        """`events.insert` on the primary calendar."""
        return await self.post_json("/calendar/v3/calendars/primary/events", event)


def _json(resp: httpx.Response, path: str) -> dict:
    if not resp.content:
        return {}
    try:
        body = resp.json()
    except ValueError as exc:
        raise GoogleError(f"{path} returned a non-JSON body: {exc}") from exc
    return body if isinstance(body, dict) else {"data": body}


__all__ = [
    "AUTHORIZE_URL",
    "TOKEN_URL",
    "API_BASE",
    "SCOPES",
    "GoogleClient",
    "GoogleError",
    "HistoryGone",
    "SyncTokenExpired",
    "credentials_configured",
    "token_source",
]
