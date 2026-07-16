"""Async Postgres access as the RLS-subject `nexus_app` role.

Every unit of work runs inside a transaction that first sets the tenant GUC:
    select set_config('request.app.tenant_id', <tenant>, true)   -- true = tx-local
so app.current_tenant_id() resolves and RLS scopes every subsequent statement to
that tenant. Without it, the role sees nothing (fail closed).

Two entry points:
  * `tenant_conn` — FastAPI dependency yielding a tenant-scoped connection.
  * `tenant_tx(tenant_id)` — async context manager for background tasks (ingestion),
    which run outside the request lifecycle.
"""
from contextlib import asynccontextmanager

from fastapi import Depends
from psycopg_pool import AsyncConnectionPool

from .config import settings
from .deps import get_tenant_id

_pool: AsyncConnectionPool | None = None


async def open_pool() -> None:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.nexus_app_db_url,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={"autocommit": False},
        )
        await _pool.open(wait=True)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Connection pool is not open. Is the app lifespan running?")
    return _pool


async def _set_tenant(conn, tenant_id: str) -> None:
    await conn.execute(
        "select set_config('request.app.tenant_id', %s, true)", (tenant_id,)
    )


@asynccontextmanager
async def tenant_tx(tenant_id: str):
    """Yield a connection inside a tenant-scoped transaction. Commits on success,
    rolls back on error. For background tasks and services outside a request."""
    pool = get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await _set_tenant(conn, tenant_id)
            yield conn


async def tenant_conn(tenant_id: str = Depends(get_tenant_id)):
    """FastAPI dependency: a tenant-scoped connection in an open transaction.

    The transaction commits when the request handler returns cleanly and rolls
    back on exception (psycopg's `transaction()` context)."""
    pool = get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await _set_tenant(conn, tenant_id)
            yield conn
