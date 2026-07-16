-- Connector infrastructure (Module 3b). Core, business-agnostic: the durable
-- cursor store real connector adapters need, plus the 'calendar' category for
-- external_ids. The adapters themselves are application code, not schema.

-- ---------------------------------------------------------------------------
-- connector_state: durable per-connector cursors / renewal watermarks
-- (Gmail historyId, Google Calendar syncToken, GoTo notification-channel ids +
-- expiries, etc.). One row per (tenant, source_system). Placeholder adapters
-- don't read it yet — it exists so real adapters have their seam.
-- ---------------------------------------------------------------------------
create table if not exists public.connector_state (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id),
  source_system text not null,
  state         jsonb not null default '{}',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (tenant_id, source_system)
);
create index if not exists connector_state_tenant_idx
  on public.connector_state (tenant_id);

create trigger connector_state_set_updated_at
  before update on public.connector_state
  for each row execute function app.set_updated_at();

-- Standard four-policy tenant isolation (identical to the other core tables).
alter table public.connector_state enable row level security;
alter table public.connector_state force row level security;
create policy connector_state_select on public.connector_state
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy connector_state_insert on public.connector_state
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy connector_state_update on public.connector_state
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy connector_state_delete on public.connector_state
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- ---------------------------------------------------------------------------
-- external_ids: add 'calendar' to the source_system category CHECK. Drop and
-- re-add the auto-named constraint; existing rows are untouched (all use the
-- prior categories, which remain valid).
-- ---------------------------------------------------------------------------
alter table public.external_ids
  drop constraint if exists external_ids_source_system_check;
alter table public.external_ids
  add constraint external_ids_source_system_check
  check (source_system in ('crm','phone','ehr','email','calendar','manual'));
