-- tenant_settings (M15b): user-facing workspace + agent preferences, one jsonb row
-- per tenant. CORE and business-agnostic — no vertical concept appears here, and a
-- new deployment gets its row lazily on first write.
--
-- This is NOT a home for infra config or credentials (CLAUDE.md): env vars remain
-- the only place those live. What lands here is what an office user can change
-- about their own workspace — its name, and how the assistant should sound.
-- Keeping it as a single jsonb column means new preference keys never need a
-- migration; the whitelist and per-key validation live in services/settings.py.
-- Idempotent.

create table if not exists public.tenant_settings (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null unique references public.tenants(id) on delete cascade,
  settings   jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop trigger if exists tenant_settings_set_updated_at on public.tenant_settings;
create trigger tenant_settings_set_updated_at
  before update on public.tenant_settings
  for each row execute function app.set_updated_at();

-- RLS: standard four-policy tenant isolation, identical to the other core tables.
alter table public.tenant_settings enable row level security;
alter table public.tenant_settings force row level security;

drop policy if exists tenant_settings_select on public.tenant_settings;
create policy tenant_settings_select on public.tenant_settings
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());

drop policy if exists tenant_settings_insert on public.tenant_settings;
create policy tenant_settings_insert on public.tenant_settings
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());

drop policy if exists tenant_settings_update on public.tenant_settings;
create policy tenant_settings_update on public.tenant_settings
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());

drop policy if exists tenant_settings_delete on public.tenant_settings;
create policy tenant_settings_delete on public.tenant_settings
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());
