-- Event Log (Module 4): add public.events to the Realtime publication so the
-- frontend can tail new audit rows live. The RLS SELECT policy already scopes
-- events per tenant, and Realtime respects it via the tenant JWT the frontend
-- fetches. Guarded so a re-run is a no-op (same pattern as ingestion_infra).

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'events'
  ) then
    alter publication supabase_realtime add table public.events;
  end if;
end
$$;
