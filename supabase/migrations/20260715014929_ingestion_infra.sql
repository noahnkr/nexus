-- Ingestion infrastructure: a private Storage bucket for original uploads, and
-- the Realtime publication for the documents table so the frontend can observe
-- status transitions (uploaded -> processing -> ready/failed) live.

insert into storage.buckets (id, name, public)
values ('documents', 'documents', false)
on conflict (id) do nothing;

-- Add documents to the Realtime publication. Guarded so a re-run is a no-op.
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'documents'
  ) then
    alter publication supabase_realtime add table public.documents;
  end if;
end
$$;
