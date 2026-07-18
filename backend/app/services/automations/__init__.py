"""Automations engine package — the WHEN/IF/THEN recipe runtime.

Importing the package bootstraps the core function registry (side-effecting import
of `functions`, which self-registers `now`/`days_since` at module load — the same
pattern the tool package uses). Vertical functions (M10 scoring) register the same
way without touching core.

Public surface: recipe validation, the engine's run entry points, and the function
registry seam.
"""
from .engine import (
    advance_run,
    cancel_after_rejection,
    cancel_run,
    get_run,
    resume_after_approval,
    start_run,
    supersede_sequence_runs,
)
from .recipe import RecipeError, validate_recipe

# Bootstrap: registers the core functions (now, days_since).
from . import functions  # noqa: E402,F401

__all__ = [
    "validate_recipe",
    "RecipeError",
    "start_run",
    "advance_run",
    "get_run",
    "resume_after_approval",
    "cancel_after_rejection",
    "cancel_run",
    "supersede_sequence_runs",
]
