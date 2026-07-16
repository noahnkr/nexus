"""Connector layer public surface + adapter bootstrap.

Importing this package runs each adapter module's module-level
`register_adapter()` call (mirroring `services/tools/__init__`), so the ingress
has every adapter registered before it serves a request.
"""
from .base import NormalizedEvent, NormalizedResult, ConnectorAdapter, sign
from .registry import all_adapters, get_adapter, register_adapter

# Bootstrap: side-effecting imports register the five placeholder adapters.
from .adapters import gcal, gmail, goto, wellsky, welcomehome  # noqa: E402,F401

__all__ = [
    "NormalizedEvent",
    "NormalizedResult",
    "ConnectorAdapter",
    "sign",
    "get_adapter",
    "register_adapter",
    "all_adapters",
]
