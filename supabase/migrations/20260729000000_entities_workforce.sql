-- Workforce & compliance (Module 18). THIS IS THE RE-TEMPLATING SEAM (like
-- entities_senior_care.sql / entities_scheduling.sql / entities_referral_partners.sql):
-- a new vertical replaces only its entity files; core tables never change. Additive +
-- idempotent so a re-run (or a run against an already-seeded DB) is a no-op.
--
-- Two things land here:
--
--   1. `resources.status` — an active/inactive flag. An inactive caregiver is
--      EXCLUDED from matching candidates and from the schedule board's roster; they
--      exist only on the Roster tab and in history (their past visits are untouched).
--      This is a lifecycle flag, not a delete: home-care staff leave and come back.
--
--   2. `resource_credentials` — DATED evidence layered over the undated
--      `qualifications` vocabulary. `resources.qualification_ids` stays the matching
--      input ("has this skill"); a credential row adds "…and it was issued on X and
--      expires on Y". Only credentials that actually expire (CPR, TB test, license)
--      need a row; a background check with no renewal date is a null `expires_at`.
--      Expiry STATUS is derived at read time from `expires_at` (services/views/
--      workforce.py, EXPIRING_DAYS) — never stored, never written by a detector loop,
--      the same rule as the M16 EVV flags.

-- ---------------------------------------------------------------------------
-- resources.status: 'active' (default) | 'inactive'.
-- ---------------------------------------------------------------------------
alter table public.resources
  add column if not exists status text not null default 'active';

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'resources_status_check'
  ) then
    alter table public.resources
      add constraint resources_status_check check (status in ('active','inactive'));
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- resource_credentials: one row per (caregiver, qualification). The unique key is
-- what makes "add CPR twice" a 409 instead of a silent duplicate. Cascade on the
-- resource FK — a deleted caregiver's credentials are meaningless on their own;
-- the qualification FK does NOT cascade (the vocabulary outlives any one holder).
-- ---------------------------------------------------------------------------
create table if not exists public.resource_credentials (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id),
  resource_id      uuid not null references public.resources(id) on delete cascade,
  qualification_id uuid not null references public.qualifications(id),
  issued_at        date,
  expires_at       date,          -- null = does not expire
  notes            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (tenant_id, resource_id, qualification_id)
);

create index if not exists resource_credentials_resource_idx
  on public.resource_credentials (tenant_id, resource_id);

do $$
begin
  if not exists (
    select 1 from pg_trigger where tgname = 'resource_credentials_set_updated_at'
  ) then
    create trigger resource_credentials_set_updated_at
      before update on public.resource_credentials
      for each row execute function app.set_updated_at();
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- RLS: standard four-policy tenant isolation, identical to the other tables.
-- ---------------------------------------------------------------------------
alter table public.resource_credentials enable row level security;
alter table public.resource_credentials force row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public'
      and tablename = 'resource_credentials' and policyname = 'resource_credentials_select'
  ) then
    create policy resource_credentials_select on public.resource_credentials
      for select to authenticated, anon using (tenant_id = app.current_tenant_id());
    create policy resource_credentials_insert on public.resource_credentials
      for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
    create policy resource_credentials_update on public.resource_credentials
      for update to authenticated, anon
      using (tenant_id = app.current_tenant_id())
      with check (tenant_id = app.current_tenant_id());
    create policy resource_credentials_delete on public.resource_credentials
      for delete to authenticated, anon using (tenant_id = app.current_tenant_id());
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Realtime: the Roster tab tails credential + resource changes live (adding a
-- credential in the drawer moves the compliance strip without a refresh). RLS
-- scopes it per tenant. Guarded so a re-run is a no-op (the M4/M5/M10/M12/M17
-- precedent).
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  -- `resources` joins the publication here too: the Roster tab's status/utilization
  -- rows change on a PATCH, not only on a credential write.
  foreach t in array array['resource_credentials', 'resources'] loop
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime'
        and schemaname = 'public'
        and tablename = t
    ) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end
$$;
