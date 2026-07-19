-- CORE migration (Module 16a): canonical-entity tagging on the parent `documents`
-- table. `document_chunks` has carried entity_type/entity_id since M0; the parent
-- did not, so "this client's documents" was unqueryable without scanning chunks.
--
-- Business-agnostic on purpose: like `events.entity_type/entity_id`, this is a
-- reference to whatever the deployment's canonical entity map declares — core
-- never interprets the values. Nullable: an untagged upload stays tenant-general
-- (the existing behavior), and a tagged one associates the document with one
-- record (a client's care plan, a caregiver's certification).
--
-- No RLS change: the existing four-policy tenant isolation on `documents` covers
-- these columns. Additive + idempotent so a re-run is a no-op.

alter table public.documents
  add column if not exists entity_type text,
  add column if not exists entity_id   uuid;

-- The one query this exists for: a profile page listing its entity's documents.
create index if not exists documents_entity_idx
  on public.documents (tenant_id, entity_type, entity_id);
