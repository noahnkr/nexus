"""Request-scoped dependencies.

get_tenant_id is the single tenant-identity seam. This phase it returns the
env-configured NEXUS_TENANT_ID; Module 6 swaps the body for the verified Supabase
JWT claim (app_metadata.tenant_id) without any caller changing.
"""
from .config import settings


def get_tenant_id() -> str:
    return settings.nexus_tenant_id
