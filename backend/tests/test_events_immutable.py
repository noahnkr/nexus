"""The events table is append-only, proven two independent ways:
  * via PostgREST: no UPDATE/DELETE policy exists, so those affect zero rows.
  * via direct psycopg: the forbid_mutation trigger raises even for an owner/
    bypass role (the RLS-independent lock).
"""
import psycopg
import pytest

from conftest import set_tenant

EVENT_ID = "88888888-0000-0000-0000-000000000005"


def _bypasses_rls(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select bool_or(rolbypassrls or rolsuper) "
            "from pg_roles where rolname = current_user"
        )
        return bool(cur.fetchone()[0])


# --- PostgREST path: no policy -> no-op, never an error ---------------------

def test_rest_update_event_is_noop(client_tenant_a):
    before = client_tenant_a.table("events").select("payload").eq("id", EVENT_ID).execute().data
    assert before, "seed event missing — apply supabase/seed.sql"

    res = client_tenant_a.table("events").update({"event_type": "tampered"}).eq(
        "id", EVENT_ID
    ).execute()
    assert res.data == []  # no UPDATE policy -> zero rows touched

    after = client_tenant_a.table("events").select("event_type").eq("id", EVENT_ID).execute().data
    assert after[0]["event_type"] != "tampered"


def test_rest_delete_event_is_noop(client_tenant_a):
    res = client_tenant_a.table("events").delete().eq("id", EVENT_ID).execute()
    assert res.data == []  # no DELETE policy -> zero rows removed
    still = client_tenant_a.table("events").select("id").eq("id", EVENT_ID).execute().data
    assert len(still) == 1


# --- psycopg path: trigger is the owner-proof lock -------------------------

def test_db_update_event_blocked(db, demo_tenant_id):
    set_tenant(db, demo_tenant_id)
    eid = "88888888-0000-0000-0000-000000000001"
    with db.cursor() as cur:
        if _bypasses_rls(db):
            # Row reaches the trigger; it must raise.
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute("update public.events set event_type = 'x' where id = %s", (eid,))
        else:
            # Non-bypass role: no UPDATE policy means the row is filtered out first.
            cur.execute("update public.events set event_type = 'x' where id = %s", (eid,))
            assert cur.rowcount == 0


def test_db_delete_event_blocked(db, demo_tenant_id):
    set_tenant(db, demo_tenant_id)
    eid = "88888888-0000-0000-0000-000000000001"
    with db.cursor() as cur:
        if _bypasses_rls(db):
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute("delete from public.events where id = %s", (eid,))
        else:
            cur.execute("delete from public.events where id = %s", (eid,))
            assert cur.rowcount == 0
