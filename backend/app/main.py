"""FastAPI application entrypoint.

Lifespan opens/closes the psycopg async pool. CORS is permissive for the local
Vite dev server (the frontend also proxies /api, so CORS is a belt-and-braces
allowance for direct calls). Every `/api` route is JWT-protected (Module 6); the
only unauthenticated openings are `/healthz`, the HMAC-verified webhook ingress,
and the static-bearer `/mcp` mount.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import close_pool, open_pool
from .routers import chat, documents, events, home, tasks, webhooks
from .services.mcp_server import build_mcp_asgi_app, session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    # The MCP session manager owns a task group for all /mcp sessions; its run()
    # context must wrap the app's serving lifetime.
    async with session_manager.run():
        try:
            yield
        finally:
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

# MCP server (Streamable HTTP) exposing the tool registry to external clients.
# Bearer-token gated; unset token fails closed. n8n consumes this same mount in M7.
app.mount("/mcp", build_mcp_asgi_app())


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
