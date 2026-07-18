-- Caregivers View (Module 10a): the applicants entity — the caregiver-recruiting
-- pipeline. THIS IS THE RE-TEMPLATING SEAM (like entities_senior_care.sql): a new
-- vertical replaces only its entity files; core tables never change.
--
-- Applicants don't exist in the M0 schema at all — `resources` is the active
-- caregiver roster with no stage column. This migration adds the hiring pipeline
-- table plus a promotion link on `resources` (the clients.lead_id precedent): when
-- an applicant is moved to `hired`, a `resources` row is created carrying
-- `applicant_id` provenance. Stages live in `applicants.stage` (no stage table).

-- ---------------------------------------------------------------------------
-- applicants: caregiver-recruiting pipeline. quals/regions/availability mirror
-- `resources` so promotion copies them verbatim onto the caregiver row.
-- ---------------------------------------------------------------------------
create table if not exists public.applicants (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants(id),
  name              text not null,
  phone             text,
  email             text,
  source            text,
  stage             text not null default 'applied'
                      check (stage in ('applied','screening','interview','offer','hired','rejected')),
  qualification_ids uuid[] not null default '{}',
  region_ids        uuid[] not null default '{}',
  availability      jsonb not null default '{}',
  notes             text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists applicants_tenant_stage_idx on public.applicants (tenant_id, stage);
create trigger applicants_set_updated_at
  before update on public.applicants
  for each row execute function app.set_updated_at();

-- Promotion provenance: the caregiver row a hired applicant became (nullable —
-- roster rows created before this module, or by other paths, carry no applicant).
alter table public.resources
  add column if not exists applicant_id uuid references public.applicants(id);

-- ---------------------------------------------------------------------------
-- RLS for applicants (lives here so a vertical swap is one file). Same standard
-- four-policy set as the core + other entity tables.
-- ---------------------------------------------------------------------------
do $$
begin
  execute 'alter table public.applicants enable row level security';
  execute 'alter table public.applicants force row level security';
  execute $f$create policy applicants_select on public.applicants
    for select to authenticated, anon using (tenant_id = app.current_tenant_id());$f$;
  execute $f$create policy applicants_insert on public.applicants
    for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());$f$;
  execute $f$create policy applicants_update on public.applicants
    for update to authenticated, anon
    using (tenant_id = app.current_tenant_id())
    with check (tenant_id = app.current_tenant_id());$f$;
  execute $f$create policy applicants_delete on public.applicants
    for delete to authenticated, anon using (tenant_id = app.current_tenant_id());$f$;
end$$;

-- ---------------------------------------------------------------------------
-- Realtime: the /caregivers directory tails inserts/updates live (a coordinator
-- working the pipeline, or a hire promotion, appears without a refresh). RLS
-- scopes it per tenant. Guarded so a re-run is a no-op (leads_view_infra pattern).
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'applicants'
  ) then
    alter publication supabase_realtime add table public.applicants;
  end if;
end
$$;
