-- Automations infrastructure (Module 7a). Core, business-agnostic: the two tables
-- the WHEN/IF/THEN engine runs on, plus the pending_actions link that lets 7b's
-- approval hook find the run a gated step parked. Both tables are core (they never
-- reference a vertical concept), so they live in a core migration and join the
-- CLAUDE.md core-tables list. Idempotent — safe to re-run.

-- ---------------------------------------------------------------------------
-- automations: the recipe. `trigger`/`conditions`/`steps` are validated,
-- declarative JSON (Pydantic `validate_recipe` is the writer's gate — the DB
-- stores the already-validated shape). `status` gates whether new runs start;
-- `next_fire_at` is cron bookkeeping maintained by 7b (null until then).
-- ---------------------------------------------------------------------------
create table if not exists public.automations (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id),
  name         text not null,
  description  text,
  status       text not null default 'paused'
                 check (status in ('active','paused')),
  trigger      jsonb not null,
  conditions   jsonb not null default '[]',
  steps        jsonb not null default '[]',
  next_fire_at timestamptz,
  created_by   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists automations_tenant_status_idx
  on public.automations (tenant_id, status);
-- Cron scheduler claim path (7b): due active cron rows.
create index if not exists automations_next_fire_idx
  on public.automations (tenant_id, status, next_fire_at);

create trigger automations_set_updated_at
  before update on public.automations
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- automation_runs: one execution of a recipe. Durable across waits — the engine
-- commits `context`/`step_index` after each step (one tx per step), so a crash
-- resumes at the next step without replaying side effects. `step_log` is the
-- per-step plain-language trail M8's run-detail timeline renders.
--   status: running -> completed/failed, or parked at waiting (delay) /
--           waiting_approval (gated tool) / cancelled (rejected approval).
--   wake_at: when a `waiting` run is due (7b's waker).
-- ---------------------------------------------------------------------------
create table if not exists public.automation_runs (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references public.tenants(id),
  automation_id   uuid not null references public.automations(id) on delete cascade,
  status          text not null default 'running'
                    check (status in ('running','waiting','waiting_approval',
                                      'completed','failed','cancelled')),
  trigger_event_id uuid references public.events(id),
  entity_type     text,
  entity_id       uuid,
  context         jsonb not null default '{}',
  step_index      int not null default 0,
  step_log        jsonb not null default '[]',
  wake_at         timestamptz,
  error           text,
  finished_at     timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists automation_runs_automation_idx
  on public.automation_runs (tenant_id, automation_id, created_at desc);
-- Waker claim path (7b): due `waiting` runs.
create index if not exists automation_runs_wake_idx
  on public.automation_runs (tenant_id, status, wake_at);

-- Concurrency guard (user-locked): one active run per (automation, entity).
-- A second trigger while one is in flight hits this unique violation and is
-- skipped with an `automation.run_skipped` event. Entity-less runs (cron) are
-- unconstrained (the partial predicate excludes null entity_id).
create unique index if not exists automation_runs_one_active_per_entity
  on public.automation_runs (tenant_id, automation_id, entity_type, entity_id)
  where status in ('running','waiting','waiting_approval') and entity_id is not null;

create trigger automation_runs_set_updated_at
  before update on public.automation_runs
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- pending_actions.automation_run_id: how 7b's approval hook finds the paused run
-- a gated automation step queued. Null for chat/MCP-queued actions.
-- ---------------------------------------------------------------------------
alter table public.pending_actions
  add column if not exists automation_run_id uuid references public.automation_runs(id);

-- ---------------------------------------------------------------------------
-- RLS: standard four-policy tenant isolation, identical to the other core tables.
-- ---------------------------------------------------------------------------
alter table public.automations enable row level security;
alter table public.automations force row level security;
create policy automations_select on public.automations
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy automations_insert on public.automations
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy automations_update on public.automations
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy automations_delete on public.automations
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

alter table public.automation_runs enable row level security;
alter table public.automation_runs force row level security;
create policy automation_runs_select on public.automation_runs
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy automation_runs_insert on public.automation_runs
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy automation_runs_update on public.automation_runs
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy automation_runs_delete on public.automation_runs
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- ---------------------------------------------------------------------------
-- Realtime publication: M8's grid live-updates for free. Guarded so a re-run is
-- a no-op (same pattern as eventlog_realtime / task_approval_infra).
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'automations'
  ) then
    alter publication supabase_realtime add table public.automations;
  end if;
end
$$;

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'automation_runs'
  ) then
    alter publication supabase_realtime add table public.automation_runs;
  end if;
end
$$;
