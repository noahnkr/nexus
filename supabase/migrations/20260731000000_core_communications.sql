-- CORE migration (v1.1.0): the Communications knowledge tier.
--
-- Messages, calls, and emails get their OWN store, kept deliberately separate
-- from `documents` (the curated file corpus) so a high-volume, low-value stream
-- never pollutes it. Two principles, both enforced here:
--   * STORE-ALL, EMBED-SELECTIVELY. Every message is stored and timeline-linked;
--     only long-form correspondence is chunked into `communication_chunks` (its
--     own index). `communications.embedded` records which were embedded.
--   * EVENT-AS-SPINE. A communication links to its originating `events` row via
--     `source_event_id`; `content_hash` deduplicates the same message arriving
--     from two sources.
--
-- Business-agnostic on purpose (like `documents`): `entity_type`/`entity_id`
-- reference whatever the deployment's canonical entity map declares — core never
-- interprets the values. Additive + idempotent so a re-run is a no-op.

-- ---------------------------------------------------------------------------
-- communications: the message/call/email store. Timeline-linked always,
-- embedded selectively. `occurred_at` is the source time (when it happened),
-- distinct from `created_at` (when we ingested it).
-- ---------------------------------------------------------------------------
create table if not exists public.communications (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references public.tenants(id),
  channel         text not null check (channel in ('call','email','sms','note','other')),
  direction       text check (direction in ('inbound','outbound')),      -- null = unknown
  occurred_at     timestamptz not null,
  subject         text,
  body            text not null,
  entity_type     text,
  entity_id       uuid,
  source          text not null,                                          -- 'welcomehome' | 'goto' | 'gmail' | 'manual' | ...
  external_id     text,                                                   -- connector id, for idempotency
  content_hash    text,                                                   -- cross-source dedup key
  source_event_id uuid references public.events(id),                     -- event-as-spine link
  embedded        boolean not null default false,                        -- store != embed
  metadata        jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
-- Idempotent re-sync: one row per (source, connector id).
create unique index if not exists communications_source_external_idx
  on public.communications (tenant_id, source, external_id)
  where external_id is not null;
create index if not exists communications_entity_idx
  on public.communications (tenant_id, entity_type, entity_id);
create index if not exists communications_occurred_idx
  on public.communications (tenant_id, occurred_at desc);
create index if not exists communications_hash_idx
  on public.communications (tenant_id, content_hash);

drop trigger if exists communications_set_updated_at on public.communications;
create trigger communications_set_updated_at
  before update on public.communications
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- communication_chunks: the SELECTIVELY-embedded retrieval unit. Mirrors
-- document_chunks (its own HNSW index). Only long-form comms get rows here;
-- short messages (an SMS) are stored in `communications` but never chunked.
-- ---------------------------------------------------------------------------
create table if not exists public.communication_chunks (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id),
  communication_id uuid not null references public.communications(id) on delete cascade,
  chunk_index      int not null,
  chunk_text       text not null,
  embedding        vector(1024),
  entity_type      text,
  entity_id        uuid,
  source           text,
  metadata         jsonb not null default '{}',
  created_at       timestamptz not null default now(),
  unique (communication_id, chunk_index)
);
create index if not exists communication_chunks_tenant_idx
  on public.communication_chunks (tenant_id);
create index if not exists communication_chunks_entity_idx
  on public.communication_chunks (tenant_id, entity_type, entity_id);
create index if not exists communication_chunks_embedding_idx
  on public.communication_chunks using hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- RLS: standard four-policy tenant isolation, copied verbatim from the
-- documents pattern in core_rls.sql. Explicit grants (nexus_app inherits via
-- authenticated).
-- ---------------------------------------------------------------------------
alter table public.communications enable row level security;
alter table public.communications force row level security;
drop policy if exists communications_select on public.communications;
create policy communications_select on public.communications
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
drop policy if exists communications_insert on public.communications;
create policy communications_insert on public.communications
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
drop policy if exists communications_update on public.communications;
create policy communications_update on public.communications
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
drop policy if exists communications_delete on public.communications;
create policy communications_delete on public.communications
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

alter table public.communication_chunks enable row level security;
alter table public.communication_chunks force row level security;
drop policy if exists communication_chunks_select on public.communication_chunks;
create policy communication_chunks_select on public.communication_chunks
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
drop policy if exists communication_chunks_insert on public.communication_chunks;
create policy communication_chunks_insert on public.communication_chunks
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
drop policy if exists communication_chunks_update on public.communication_chunks;
create policy communication_chunks_update on public.communication_chunks
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
drop policy if exists communication_chunks_delete on public.communication_chunks;
create policy communication_chunks_delete on public.communication_chunks
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());

grant select, insert, update, delete on public.communications to authenticated, anon;
grant select, insert, update, delete on public.communication_chunks to authenticated, anon;

-- ---------------------------------------------------------------------------
-- entity_summaries: add a `kind` discriminator so a comm profile coexists with
-- the smart summary for one entity. Existing rows default to 'smart_summary'.
-- Guarded PK swap so a re-run is a no-op.
-- ---------------------------------------------------------------------------
alter table public.entity_summaries
  add column if not exists kind text not null default 'smart_summary';

do $$
begin
  if exists (
    select 1 from pg_constraint
     where conname = 'entity_summaries_pkey'
       and array_length(conkey, 1) = 3        -- the old 3-column PK
  ) then
    alter table public.entity_summaries drop constraint entity_summaries_pkey;
    alter table public.entity_summaries
      add constraint entity_summaries_pkey
      primary key (tenant_id, entity_type, entity_id, kind);
  end if;
end $$;
