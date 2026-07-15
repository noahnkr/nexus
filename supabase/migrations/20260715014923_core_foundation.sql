-- Core foundation: extensions, tenants, tenant-resolution helper, shared triggers.
-- This file is identical across deployments (no per-vertical content).

create extension if not exists vector;

create schema if not exists app;
grant usage on schema app to authenticated, anon, service_role;

-- ---------------------------------------------------------------------------
-- tenants: the top-level isolation boundary. Not tenant-scoped by column;
-- a session may only see its own row (RLS added in core_rls.sql).
-- ---------------------------------------------------------------------------
create table if not exists public.tenants (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- app.current_tenant_id(): the single source of truth every RLS policy uses.
-- Resolution order:
--   1. JWT claim app_metadata.tenant_id  (Supabase client / Realtime access)
--   2. GUC request.app.tenant_id         (FastAPI backend: SET LOCAL per request)
--   3. NULL                              (policies then deny — fail closed)
-- auth.jwt() is wrapped so this also works over a direct psycopg connection,
-- where no request context exists.
-- ---------------------------------------------------------------------------
create or replace function app.current_tenant_id()
returns uuid
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  claim_tenant text;
  guc_tenant   text;
begin
  begin
    claim_tenant := auth.jwt() -> 'app_metadata' ->> 'tenant_id';
  exception when others then
    claim_tenant := null;
  end;

  if claim_tenant is not null and claim_tenant <> '' then
    return claim_tenant::uuid;
  end if;

  guc_tenant := nullif(current_setting('request.app.tenant_id', true), '');
  if guc_tenant is not null then
    return guc_tenant::uuid;
  end if;

  return null;
end;
$$;

grant execute on function app.current_tenant_id() to authenticated, anon, service_role;

-- ---------------------------------------------------------------------------
-- app.set_updated_at(): shared BEFORE UPDATE trigger to maintain updated_at.
-- ---------------------------------------------------------------------------
create or replace function app.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- app.forbid_mutation(): BEFORE UPDATE OR DELETE trigger used by the immutable
-- events table. Blocks even privileged/owner roles from mutating rows.
-- ---------------------------------------------------------------------------
create or replace function app.forbid_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception '% on % is not permitted: table is append-only',
    TG_OP, TG_TABLE_NAME;
end;
$$;
