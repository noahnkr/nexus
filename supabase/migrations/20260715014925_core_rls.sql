-- Row-Level Security for core tables. Tenant isolation at the Postgres level:
-- every policy gates on tenant_id = app.current_tenant_id().
--
-- Connection-role note for later modules: the FastAPI backend must connect as a
-- NON-bypass role (the `authenticated` role via PostgREST, or a dedicated app
-- role) and `SET LOCAL request.app.tenant_id` per request. The service-role key
-- bypasses RLS and is reserved for migrations/ops only.

-- tenants: a session sees only its own row. No writes from app roles.
alter table public.tenants enable row level security;
alter table public.tenants force row level security;

create policy tenants_select on public.tenants
  for select to authenticated, anon
  using (id = app.current_tenant_id());

-- Standard four-policy set for a tenant-scoped, mutable core table.
-- external_ids
alter table public.external_ids enable row level security;
alter table public.external_ids force row level security;
create policy external_ids_select on public.external_ids
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy external_ids_insert on public.external_ids
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy external_ids_update on public.external_ids
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy external_ids_delete on public.external_ids
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- documents
alter table public.documents enable row level security;
alter table public.documents force row level security;
create policy documents_select on public.documents
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy documents_insert on public.documents
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy documents_update on public.documents
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy documents_delete on public.documents
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- document_chunks
alter table public.document_chunks enable row level security;
alter table public.document_chunks force row level security;
create policy document_chunks_select on public.document_chunks
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy document_chunks_insert on public.document_chunks
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy document_chunks_update on public.document_chunks
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy document_chunks_delete on public.document_chunks
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- tasks
alter table public.tasks enable row level security;
alter table public.tasks force row level security;
create policy tasks_select on public.tasks
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy tasks_insert on public.tasks
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy tasks_update on public.tasks
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy tasks_delete on public.tasks
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- pending_actions
alter table public.pending_actions enable row level security;
alter table public.pending_actions force row level security;
create policy pending_actions_select on public.pending_actions
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy pending_actions_insert on public.pending_actions
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy pending_actions_update on public.pending_actions
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy pending_actions_delete on public.pending_actions
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- events: SELECT + INSERT only. No UPDATE/DELETE policies exist, so those are
-- denied under RLS; the forbid_mutation trigger is the second, owner-proof lock.
alter table public.events enable row level security;
alter table public.events force row level security;
create policy events_select on public.events
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy events_insert on public.events
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
