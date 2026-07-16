"""CORE tool — `create_task` (safe). Lets the agent create an internal
coordination task directly (from chat or MCP) without an approval gate: a task is
a note-to-a-human with no effect outside the system, so it is `safe=True`
(user-locked). State-changing actions still go through the gated write tools.

Business-agnostic: a task has a title/description/priority/due date and nothing
vertical. The insert reads the tenant from the RLS GUC (`app.current_tenant_id()`),
so the handler needs no `tenant_id` input, consistent with every other tool.
"""
from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from .core import ToolDef, ToolInputError, ToolResult
from .registry import register

TASK_PRIORITIES = ["low", "normal", "high", "urgent"]


async def _create_task(conn, args: dict) -> ToolResult:
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ToolInputError("'title' is required.")
    title = title.strip()

    description = args.get("description")
    if description is not None and not isinstance(description, str):
        raise ToolInputError("'description' must be text.")

    priority = args.get("priority", "normal")
    if priority not in TASK_PRIORITIES:
        raise ToolInputError(f"'priority' must be one of: {', '.join(TASK_PRIORITIES)}.")

    due_at = args.get("due_at")
    if due_at is not None and str(due_at).strip():
        try:
            due_at = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ToolInputError("'due_at' must be an ISO-8601 date-time.")
    else:
        due_at = None

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """insert into public.tasks
                 (tenant_id, title, description, priority, due_at)
               values (app.current_tenant_id(), %s, %s, %s, %s)
               returning id""",
            (title, description, priority, due_at),
        )
        task_id = str((await cur.fetchone())["id"])

    return ToolResult(
        f"Created task: {title}.",
        {"task_id": task_id, "title": title, "priority": priority},
    )


register(ToolDef(
    name="create_task",
    description=(
        "Create an internal coordination task (a to-do for the team) — for "
        "follow-ups, reminders, or handoffs. This has no effect outside the system, "
        "so it is created immediately without approval. Use the specific action "
        "tools (which require approval) to change client/lead/schedule records or "
        "send messages."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short task title."},
            "description": {"type": "string", "description": "Optional detail / context."},
            "priority": {
                "type": "string",
                "enum": TASK_PRIORITIES,
                "description": "Task priority (default normal).",
            },
            "due_at": {"type": "string", "description": "Optional due date-time (ISO-8601)."},
        },
        "required": ["title"],
    },
    handler=_create_task,
    safe=True,
))
