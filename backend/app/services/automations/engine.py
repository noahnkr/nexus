"""The step-executing engine — runs a validated recipe to completion, or parks it
at a delay (`waiting`) or an approval gate (`waiting_approval`).

Two entry points:
  * `start_run` — build the run's scope (trigger event -> `trigger.`, linked entity
    -> `entity.`), evaluate entry conditions (all-true or no run — condition-false
    at the entry is normal filtering, not an event), insert the run row (a
    concurrency unique-violation -> `automation.run_skipped` + None), log
    `automation.run_started`.
  * `advance_run` — loop from `step_index`, **one `tenant_tx` per step** so a crash
    resumes at the next step without replaying side effects. Each step commits its
    effects + the `context`/`step_index` bump + one `step_log` entry together.

Every step effect goes through an existing seam: tool steps via `execute_tool`
(audited + gated for free), LLM via `get_anthropic()`, audit via `log_event`. The
engine adds orchestration, never a second execution path. `source_system='automation'`
on everything it writes.

Failure (user-locked): fail the run + a plain-language review task + an
`automation.run_failed` event. No retries this phase.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ...config import settings
from ...db import tenant_tx
from ...llm import get_anthropic, traceable
from ..events import log_event
from ..tools import execute_tool
from .entities import get_entity
from .recipe import MAX_STEPS
from .templates import TemplateError, render

# advance_run processes at most this many step-transactions before returning — a
# backstop; a recipe is capped at MAX_STEPS and each iteration bumps the index,
# parks, or terminates, so a healthy run stops well inside this bound.
_MAX_ITERATIONS = MAX_STEPS + 2

_GENERATE_MAX_TOKENS = 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# scope + condition evaluation (declarative — no code, no LLM in the control path)
# ---------------------------------------------------------------------------
def _trigger_scope(event: dict | None) -> dict:
    if not event:
        return {}
    entity_id = event.get("entity_id")
    created_at = event.get("created_at")
    return {
        "event_type": event.get("event_type"),
        "source_system": event.get("source_system"),
        "entity_type": event.get("entity_type"),
        "entity_id": str(entity_id) if entity_id else None,
        "payload": event.get("payload") or {},
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


def _get_path(scope: dict, field: str) -> tuple[bool, Any]:
    cur: Any = scope
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _compare(op: str, val: Any, target: Any) -> bool:
    try:
        if op == "eq":
            return val == target
        if op == "neq":
            return val != target
        if op in ("gt", "gte", "lt", "lte"):
            a, b = val, target
            # Coerce numeric-looking strings so "5" > 3 works; fall back to native
            # comparison (ISO date strings order correctly lexically).
            if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
                try:
                    a, b = float(a), float(b)
                except (ValueError, TypeError):
                    pass
            if op == "gt":
                return a > b
            if op == "gte":
                return a >= b
            if op == "lt":
                return a < b
            return a <= b
        if op == "contains":
            return target in val
        if op == "not_contains":
            return target not in val
    except TypeError:
        return False
    return False


def _eval_condition(cond: dict, scope: dict) -> bool:
    field = cond.get("field", "")  # a path, never rendered — it stays literal
    op = cond.get("op")
    found, val = _get_path(scope, field)
    if op == "exists":
        return found and val is not None
    if op == "not_exists":
        return (not found) or val is None
    if not found:
        return False
    # The comparison VALUE is template-rendered (Module 11a): value="{{context.score}}"
    # compares against the real number, not the literal string. An unresolvable path
    # makes the condition FALSE (never a run failure) — one rule for entry conditions
    # (no run exists yet to fail) and step conditions: "a comparison against a value
    # that isn't there is simply not true". A plain literal renders to itself.
    try:
        target = render(cond.get("value"), scope)
    except TemplateError:
        return False
    return _compare(op, val, target)


def _freeze_conditions(conditions: list, scope: dict) -> list:
    """Freeze a wait_until's condition VALUES against the current scope (fields stay
    literal, to match the future event). An unresolvable value is left as its raw
    template so resume-time evaluation applies the same false-on-missing rule
    (`_eval_condition`) rather than failing the wait — the Module 11a alignment."""
    frozen: list = []
    for c in conditions:
        c2 = dict(c)
        if "value" in c2:
            try:
                c2["value"] = render(c2["value"], scope)
            except TemplateError:
                pass  # keep raw; re-rendered (false on miss) at resume
        frozen.append(c2)
    return frozen


def _eval_all(conditions: list, scope: dict) -> bool:
    return all(_eval_condition(c, scope) for c in conditions)


# ---------------------------------------------------------------------------
# row loading
# ---------------------------------------------------------------------------
async def _load_automation(conn, automation_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, name, trigger, conditions, steps, status "
            "from public.automations where id = %s",
            (automation_id,),
        )
        return await cur.fetchone()


async def _load_run_for_update(conn, run_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from public.automation_runs where id = %s for update", (run_id,)
        )
        return await cur.fetchone()


async def get_run(conn, run_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.automation_runs where id = %s", (run_id,))
        return await cur.fetchone()


async def _load_event(conn, event_id: str) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, event_type, source_system, entity_type, entity_id, payload, "
            "created_at from public.events where id = %s",
            (event_id,),
        )
        return await cur.fetchone()


async def _build_scope(conn, run: dict) -> dict:
    """Rebuild {trigger, entity, context} from the durable run — safe to call on
    every advance (survives restarts; `trigger`/`entity` are reloaded, `context`
    is the accumulated step output)."""
    trigger: dict = {}
    if run.get("trigger_event_id"):
        ev = await _load_event(conn, str(run["trigger_event_id"]))
        trigger = _trigger_scope(ev)
    entity = await get_entity(
        conn,
        run.get("entity_type"),
        str(run["entity_id"]) if run.get("entity_id") else None,
    ) or {}
    return {"trigger": trigger, "entity": entity, "context": run.get("context") or {}}


# ---------------------------------------------------------------------------
# run-state writers
# ---------------------------------------------------------------------------
def _append_log(run: dict, index: int, type_: str, summary: str, status: str) -> list:
    log = list(run.get("step_log") or [])
    log.append({"index": index, "type": type_, "summary": summary,
                "status": status, "at": _now_iso()})
    return log


async def _save_step(
    conn,
    run_id: str,
    *,
    context: dict,
    step_index: int,
    step_log: list,
    status: str = "running",
    wake_at_minutes: int | None = None,
) -> None:
    if wake_at_minutes is not None:
        await conn.execute(
            """update public.automation_runs
                  set context=%s, step_index=%s, step_log=%s, status=%s,
                      wake_at = now() + make_interval(mins => %s)
                where id=%s""",
            (Json(context), step_index, Json(step_log), status, wake_at_minutes, run_id),
        )
    else:
        await conn.execute(
            """update public.automation_runs
                  set context=%s, step_index=%s, step_log=%s, status=%s
                where id=%s""",
            (Json(context), step_index, Json(step_log), status, run_id),
        )


async def _log_run_event(
    conn, tenant_id: str, run: dict, automation: dict, event_type: str, summary: str
) -> str:
    entity_type = run.get("entity_type")
    entity_id = str(run["entity_id"]) if run.get("entity_id") else None
    return await log_event(
        conn,
        tenant_id=tenant_id,
        source_system="automation",
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload={
            "summary": summary,
            "automation_id": str(automation["id"]),
            "automation_name": automation["name"],
            "run_id": str(run["id"]),
        },
    )


async def _complete(
    conn, tenant_id: str, run: dict, automation: dict, *, note: str | None = None
) -> None:
    await conn.execute(
        "update public.automation_runs set status='completed', finished_at=now() where id=%s",
        (run["id"],),
    )
    summary = f"Automation '{automation['name']}' completed"
    if note:
        summary += f" ({note})"
    await _log_run_event(conn, tenant_id, run, automation, "automation.run_completed", summary)


async def _fail_run(
    conn, tenant_id: str, run: dict, automation: dict, index: int,
    step_type: str, error: str, *, create_task: bool = True,
) -> None:
    """Fail path (user-locked): mark the run failed, log the failure, and (by
    default) raise a plain-language review task linked to the failure event
    (resolution.py pattern).

    `create_task=False` is the post-approval-failure case (7b): Module 5 already
    left the failed action's task pending, so a second review task would be a
    duplicate human surface — one per failure."""
    log = _append_log(run, index, step_type, f"Step failed: {error}", "failed")
    await conn.execute(
        "update public.automation_runs set status='failed', error=%s, step_log=%s, "
        "finished_at=now() where id=%s",
        (error, Json(log), run["id"]),
    )
    event_id = await _log_run_event(
        conn, tenant_id, run, automation, "automation.run_failed",
        f"Automation '{automation['name']}' failed: {error}",
    )
    if create_task:
        await conn.execute(
            """insert into public.tasks
                 (tenant_id, title, description, priority, originating_event_id)
               values (%s, %s, %s, 'high', %s)""",
            (
                tenant_id,
                f"Automation failed: {automation['name']}",
                f"The automation '{automation['name']}' stopped at step {index + 1} "
                f"({step_type}) because: {error}. Review the recipe and re-run if appropriate.",
                event_id,
            ),
        )


# ---------------------------------------------------------------------------
# start_run
# ---------------------------------------------------------------------------
async def start_run(
    conn,
    tenant_id: str,
    automation: dict,
    *,
    trigger_event: dict | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    skip_conditions: bool = False,
    defer: bool = False,
) -> str | None:
    """Create a run if the entry conditions pass and no active run already exists
    for this (automation, entity). Returns the run id, or None when filtered out
    (conditions false) or skipped by the concurrency guard.

    `skip_conditions=True` is the explicit manual-run override ("Run now" forces
    the run regardless of entry conditions) — with it, a None return can only mean
    the concurrency guard fired, which lets the manual endpoint answer 409 cleanly.

    `defer=True` (M15c) parks the run `waiting` with `wake_at=now()` instead of
    starting it `running`, so the M7b waker advances it on its next poll. This
    exists for callers that CANNOT commit before advancing — notably a tool
    handler, which runs inside `execute_tool`'s savepoint on the caller's still-open
    transaction. Rather than invent a post-commit hook, the deferred run rides the
    durable machinery that already exists; it starts a poll interval later, which
    is fine for a human-initiated "run this".

    The caller must COMMIT before calling `advance_run` (advance opens its own
    per-step transactions and must see the committed run row)."""
    # Entity: explicit args win, else inherit from the triggering event.
    if entity_type is None and trigger_event is not None:
        entity_type = trigger_event.get("entity_type")
        entity_id = str(trigger_event["entity_id"]) if trigger_event.get("entity_id") else None

    # Entry conditions over {trigger, entity, context={}}.
    if not skip_conditions:
        scope = {
            "trigger": _trigger_scope(trigger_event),
            "entity": await get_entity(conn, entity_type, entity_id) or {},
            "context": {},
        }
        if not _eval_all(automation.get("conditions") or [], scope):
            return None  # normal filtering — not an event

    trigger_event_id = trigger_event["id"] if trigger_event else None
    try:
        async with conn.transaction():  # savepoint: a unique violation rolls back to here
            async with conn.cursor() as cur:
                await cur.execute(
                    """insert into public.automation_runs
                         (tenant_id, automation_id, status, trigger_event_id,
                          entity_type, entity_id, wake_at, step_log)
                       values (%s, %s, %s, %s, %s, %s, %s, %s)
                       returning id""",
                    (
                        tenant_id,
                        automation["id"],
                        "waiting" if defer else "running",
                        trigger_event_id,
                        entity_type,
                        entity_id,
                        # now() so the very next waker poll claims it.
                        datetime.now(timezone.utc) if defer else None,
                        Json([{"note": "queued — waiting for the next run cycle"}])
                        if defer
                        else Json([]),
                    ),
                )
                run_id = str((await cur.fetchone())[0])
    except errors.UniqueViolation:
        # One active run per (automation, entity) — a re-trigger while in flight.
        await log_event(
            conn,
            tenant_id=tenant_id,
            source_system="automation",
            event_type="automation.run_skipped",
            entity_type=entity_type,
            entity_id=entity_id,
            payload={
                "summary": (
                    f"Automation '{automation['name']}' skipped — already running "
                    "for this record."
                ),
                "automation_id": str(automation["id"]),
                "automation_name": automation["name"],
            },
        )
        return None

    run = {"id": run_id, "entity_type": entity_type, "entity_id": entity_id}
    await _log_run_event(
        conn, tenant_id, run, automation, "automation.run_started",
        f"Automation '{automation['name']}' queued to run"
        if defer
        else f"Automation '{automation['name']}' started",
    )
    return run_id


# ---------------------------------------------------------------------------
# advance_run + per-step execution
# ---------------------------------------------------------------------------
@traceable(run_type="chain", name="automation_run")
async def advance_run(tenant_id: str, run_id: str) -> None:
    """Drive a `running` run forward, one committed step per transaction, until it
    completes, parks (`waiting`/`waiting_approval`), or fails. Idempotent per step:
    a re-entry (waker, recovery, a crashed retry) resumes cleanly at `step_index`.

    This is the background/manual path — each step is its own `tenant_tx` (durable
    across crashes). The approval-resume path advances within the request
    transaction instead (see `_advance_in_conn`)."""
    for _ in range(_MAX_ITERATIONS):
        async with tenant_tx(tenant_id) as conn:
            keep_going = await _advance_one_step(conn, tenant_id, run_id)
        if not keep_going:
            return


async def _advance_in_conn(conn, tenant_id: str, run_id: str) -> None:
    """Advance a run within an existing transaction (`conn`) — used by the approval
    resume, which continues the run in the approving request's transaction, exactly
    like a chat-approved action runs in-request. Steps share the one transaction
    (no per-step commit); the caller's commit persists the whole tail atomically."""
    for _ in range(_MAX_ITERATIONS):
        if not await _advance_one_step(conn, tenant_id, run_id):
            return


async def _advance_one_step(conn, tenant_id: str, run_id: str) -> bool:
    """Process exactly one step inside `conn`'s transaction. Returns True if the
    caller should continue to the next step, False if the run parked or terminated."""
    run = await _load_run_for_update(conn, run_id)
    if run is None or run["status"] != "running":
        return False  # parked, terminal, or vanished — nothing to advance

    automation = await _load_automation(conn, str(run["automation_id"]))
    if automation is None:
        await _fail_run(conn, tenant_id, run, {"id": run["automation_id"], "name": "(deleted)"},
                        run["step_index"], "unknown", "the automation no longer exists")
        return False

    steps = automation.get("steps") or []
    idx = run["step_index"]
    if idx >= len(steps):
        await _complete(conn, tenant_id, run, automation)
        return False

    step = steps[idx]
    step_type = step.get("type")
    try:
        scope = await _build_scope(conn, run)
        handler = _STEP_HANDLERS.get(step_type)
        if handler is None:
            await _fail_run(conn, tenant_id, run, automation, idx, step_type or "unknown",
                            f"unknown step type '{step_type}'")
            return False
        return await handler(conn, tenant_id, run, automation, idx, step, scope)
    except TemplateError as exc:
        await _fail_run(conn, tenant_id, run, automation, idx, step_type or "unknown", str(exc))
        return False
    except Exception as exc:  # noqa: BLE001 — any step error fails the run, never crashes the loop
        await _fail_run(conn, tenant_id, run, automation, idx, step_type or "unknown", str(exc))
        return False


async def _step_tool(conn, tenant_id, run, automation, idx, step, scope) -> bool:
    tool_name = step["tool"]
    rendered = render(step.get("input") or {}, scope)
    result = await execute_tool(
        conn, tenant_id, tool_name, rendered, source_system="automation"
    )
    data = result.data if isinstance(result.data, dict) else {}

    # Gate: a state-changing tool queued for approval — park the run and stamp the
    # pending action so 7b's approval hook can find and resume it.
    if data.get("status") == "queued":
        action_id = data.get("pending_action_id")
        await conn.execute(
            "update public.pending_actions set automation_run_id=%s where id=%s",
            (run["id"], action_id),
        )
        log = _append_log(run, idx, "tool", result.summary, "queued")
        await _save_step(
            conn, run["id"], context=run.get("context") or {}, step_index=idx,
            step_log=log, status="waiting_approval",
        )
        return False

    if result.is_error:
        await _fail_run(conn, tenant_id, run, automation, idx, "tool", result.summary)
        return False

    context = dict(run.get("context") or {})
    if step.get("save_as"):
        context[step["save_as"]] = data
    log = _append_log(run, idx, "tool", result.summary, "ok")
    await _save_step(conn, run["id"], context=context, step_index=idx + 1, step_log=log)
    return True


async def _step_delay(conn, tenant_id, run, automation, idx, step, scope) -> bool:
    minutes = step.get("minutes") or 0
    hours = step.get("hours") or 0
    days = step.get("days") or 0
    total = minutes + hours * 60 + days * 1440
    if days:
        human = f"waiting {days} day(s)"
    elif hours:
        human = f"waiting {hours} hour(s)"
    else:
        human = f"waiting {minutes} minute(s)"
    log = _append_log(run, idx, "delay", human, "waiting")
    # Bump the index now so the waker resumes at the NEXT step, not the delay again.
    await _save_step(
        conn, run["id"], context=run.get("context") or {}, step_index=idx + 1,
        step_log=log, status="waiting", wake_at_minutes=total,
    )
    return False


async def _step_condition(conn, tenant_id, run, automation, idx, step, scope) -> bool:
    if _eval_all(step.get("conditions") or [], scope):
        log = _append_log(run, idx, "condition", "condition met", "ok")
        await _save_step(
            conn, run["id"], context=run.get("context") or {}, step_index=idx + 1, step_log=log
        )
        return True
    # False -> the run completes early (linear stop-guard; no else-branch this phase).
    log = _append_log(run, idx, "condition", "condition not met — stopped early", "stopped")
    await conn.execute(
        "update public.automation_runs set step_log=%s where id=%s",
        (Json(log), run["id"]),
    )
    run["step_log"] = log
    await _complete(
        conn, tenant_id, run, automation,
        note=f"stopped early: condition not met at step {idx + 1}",
    )
    return False


async def _step_function(conn, tenant_id, run, automation, idx, step, scope) -> bool:
    from .functions import get_function

    fn = get_function(step["function"])
    if fn is None:
        await _fail_run(conn, tenant_id, run, automation, idx, "function",
                        f"no function named '{step['function']}' exists")
        return False
    rendered_args = render(step.get("args") or {}, scope)
    value = await fn.handler(conn, rendered_args)
    context = dict(run.get("context") or {})
    if step.get("save_as"):
        context[step["save_as"]] = value
    log = _append_log(run, idx, "function", f"computed {fn.name}", "ok")
    await _save_step(conn, run["id"], context=context, step_index=idx + 1, step_log=log)
    return True


async def _step_wait_until(conn, tenant_id, run, automation, idx, step, scope) -> bool:
    """Park the run until a matching event arrives (WS5). The awaited pattern is
    stored in `awaiting`; `wake_at` holds the optional timeout deadline (the waker
    stops a timed-out wait). Condition VALUES are frozen against the current context
    now; field paths stay literal to evaluate against the future event at resume.
    step_index is bumped so resume continues at the NEXT step."""
    event_type = step["event_type"]
    conditions = _freeze_conditions(step.get("conditions") or [], scope)
    awaiting = {"event_type": event_type, "conditions": conditions}
    timeout = step.get("timeout_minutes")
    log = _append_log(run, idx, "wait_until", f"waiting for {event_type}", "waiting")
    if timeout:
        await conn.execute(
            """update public.automation_runs
                  set status='waiting_event', step_index=%s, step_log=%s, awaiting=%s,
                      wake_at = now() + make_interval(mins => %s)
                where id=%s""",
            (idx + 1, Json(log), Json(awaiting), timeout, run["id"]),
        )
    else:
        await conn.execute(
            """update public.automation_runs
                  set status='waiting_event', step_index=%s, step_log=%s, awaiting=%s,
                      wake_at = null
                where id=%s""",
            (idx + 1, Json(log), Json(awaiting), run["id"]),
        )
    return False


async def timeout_wait(conn, tenant_id: str, run_id: str) -> bool:
    """Stop a `waiting_event` run whose timeout deadline passed without the event
    (WS5, waker path). 'Stop' = complete with a plain note (not a failure — nothing
    went wrong; the awaited event simply didn't arrive in time)."""
    run = await _load_run_for_update(conn, run_id)
    if run is None or run["status"] != "waiting_event":
        return False
    automation = await _load_automation(conn, str(run["automation_id"])) or {
        "id": run["automation_id"], "name": "(deleted)",
    }
    log = _append_log(
        run, run["step_index"], "wait_until",
        "stopped — timed out waiting for the event", "stopped",
    )
    await conn.execute(
        "update public.automation_runs set status='completed', step_log=%s, "
        "awaiting=null, finished_at=now() where id=%s",
        (Json(log), run_id),
    )
    run["step_log"] = log
    await _log_run_event(
        conn, tenant_id, run, automation, "automation.run_completed",
        f"Automation '{automation['name']}' completed (timed out waiting for an event)",
    )
    return True


async def _step_generate(conn, tenant_id, run, automation, idx, step, scope) -> bool:
    prompt = render(step["prompt"], scope)
    model = settings.fast_model if step.get("model") == "fast" else settings.chat_model
    response = await get_anthropic().messages.create(
        model=model,
        max_tokens=_GENERATE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        getattr(block, "text", "") for block in response.content
        if getattr(block, "type", None) == "text"
    ).strip()
    context = dict(run.get("context") or {})
    context[step["save_as"]] = text
    log = _append_log(run, idx, "generate", "generated content", "ok")
    await _save_step(conn, run["id"], context=context, step_index=idx + 1, step_log=log)
    return True


_STEP_HANDLERS = {
    "tool": _step_tool,
    "delay": _step_delay,
    "condition": _step_condition,
    "function": _step_function,
    "generate": _step_generate,
    "wait_until": _step_wait_until,
}


# ---------------------------------------------------------------------------
# approval resume / cancel (7b) — called by services/approvals.py's end-of-function
# hook. The run-state machine stays in this one file; approvals stays thin.
# ---------------------------------------------------------------------------
async def resume_after_approval(
    conn, tenant_id: str, run_id: str, *, tool_result: dict, is_error: bool, error: str | None,
) -> None:
    """Resume a run parked at `waiting_approval` after its gated tool executed on
    approval. Runs inside the approval's request transaction (`conn`); the actual
    step advance then continues in `advance_run`'s own per-step transactions —
    exactly like a chat-approved action executes in-request.

    On a post-approval handler *failure* the run fails with no second review task:
    Module 5 already leaves the failed action's task pending (one human surface per
    failure)."""
    run = await _load_run_for_update(conn, run_id)
    if run is None or run["status"] != "waiting_approval":
        return  # already resumed/resolved (double-resolve guard is in approvals.py)
    automation = await _load_automation(conn, str(run["automation_id"]))
    idx = run["step_index"]
    steps = (automation or {}).get("steps") or []
    step = steps[idx] if idx < len(steps) else {}

    if is_error:
        # Post-approval failure: fail the run but create NO review task — the failed
        # action's task already stays pending (Module 5), one human surface per failure.
        await _fail_run(
            conn, tenant_id, run, automation or {"id": run["automation_id"], "name": "(deleted)"},
            idx, "tool", error or "the approved action failed", create_task=False,
        )
        return

    # Merge the tool result into context under the paused step's save_as, bump past
    # the gated step, flip back to running, and continue in THIS transaction.
    context = dict(run.get("context") or {})
    if step.get("save_as"):
        context[step["save_as"]] = tool_result
    log = _append_log(run, idx, "tool", "approved and sent", "ok")
    await _save_step(conn, run_id, context=context, step_index=idx + 1, step_log=log,
                     status="running")
    await _advance_in_conn(conn, tenant_id, run_id)


async def cancel_after_rejection(
    conn, tenant_id: str, run_id: str, *, resolved_by: str | None = None,
) -> None:
    """Cancel a run whose gated step was rejected. `automation.run_cancelled` names
    the automation and the rejecting user; later steps never run."""
    run = await _load_run_for_update(conn, run_id)
    if run is None or run["status"] != "waiting_approval":
        return
    automation = await _load_automation(conn, str(run["automation_id"])) or {
        "id": run["automation_id"], "name": "(deleted)",
    }
    log = _append_log(run, run["step_index"], "tool", "approval rejected — cancelled", "stopped")
    await conn.execute(
        "update public.automation_runs set status='cancelled', step_log=%s, "
        "finished_at=now() where id=%s",
        (Json(log), run_id),
    )
    run["step_log"] = log
    who = f" by {resolved_by}" if resolved_by else ""
    await _log_run_event(
        conn, tenant_id, run, automation, "automation.run_cancelled",
        f"Automation '{automation['name']}' cancelled — approval rejected{who}",
    )


async def supersede_sequence_runs(
    conn, tenant_id: str, entity_type: str | None, entity_id: str | None, *, view: str
) -> int:
    """Cancel every active run of a *bound* sequence (`binding->>'view' = view`) for
    this entity — so advancing an entity's stage instantly ends the prior stage's
    sequence and only the current stage's can be in flight. Returns how many were
    cancelled.

    Generic and binding-driven (reads only the core `binding` column, never a stage
    name), so M10 reuses it verbatim with `view='caregivers'`. A `waiting_approval`
    run holding a still-pending action is rejected through the approvals seam (the
    same path the cancel-run endpoint uses, so no approval is orphaned); every other
    active run is cancelled directly. The caller invokes this on a stage change,
    before the dispatcher starts the new stage's run — so there is never a window
    with two active sequence runs for the entity.
    """
    if entity_id is None:
        return 0
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select r.id, r.status
                 from public.automation_runs r
                 join public.automations a on a.id = r.automation_id
                where r.entity_type = %s and r.entity_id = %s
                  and r.status in ('running','waiting','waiting_approval','waiting_event')
                  and a.binding->>'view' = %s""",
            (entity_type, entity_id, view),
        )
        runs = await cur.fetchall()

    cancelled = 0
    for run in runs:
        run_id = str(run["id"])
        if run["status"] == "waiting_approval":
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select id from public.pending_actions "
                    "where automation_run_id = %s and status = 'pending' limit 1",
                    (run_id,),
                )
                pending = await cur.fetchone()
            if pending is not None:
                # Lazy import breaks the engine<->approvals cycle (approvals imports
                # this package). reject_action -> cancel_after_rejection cancels the run.
                from ..approvals import ActionAlreadyResolved, reject_action

                try:
                    await reject_action(
                        conn, tenant_id, str(pending["id"]),
                        note="Superseded — the record advanced to a new stage",
                    )
                    cancelled += 1
                    continue
                except ActionAlreadyResolved:
                    pass  # raced to resolved — fall through to a direct cancel
        if await cancel_run(conn, tenant_id, run_id):
            cancelled += 1
    return cancelled


async def cancel_run(
    conn, tenant_id: str, run_id: str, *, resolved_by: str | None = None,
) -> bool:
    """Directly cancel an active run (Module 8a's cancel-run endpoint) for the
    `running`/`waiting` case, or a `waiting_approval` run that has no still-pending
    action to reject through. Returns False if the run is already terminal (the
    router turns that into a 409). The `waiting_approval`-with-pending-action case
    is handled by the router via `approvals.reject_action` — the one sanctioned
    seam — so this never orphans a pending approval."""
    run = await _load_run_for_update(conn, run_id)
    if run is None or run["status"] not in ("running", "waiting", "waiting_approval"):
        return False
    automation = await _load_automation(conn, str(run["automation_id"])) or {
        "id": run["automation_id"], "name": "(deleted)",
    }
    log = _append_log(run, run["step_index"], "run", "cancelled by user", "stopped")
    await conn.execute(
        "update public.automation_runs set status='cancelled', step_log=%s, "
        "finished_at=now() where id=%s",
        (Json(log), run_id),
    )
    run["step_log"] = log
    who = f" by {resolved_by}" if resolved_by else ""
    await _log_run_event(
        conn, tenant_id, run, automation, "automation.run_cancelled",
        f"Automation '{automation['name']}' cancelled{who}",
    )
    return True
