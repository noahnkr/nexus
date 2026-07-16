"""Connector adapter registry: register/lookup by source name.

Mirrors the tool registry's shape. The package `__init__` imports every adapter
module so their module-level `register_adapter()` calls populate this map before
the ingress serves a request.
"""
from __future__ import annotations

from typing import Any

_ADAPTERS: dict[str, Any] = {}


def register_adapter(adapter: Any) -> None:
    _ADAPTERS[adapter.source] = adapter


def get_adapter(source: str) -> Any | None:
    return _ADAPTERS.get(source)


def all_adapters() -> list[Any]:
    return list(_ADAPTERS.values())
