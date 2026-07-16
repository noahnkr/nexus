"""FastAPI application entrypoint.

Lifespan opens/closes the psycopg async pool. CORS is permissive for the local
Vite dev server (the frontend also proxies /api, so CORS is a belt-and-braces
allowance for direct calls). Routers: auth (realtime token), documents (ingestion),
chat (threads + SSE).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import close_pool, open_pool
from .routers import auth, chat, documents


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
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

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
