-- Automation binding (Module 9b): a generic "this automation belongs to surface X,
-- slot Y" tag. CORE and business-agnostic — the column and its uniqueness are the
-- mechanism; the meaning (which view, which stage) stays in the vertical seam. M10
-- binds {"view":"caregivers","stage":…} with zero further schema work.
--
-- binding shape (validated in the API, not the DB): a jsonb object that always
-- carries a "view" key; pipeline sequences add "stage". null = unbound (default).
-- Idempotent — safe to re-run.

alter table public.automations
  add column if not exists binding jsonb;

-- One sequence per (tenant, view, stage). Partial: unbound rows (binding is null)
-- are unconstrained. Nulls in the expression keys are distinct, so a view-only
-- binding (no stage) is likewise unconstrained — only fully-specified (view+stage)
-- pipeline bindings collide, which is exactly the one-sequence-per-stage rule.
create unique index if not exists automations_binding_uniq
  on public.automations (tenant_id, (binding->>'view'), (binding->>'stage'))
  where binding is not null;
