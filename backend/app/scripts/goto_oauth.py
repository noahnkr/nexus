"""One-time GoTo Connect OAuth consent (v1.2.0).

    python -m app.scripts.goto_oauth

GoTo's OAuth client REJECTS `client_credentials` for this API surface (401
"Unauthorized grant type", probed 2026-07-20), so an unattended integration still
needs one interactive consent: a human logs in once, and the refresh token that
falls out drives everything afterwards.

WHAT THIS DOES
  1. Starts a localhost HTTP listener on `GOTO_CONNECT_REDIRECT_PORT` (default
     8765) — the OAuth client must have `http://localhost:{port}` registered as a
     redirect URI, or GoTo refuses the authorization request before consent.
  2. Prints (and tries to open) the consent URL, carrying a random `state`.
  3. Receives the redirect, checks `state`, exchanges the code at the token
     endpoint.
  4. Prints `GOTO_CONNECT_REFRESH_TOKEN=…` for the operator to paste into `.env`.

WHAT IT DOES NOT DO
  It never writes `.env`, never persists a token anywhere, and never prints the
  ACCESS token (short-lived, but still a bearer credential in scrollback). The
  refresh token is printed because printing it is the entire point — run this in
  a terminal you are willing to have it in, and clear the scrollback after.

SCOPES are `goto_client.SCOPES`. If the consent screen refuses one, the account's
plan or the OAuth client's registration is missing it: fix it there rather than
dropping the scope here — the transcript scope in particular is load-bearing
(v1.2.0's hard gate).
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from ..config import settings
from ..services.connectors.goto_client import AUTHORIZE_URL, SCOPES, TOKEN_URL

_PAGE = (
    "<html><body style='font-family:sans-serif;padding:3rem'>"
    "<h2>{title}</h2><p>{message}</p></body></html>"
)


class _CallbackHandler(BaseHTTPRequestHandler):
    """Serves exactly one redirect, stashing the query on the server object."""

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's naming
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.oauth_result = {k: v[0] for k, v in params.items()}  # type: ignore[attr-defined]

        ok = "code" in params
        page = _PAGE.format(
            title="Consent complete" if ok else "Consent failed",
            message=(
                "You can close this tab and return to the terminal."
                if ok
                else "No authorization code came back — check the terminal for details."
            ),
        )
        body = page.encode("utf-8")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Silence the default stderr access log — it would echo the code."""


def build_consent_url(client_id: str, redirect_uri: str, state: str) -> str:
    """The authorization-code consent URL. Pure, so a test can assert its shape
    without opening a browser."""
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}"


def wait_for_code(port: int, state: str, timeout: float = 300.0) -> str:
    """Block until the browser hits the localhost redirect; return the code.

    Raises RuntimeError on a mismatched `state` (the CSRF check — a redirect we
    did not initiate is never exchanged) or on an error response from GoTo.
    """
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.oauth_result = None  # type: ignore[attr-defined]
    server.timeout = timeout
    try:
        server.handle_request()
    finally:
        server.server_close()

    result = getattr(server, "oauth_result", None)
    if not result:
        raise RuntimeError(f"no redirect arrived within {timeout:.0f}s")
    if result.get("error"):
        raise RuntimeError(
            f"GoTo refused the consent: {result['error']} "
            f"({result.get('error_description', 'no description')})"
        )
    if result.get("state") != state:
        raise RuntimeError("state mismatch — ignoring a redirect this run did not start")
    code = result.get("code")
    if not code:
        raise RuntimeError("redirect carried no authorization code")
    return code


async def exchange_code(code: str, redirect_uri: str) -> dict:
    """Trade the authorization code for tokens. Returns the decoded token body."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(settings.goto_connect_client_id, settings.goto_connect_client_secret),
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"token exchange rejected (HTTP {resp.status_code}) — {resp.text[:300]}"
        )
    return resp.json()


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-time GoTo Connect OAuth consent")
    parser.add_argument(
        "--port", type=int, default=settings.goto_connect_redirect_port,
        help="localhost redirect port (must be registered on the OAuth client)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening it"
    )
    parser.add_argument(
        "--client-id", default=None,
        help=(
            "override GOTO_CONNECT_CLIENT_ID for this run. Only useful when an "
            "account has several OAuth clients and you are working out which one "
            "carries the registered redirect URI."
        ),
    )
    parser.add_argument(
        "--redirect-uri", default=None,
        help=(
            "override the redirect URI sent to GoTo. It must match the OAuth "
            "client's registration EXACTLY (scheme, host, port, path, trailing "
            "slash); the listener still binds --port on localhost."
        ),
    )
    args = parser.parse_args(argv)

    client_id = args.client_id or settings.goto_connect_client_id
    if not client_id or not settings.goto_connect_client_secret:
        print(
            "GOTO_CONNECT_CLIENT_ID / GOTO_CONNECT_CLIENT_SECRET are not set in .env.",
            file=sys.stderr,
        )
        return 2

    redirect_uri = args.redirect_uri or f"http://localhost:{args.port}"
    state = secrets.token_urlsafe(24)
    url = build_consent_url(client_id, redirect_uri, state)

    print(f"Redirect URI (must be registered on the OAuth client): {redirect_uri}")
    print(f"Scopes requested: {' '.join(SCOPES)}")
    print("\nOpen this URL and complete the consent:\n")
    print(url + "\n")
    if not args.no_browser:
        webbrowser.open(url)

    print(f"Waiting for the redirect on {redirect_uri} …")
    try:
        code = await asyncio.to_thread(wait_for_code, args.port, state)
        tokens = await exchange_code(code, redirect_uri)
    except Exception as exc:  # noqa: BLE001 — an ops script reports, it doesn't trace
        print(f"\nConsent failed: {exc}", file=sys.stderr)
        return 1

    refresh = tokens.get("refresh_token")
    if not refresh:
        print(
            "\nThe token response carried no refresh_token. The OAuth client is "
            "probably not configured for offline access — fix that on the client "
            "registration and re-run.",
            file=sys.stderr,
        )
        return 1

    print("\nConsent complete. Paste this into .env (and clear your scrollback):\n")
    print(f"GOTO_CONNECT_REFRESH_TOKEN={refresh}\n")
    scopes = tokens.get("scope")
    if scopes:
        print(f"Scopes actually granted: {scopes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
