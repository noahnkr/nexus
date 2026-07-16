"""Proves the RLS-subject backend path: the `nexus_app` role (nobypassrls) sees
zero rows without the tenant GUC, sees only its tenant's rows with it, and cannot
insert rows stamped for another tenant. This is what closes the postgres-BYPASSRLS
hole — the backend must connect as nexus_app, never as postgres.

Skipped until the one-time ops step sets nexus_app's password + NEXUS_APP_DB_URL.
"""
import uuid

import psycopg
import pytest

from conftest import set_tenant


def test_nexus_app_is_not_bypassrls(app_db):
    with app_db.cursor() as cur:
        cur.execute("select rolbypassrls from pg_roles where rolname = current_user")
        assert cur.fetchone()[0] is False
        cur.execute("select current_user")
        assert cur.fetchone()[0] == "nexus_app"


def test_no_rows_without_guc(app_db):
    """With no request.app.tenant_id set, app.current_tenant_id() is NULL -> deny."""
    with app_db.cursor() as cur:
        cur.execute("select count(*) from public.documents")
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from public.chat_threads")
        assert cur.fetchone()[0] == 0


def test_rows_visible_with_guc(app_db, demo_tenant_id):
    set_tenant(app_db, demo_tenant_id)
    with app_db.cursor() as cur:
        cur.execute(
            """insert into public.chat_threads (tenant_id, title)
               values (%s, 'app-role-visible') returning id""",
            (demo_tenant_id,),
        )
        tid = cur.fetchone()[0]
        cur.execute("select count(*) from public.chat_threads where id = %s", (tid,))
        assert cur.fetchone()[0] == 1
    app_db.rollback()


def test_cross_tenant_insert_rejected(app_db, demo_tenant_id, probe_tenant_id):
    """GUC = demo tenant, but INSERT stamps the probe tenant -> WITH CHECK denies."""
    set_tenant(app_db, demo_tenant_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with app_db.cursor() as cur:
            cur.execute(
                """insert into public.chat_threads (tenant_id, title)
                   values (%s, 'cross-tenant')""",
                (probe_tenant_id,),
            )
    app_db.rollback()


def test_cross_tenant_rows_invisible(app_db, demo_tenant_id, probe_tenant_id):
    set_tenant(app_db, demo_tenant_id)
    with app_db.cursor() as cur:
        cur.execute(
            "select count(*) from public.chat_threads where tenant_id = %s",
            (probe_tenant_id,),
        )
        assert cur.fetchone()[0] == 0
