-- Smart Staffing scheduling surgery (Module 12a). THIS IS THE RE-TEMPLATING SEAM
-- (like entities_senior_care.sql / entities_applicants.sql): a new vertical
-- replaces only its entity files; core tables never change. Additive + idempotent
-- so a re-run (or a run against an already-seeded DB) is a no-op.
--
-- Two shapes the one `schedules` table could not represent before:
--   * an OPEN shift — a visit nobody holds yet (resource_id null, status 'open'),
--   * a CALLED-OUT visit — a scheduled visit whose caregiver dropped, retained for
--     "who called out" while a linked open replacement is created.
-- A parallel shifts table would duplicate RLS, Realtime, events, and the board
-- query for zero modeling gain, so both live here.
--
-- Plus the matching-engine inputs: address / zip / languages on clients and
-- resources, client `preferences` (free tags: "female caregiver", "no dogs") and
-- the caregiver-side `traits` they match against.

-- ---------------------------------------------------------------------------
-- schedules: nullable resource_id + open/called_out statuses + coherence CHECKs.
-- ---------------------------------------------------------------------------
alter table public.schedules alter column resource_id drop not null;

alter table public.schedules
  add column if not exists required_qualification_ids uuid[] not null default '{}',
  add column if not exists replaces_schedule_id uuid references public.schedules(id),
  add column if not exists notes text;

-- Recreate the status CHECK with the two new statuses (the inline constraint from
-- the M0 migration is auto-named schedules_status_check). Two coherence CHECKs
-- tie resource presence to status: an open shift is unassigned; a held or finished
-- visit must have a caregiver. `cancelled` is deliberately unconstrained — a
-- cancelled row may have been either an open shift or an assigned visit.
alter table public.schedules drop constraint if exists schedules_status_check;
alter table public.schedules
  add constraint schedules_status_check
  check (status in ('open','scheduled','called_out','completed','cancelled','no_show'));

alter table public.schedules drop constraint if exists schedules_open_unassigned;
alter table public.schedules
  add constraint schedules_open_unassigned
  check (status <> 'open' or resource_id is null);

alter table public.schedules drop constraint if exists schedules_assigned_has_resource;
alter table public.schedules
  add constraint schedules_assigned_has_resource
  check (status not in ('scheduled','called_out','completed','no_show')
         or resource_id is not null);

-- The board's "unfilled" column + the matcher scan open shifts by start time.
create index if not exists schedules_open_idx
  on public.schedules (tenant_id, start_time) where status = 'open';
create index if not exists schedules_replaces_idx
  on public.schedules (replaces_schedule_id);

-- ---------------------------------------------------------------------------
-- clients + resources: geography, language, and matching tags.
--   clients.preferences — what the client wants ("female caregiver", "no dogs").
--   resources.traits     — the caregiver-side tags preferences match against.
-- ---------------------------------------------------------------------------
alter table public.clients
  add column if not exists address     text,
  add column if not exists zip         text,
  add column if not exists languages   text[] not null default '{}',
  add column if not exists preferences text[] not null default '{}';

alter table public.resources
  add column if not exists address   text,
  add column if not exists zip       text,
  add column if not exists languages text[] not null default '{}',
  add column if not exists traits    text[] not null default '{}';

-- ---------------------------------------------------------------------------
-- Realtime: the Schedule board (12b) tails visit inserts/updates live (a fill, a
-- call-out, an outcome appears without a refresh). RLS scopes it per tenant.
-- Guarded so a re-run is a no-op (the M4/M5/M10 precedent).
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'schedules'
  ) then
    alter publication supabase_realtime add table public.schedules;
  end if;
end
$$;
