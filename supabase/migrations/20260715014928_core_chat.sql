-- Chat persistence (core; identical across deployments). Threads + messages,
-- tenant-scoped with the standard four-policy RLS copied from the documents
-- pattern in core_rls.sql. Message content is the Anthropic content-block array
-- stored verbatim (forward-compatible with Module 2 tool_use/tool_result blocks,
-- which live inside user/assistant messages).

create table if not exists public.chat_threads (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references public.tenants(id),
  title      text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists chat_threads_tenant_updated_idx
  on public.chat_threads (tenant_id, updated_at desc);

create trigger chat_threads_set_updated_at
  before update on public.chat_threads
  for each row execute function app.set_updated_at();

create table if not exists public.chat_messages (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references public.tenants(id),
  thread_id  uuid not null references public.chat_threads(id) on delete cascade,
  seq        bigint generated always as identity,
  role       text not null check (role in ('user','assistant')),
  content    jsonb not null,
  citations  jsonb not null default '[]',
  metadata   jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists chat_messages_thread_seq_idx
  on public.chat_messages (thread_id, seq);

-- ---------------------------------------------------------------------------
-- RLS: four-policy tenant isolation, copied exactly from the documents pattern.
-- ---------------------------------------------------------------------------
alter table public.chat_threads enable row level security;
alter table public.chat_threads force row level security;
create policy chat_threads_select on public.chat_threads
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy chat_threads_insert on public.chat_threads
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy chat_threads_update on public.chat_threads
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy chat_threads_delete on public.chat_threads
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

alter table public.chat_messages enable row level security;
alter table public.chat_messages force row level security;
create policy chat_messages_select on public.chat_messages
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy chat_messages_insert on public.chat_messages
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy chat_messages_update on public.chat_messages
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy chat_messages_delete on public.chat_messages
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

-- Explicit table privileges (nexus_app inherits these via authenticated).
grant select, insert, update, delete on public.chat_threads to authenticated, anon;
grant select, insert, update, delete on public.chat_messages to authenticated, anon;
