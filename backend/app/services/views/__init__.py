"""Vertical view-services seam (Modules 9–10).

Home to the pipeline views' server-side content: stage configs, funnel metrics,
and smart-summary prompts. `summary.py` is the view-agnostic smart-summary helper
(M10 reuses it); the per-view modules (`leads.py`, later `caregivers.py`) supply
only the vertical content — stage labels, metric queries, and the prompt intro.

Core never imports from this package — the dependency runs one way (vertical view
code -> core helpers like `events`, `llm`, `event_summaries`), never back.
"""
