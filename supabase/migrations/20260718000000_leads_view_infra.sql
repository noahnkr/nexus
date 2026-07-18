-- Leads View (Module 9a): add public.leads to the Realtime publication so the
-- /leads directory can tail inserts/updates live (a signed webhook creating a
-- lead, or another coordinator working the pipeline, appears without a refresh).
-- The RLS SELECT policy already scopes leads per tenant, and Realtime respects it
-- via the tenant JWT the frontend fetches. Guarded so a re-run is a no-op (same
-- pattern as eventlog_realtime / automations_infra).
--
-- Vertical migration: `leads` is a re-templating-seam table, so this lives apart
-- from any core change. No other schema change — stages stay in leads.status.

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'leads'
  ) then
    alter publication supabase_realtime add table public.leads;
  end if;
end
$$;
