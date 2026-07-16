"""LLM / embedding client wiring + LangSmith tracing.

One wrapped Anthropic client and one Voyage client, lazily constructed. LangSmith
is configured from settings when a key is present; `@traceable` and `wrap_anthropic`
are safe no-ops when it is not, so nothing here requires a LangSmith key to run.

`traceable` is re-exported so services decorate their spans from a single import.
"""
from __future__ import annotations

import os

import anthropic
import voyageai
from langsmith import traceable  # re-exported; no-ops without LangSmith env
from langsmith.wrappers import wrap_anthropic

from .config import settings

# Surface LangSmith config (pydantic-settings reads .env into `settings`, but the
# langsmith SDK reads os.environ). Only set when a key is actually configured.
if settings.langsmith_api_key:
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_TRACING", settings.langsmith_tracing or "true")
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)

__all__ = ["traceable", "get_anthropic", "get_voyage"]

_anthropic_client: anthropic.AsyncAnthropic | None = None
_voyage_client: "voyageai.AsyncClient | None" = None


def get_anthropic() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = wrap_anthropic(
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
        )
    return _anthropic_client


def get_voyage() -> "voyageai.AsyncClient":
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.AsyncClient(api_key=settings.voyage_api_key or None)
    return _voyage_client
