"""Connector layer public surface + adapter/runner bootstrap.

Importing this package runs each adapter module's module-level
`register_adapter()` call (mirroring `services/tools/__init__`), so the ingress
has every adapter registered before it serves a request — and, since Module 18a,
each sync runner's `register_runner()` call, so the connector loop finds its
pollable sources. A runner whose credentials are unset registers but reports
`enabled() == False`, which is how "no key configured" stays a no-op rather than
an error.
"""
from .base import NormalizedEvent, NormalizedResult, ConnectorAdapter, sign
from .registry import all_adapters, get_adapter, register_adapter

# Bootstrap: side-effecting imports register the five placeholder adapters...
from .adapters import gcal, gmail, goto, wellsky, welcomehome  # noqa: E402,F401
# ...and the poll-based sync runners.
from . import wh_runner  # noqa: E402,F401

__all__ = [
    "NormalizedEvent",
    "NormalizedResult",
    "ConnectorAdapter",
    "sign",
    "get_adapter",
    "register_adapter",
    "all_adapters",
]
