"""CORE tool — `run_report`. Read-only text-to-SQL for aggregate/analytical
questions the parameterized entity tools can't answer.

Three independent safety layers (per PRD / CLAUDE.md — text-to-SQL is read-only,
reporting-scoped, never against writable state through generated SQL):

  1. Guard (`sql_guard.validate_report_sql`) — single SELECT/WITH, allowlisted
     tables, no writes/DDL/locking/second statement.
  2. Postgres — a dedicated transaction that is SET READ ONLY *before* any query
     and carries a 5s statement_timeout, so even a guard bypass cannot write or
     hang the pool. Runs as the same RLS-subject `nexus_app` role, tenant-scoped.
  3. Output cap — fetch 201, return at most 200 rows with a `truncated` flag.

`SET TRANSACTION READ ONLY` must precede the transaction's first snapshot, so it
is issued first; `set_config` (a read) and the report query follow under
read-only. The tenant is read off the caller's already-scoped connection so this
tool needs no `tenant_id` input.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from ...db import get_pool
from .core import ToolDef, ToolInputError, ToolResult, _jsonable
from .entities import SQL_SCHEMA_DOC
from .registry import register
from .sql_guard import ReportSqlError, validate_report_sql

ROW_CAP = 200
STATEMENT_TIMEOUT = "5s"


async def _run_report(conn, args: dict) -> ToolResult:
    raw_sql = args.get("sql")
    if not isinstance(raw_sql, str) or not raw_sql.strip():
        raise ToolInputError("'sql' is required.")
    try:
        sql = validate_report_sql(raw_sql)
    except ReportSqlError as exc:
        raise ToolInputError(f"Report query rejected: {exc}")

    # Read the tenant off the already-scoped caller connection.
    async with conn.cursor() as cur:
        await cur.execute("select current_setting('request.app.tenant_id', true)")
        tenant_id = (await cur.fetchone())[0]
    if not tenant_id:
        raise ToolInputError("no tenant in scope for reporting")

    columns, rows = await _execute_readonly(sql, tenant_id)

    truncated = len(rows) > ROW_CAP
    rows = rows[:ROW_CAP]
    data = {
        "columns": columns,
        "rows": [_jsonable(dict(r)) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
    tail = f", truncated to {ROW_CAP}" if truncated else ""
    return ToolResult(f"Report returned {len(rows)} row(s){tail}.", data)


async def _execute_readonly(sql: str, tenant_id: str):
    """Run `sql` in a fresh, dedicated READ ONLY transaction. Separated out so a
    test can drive it directly and prove the read-only barrier holds."""
    pool = get_pool()
    async with pool.connection() as conn2:
        async with conn2.transaction():
            # Order matters: READ ONLY must come before the first snapshot.
            await conn2.execute("set transaction read only")
            await conn2.execute(f"set local statement_timeout = '{STATEMENT_TIMEOUT}'")
            await conn2.execute(
                "select set_config('request.app.tenant_id', %s, true)", (tenant_id,)
            )
            async with conn2.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql)
                rows = await cur.fetchmany(ROW_CAP + 1)
                columns = [d.name for d in cur.description] if cur.description else []
    return columns, rows


register(ToolDef(
    name="run_report",
    description=(
        "Run a single read-only SQL query for aggregate/analytical/reporting "
        "questions the other tools can't answer — counts, breakdowns, group-bys, "
        "cross-table joins. SELECT or WITH…SELECT only; no writes. Tenant filtering "
        "is automatic — never add a tenant_id condition. Results are capped at "
        f"{ROW_CAP} rows.\n\n" + SQL_SCHEMA_DOC
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A single read-only SELECT (or WITH … SELECT) statement.",
            },
            "purpose": {
                "type": "string",
                "description": "One-line plain-language description of what this report answers.",
            },
        },
        "required": ["sql", "purpose"],
    },
    handler=_run_report,
))
