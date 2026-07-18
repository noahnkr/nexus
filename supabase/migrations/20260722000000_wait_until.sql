-- wait_until step (WS5): a run can now park waiting for a future event, not just a
-- timed delay. Two additions to automation_runs, both core/business-agnostic:
--   * a new terminal-of-waiting status `waiting_event` (the run is parked until a
--     matching event arrives, or its optional timeout deadline in `wake_at` passes);
--   * an `awaiting jsonb` holding the event pattern to match ({event_type, conditions}).
-- Idempotent — safe to re-run.

-- Recreate the status check to admit 'waiting_event' (inline check on create table
-- is named automation_runs_status_check).
alter table public.automation_runs
  drop constraint if exists automation_runs_status_check;
alter table public.automation_runs
  add constraint automation_runs_status_check
  check (status in ('running','waiting','waiting_approval','waiting_event',
                    'completed','failed','cancelled'));

alter table public.automation_runs
  add column if not exists awaiting jsonb;

-- The dispatcher resumes waiting_event runs on a matching event; keep those lookups
-- (by tenant + status + entity) cheap.
create index if not exists automation_runs_waiting_event_idx
  on public.automation_runs (tenant_id, status, entity_type, entity_id)
  where status = 'waiting_event';
