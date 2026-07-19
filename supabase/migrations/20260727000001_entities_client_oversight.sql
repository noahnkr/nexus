-- Client & care oversight (Module 16a). THIS IS THE RE-TEMPLATING SEAM (like
-- entities_senior_care.sql / entities_scheduling.sql): a new vertical replaces
-- only its entity files; core tables never change. Additive + idempotent so a
-- re-run (or a run against an already-seeded DB) is a no-op.
--
-- Three things the clients surface needs and the M0 shape could not carry:
--   * OVERSIGHT FIELDS — payer, authorized hours/week, region, care summary. The
--     authorized-hours number is the census denominator: authorized minus
--     delivered is direct revenue leakage.
--   * FAMILY CONTACTS — home care is coordinated with a daughter/son/POA, not
--     only the client. Vertical by nature (senior-care family contacts), so it
--     lives here rather than in a core table.
--   * EVV CLOCK DATA — check_in_at / check_out_at on schedules. Electronic Visit
--     Verification is legally mandated for Medicaid-funded home care in most
--     states. In-app EVV-lite this module (recorded from the visit drawer and
--     gated agent tools); connector-fed clock-ins (telephony, WellSky) land in
--     these same columns via Module 14's ingest path. Late/missed are computed at
--     READ time from these stamps — no stored flag, no detector loop.
--
-- Statuses also change meaning here: active/paused/ended was a generic lifecycle;
-- home care runs on active / hospital_hold / discharged. The data migration runs
-- BEFORE the new CHECK so existing rows survive the rename.

-- ---------------------------------------------------------------------------
-- clients: oversight fields.
-- ---------------------------------------------------------------------------
alter table public.clients
  add column if not exists region_id                 uuid references public.regions(id),
  add column if not exists payer                     text,
  add column if not exists authorized_hours_per_week numeric(5,1),
  add column if not exists care_summary              text;

-- Nullable = unknown payer (an intake in progress), so no NOT NULL / default.
alter table public.clients drop constraint if exists clients_payer_check;
alter table public.clients
  add constraint clients_payer_check
  check (payer is null or payer in ('private_pay','medicaid','ltc_insurance','va','other'));

alter table public.clients drop constraint if exists clients_authorized_hours_check;
alter table public.clients
  add constraint clients_authorized_hours_check
  check (authorized_hours_per_week is null or authorized_hours_per_week >= 0);

-- Status rename. Order matters in BOTH directions: the OLD check forbids the new
-- values, so it comes off first; the NEW check forbids the old values, so it goes
-- on last — with the data migration in between, where no constraint objects to it.
-- 'paused' was how a hospital stay was recorded; 'ended' was a discharge.
alter table public.clients drop constraint if exists clients_status_check;

update public.clients set status = 'hospital_hold' where status = 'paused';
update public.clients set status = 'discharged'    where status = 'ended';

alter table public.clients
  add constraint clients_status_check
  check (status in ('active','hospital_hold','discharged'));

-- Census groups active clients by region; the directory filters on payer.
create index if not exists clients_region_idx on public.clients (tenant_id, region_id);

-- ---------------------------------------------------------------------------
-- client_contacts: family / POA contacts for one client.
-- ---------------------------------------------------------------------------
create table if not exists public.client_contacts (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id),
  client_id    uuid not null references public.clients(id) on delete cascade,
  name         text not null,
  relationship text,
  phone        text,
  email        text,
  is_primary   boolean not null default false,
  notes        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists client_contacts_client_idx
  on public.client_contacts (tenant_id, client_id);

do $$
begin
  if not exists (
    select 1 from pg_trigger where tgname = 'client_contacts_set_updated_at'
  ) then
    create trigger client_contacts_set_updated_at
      before update on public.client_contacts
      for each row execute function app.set_updated_at();
  end if;
end
$$;

-- Standard four-policy tenant isolation (the entity-migration pattern).
do $$
declare
  t text := 'client_contacts';
begin
  execute format('alter table public.%I enable row level security;', t);
  execute format('alter table public.%I force row level security;', t);
  if not exists (select 1 from pg_policies
                 where schemaname = 'public' and tablename = t
                   and policyname = t || '_select') then
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
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- schedules: EVV clock stamps.
-- ---------------------------------------------------------------------------
alter table public.schedules
  add column if not exists check_in_at  timestamptz,
  add column if not exists check_out_at timestamptz;

-- Coherence: you cannot clock out of a visit you never clocked into, and a visit
-- cannot end before it started. Delivered-hours math depends on both holding.
alter table public.schedules drop constraint if exists schedules_checkout_needs_checkin;
alter table public.schedules
  add constraint schedules_checkout_needs_checkin
  check (check_out_at is null or check_in_at is not null);

alter table public.schedules drop constraint if exists schedules_checkout_after_checkin;
alter table public.schedules
  add constraint schedules_checkout_after_checkin
  check (check_out_at is null or check_out_at > check_in_at);

-- ---------------------------------------------------------------------------
-- Realtime: the clients directory + profile tail status changes and contact
-- edits live. RLS scopes the stream per tenant. Guarded so a re-run is a no-op
-- (the M4/M5/M10/M12 precedent).
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array['clients','client_contacts']
  loop
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t
    ) then
      execute format('alter publication supabase_realtime add table public.%I;', t);
    end if;
  end loop;
end
$$;
