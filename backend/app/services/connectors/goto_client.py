"""GoTo Connect HTTP client (v1.2.0) — the only place that speaks HTTP to GoTo.

`goto_runner.py` drives it, `gt_map.py` translates what it returns, and
`goto_bridge.py` consumes the WebSocket channel it creates. Nothing here writes
to the database.

ENDPOINTS (from developer.goto.com at build time — the portal, not memory, is the
authority; re-check before changing any of these):

  * Authorization  `https://authentication.logmeininc.com/oauth/authorize`
                   — authorization-code flow is MANDATORY: this OAuth client
                     rejects `client_credentials` with 401 "Unauthorized grant
                     type" (probed 2026-07-20).
  * Token          `https://identity.goto.com/oauth/token`
                   — the host the 2026-07-20 probe verified. The portal's
                     migration guide names `authentication.logmeininc.com/oauth/
                     token` for the same service; both front one identity system.
  * Channels       `POST /notification-channel/v1/channels/{nickname}`
                   — `{"channelType": "WebSockets", "applicationTag": …}` returns
                     `{channelId, channelURL (wss://…), channelLifetime}`.
                     **channelLifetime is ~1200 seconds** for a WebSocket channel,
                     which is why channel upkeep runs every connector cycle rather
                     than daily.
  * Subscriptions  `POST /call-events/v1/subscriptions`
                   — `{"channelId": …, "accountKeys": [{"id": …, "events": ["ENDING"]}]}`;
                     answers HTTP 207 Multi-Status with per-account results and
                     **no subscription ids**. `REPORT_SUMMARY` is rejected here —
                     see `subscribe_calls`.
                   `POST /messaging/v1/subscriptions` additionally REQUIRES
                     `ownerPhoneNumber` (the subscription is per line, not per
                     account) — see `subscribe_messages`.

    **Read the `constraints` array, never the `message`.** Every 400 on the
    call-events endpoint says `"The Notification Channel must be a WebSocket
    channel"` regardless of what is actually wrong — a plain GET with no
    parameters returns that same sentence with `{"field": "ID", "constraint":
    "REQUIRED"}`. It is boilerplate. Two hours went into the channel before the
    constraint array turned out to be naming the event vocabulary.
  * Messaging      `POST /messaging/v1/messages`
                   — `{"ownerPhoneNumber", "contactPhoneNumbers": [...], "body"}`.

SCOPES (requested by `app/scripts/goto_oauth.py`; the consent screen is the
authority on what the account actually grants):
  call-events.v1.notifications.manage, call-events.v1.events.read,
  messaging.v1.send, messaging.v1.read, recording.v1.notifications.manage,
  identity:scim.me

Failures raise `GoToError`, which the runner converts into a
`connector.sync_failed` event — a bad cycle must never kill the sync loop.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ...config import settings
from .oauth import TokenError, TokenSource

log = logging.getLogger("nexus.connectors.goto")

AUTHORIZE_URL = "https://authentication.logmeininc.com/oauth/authorize"
TOKEN_URL = "https://identity.goto.com/oauth/token"
API_BASE = "https://api.goto.com"

# The scopes the one-time consent asks for. Kept here (not in the script) so the
# client, the script and the README quote one list. Least privilege: this account's
# OAuth client offers ~35 scopes (fax, presence, contacts, call-parking, webrtc,
# call-control…) and none of the rest are asked for.
#
# The A2 probe (2026-07-21) established that the first six alone are NOT enough:
# they receive events and send SMS, but every read surface where a transcript
# could live answers 403 AUTHZ_INSUFFICIENT_SCOPE. `recording.v1.read` —
# "Retrieve call recordings and transcripts" — is the scope the transcript
# requirement rests on; without it v1.2.0 cannot ship (user-locked).
SCOPES = (
    # inbound call events over the notification channel
    "call-events.v1.notifications.manage",
    "call-events.v1.events.read",
    # recordings + TRANSCRIPTS: the notification tells us one is ready, the read
    # scope fetches its text.
    "recording.v1.notifications.manage",
    "recording.v1.read",
    # SMS both ways. `notifications.manage` is what lets inbound texts ride the
    # same channel as calls instead of needing a Messaging-API poll (Task 7).
    "messaging.v1.send",
    "messaging.v1.read",
    "messaging.v1.notifications.manage",
    # call history for the PBX's lines — the record a completed call resolves to.
    "cr.v1.read",
    # line/phone-number lookup, for `goto_connect_line_id` auto-discovery.
    "users.v1.lines.read",
    "voice-admin.v1.read",
    # the account key the call-events subscription is keyed by.
    "identity:scim.me",
)

_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


class GoToError(RuntimeError):
    """Any failure talking to GoTo — auth, network, bad status, bad body."""


def token_source(*, client: httpx.AsyncClient | None = None) -> TokenSource:
    """A `TokenSource` bound to the configured GoTo OAuth client."""
    return TokenSource(
        TOKEN_URL,
        settings.goto_connect_client_id,
        settings.goto_connect_client_secret,
        settings.goto_connect_refresh_token,
        client=client,
    )


def credentials_configured() -> bool:
    """All three credential pieces present — the runner/bridge activation check."""
    return bool(
        settings.goto_connect_client_id
        and settings.goto_connect_client_secret
        and settings.goto_connect_refresh_token
    )


class GoToClient:
    """Async GoTo API client. One instance per sync cycle (or per script).

        async with GoToClient() as goto:
            me = await goto.me()
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
    async def __aenter__(self) -> "GoToClient":
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
            raise GoToError(str(exc)) from exc
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # -- request plumbing --------------------------------------------------
    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One authed request with bounded retries on transient statuses.

        A 401 refreshes the access token once and retries — a channel-upkeep cycle
        should survive a token revoked mid-flight rather than fail the sweep.
        Any other 4xx raises immediately: retrying a 403 just burns quota.
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
                if resp.status_code == 401 and not refreshed:
                    self._tokens.invalidate()
                    refreshed = True
                    continue
                if resp.status_code not in _RETRY_STATUSES:
                    raise GoToError(f"{method} {url} failed — {last_error}")
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
        raise GoToError(f"{method} {url} failed after {_MAX_RETRIES} attempts — {last_error}")

    async def get_json(self, path: str, params: dict | None = None) -> dict:
        return _json(await self.request("GET", path, params=params), path)

    async def post_json(self, path: str, body: dict) -> dict:
        return _json(await self.request("POST", path, json=body), path)

    # -- public surface ----------------------------------------------------
    async def me(self) -> dict:
        """`GET /identity/v1/Users/me` — the credential smoke test."""
        return await self.get_json("/identity/v1/Users/me")

    async def account_key(self) -> str:
        """The PBX account key every call-events subscription is keyed by.

        `/identity/v1/Users/me` does NOT carry it (verified 2026-07-21 — it
        returns SCIM identity, not telephony), so it comes from the user's lines.
        The caller caches the result in `connector_state`; this is a discovery
        call, not a per-cycle one.
        """
        lines = await self.get_json("/users/v1/lines")
        for item in lines.get("items") or []:
            key = item.get("accountKey")
            if key:
                return str(key)
        raise GoToError("no accountKey on any line — cannot subscribe to call events")

    async def create_channel(self, nickname: str) -> dict:
        """Create a WebSocket notification channel.

        Returns `{"channel_id", "url", "lifetime_seconds"}`. The portal's example
        shows `channelURL` at the top level while the live API nests it under
        `channelData` (verified 2026-07-21) — both are accepted, because a
        response shape that varies by documentation version is not something to
        be strict about.

        **Channels are short-lived.** `channelLifetime` comes back around 1200
        seconds, which is why upkeep runs every connector cycle rather than daily.
        """
        body = await self.post_json(
            f"/notification-channel/v1/channels/{nickname}",
            {"channelType": "WebSockets", "applicationTag": nickname},
        )
        url = body.get("channelURL") or (body.get("channelData") or {}).get("channelURL")
        if not url:
            raise GoToError("channel response carried no channelURL")
        return {
            "channel_id": body.get("channelId"),
            "url": url,
            "lifetime_seconds": int(body.get("channelLifetime") or 0),
        }

    async def subscribe_calls(self, channel_id: str, account_key: str) -> list[str]:
        """Subscribe a channel to call-ending events. Returns the account keys
        that actually subscribed.

        **`ENDING`, not `REPORT_SUMMARY` — established live 2026-07-22.** The
        original code asked for `REPORT_SUMMARY` because it carries a COMPLETE
        call in one frame, which spares the bridge from correlating a
        STARTING/ENDING pair. This account's API rejects it:

            400 constraints: [{"field": "Events[0]", "constraint": "INVALID"}]

        Two grammars exist in GoTo's own docs and only one is accepted here. The
        Call Events *Report* guide documents a top-level
        `{"eventTypes": ["REPORT_SUMMARY"], "accountKeys": ["<key>"]}` — that
        answers `MALFORMED_REQUEST` on this endpoint. The Call Events guide's
        per-account `{"accountKeys": [{"id", "events"}]}` is the one that works,
        and its `events` vocabulary takes `STARTING`/`ENDING`. (`REPORT_SUMMARY`
        may need the `cr.v1.read` scope this account has never consented to —
        untested, since testing it costs a re-consent. See the roadmap.)

        **Only `ENDING` is subscribed, not the documented pair.** A completed
        call is the thing worth putting on a timeline, and `gt_map.frame_kind`
        classifies *any* `call-events` frame as a call — so subscribing to
        `STARTING` too would put two entries on the record for one conversation.
        One event per call keeps the original design's property without the
        correlation state.

        **DO NOT trust the status code alone.** The endpoint answers 207
        Multi-Status carrying per-account results and **no subscription ids at
        all**:

            {"accountKeys": [{"id": "…", "status": 200, "message": "Success"}]}

        The previous implementation looked for `items[].id`, found none, logged a
        warning and returned `[]` — which the caller read as a successful
        subscription with nothing to record. A subscription that did not take is
        an outage of the entire inbound call path, so it raises here.
        """
        resp = await self.request(
            "POST",
            "/call-events/v1/subscriptions",
            json={
                "channelId": channel_id,
                "accountKeys": [{"id": account_key, "events": ["ENDING"]}],
            },
        )
        body = _json(resp, "/call-events/v1/subscriptions")
        results = body.get("accountKeys") or []
        subscribed = [
            str(r.get("id"))
            for r in results
            if isinstance(r, dict) and int(r.get("status") or 0) < 300 and r.get("id")
        ]
        if not subscribed:
            raise GoToError(
                "call-events subscription did not take for any account key "
                f"(HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return subscribed

    async def subscribe_messages(self, channel_id: str, account_key: str) -> list[str]:
        """Subscribe a channel to inbound SMS. Returns the subscription ids.

        **The plan left "does SMS ride the notification channel, or does it need a
        Messaging-API poll?" to be answered empirically. It rides the channel:**
        the A2 probe found `INCOMING_MESSAGE_SNIPPET` live among this account's
        own subscriptions (2026-07-21), so a poll fallback is unnecessary and the
        Messaging API is needed only for sending.

        **`ownerPhoneNumber` is REQUIRED and was missing — fixed 2026-07-22.** The
        subscription is per business line, not per account, so leaving it out
        failed every time with:

            400 constraintViolations: [{"field": "ownerPhoneNumber",
                                        "constraint": "Invalid"}]

        E.164 with the leading `+` is what the API takes (verified live: `201`
        with a subscription id). Sending it without the `+` reaches the same
        subscription, so the number is normalised on their side.

        **A 409 is success, not failure.** `"Subscription already exists"` means a
        previous cycle's subscription for this line is still live — which is the
        healthy steady state on a channel that renews faster than subscriptions
        expire, so it must not be treated as an outage.

        Failures other than that raise. The previous implementation swallowed
        every error into an empty list on the reasoning that losing texts should
        not cost a channel that is carrying calls — but combined with a caller
        that read `[]` as success, it meant inbound SMS was dead for the entire
        life of the integration while every surface reported healthy. Silence is
        the one outcome an integration must never have.
        """
        if not settings.goto_business_number:
            raise GoToError(
                "GOTO_BUSINESS_NUMBER is not set — the messaging subscription is "
                "per line and cannot be created without it, so inbound SMS would "
                "silently never arrive"
            )
        try:
            resp = await self.request(
                "POST",
                "/messaging/v1/subscriptions",
                json={
                    "channelId": channel_id,
                    "accountKey": account_key,
                    "ownerPhoneNumber": settings.goto_business_number,
                    "eventTypes": ["INCOMING_MESSAGE_SNIPPET"],
                },
            )
        except GoToError as exc:
            if "409" in str(exc) or "already exists" in str(exc).lower():
                log.info("messaging subscription already live for this line")
                return []
            raise
        body = _json(resp, "/messaging/v1/subscriptions")
        items = body.get("items") or body.get("subscriptions") or []
        ids = [str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")]
        # A single-subscription create answers with the object itself, not a list.
        if not ids and body.get("id"):
            ids = [str(body["id"])]
        return ids

    async def send_sms(self, owner_number: str, to_number: str, body: str) -> dict:
        """`POST /messaging/v1/messages` — the real outbound SMS send.

        `ownerPhoneNumber` must be a number the account owns; `contactPhoneNumbers`
        is a LIST even for one recipient (the API rejects a bare string — verified
        2026-07-21 when it named both parameters in a 400).
        """
        return await self.post_json(
            "/messaging/v1/messages",
            {
                "ownerPhoneNumber": owner_number,
                "contactPhoneNumbers": [to_number],
                "body": body,
            },
        )


def _json(resp: httpx.Response, path: str) -> dict:
    if not resp.content:
        return {}
    try:
        body = resp.json()
    except ValueError as exc:
        raise GoToError(f"{path} returned a non-JSON body: {exc}") from exc
    return body if isinstance(body, dict) else {"data": body}


__all__ = [
    "AUTHORIZE_URL",
    "TOKEN_URL",
    "API_BASE",
    "SCOPES",
    "GoToClient",
    "GoToError",
    "credentials_configured",
    "token_source",
]
