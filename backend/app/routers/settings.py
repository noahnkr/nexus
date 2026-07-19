"""Tenant settings API (Module 15b) — user-facing workspace + agent preferences.

GET   /api/settings          every whitelisted key, defaults filled in
PATCH /api/settings          partial update; 422 on unknown key / invalid value

User JWT only: these are preferences a person sets about their own workspace, so
there is deliberately no machine path here (`/mcp` and webhooks resolve tenant from
env and have no preferences to honor). All validation lives in the
`services/settings.py` seam — this router only translates its error into a 422.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..db import tenant_conn
from ..deps import get_tenant_id
from ..schemas import SettingsOut, SettingsPatch
from ..services.settings import SettingsError, get_settings, update_settings

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
async def read_settings(conn=Depends(tenant_conn)):
    return SettingsOut(**await get_settings(conn))


@router.patch("/settings", response_model=SettingsOut)
async def patch_settings(
    body: SettingsPatch,
    conn=Depends(tenant_conn),
    tenant_id: str = Depends(get_tenant_id),
):
    patch = body.model_dump(exclude_unset=True)
    try:
        merged = await update_settings(conn, tenant_id, patch)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return SettingsOut(**merged)
