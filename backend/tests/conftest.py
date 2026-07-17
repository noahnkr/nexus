"""Shared fixtures for the Module 0 schema/RLS test harness.

Two connection paths are exercised:
  * PostgREST via supabase-py, using locally-minted tenant JWTs — the real RLS
    surface (connects as the `authenticated` role, not a bypass role).
  * A direct psycopg connection to Postgres for structural/trigger tests, using
    the `request.app.tenant_id` GUC as the FastAPI backend will in later modules.

Tests are skipped (not failed) when required env vars are absent, so the suite
is safe to collect before a Supabase project is provisioned.
"""
import asyncio
import os
import sys
import time

import pytest
from dotenv import load_dotenv

# psycopg's async pool/connection cannot run on Windows' default ProactorEventLoop
# (it needs a selector loop). The gated tests drive async DB code via asyncio.run,
# so pin the selector policy for the whole harness on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Load repo-root .env (backend/tests/ -> repo root is two levels up).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

DEMO_TENANT = os.getenv("NEXUS_TENANT_ID", "00000000-0000-0000-0000-000000000001")
PROBE_TENANT = os.getenv("NEXUS_PROBE_TENANT_ID", "00000000-0000-0000-0000-000000000002")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
NEXUS_APP_DB_URL = os.getenv("NEXUS_APP_DB_URL")


def _require(*names):
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}", allow_module_level=True)


def mint_tenant_jwt(tenant_id: str, email: str | None = None) -> str:
    """HS256 token PostgREST + the backend both verify: role=authenticated, tenant
    in app_metadata. The backend's `get_tenant_id` accepts this HS256 path (no
    network) alongside the ES256 tokens Supabase Auth issues. An optional `email`
    claim populates `get_current_user` (used to exercise `resolved_by` identity)."""
    import jwt

    now = int(time.time())
    payload = {
        "role": "authenticated",
        "aud": "authenticated",
        "sub": "00000000-0000-0000-0000-0000000000ff",
        "app_metadata": {"tenant_id": tenant_id},
        "iat": now,
        "exp": now + 3600,
    }
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, SUPABASE_JWT_SECRET, algorithm="HS256")


def bearer_headers(tenant_id: str = DEMO_TENANT, email: str | None = None) -> dict:
    """Authorization header carrying an HS256 tenant JWT — the app-client shape for
    the JWT-protected `/api` routes (Module 6). Plain function (not just a fixture)
    so the asyncio.run-based API scenarios can build it inline."""
    return {"Authorization": f"Bearer {mint_tenant_jwt(tenant_id, email=email)}"}


def _rest_client(jwt_token: str | None):
    from supabase import create_client

    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    if jwt_token is not None:
        client.postgrest.auth(jwt_token)
    return client


@pytest.fixture(scope="session")
def demo_tenant_id():
    return DEMO_TENANT


@pytest.fixture(scope="session")
def probe_tenant_id():
    return PROBE_TENANT


@pytest.fixture()
def client_tenant_a():
    """PostgREST client scoped to the demo tenant."""
    _require("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET")
    return _rest_client(mint_tenant_jwt(DEMO_TENANT))


@pytest.fixture()
def client_tenant_b():
    """PostgREST client scoped to the probe tenant."""
    _require("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET")
    return _rest_client(mint_tenant_jwt(PROBE_TENANT))


@pytest.fixture()
def client_anon():
    """PostgREST client with no tenant claim (anon key only)."""
    _require("SUPABASE_URL", "SUPABASE_ANON_KEY")
    return _rest_client(None)


@pytest.fixture()
def db():
    """Direct psycopg connection. Autocommit off so failing statements roll back."""
    _require("SUPABASE_DB_URL")
    import psycopg

    conn = psycopg.connect(SUPABASE_DB_URL)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def app_db():
    """Direct psycopg connection as the RLS-subject `nexus_app` role (nobypassrls).

    This is the exact path the FastAPI backend uses: an RLS-subject role that only
    sees rows once request.app.tenant_id is set. Skipped until the one-time ops
    step (set nexus_app password + NEXUS_APP_DB_URL in .env) is done.
    """
    _require("NEXUS_APP_DB_URL")
    import psycopg

    conn = psycopg.connect(NEXUS_APP_DB_URL)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def set_tenant(conn, tenant_id: str):
    """Set the request.app.tenant_id GUC on a psycopg connection (session scope)."""
    with conn.cursor() as cur:
        cur.execute("select set_config('request.app.tenant_id', %s, false)", (tenant_id,))


@pytest.fixture()
def auth_headers():
    """Demo-tenant bearer header for JWT-protected `/api` routes."""
    _require("SUPABASE_JWT_SECRET")
    return bearer_headers(DEMO_TENANT)


@pytest.fixture()
def auth_headers_probe():
    """Probe-tenant bearer header — used to prove RLS isolation through the API."""
    _require("SUPABASE_JWT_SECRET")
    return bearer_headers(PROBE_TENANT)
