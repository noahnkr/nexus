-- Lead stage set: one-to-one with the WelcomeHome funnel (v1.1.2). ENTITY SEAM —
-- the stage vocabulary is vertical content, so this file lives beside the other
-- `entities_*` migrations and no core table is touched.
--
-- The old five-value set collapsed two WelcomeHome stages into `contacted` and
-- two more into `qualified`, so a lead's position in Nexus was coarser than what
-- the office sees in their CRM. The new seven-value set mirrors it exactly:
--   new -> contact_attempted -> contacted -> visit_scheduled -> visit_completed
--   -> converted, plus terminal `lost`.
--
-- `qualified` rows remap to `visit_scheduled` — the conservative half of the old
-- bucket, since nothing local records whether the visit actually happened. The
-- corrective WelcomeHome re-sweep that follows this migration sets WH-sourced
-- leads precisely. Drop -> remap -> re-add is safely re-runnable.

alter table public.leads drop constraint if exists leads_status_check;

update public.leads set status = 'visit_scheduled' where status = 'qualified';

alter table public.leads add constraint leads_status_check
  check (status in ('new','contact_attempted','contacted','visit_scheduled',
                    'visit_completed','converted','lost'));
