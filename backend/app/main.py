"""FastAPI application entrypoint.

Lifespan opens/closes the psycopg async pool. CORS is permissive for the local
Vite dev server (the frontend also proxies /api, so CORS is a belt-and-braces
allowance for direct calls). Every `/api` route is JWT-protected (Module 6); the
only unauthenticated openings are `/healthz`, the HMAC-verified webhook ingress,
and the static-bearer `/mcp` mount.
"""
import asyncio
import contextlib
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# psycopg's async pool cannot run on Windows' default ProactorEventLoop — it needs
# a selector loop. Uvicorn builds its loop AFTER importing this module, so setting
# the policy here (before anything creates one) is what makes the documented
# `python -m uvicorn app.main:app` command work on Windows at all. Without it the
# app starts, then dies 30 seconds later with `PoolTimeout: pool initialization
# incomplete` — a message that reads like bad credentials rather than a loop
# mismatch. The test harness (conftest) and the backfill script each set the same
# policy for the same reason; this is the third and last place that needed it.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from .config import settings
from .db import close_pool, open_pool
from .routers import (
    applicants,
    automations,
    chat,
    clients,
    documents,
    events,
    home,
    leads,
    referrals,
    schedule,
    settings as settings_router,
    tasks,
    webhooks,
    workforce,
)
from .services.automations.scheduler import engine_loop
from .services.connectors.sync import connectors_loop
from .services.mcp_server import build_mcp_asgi_app, session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    # The automations engine loops run in-process (one process, one pool). Started
    # only when enabled and only once the pool is open; tests use httpx
    # ASGITransport, which never runs the lifespan, so the loop stays off there.
    background: list[asyncio.Task] = []
    if settings.nexus_automations_enabled:
        background.append(asyncio.create_task(engine_loop()))
    # The connector sync loop polls sources with no webhooks (Module 18a). Same
    # shape as the automations loop; a runner with no credentials simply never
    # registers, so this is a no-op cycle on an unconfigured deployment.
    if settings.nexus_connectors_enabled:
        background.append(asyncio.create_task(connectors_loop()))
    # The MCP session manager owns a task group for all /mcp sessions; its run()
    # context must wrap the app's serving lifetime.
    async with session_manager.run():
        try:
            yield
        finally:
            for task in background:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await close_pool()


app = FastAPI(title="Nexus Control Center", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(home.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(webhooks.router)
app.include_router(events.router)
app.include_router(tasks.router)
app.include_router(automations.router)
app.include_router(leads.router)
app.include_router(referrals.router)
app.include_router(applicants.router)
app.include_router(clients.router)
app.include_router(schedule.router)
app.include_router(workforce.router)
app.include_router(settings_router.router)

# MCP server (Streamable HTTP) exposing the tool registry to external clients.
# Bearer-token gated; unset token fails closed. n8n consumes this same mount in M7.
app.mount("/mcp", build_mcp_asgi_app())


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
