"""Schema + RLS tests for the Module 1 chat tables (chat_threads, chat_messages).

Table existence and policy counts run over the direct psycopg (postgres) fixture.
Cross-tenant invisibility runs over the real RLS surface via PostgREST clients.
"""
import pytest
from postgrest.exceptions import APIError

CHAT_TABLES = ("chat_threads", "chat_messages")


def test_chat_tables_exist(db):
    with db.cursor() as cur:
        cur.execute(
            """select table_name from information_schema.tables
               where table_schema = 'public' and table_name = any(%s)""",
            (list(CHAT_TABLES),),
        )
        found = {r[0] for r in cur.fetchall()}
    assert found == set(CHAT_TABLES)


@pytest.mark.parametrize("table", CHAT_TABLES)
def test_four_policies_and_forced_rls(db, table):
    with db.cursor() as cur:
        cur.execute(
            "select count(*) from pg_policies where schemaname='public' and tablename=%s",
            (table,),
        )
        assert cur.fetchone()[0] == 4, f"{table} should have 4 RLS policies"
        cur.execute(
            "select relforcerowsecurity from pg_class where relname=%s and relnamespace='public'::regnamespace",
            (table,),
        )
        assert cur.fetchone()[0] is True, f"{table} must force row level security"


def test_message_seq_and_role_constraints(db):
    with db.cursor() as cur:
        # role check constraint present
        cur.execute(
            """select 1 from information_schema.check_constraints
               where constraint_schema='public' and check_clause ilike '%role%'
                 and check_clause ilike '%user%' and check_clause ilike '%assistant%'"""
        )
        assert cur.fetchone() is not None, "role check constraint missing"
        # seq is a generated identity column
        cur.execute(
            """select is_identity from information_schema.columns
               where table_schema='public' and table_name='chat_messages' and column_name='seq'"""
        )
        assert cur.fetchone()[0] == "YES"


def test_cross_tenant_thread_invisibility(client_tenant_a, client_tenant_b, demo_tenant_id):
    """A thread created by tenant A is invisible to tenant B, and B cannot forge one."""
    created = (
        client_tenant_a.table("chat_threads")
        .insert({"tenant_id": demo_tenant_id, "title": "tenant-a private thread"})
        .execute()
    )
    thread_id = created.data[0]["id"]
    try:
        b_ids = {
            r["id"]
            for r in client_tenant_b.table("chat_threads").select("id").execute().data
        }
        assert thread_id not in b_ids

        with pytest.raises(APIError):
            client_tenant_b.table("chat_threads").insert(
                {"tenant_id": demo_tenant_id, "title": "cross-tenant forge"}
            ).execute()
    finally:
        client_tenant_a.table("chat_threads").delete().eq("id", thread_id).execute()
