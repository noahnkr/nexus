-- Senior-care entity instantiation. THIS IS THE RE-TEMPLATING SEAM: a new
-- vertical replaces only this file (tables + their RLS). The core tables,
-- helpers, and RLS in the other migrations never change.

-- ---------------------------------------------------------------------------
-- regions: service areas.
-- ---------------------------------------------------------------------------
create table if not exists public.regions (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references public.tenants(id),
  name       text not null,
  zip_codes  text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists regions_tenant_idx on public.regions (tenant_id);
create trigger regions_set_updated_at
  before update on public.regions
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- qualifications: caregiver skills/certifications.
-- ---------------------------------------------------------------------------
create table if not exists public.qualifications (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id),
  name        text not null,
  description text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (tenant_id, name)
);
create trigger qualifications_set_updated_at
  before update on public.qualifications
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- leads: prospective clients.
-- ---------------------------------------------------------------------------
create table if not exists public.leads (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id),
  name         text not null,
  phone        text,
  email        text,
  source       text,
  status       text not null default 'new'
                 check (status in ('new','contacted','qualified','converted','lost')),
  region_id    uuid references public.regions(id),
  requirements jsonb not null default '{}',
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists leads_tenant_status_idx on public.leads (tenant_id, status);
create trigger leads_set_updated_at
  before update on public.leads
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- clients: active care recipients.
-- ---------------------------------------------------------------------------
create table if not exists public.clients (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id),
  lead_id      uuid references public.leads(id),
  name         text not null,
  phone        text,
  email        text,
  status       text not null default 'active'
                 check (status in ('active','paused','ended')),
  requirements jsonb not null default '{}',
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists clients_tenant_status_idx on public.clients (tenant_id, status);
create trigger clients_set_updated_at
  before update on public.clients
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- resources: caregivers. qualification_ids / region_ids are uuid[] rather than
-- join tables — deliberate for this small-scale vertical. availability is an
-- inline weekday->time-range jsonb (the PRD's availability_ref, realized inline
-- this phase).
-- ---------------------------------------------------------------------------
create table if not exists public.resources (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id),
  name             text not null,
  phone            text,
  email            text,
  qualification_ids uuid[] not null default '{}',
  region_ids       uuid[] not null default '{}',
  availability     jsonb not null default '{}',
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists resources_tenant_idx on public.resources (tenant_id);
create trigger resources_set_updated_at
  before update on public.resources
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- schedules: caregiver<->client assignments over a time window.
-- ---------------------------------------------------------------------------
create table if not exists public.schedules (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id),
  resource_id uuid not null references public.resources(id),
  client_id   uuid not null references public.clients(id),
  start_time  timestamptz not null,
  end_time    timestamptz not null,
  status      text not null default 'scheduled'
                check (status in ('scheduled','completed','cancelled','no_show')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  check (end_time > start_time)
);
create index if not exists schedules_tenant_start_idx on public.schedules (tenant_id, start_time);
create index if not exists schedules_resource_idx on public.schedules (tenant_id, resource_id);
create index if not exists schedules_client_idx on public.schedules (tenant_id, client_id);
create trigger schedules_set_updated_at
  before update on public.schedules
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- RLS for the six entity tables (lives here so a vertical swap is one file).
-- Same standard four-policy set as the core tables.
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array['regions','qualifications','leads','clients','resources','schedules']
  loop
    execute format('alter table public.%I enable row level security;', t);
    execute format('alter table public.%I force row level security;', t);
    execute format($f$create policy %I on public.%I
      for select to authenticated, anon using (tenant_id = app.current_tenant_id());$f$,
      t || '_select', t);
    execute format($f$create policy %I on public.%I
      for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());$f$,
      t || '_insert', t);
    execute format($f$create policy %I on public.%I
      for update to authenticated, anon
      using (tenant_id = app.current_tenant_id())
      with check (tenant_id = app.current_tenant_id());$f$,
      t || '_update', t);
    execute format($f$create policy %I on public.%I
      for delete to authenticated, anon using (tenant_id = app.current_tenant_id());$f$,
      t || '_delete', t);
  end loop;
end$$;
