-- Dedicated login role for the FastAPI backend.
--
-- The SUPABASE_DB_URL role (postgres) has BYPASSRLS, so it must never be used
-- for tenant data access. nexus_app is RLS-subject (nobypassrls) and gains the
-- existing "to authenticated, anon" policies via membership in the authenticated
-- role. The backend runs `select set_config('request.app.tenant_id', ..., true)`
-- per transaction; without it, app.current_tenant_id() returns NULL and every
-- policy denies (fail closed).
--
-- The password is NOT set here (never committed). One-time ops step, via psql
-- connected as postgres:
--   alter role nexus_app with password '<strong-password>';
-- then set NEXUS_APP_DB_URL in .env to the Session Pooler URI with username
-- nexus_app.<project-ref>.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'nexus_app') then
    create role nexus_app login nobypassrls;
  end if;
end
$$;

-- Idempotent: ensure attributes are correct even if the role pre-existed.
alter role nexus_app with login nobypassrls;

grant authenticated to nexus_app;
grant usage on schema public, app to nexus_app;

comment on role nexus_app is
  'FastAPI backend login role. RLS-subject (nobypassrls); inherits tenant '
  'policies via authenticated membership. Sets request.app.tenant_id GUC per '
  'transaction.';
