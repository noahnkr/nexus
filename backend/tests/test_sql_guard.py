"""Offline unit tests for the read-only text-to-SQL guard (Module 2, Task 4).

The guard is a pure function, so this runs with no DB and no keys. It is the
first of run_report's three safety layers; these tests pin exactly what it
accepts and rejects.
"""
import pytest

from app.services.tools.sql_guard import (
    TABLE_ALLOWLIST,
    ReportSqlError,
    validate_report_sql,
)

ACCEPTED = [
    "select count(*) from leads group by status",
    "SELECT count(*) FROM leads GROUP BY status",
    "with x as (select status, count(*) c from leads group by status) select * from x",
    "select l.name, r.name from leads l join regions r on r.id = l.region_id",
    "select count(*) from schedules where status = 'no_show'",
    "select event_type, count(*) from events group by event_type limit 10",
    "select date_trunc('day', created_at) d, count(*) from leads group by d",
    "  select count(*) from clients ;  ",  # trailing semicolon + whitespace stripped
    "select tool_name from pending_actions",
    "select filename from documents where status = 'ready'",
]

REJECTED = [
    "insert into leads (name) values ('x')",
    "update leads set status = 'lost'",
    "delete from leads",
    "select * from leads; drop table leads",  # multiple statements
    "select * from leads into outfile 'x'",  # SELECT ... INTO
    "select * from leads for update",  # locking clause
    "select pg_sleep(10)",  # dangerous function
    "select set_config('x', 'y', true)",  # 'set' forbidden token
    "select * from document_chunks",  # not allowlisted (embeddings)
    "select * from chat_messages",  # not allowlisted (conversation content)
    "select * from tenants",  # not allowlisted (tenant registry)
    "truncate leads",
    "drop table leads",
    "select * from information_schema.tables",  # schema-prefixed, not allowlisted
    "",  # empty
    "   ",  # whitespace only
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_accepts(sql):
    out = validate_report_sql(sql)
    assert out and ";" not in out
    assert not out.endswith(";")


@pytest.mark.parametrize("sql", REJECTED)
def test_rejects(sql):
    with pytest.raises(ReportSqlError):
        validate_report_sql(sql)


def test_allowlist_membership():
    # The reporting surface excludes conversation/embedding/tenant tables.
    assert "documents" in TABLE_ALLOWLIST
    assert "document_chunks" not in TABLE_ALLOWLIST
    assert "chat_messages" not in TABLE_ALLOWLIST
    assert "tenants" not in TABLE_ALLOWLIST
    # All six entity tables are reportable.
    for t in ("leads", "clients", "resources", "schedules", "regions", "qualifications"):
        assert t in TABLE_ALLOWLIST


def test_normalizes_trailing_semicolon():
    assert validate_report_sql("select 1 from leads;") == "select 1 from leads"
