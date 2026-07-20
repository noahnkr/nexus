"""WelcomeHome CRM HTTP client (Module 18a).

WelcomeHome has **no webhooks** — polling its export API is the only inbound
mechanism (verified against the live account at planning time). This module is the
only place that speaks HTTP to WelcomeHome; `wh_runner.py` drives it and
`wh_map.py` translates what it returns. Nothing here writes to the database.

Two surfaces:

  * `reference(name)` — the small JSON config endpoints (`stages`, `activity_types`,
    `lead_sources`, `prospect_fields`). Fetched once per sync cycle and cached by
    the runner; they are the vocabulary `wh_map` resolves ids against.
  * `export_pages(table, updated_at_after)` — the sync backbone:
    `GET /api/exports/community/{cid}/table/{table}` streams **live paginated CSV**.
    Pagination is by `Link: <url>; rel="next"` header, and the API limits a cursor
    to **3 reuses per minute** — `_CURSOR_INTERVAL_SECONDS` paces the walk so a
    long backfill never trips it.

Auth is `Authorization: Token token={key}` (WelcomeHome's scheme, not Bearer).
Failures raise `WelcomeHomeError`, which the runner converts into a
`connector.sync_failed` event — a bad cycle must never kill the sync loop.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from typing import AsyncIterator

import httpx

from ...config import settings

log = logging.getLogger("nexus.connectors.welcomehome")

# The export API allows 3 uses of a given cursor per minute. One request every 21s
# keeps a single-threaded walk comfortably under that even with clock skew.
_CURSOR_INTERVAL_SECONDS = 21.0
# Page size requested from the export endpoint (the API caps this itself).
_PAGE_LIMIT = 500
# Hard stop on a single table walk, so a pagination bug can't loop forever.
_MAX_PAGES = 2000

_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3

# JSON reference endpoints this client is allowed to fetch. An allowlist rather
# than free-form paths: the runner never needs an arbitrary endpoint, and this
# keeps the surface auditable.
REFERENCE_ENDPOINTS = {
    "stages": "/api/stages",
    "activity_types": "/api/activity_types",
    "lead_sources": "/api/lead_sources",
    "prospect_fields": "/api/prospect_fields",
    "relationships": "/api/relationships",
}

# Export tables the sync uses, in dependency order (prospects before the rows that
# reference them).
EXPORT_TABLES = ("Prospects", "Residents", "Influencers", "Activities")

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"', re.IGNORECASE)


class WelcomeHomeError(RuntimeError):
    """Any failure talking to WelcomeHome — network, auth, bad status, bad body.

    The runner catches this and writes `connector.sync_failed`; it never escapes
    to the loop.
    """


def parse_next_link(link_header: str | None) -> str | None:
    """Extract the `rel="next"` URL from an RFC-5988 Link header, or None when the
    header is absent or has no next relation (i.e. the last page)."""
    if not link_header:
        return None
    match = _LINK_NEXT_RE.search(link_header)
    return match.group(1) if match else None


def parse_csv_rows(text: str) -> list[dict]:
    """Parse an export page's CSV body into row dicts.

    Header-only bodies (the normal "no changes since the cursor" response) yield an
    empty list. Keys are stripped so a header with stray whitespace still matches
    what `wh_map` looks up.
    """
    if not text or not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    for raw in reader:
        rows.append({
            (k or "").strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in raw.items()
            if k is not None
        })
    return rows


class WelcomeHomeClient:
    """Async WelcomeHome API client. One instance per sync cycle (or per script).

    Use as an async context manager so the underlying httpx client is closed:
        async with WelcomeHomeClient() as wh:
            refs = await wh.reference("stages")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        community_id: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cursor_interval: float = _CURSOR_INTERVAL_SECONDS,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.welcomehome_api_key
        self.base_url = (base_url or settings.welcomehome_base_url).rstrip("/")
        self.community_id = community_id or settings.welcomehome_community_id or "all"
        self.cursor_interval = cursor_interval
        self._client = client
        self._owns_client = client is None

    # -- lifecycle ---------------------------------------------------------
    async def __aenter__(self) -> "WelcomeHomeClient":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    def _headers(self) -> dict:
        if not self.api_key:
            raise WelcomeHomeError("WELCOMEHOME_API_KEY is not configured")
        return {
            "Authorization": f"Token token={self.api_key}",
            "Accept": "*/*",
        }

    # -- request plumbing --------------------------------------------------
    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        """GET with bounded retries on transient statuses. Anything else — and a
        4xx that isn't 429 — raises `WelcomeHomeError` immediately: retrying a 401
        just burns the rate limit."""
        last_error: str = "no attempt made"
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._http().get(url, params=params, headers=self._headers())
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code < 400:
                    return resp
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code not in _RETRY_STATUSES:
                    raise WelcomeHomeError(f"GET {url} failed — {last_error}")
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
        raise WelcomeHomeError(f"GET {url} failed after {_MAX_RETRIES} attempts — {last_error}")

    # -- public surface ----------------------------------------------------
    async def ping(self) -> dict:
        """`GET /api/ping` — the credential smoke test. Returns the decoded body
        (which carries `account_id`)."""
        resp = await self._get(f"{self.base_url}/api/ping")
        try:
            body = resp.json()
        except ValueError as exc:
            raise WelcomeHomeError(f"ping returned a non-JSON body: {exc}") from exc
        return body if isinstance(body, dict) else {"result": body}

    async def reference(self, name: str) -> list[dict]:
        """Fetch one JSON reference list (see REFERENCE_ENDPOINTS). Returns a list
        of dicts; a payload wrapped in an envelope key is unwrapped."""
        path = REFERENCE_ENDPOINTS.get(name)
        if path is None:
            raise WelcomeHomeError(f"unknown reference endpoint '{name}'")
        resp = await self._get(f"{self.base_url}{path}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise WelcomeHomeError(f"reference '{name}' returned a non-JSON body: {exc}") from exc
        if isinstance(body, list):
            return [r for r in body if isinstance(r, dict)]
        if isinstance(body, dict):
            # Envelope shapes: {"stages": [...]} or {"data": [...]}.
            for key in (name, "data", "results"):
                value = body.get(key)
                if isinstance(value, list):
                    return [r for r in value if isinstance(r, dict)]
        return []

    async def export_pages(
        self, table: str, updated_at_after: str | None = None
    ) -> AsyncIterator[list[dict]]:
        """Yield export rows one page at a time, following the `Link` cursor.

        `updated_at_after` is the incremental watermark (ISO-8601); omitted on a
        first/full pull. Pages are paced to respect the 3-uses-per-minute cursor
        limit, so this is a slow generator by design — the caller streams rather
        than accumulating.
        """
        url = f"{self.base_url}/api/exports/community/{self.community_id}/table/{table}"
        params: dict | None = {"limit": _PAGE_LIMIT}
        if updated_at_after:
            params["filters[updated_at_after]"] = updated_at_after

        pages = 0
        while url and pages < _MAX_PAGES:
            if pages:
                # Only pace BETWEEN pages — the first request of a table uses no
                # cursor, so it needn't wait.
                await asyncio.sleep(self.cursor_interval)
            resp = await self._get(url, params=params)
            rows = parse_csv_rows(resp.text)
            pages += 1
            if rows:
                yield rows
            # The next link carries its own query string; params must not be re-sent.
            url = parse_next_link(resp.headers.get("link"))
            params = None

        if pages >= _MAX_PAGES:
            log.warning("WelcomeHome export '%s' hit the %s-page cap", table, _MAX_PAGES)
