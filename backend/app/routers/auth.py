"""Auth endpoints.

Dev seam for Supabase Realtime: the anon Realtime client sees zero rows (RLS fails
closed, which is correct), so the frontend needs a token carrying the tenant claim.
This endpoint mints a short-lived HS256 JWT with app_metadata.tenant_id — the same
shape the test harness mints — signed with SUPABASE_JWT_SECRET. The frontend passes
it to supabase.realtime.setAuth(). Module 6 replaces this with real Supabase Auth.
"""
import time

import jwt
from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..deps import get_tenant_id

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/realtime-token")
async def realtime_token(tenant_id: str = Depends(get_tenant_id)):
    if not settings.supabase_jwt_secret:
        raise HTTPException(status_code=503, detail="SUPABASE_JWT_SECRET not configured")
    now = int(time.time())
    payload = {
        "role": "authenticated",
        "aud": "authenticated",
        "sub": "00000000-0000-0000-0000-0000000000ff",
        "app_metadata": {"tenant_id": tenant_id},
        "iat": now,
        "exp": now + 3600,
    }
    token = jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")
    return {"token": token, "expires_in": 3600}
