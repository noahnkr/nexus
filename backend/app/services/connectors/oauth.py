"""Shared OAuth2 refresh-token access-token source (v1.2.0).

One implementation, several configs: GoTo Connect uses it here, Google Workspace
(v1.3.0) reuses it verbatim with a different token URL and credentials. Nothing
in this module touches the database — refresh tokens are env-only config
(CLAUDE.md: credentials and OAuth refresh tokens live in env vars, never in the
database), and access tokens are cached in-process only.

The contract is deliberately small:

    source = TokenSource(TOKEN_URL, client_id, client_secret, refresh_token)
    headers = {"Authorization": f"Bearer {await source.token()}"}

`token()` returns the cached access token until it is within `expiry_slack`
seconds of expiring, then refreshes. Concurrent callers share one refresh (the
bridge and the sync runner both hold a source), so a token lapse can't stampede
the identity endpoint.

ROTATING REFRESH TOKENS. Some providers return a NEW refresh token on each
refresh and invalidate the old one. When that happens the new value is adopted
in-process and logged loudly, because the value in `.env` is now stale: the
process keeps working, but a restart would fail until an operator pastes the new
token in. Failing silently there is how a connector dies overnight.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger("nexus.connectors.oauth")

# Refresh this many seconds before the token actually expires, so a request never
# races the boundary.
_EXPIRY_SLACK_SECONDS = 60.0

# Providers may omit expires_in; assume a conservative lifetime rather than
# treating the token as immortal.
_DEFAULT_EXPIRES_IN = 3600.0

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TokenError(RuntimeError):
    """Any failure obtaining an access token — missing config, network, or a
    rejection from the identity endpoint. Callers surface it plainly; the sync
    loop turns it into `connector.sync_failed`."""


class TokenSource:
    """Cached access tokens for one OAuth2 client, refreshed on demand."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        client: httpx.AsyncClient | None = None,
        expiry_slack: float = _EXPIRY_SLACK_SECONDS,
        time_fn=time.monotonic,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.expiry_slack = expiry_slack
        self._time = time_fn
        self._client = client
        self._owns_client = client is None
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------
    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "TokenSource":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    def configured(self) -> bool:
        """All three credential pieces present. The runner's `enabled()` check."""
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    # -- the one public call ----------------------------------------------
    async def token(self) -> str:
        """A valid access token, refreshing if the cached one is expiring."""
        if self._access_token and self._time() < self._expires_at - self.expiry_slack:
            return self._access_token
        async with self._lock:
            # Another caller may have refreshed while we waited for the lock.
            if self._access_token and self._time() < self._expires_at - self.expiry_slack:
                return self._access_token
            return await self._refresh()

    def invalidate(self) -> None:
        """Drop the cached token so the next `token()` refreshes. Used when a call
        comes back 401 despite a token we believed was live."""
        self._access_token = None
        self._expires_at = 0.0

    async def _refresh(self) -> str:
        if not self.configured():
            raise TokenError(
                "OAuth client is not fully configured "
                "(client id, client secret and refresh token are all required)"
            )
        try:
            resp = await self._http().post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
                # Client credentials go in the Basic header — the form both GoTo
                # and Google accept, and it keeps the secret out of the body.
                auth=(self.client_id, self.client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise TokenError(f"token refresh failed — {type(exc).__name__}: {exc}") from exc

        if resp.status_code >= 400:
            raise TokenError(
                f"token refresh rejected (HTTP {resp.status_code}) — {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise TokenError(f"token endpoint returned a non-JSON body: {exc}") from exc

        access = body.get("access_token")
        if not access:
            raise TokenError("token endpoint returned no access_token")

        try:
            expires_in = float(body.get("expires_in") or _DEFAULT_EXPIRES_IN)
        except (TypeError, ValueError):
            expires_in = _DEFAULT_EXPIRES_IN

        rotated = body.get("refresh_token")
        if rotated and rotated != self.refresh_token:
            self.refresh_token = rotated
            log.warning(
                "%s rotated its refresh token — the value in .env is now stale. "
                "Paste the new one in before the next restart.",
                self.token_url,
            )

        self._access_token = access
        self._expires_at = self._time() + expires_in
        return access


__all__ = ["TokenSource", "TokenError"]
