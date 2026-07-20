-- CRM sync schema additions (Module 18a). THIS IS THE RE-TEMPLATING SEAM (like
-- entities_senior_care.sql / entities_client_oversight.sql): a new vertical
-- replaces only its entity files; core tables never change. Additive +
-- idempotent so a re-run (or a run against an already-seeded DB) is a no-op.
--
-- Two things the WelcomeHome sync needs and the as-built shape could not carry:
--
--   * LEAD LOCATION + BACKGROUND — a CRM prospect arrives with the care
--     recipient's home address and a free-text story ("daughter called after a
--     fall; needs help four mornings a week"). `clients` and `resources` already
--     carry address/zip (M12); `leads` did not, so a converted lead had nowhere
--     to hand those values to its client row. Matching and territory questions
--     start at the lead, not at the client.
--
--   * RELATED PEOPLE — home care is sold to a daughter, a son, a POA. WelcomeHome
--     models these as `influencers` (family) and additional `residents` (a spouse
--     who is also a care recipient). The lead row holds the PRIMARY resident; the
--     rest need a queryable home. `lead_contacts` deliberately MIRRORS the M15
--     `client_contacts` shape column-for-column, so Start-of-Care promotion is a
--     straight copy rather than a translation.
--
-- No Realtime publication for lead_contacts: the lead profile's freshness comes
-- from its timeline events, and a contact edit always lands with one.

-- ---------------------------------------------------------------------------
-- leads: location + background.
-- ---------------------------------------------------------------------------
alter table public.leads
  add column if not exists address    text,
  add column if not exists zip        text,
  add column if not exists background text;

-- ---------------------------------------------------------------------------
-- lead_contacts: family / decision-maker contacts for one lead.
-- Shape mirrors public.client_contacts exactly (plus `source`, which records
-- which external system the row came from) so promotion copies straight across.
-- ---------------------------------------------------------------------------
create table if not exists public.lead_contacts (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id),
  lead_id      uuid not null references public.leads(id) on delete cascade,
  name         text not null,
  relationship text,
  phone        text,
  email        text,
  is_primary   boolean not null default false,
  notes        text,
  source       text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists lead_contacts_lead_idx
  on public.lead_contacts (tenant_id, lead_id);

do $$
begin
  if not exists (
    select 1 from pg_trigger where tgname = 'lead_contacts_set_updated_at'
  ) then
    create trigger lead_contacts_set_updated_at
      before update on public.lead_contacts
      for each row execute function app.set_updated_at();
  end if;
end
$$;

-- Standard four-policy tenant isolation (the entity-migration pattern).
do $$
declare
  t text := 'lead_contacts';
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
