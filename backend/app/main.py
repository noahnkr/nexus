"""FastAPI application entrypoint.

Lifespan opens/closes the psycopg async pool. CORS is permissive for the local
Vite dev server (the frontend also proxies /api, so CORS is a belt-and-braces
allowance for direct calls). Every `/api` route is JWT-protected (Module 6); the
only unauthenticated openings are `/healthz`, the HMAC-verified webhook ingress,
and the static-bearer `/mcp` mount.
"""
import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
)
from .services.automations.scheduler import engine_loop
from .services.mcp_server import build_mcp_asgi_app, session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    # The automations engine loops run in-process (one process, one pool). Started
    # only when enabled and only once the pool is open; tests use httpx
    # ASGITransport, which never runs the lifespan, so the loop stays off there.
    engine_task: asyncio.Task | None = None
    if settings.nexus_automations_enabled:
        engine_task = asyncio.create_task(engine_loop())
    # The MCP session manager owns a task group for all /mcp sessions; its run()
    # context must wrap the app's serving lifetime.
    async with session_manager.run():
        try:
            yield
        finally:
            if engine_task is not None:
                engine_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await engine_task
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
app.include_router(settings_router.router)

# MCP server (Streamable HTTP) exposing the tool registry to external clients.
# Bearer-token gated; unset token fails closed. n8n consumes this same mount in M7.
app.mount("/mcp", build_mcp_asgi_app())


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
