"""Request-scoped dependencies — the tenant-identity + current-user seam.

`get_tenant_id` verifies the Supabase JWT on the `Authorization` header and returns
the tenant from `app_metadata.tenant_id`. Two signing schemes are accepted, chosen
by the token header's `alg`:
  * HS256 via the legacy shared secret (`SUPABASE_JWT_SECRET`) — the offline test
    harness (`conftest.mint_tenant_jwt`) and Realtime-heritage tokens.
  * ES256/RS256 via the project JWKS — what Supabase Auth actually issues now
    (verified empirically: the project's JWKS serves an ES256 key).
Both paths pin `algorithms` explicitly (no `alg=none`/confusion), require
`aud=authenticated`, and enforce `exp`. Any verification failure ⇒ 401 (fail
closed, including unset secret/URL). A *valid* token with no tenant claim ⇒ 403.

`get_machine_tenant_id` is the ONLY runtime reader of the env tenant — used by the
credentialed machine paths (webhook ingress, `/mcp`) that authenticate by HMAC
signature / static bearer, never a user JWT.
"""
from __future__ import annotations

import jwt
from fastapi import HTTPException, Request

from .config import settings

# Asymmetric algorithms accepted on the JWKS path. Pinned to a fixed allowlist so
# the alg can never be downgraded to a symmetric/none scheme via the token header.
_JWKS_ALGORITHMS = ["ES256", "RS256"]

_JWKS_CLIENT: jwt.PyJWKClient | None = None


def _jwks_client() -> jwt.PyJWKClient:
    """Lazy module-singleton JWKS client. Built on the first ES256/RS256
    verification only — offline HS256 test runs never touch the network."""
    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        if not settings.supabase_url:
            raise HTTPException(status_code=401, detail="auth not configured")
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _JWKS_CLIENT = jwt.PyJWKClient(url, cache_keys=True)
    return _JWKS_CLIENT


def _verify_token(token: str) -> dict:
    """Verify a Supabase access token, dispatching on the header `alg`. Returns the
    decoded claims. Raises HTTPException(401) on any verification failure."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")

    alg = header.get("alg")
    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise HTTPException(status_code=401, detail="auth not configured")
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        if header.get("kid"):
            signing_key = _jwks_client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=_JWKS_ALGORITHMS,
                audience="authenticated",
            )
        raise HTTPException(status_code=401, detail="unsupported token algorithm")
    except HTTPException:
        raise
    except jwt.PyJWTError:
        # Bad signature, expired, wrong audience, malformed — all fail closed.
        raise HTTPException(status_code=401, detail="invalid token")
    except Exception:
        # JWKS fetch failure, unusable key, etc. — fail closed rather than 500.
        raise HTTPException(status_code=401, detail="token verification failed")


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return header[len("bearer ") :].strip()


def _claims(request: Request) -> dict:
    """Verify the request's bearer token once, caching the decoded claims on
    `request.state` so `get_tenant_id` and `get_current_user` share one verify."""
    cached = getattr(request.state, "jwt_claims", None)
    if cached is not None:
        return cached
    claims = _verify_token(_bearer_token(request))
    request.state.jwt_claims = claims
    return claims


async def get_tenant_id(request: Request) -> str:
    """Verified tenant identity for every `/api` route (the user surface). Signature
    is `(request)` so every existing `Depends(get_tenant_id)` call site — including
    the chain through `db.tenant_conn` — keeps working unchanged."""
    claims = _claims(request)
    app_metadata = claims.get("app_metadata") or {}
    tenant_id = app_metadata.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="no tenant claim")
    return str(tenant_id)


async def get_current_user(request: Request) -> dict:
    """The verified user behind the request: `{"id": sub, "email": email}`. Shares
    the per-request claim verification with `get_tenant_id`."""
    claims = _claims(request)
    return {"id": claims.get("sub"), "email": claims.get("email")}


def get_machine_tenant_id() -> str:
    """Env-configured tenant for the credentialed machine paths (webhook ingress,
    `/mcp`). These authenticate by HMAC signature / static bearer, never a user JWT.

    Future: multi-tenant connector routing will map source -> tenant via connector
    config, not env — this is the seam that changes, and nothing else reads the env
    tenant at request time.
    """
    return settings.nexus_tenant_id
