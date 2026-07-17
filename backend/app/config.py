"""Application settings, loaded from the repo-root .env via pydantic-settings.

Only NEXUS_APP_DB_URL is strictly required to boot the API. External-service keys
(Anthropic, Voyage, LangSmith) are optional so the app and its non-live tests run
without them; the features that need a key fail loudly only when exercised.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] is the repo root, where .env lives.
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database — the RLS-subject backend role (never the postgres/service role).
    nexus_app_db_url: str = ""

    # Tenancy. Env-configured this phase; JWT claim in Module 6 (see deps.get_tenant_id).
    nexus_tenant_id: str = "00000000-0000-0000-0000-000000000001"

    # Supabase (Storage uploads use the service-role key — documented exception).
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""

    # External services.
    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    # MCP server bearer token (static until Module 6 auth). Unset ⇒ /mcp 401s
    # every request (fail closed); the rest of the API is unaffected.
    nexus_mcp_token: str = ""

    # Shared HMAC secret for the webhook ingress (placeholder-adapter verification
    # until each real connector brings its platform's scheme). Unset ⇒ every
    # /api/webhooks/{source} request 401s (fail closed).
    nexus_webhook_secret: str = ""

    # Model ids (overridable). Sonnet is primary for chat; Haiku for cheap routing.
    chat_model: str = "claude-sonnet-5"
    # Cheap model for high-volume/low-stakes generation — the automations `generate`
    # step's `fast` option maps here (Haiku-for-high-volume stack rule).
    fast_model: str = "claude-haiku-4-5-20251001"
    embedding_model: str = "voyage-3.5"

    # Automations engine loops (Module 7b). The in-process dispatcher/cron/waker/
    # recovery cycle runs in the FastAPI lifespan. All optional/overridable:
    #   enabled=false disables the loops entirely (the REST API + manual runs still
    #   work); poll_seconds is the cycle interval; stale_minutes is how long a run
    #   may sit in `running` before the recovery sweep re-advances it.
    nexus_automations_enabled: bool = True
    nexus_automations_poll_seconds: float = 5
    nexus_automations_stale_minutes: int = 10

    # LangSmith — no-ops gracefully when the key is unset.
    langsmith_tracing: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "nexus"

    # CORS origins for the Vite dev server.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()
