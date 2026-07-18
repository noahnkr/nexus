-- entity_summaries (WS7): a per-entity cache for the on-demand AI smart summary, so
-- a profile open no longer regenerates every time — the first open generates + caches,
-- later opens serve the cached row instantly, and a manual Regenerate refreshes it.
-- Core and business-agnostic (keyed by entity_type/entity_id, no vertical concept),
-- so M10's caregiver profiles reuse it. Idempotent.

create table if not exists public.entity_summaries (
  tenant_id    uuid not null references public.tenants(id),
  entity_type  text not null,
  entity_id    uuid not null,
  summary      text not null,
  model        text,
  generated_at timestamptz not null default now(),
  primary key (tenant_id, entity_type, entity_id)
);

-- RLS: standard four-policy tenant isolation, identical to the other core tables.
alter table public.entity_summaries enable row level security;
alter table public.entity_summaries force row level security;
create policy entity_summaries_select on public.entity_summaries
  for select to authenticated, anon using (tenant_id = app.current_tenant_id());
create policy entity_summaries_insert on public.entity_summaries
  for insert to authenticated, anon with check (tenant_id = app.current_tenant_id());
create policy entity_summaries_update on public.entity_summaries
  for update to authenticated, anon
  using (tenant_id = app.current_tenant_id())
  with check (tenant_id = app.current_tenant_id());
create policy entity_summaries_delete on public.entity_summaries
  for delete to authenticated, anon using (tenant_id = app.current_tenant_id());
