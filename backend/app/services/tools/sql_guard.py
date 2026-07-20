"""Read-only text-to-SQL guard: `validate_report_sql`.

This is the FIRST of three independent layers protecting `run_report` (the other
two are Postgres-level `SET TRANSACTION READ ONLY` + statement_timeout, and a
row cap). It is a pure function so it is exhaustively unit-testable offline.

It is intentionally conservative — it rejects anything it can't prove is a single
read over the allowlisted reporting tables. The Postgres READ ONLY transaction is
the true write-safety guarantee; this layer keeps the model from *reading* tables
outside the reporting surface (chat content, embeddings, other tenants' metadata)
and from smuggling in a second statement.
"""
from __future__ import annotations

import re

# Reporting surface: the six entity tables + the operational/audit tables. NOT
# document_chunks (embedding column; document content is search_documents' job),
# NOT chat_* (conversation content), NOT tenants (tenant registry).
TABLE_ALLOWLIST = frozenset({
    "leads",
    "clients",
    "resources",
    "resource_credentials",
    "schedules",
    "regions",
    "qualifications",
    "events",
    "tasks",
    "pending_actions",
    "external_ids",
    "documents",
})

# Word-boundary forbidden tokens: writes, DDL, DCL, session/programmatic control,
# and dangerous functions. Matched anywhere in the (lowercased) statement.
_FORBIDDEN = [
    "insert", "update", "delete", "merge", "truncate", "drop", "alter", "create",
    "grant", "revoke", "copy", "execute", "call", "do", "vacuum", "analyze",
    "set", "reset", "listen", "notify", "refresh", "lock", "comment", "security",
    "pg_read", "pg_sleep",
    # set_config() would re-point the tenant GUC mid-query and bypass RLS — it is
    # its own word token, so `\bset\b` above does not catch it. Forbid explicitly.
    "set_config",
]
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN) + r")\b")

# Locking clauses and SELECT ... INTO (both can escape a pure read).
_LOCKING_RE = re.compile(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b")
_INTO_RE = re.compile(r"\binto\b")

# CTE names introduced by WITH ... AS ( — these are legitimate `from` targets.
_CTE_RE = re.compile(r"(?:\bwith|,)\s+([a-z_][a-z0-9_]*)\s+as\s*\(", re.IGNORECASE)

# `from`/`join` targets (first identifier only; schema prefix stripped below).
_TABLE_RE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_.\"]*)", re.IGNORECASE)


class ReportSqlError(ValueError):
    """Raised when a report statement fails validation."""


def validate_report_sql(sql: str) -> str:
    """Return the normalized (single, semicolon-stripped) statement, or raise
    ReportSqlError describing why it was rejected."""
    if not isinstance(sql, str) or not sql.strip():
        raise ReportSqlError("empty statement")

    stmt = sql.strip()
    # Strip a single trailing semicolon; any remaining ';' means multiple statements.
    if stmt.endswith(";"):
        stmt = stmt[:-1].rstrip()
    if ";" in stmt:
        raise ReportSqlError("only a single statement is allowed")

    lowered = stmt.lower()

    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ReportSqlError("statement must start with SELECT or WITH")

    m = _FORBIDDEN_RE.search(lowered)
    if m:
        raise ReportSqlError(f"forbidden keyword: {m.group(1)}")
    if _LOCKING_RE.search(lowered):
        raise ReportSqlError("locking clauses (FOR UPDATE/SHARE) are not allowed")
    if _INTO_RE.search(lowered):
        raise ReportSqlError("SELECT ... INTO is not allowed")

    cte_names = {m.group(1).lower() for m in _CTE_RE.finditer(stmt)}

    referenced = set()
    for match in _TABLE_RE.finditer(stmt):
        ident = match.group(1).lower().strip('"')
        # Strip an optional schema prefix (public.leads -> leads).
        if "." in ident:
            ident = ident.split(".")[-1]
        referenced.add(ident)

    # Every from/join target must be an allowlisted table or a CTE defined above.
    # (Comma-joined tables past the first are not extracted here; the Postgres
    # READ ONLY transaction + per-table RLS remain the backstop.)
    for ident in referenced:
        if ident in cte_names:
            continue
        if ident not in TABLE_ALLOWLIST:
            raise ReportSqlError(f"table not allowed for reporting: {ident}")

    return stmt
