"""Structural tests over a direct psycopg connection: tables exist, CHECK and FK
constraints bite, and the updated_at trigger fires."""
import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation

from conftest import set_tenant

CORE_TABLES = [
    "tenants",
    "external_ids",
    "documents",
    "document_chunks",
    "events",
    "tasks",
    "pending_actions",
]
ENTITY_TABLES = [
    "regions", "qualifications", "leads", "clients", "resources", "schedules",
    "referral_partners",
]


@pytest.mark.parametrize("table", CORE_TABLES + ENTITY_TABLES)
def test_table_exists(db, table):
    with db.cursor() as cur:
        cur.execute("select to_regclass(%s)", (f"public.{table}",))
        assert cur.fetchone()[0] is not None, f"{table} missing"


def test_task_status_check_rejects_bogus(db, demo_tenant_id):
    set_tenant(db, demo_tenant_id)
    with db.cursor() as cur:
        with pytest.raises(CheckViolation):
            cur.execute(
                "insert into public.tasks (tenant_id, title, status) values (%s, %s, %s)",
                (demo_tenant_id, "bad status", "bogus"),
            )


def test_schedule_time_check_rejects_inverted(db, demo_tenant_id):
    set_tenant(db, demo_tenant_id)
    with db.cursor() as cur:
        with pytest.raises(CheckViolation):
            cur.execute(
                """insert into public.schedules
                     (tenant_id, resource_id, client_id, start_time, end_time)
                   values (%s,
                           '55555555-0000-0000-0000-000000000001',
                           '44444444-0000-0000-0000-000000000001',
                           now(), now() - interval '1 hour')""",
                (demo_tenant_id,),
            )


def test_lead_bad_region_fk_rejected(db, demo_tenant_id):
    """FK enforcement — nonexistent region_id, with a valid (RLS-permitted) tenant."""
    set_tenant(db, demo_tenant_id)
    with db.cursor() as cur:
        with pytest.raises(ForeignKeyViolation):
            cur.execute(
                """insert into public.leads (tenant_id, name, region_id)
                   values (%s, %s, %s)""",
                (demo_tenant_id, "orphan lead", "00000000-0000-0000-0000-0000000000ee"),
            )


def test_updated_at_trigger_fires(db, demo_tenant_id):
    """Updating a seeded task bumps updated_at (seed ran in an earlier txn, so the
    trigger's now() is strictly newer)."""
    set_tenant(db, demo_tenant_id)
    task_id = "99999999-0000-0000-0000-000000000002"
    with db.cursor() as cur:
        cur.execute("select updated_at from public.tasks where id = %s", (task_id,))
        row = cur.fetchone()
        assert row is not None, "seed task missing — apply supabase/seed.sql"
        before = row[0]

        cur.execute(
            "update public.tasks set description = %s where id = %s",
            ("touched by trigger test", task_id),
        )
        cur.execute("select updated_at from public.tasks where id = %s", (task_id,))
        after = cur.fetchone()[0]

    assert after > before
