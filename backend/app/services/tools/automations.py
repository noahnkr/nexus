"""CORE tool — `run_automation`: start a manual-trigger automation (M15c).

**Safe** by definition: creating a run has no direct external effect. Every step
inside the run still goes through `execute_tool`, so a gated step parks the run
`waiting_approval` behind a task exactly as it would on any other path. Starting a
run does not bypass the gate; it only queues work that respects it.

Three refusals, all plain-language `ToolInputError`s:
  * the automation isn't found (the message lists what CAN be started),
  * its trigger isn't `manual` — an event/cron automation runs on its own trigger
    and starting it by hand would sidestep the conditions that define when it
    should fire,
  * the caller is an automation. This extends CLAUDE.md's automations-don't-trigger-
    automations rule from the events dispatcher to the tool layer. The dispatcher
    guard still stands on its own; this is an additional layer, not a replacement.

Why the run is created DEFERRED: a tool handler executes inside `execute_tool`'s
savepoint on a transaction that has not committed, and `advance_run` opens its own
per-step transactions that would not see the run row. So this queues the run
`waiting` with `wake_at=now()` and lets the M7b waker advance it on its next poll
(a few seconds). No new execution machinery, and the run is durable the moment the
caller's transaction commits.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from .core import ToolDef, ToolInputError, ToolResult, current_invocation
from .registry import register


async def _manual_automations(conn) -> list[dict]:
    """Every automation this tenant can start by hand (RLS scopes the query)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, name, status, trigger, conditions, steps
                 from public.automations
                where trigger->>'type' = 'manual'
                order by name"""
        )
        return await cur.fetchall()


async def _find(conn, needle: str) -> dict | None:
    """Resolve by id, else case-insensitive exact name. Deliberately not fuzzy —
    starting the wrong automation is a real effect, so an ambiguous name should
    fail and list the options rather than guess."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select id, name, status, trigger, conditions, steps
                 from public.automations
                where id::text = %s or lower(name) = lower(%s)
                limit 2""",
            (needle, needle),
        )
        rows = await cur.fetchall()
    return rows[0] if len(rows) == 1 else None


async def _run_automation(conn, args: dict) -> ToolResult:
    # Imported here, not at module scope: the engine imports `execute_tool` from
    # this package, so a top-level import would close a cycle through the tools
    # bootstrap. Deferring it to call time keeps the package import order simple.
    from ..automations.engine import start_run

    invocation = current_invocation() or {}
    tenant_id = invocation.get("tenant_id")
    if not tenant_id:
        # Only reachable if a handler were called outside execute_tool, which the
        # seam rule forbids — fail loudly rather than start an untenanted run.
        raise ToolInputError("Couldn't determine which workspace to run this in.")
    if invocation.get("source_system") == "automation":
        raise ToolInputError(
            "Automations can't start other automations."
        )

    needle = args.get("automation")
    if not isinstance(needle, str) or not needle.strip():
        raise ToolInputError("Which automation should I run?")
    needle = needle.strip()

    automation = await _find(conn, needle)
    if automation is None:
        available = [a["name"] for a in await _manual_automations(conn)]
        if not available:
            raise ToolInputError(
                f"There's no automation called '{needle}', and none are set up to "
                "be run manually."
            )
        listed = ", ".join(f"'{n}'" for n in available[:10])
        raise ToolInputError(
            f"There's no automation called '{needle}'. These can be run manually: "
            f"{listed}."
        )

    trigger_type = (automation.get("trigger") or {}).get("type")
    if trigger_type != "manual":
        raise ToolInputError(
            f"'{automation['name']}' runs on its own trigger — it can't be started "
            "this way."
        )

    entity_type = args.get("entity_type") or None
    entity_id = args.get("entity_id") or None

    run_id = await start_run(
        conn,
        tenant_id,
        automation,
        entity_type=entity_type if isinstance(entity_type, str) else None,
        entity_id=entity_id if isinstance(entity_id, str) else None,
        # A manual run is an explicit "do this now" — entry conditions are the
        # trigger's filter, not the operator's.
        skip_conditions=True,
        defer=True,
    )

    if run_id is None:
        # With skip_conditions the only None is the concurrency guard. Not an
        # error — the thing the user asked for is already happening.
        return ToolResult(
            f"'{automation['name']}' is already running — I left it to finish.",
            {"status": "already_running", "automation_id": str(automation["id"])},
        )

    return ToolResult(
        f"Started '{automation['name']}' — it will run within a few seconds; "
        "check its run history.",
        {"status": "queued", "run_id": run_id, "automation_id": str(automation["id"])},
    )


register(ToolDef(
    name="run_automation",
    description=(
        "Start an automation that is set up to be run manually. Use it when the "
        "user asks to run, trigger, or kick off a named automation. Only "
        "manual-trigger automations can be started this way — ones that run on an "
        "event or a schedule fire on their own. The run starts within a few "
        "seconds; any step inside it that needs approval still creates a task."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "automation": {
                "type": "string",
                "description": "The automation's name (or its id).",
            },
            "entity_type": {
                "type": "string",
                "description": (
                    "Optional record type to run it against, e.g. 'lead' or "
                    "'client'."
                ),
            },
            "entity_id": {
                "type": "string",
                "description": "Optional id of the record to run it against.",
            },
        },
        "required": ["automation"],
    },
    handler=_run_automation,
    safe=True,
))
