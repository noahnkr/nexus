"""One-time Google Workspace OAuth consent (v1.3.0).

    python -m app.scripts.google_oauth

Same shape as `goto_oauth.py` — a human consents once, and the refresh token that
falls out drives the Gmail and Calendar integrations unattended forever after.
Deliberately a sibling rather than a shared abstraction: the two providers differ
in the parameters that matter (Google needs `access_type=offline` and
`prompt=consent`; GoTo authenticates the exchange with HTTP Basic), and folding
those differences into one function would make both harder to read than two
scripts that each do one thing plainly.

BEFORE RUNNING — the operator sets up a GCP OAuth client:
  1. Google Cloud console → APIs & Services → **enable the Gmail API and the
     Google Calendar API** on the project. Consent without the API enabled
     succeeds and then every call 403s, which is a confusing way to find out.
  2. Credentials → Create credentials → OAuth client ID → **Web application**.
  3. Add `http://localhost:{GOOGLE_REDIRECT_PORT}` (default 8766) as an
     **Authorized redirect URI**. It must match exactly — scheme, host, port, no
     trailing slash.
  4. Put the client id and secret in `.env` as `GOOGLE_CLIENT_ID` /
     `GOOGLE_CLIENT_SECRET`.
  5. If the consent screen is in **Testing** mode, add the business Google
     account as a **test user** — otherwise consent fails with "access blocked"
     for an account that is not on the list. (Testing-mode refresh tokens also
     expire after 7 days; publish the app when the integration goes live, or it
     will quietly stop about a week later.)

WHY `prompt=consent`: Google returns a refresh token only on the FIRST consent
for a given client+account pair. Re-running this after an earlier consent would
otherwise hand back an access token and no refresh token, which looks like a bug
in this script. Forcing the prompt makes a re-run always produce one.

WHAT IT DOES NOT DO: it never writes `.env`, never persists a token, and never
prints the access token. The refresh token is printed because printing it is the
point — run this in a terminal you are willing to have it in, and clear the
scrollback afterwards.
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
from ..services.connectors.google_client import AUTHORIZE_URL, SCOPES, TOKEN_URL

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
    without opening a browser.

    `access_type=offline` is what asks for a refresh token at all; `prompt=consent`
    forces one to be issued even on a repeat consent (see the module docstring).
    """
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    })
    return f"{AUTHORIZE_URL}?{query}"


def wait_for_code(port: int, state: str, timeout: float = 300.0) -> str:
    """Block until the browser hits the localhost redirect; return the code.

    Raises RuntimeError on a mismatched `state` (the CSRF check — a redirect this
    run did not initiate is never exchanged) or on an error from Google.
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
            f"Google refused the consent: {result['error']} "
            f"({result.get('error_description', 'no description')})"
        )
    if result.get("state") != state:
        raise RuntimeError("state mismatch — ignoring a redirect this run did not start")
    code = result.get("code")
    if not code:
        raise RuntimeError("redirect carried no authorization code")
    return code


async def exchange_code(code: str, redirect_uri: str, client_id: str) -> dict:
    """Trade the authorization code for tokens. Returns the decoded token body.

    Google expects the client credentials in the FORM BODY (unlike GoTo, which
    wants HTTP Basic) — sending them as Basic auth here yields `invalid_client`.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": settings.google_client_secret,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"token exchange rejected (HTTP {resp.status_code}) — {resp.text[:300]}"
        )
    return resp.json()


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-time Google Workspace OAuth consent")
    parser.add_argument(
        "--port", type=int, default=settings.google_redirect_port,
        help="localhost redirect port (must be registered on the OAuth client)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening it"
    )
    parser.add_argument(
        "--client-id", default=None,
        help="override GOOGLE_CLIENT_ID for this run (diagnosing a client mismatch)",
    )
    parser.add_argument(
        "--redirect-uri", default=None,
        help=(
            "override the redirect URI sent to Google. It must match the OAuth "
            "client's registration EXACTLY; the listener still binds --port."
        ),
    )
    args = parser.parse_args(argv)

    client_id = args.client_id or settings.google_client_id
    if not client_id or not settings.google_client_secret:
        print(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set in .env. "
            "Create a GCP OAuth client first — see this script's docstring.",
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
        tokens = await exchange_code(code, redirect_uri, client_id)
    except Exception as exc:  # noqa: BLE001 — an ops script reports, it doesn't trace
        print(f"\nConsent failed: {exc}", file=sys.stderr)
        return 1

    refresh = tokens.get("refresh_token")
    if not refresh:
        print(
            "\nThe token response carried no refresh_token. That usually means "
            "this account already consented to this client before — re-run "
            "(this script forces prompt=consent, which should fix it), or revoke "
            "the app's access at https://myaccount.google.com/permissions and "
            "consent again.",
            file=sys.stderr,
        )
        return 1

    print("\nConsent complete. Paste this into .env (and clear your scrollback):\n")
    print(f"GOOGLE_REFRESH_TOKEN={refresh}\n")
    scopes = tokens.get("scope")
    if scopes:
        print(f"Scopes actually granted: {scopes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
