"""Tenant settings seam — user-facing workspace + agent preferences (Module 15b).

The ONE place `tenant_settings.settings` is read or written. Everything about a key
lives here: its default, its validation, and its plain-language label for the audit
line. Callers get a fully-populated dict and never think about the jsonb.

Two boundaries this module holds:

  * **Not a config store.** Infra config and credentials stay in env vars
    (CLAUDE.md). Nothing here is a secret, and nothing here is read by machine
    paths (`/mcp`, webhooks) — those resolve tenant from env and have no user
    preferences to honor.
  * **Not a junk drawer.** Unknown keys are rejected rather than stored, so the
    jsonb can't silently accumulate dead preferences that no code reads.

Audit: `update_settings` logs `settings.updated` naming the changed KEYS only —
never their values. `agent_instructions` is free text an owner may treat as
private, and the event log is a broadly-readable surface.
"""
from __future__ import annotations

from typing import Any, Callable

from psycopg.rows import dict_row
from psycopg.types.json import Json

from .events import log_event

MAX_WORKSPACE_NAME = 80
MAX_AGENT_INSTRUCTIONS = 4000

AGENT_TONES = ("balanced", "professional", "friendly", "concise")


class SettingsError(Exception):
    """Invalid key or value. The message is user-facing — the router returns it as
    the 422 detail, so it must read as plain language, not a type error."""


def _validate_text(key: str, label: str, limit: int) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise SettingsError(f"{label} must be text.")
        text = value.strip()
        if len(text) > limit:
            raise SettingsError(f"{label} must be {limit} characters or fewer.")
        return text

    return check


def _validate_tone(value: Any) -> str:
    if not isinstance(value, str) or value not in AGENT_TONES:
        raise SettingsError(f"Tone must be one of: {', '.join(AGENT_TONES)}.")
    return value


# The whitelist: key -> (default, validator, audit label). Adding a preference is a
# one-line change here plus its UI; no migration, because the column is jsonb.
SETTINGS_KEYS: dict[str, tuple[Any, Callable[[Any], Any], str]] = {
    "workspace_name": (
        "",
        _validate_text("workspace_name", "Workspace name", MAX_WORKSPACE_NAME),
        "workspace name",
    ),
    "agent_instructions": (
        "",
        _validate_text("agent_instructions", "Agent instructions", MAX_AGENT_INSTRUCTIONS),
        "agent instructions",
    ),
    "agent_tone": ("balanced", _validate_tone, "tone"),
}


def defaults() -> dict:
    return {key: default for key, (default, _v, _l) in SETTINGS_KEYS.items()}


async def get_settings(conn) -> dict:
    """Every whitelisted key, defaults filled in. A tenant with no row yet is not a
    special case for callers — it just gets defaults."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select settings from public.tenant_settings limit 1")
        row = await cur.fetchone()

    stored = (row or {}).get("settings") or {}
    merged = defaults()
    for key in merged:
        if key in stored:
            merged[key] = stored[key]
    return merged


async def update_settings(conn, tenant_id: str, patch: dict) -> dict:
    """Validate and apply a partial update; returns the full settings dict.

    Raises SettingsError (-> 422) on an unknown key or an invalid value, before
    anything is written — a rejected patch leaves the stored row untouched.
    """
    if not isinstance(patch, dict) or not patch:
        raise SettingsError("No settings were provided.")

    unknown = [k for k in patch if k not in SETTINGS_KEYS]
    if unknown:
        raise SettingsError(f"Unknown setting: {unknown[0]}.")

    # Validate everything first so a bad second key can't leave the first applied.
    cleaned = {key: SETTINGS_KEYS[key][1](value) for key, value in patch.items()}

    current = await get_settings(conn)
    changed = sorted(k for k, v in cleaned.items() if current.get(k) != v)
    merged = {**current, **cleaned}

    await conn.execute(
        """insert into public.tenant_settings (tenant_id, settings)
           values (%s, %s)
           on conflict (tenant_id) do update set settings = excluded.settings""",
        (tenant_id, Json(merged)),
    )

    if changed:
        labels = [SETTINGS_KEYS[k][2] for k in changed]
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="user",
            event_type="settings.updated",
            payload={
                # Key names only — never the values (see the module docstring).
                "summary": f"Settings updated: {', '.join(labels)}",
                "keys": changed,
            },
        )

    return merged
