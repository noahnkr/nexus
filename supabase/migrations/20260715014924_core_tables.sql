-- Core tables: identical across deployments. Per-vertical entity tables live in
-- entities_*.sql. Every table is tenant-scoped; RLS is applied in core_rls.sql.

-- ---------------------------------------------------------------------------
-- external_ids: entity resolution. Every inbound connector event resolves to a
-- canonical entity here before anything else is written. entity_id is
-- intentionally NOT a foreign key — it points into per-vertical tables that
-- vary by deployment; integrity is application-enforced by design.
-- ---------------------------------------------------------------------------
create table if not exists public.external_ids (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references public.tenants(id),
  entity_type    text not null,
  entity_id      uuid not null,
  source_system  text not null check (source_system in ('crm','phone','ehr','email','manual')),
  external_id    text not null,
  last_synced_at timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (tenant_id, source_system, external_id)
);
create index if not exists external_ids_entity_idx
  on public.external_ids (tenant_id, entity_type, entity_id);

create trigger external_ids_set_updated_at
  before update on public.external_ids
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- documents: parent of document_chunks. Ingestion (Module 1) fills this.
-- ---------------------------------------------------------------------------
create table if not exists public.documents (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id),
  filename     text not null,
  mime_type    text,
  storage_path text,
  status       text not null default 'uploaded'
                 check (status in ('uploaded','processing','ready','failed')),
  error        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists documents_tenant_idx on public.documents (tenant_id);

create trigger documents_set_updated_at
  before update on public.documents
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- document_chunks: retrieval unit. Embedding is nullable (chunks exist before
-- embedding completes). Chunks may be tagged to a canonical entity.
-- ---------------------------------------------------------------------------
create table if not exists public.document_chunks (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id),
  document_id   uuid not null references public.documents(id) on delete cascade,
  chunk_index   int not null,
  chunk_text    text not null,
  embedding     vector(1024),
  entity_type   text,
  entity_id     uuid,
  source_system text,
  metadata      jsonb not null default '{}',
  created_at    timestamptz not null default now(),
  unique (document_id, chunk_index)
);
create index if not exists document_chunks_tenant_idx on public.document_chunks (tenant_id);
create index if not exists document_chunks_entity_idx
  on public.document_chunks (tenant_id, entity_type, entity_id);
create index if not exists document_chunks_embedding_idx
  on public.document_chunks using hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- events: the immutable audit trail. Every tool call, webhook, and gated-action
-- resolution writes a row here. No updated_at. Append-only, enforced by trigger
-- (and by the absence of UPDATE/DELETE RLS policies).
-- ---------------------------------------------------------------------------
create table if not exists public.events (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id),
  source_system text not null,
  event_type    text not null,
  entity_type   text,
  entity_id     uuid,
  payload       jsonb not null default '{}',
  created_at    timestamptz not null default now()
);
create index if not exists events_tenant_created_idx
  on public.events (tenant_id, created_at desc);
create index if not exists events_entity_idx
  on public.events (tenant_id, entity_type, entity_id);

create trigger events_forbid_mutation
  before update or delete on public.events
  for each row execute function app.forbid_mutation();

-- ---------------------------------------------------------------------------
-- tasks: anything needing a human decision.
-- ---------------------------------------------------------------------------
create table if not exists public.tasks (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants(id),
  title               text not null,
  description         text,
  status              text not null default 'pending'
                        check (status in ('pending','in_progress','done','cancelled')),
  priority            text not null default 'normal'
                        check (priority in ('low','normal','high','urgent')),
  originating_event_id uuid references public.events(id),
  assigned_to         text,
  due_at              timestamptz,
  resolved_at         timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create index if not exists tasks_tenant_status_idx on public.tasks (tenant_id, status);

create trigger tasks_set_updated_at
  before update on public.tasks
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- pending_actions: the approval gate. State-changing tool calls write here
-- instead of executing, until a human approves.
-- ---------------------------------------------------------------------------
create table if not exists public.pending_actions (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id),
  task_id     uuid not null references public.tasks(id),
  tool_name   text not null,
  tool_input  jsonb not null,
  status      text not null default 'pending'
                check (status in ('pending','approved','rejected','executed','failed')),
  resolved_at timestamptz,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists pending_actions_tenant_status_idx
  on public.pending_actions (tenant_id, status);

create trigger pending_actions_set_updated_at
  before update on public.pending_actions
  for each row execute function app.set_updated_at();
