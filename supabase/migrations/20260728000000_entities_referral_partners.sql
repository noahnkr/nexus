-- Referral-source enrichment (Module 17). THIS IS THE RE-TEMPLATING SEAM (like
-- entities_senior_care.sql / entities_scheduling.sql): a new vertical replaces only
-- its entity files; core tables never change. Additive + idempotent so a re-run (or
-- a run against an already-seeded DB) is a no-op.
--
-- A referral SOURCE is just free text on `leads.source` (written by manual create,
-- webhooks, and the future WelcomeHome sync). This table ENRICHES a source by name:
-- a tracked partner (a hospital, a senior-living community, a discharge planner)
-- carries a category and contact details, joined to leads by EXACT source-name
-- match. No lead schema change, no FK, no backfill — an unmatched source keeps
-- working untouched and can be promoted to a tracked partner in one click.

-- ---------------------------------------------------------------------------
-- referral_partners: name unique per tenant (the join key). `category` nullable
-- = an untyped partner. Contact/notes are the relationship detail the owner keeps.
-- ---------------------------------------------------------------------------
create table if not exists public.referral_partners (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id),
  name         text not null,
  category     text check (category in
                 ('hospital','senior_living','discharge_planner','home_health','community','other')),
  contact_name text,
  phone        text,
  email        text,
  notes        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (tenant_id, name)
);

create trigger referral_partners_set_updated_at
  before update on public.referral_partners
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- RLS: standard four-policy tenant isolation, identical to the other tables.
-- ---------------------------------------------------------------------------
alter table public.referral_partners enable row level security;
alter table public.referral_partners force row level security;
create policy referral_partners_select on public.referral_partners
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy referral_partners_insert on public.referral_partners
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy referral_partners_update on public.referral_partners
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy referral_partners_delete on public.referral_partners
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- ---------------------------------------------------------------------------
-- Realtime: the Referrals page tails partner inserts/updates/deletes live (a
-- Track / edit / delete appears without a refresh). RLS scopes it per tenant.
-- Guarded so a re-run is a no-op (the M4/M5/M10/M12 precedent).
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'referral_partners'
  ) then
    alter publication supabase_realtime add table public.referral_partners;
  end if;
end
$$;
