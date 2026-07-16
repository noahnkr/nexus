-- Module 5 (approval gate & tasks): the smallest schema delta the gate needs.
-- pending_actions gains three columns so the UI can render outcomes and who/what
-- queued a call without joining events; tasks + pending_actions join the Realtime
-- publication so the Tasks page live-updates. Everything else (both tables, RLS,
-- status vocabularies) was built in Module 0. Idempotent — safe to re-run.

-- ---------------------------------------------------------------------------
-- pending_actions columns
--   source_system : who queued it (chat / mcp / workflow) — mirrors events.
--   resolved_by   : free text until Module 6 auth supplies a real identity.
--   result        : execution outcome {summary, error?}, read directly by the UI.
-- ---------------------------------------------------------------------------
alter table public.pending_actions
  add column if not exists source_system text not null default 'chat';
alter table public.pending_actions
  add column if not exists resolved_by text;
alter table public.pending_actions
  add column if not exists result jsonb;

-- ---------------------------------------------------------------------------
-- Realtime publication: tail task/action changes live (CLAUDE.md — Realtime for
-- task/event queue updates). RLS still scopes rows per tenant via the frontend's
-- tenant JWT. Guarded so a re-run is a no-op (same pattern as eventlog_realtime).
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'tasks'
  ) then
    alter publication supabase_realtime add table public.tasks;
  end if;
end
$$;

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'pending_actions'
  ) then
    alter publication supabase_realtime add table public.pending_actions;
  end if;
end
$$;
