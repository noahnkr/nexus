-- Idempotent seed data (fixed UUIDs + ON CONFLICT DO NOTHING).
-- Safe to apply repeatedly. Two tenants: the demo tenant with a full data set,
-- and a probe tenant with a single lead+task so RLS tests prove cross-tenant
-- invisibility against real rows.

-- ===========================================================================
-- Tenants
-- ===========================================================================
insert into public.tenants (id, name) values
  ('00000000-0000-0000-0000-000000000001', 'Nexus Demo Care Co.'),
  ('00000000-0000-0000-0000-000000000002', 'RLS Probe Tenant')
on conflict do nothing;

-- ===========================================================================
-- Demo tenant (00000000-…-0001)
-- ===========================================================================

-- Regions
insert into public.regions (id, tenant_id, name, zip_codes) values
  ('11111111-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'North County', '{92008,92009,92010,92011}'),
  ('11111111-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Central',      '{92101,92102,92103,92104}'),
  ('11111111-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'South Bay',     '{91910,91911,91913,91915}')
on conflict do nothing;

-- Qualifications
insert into public.qualifications (id, tenant_id, name, description) values
  ('22222222-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CNA',                   'Certified Nursing Assistant'),
  ('22222222-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'HHA',                   'Home Health Aide'),
  ('22222222-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'Dementia Care',         'Specialized dementia / Alzheimer''s training'),
  ('22222222-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'Hoyer Lift Certified',  'Trained on mechanical patient transfer lifts'),
  ('22222222-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', 'Medication Management', 'Medication reminders and administration')
on conflict do nothing;

-- Leads (all five statuses represented)
insert into public.leads (id, tenant_id, name, phone, email, source, status, region_id, requirements) values
  ('33333333-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Margaret Ellison', '+16195550101', 'margaret.e@example.com', 'website',   'new',       '11111111-0000-0000-0000-000000000001', '{"hours_per_week": 20, "needed_qualifications": ["CNA"], "notes": "Morning visits preferred"}'),
  ('33333333-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Harold Byrne',     '+16195550102', 'hbyrne@example.com',      'referral',  'contacted', '11111111-0000-0000-0000-000000000002', '{"hours_per_week": 15, "needed_qualifications": ["HHA"]}'),
  ('33333333-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'Doris Nakamura',   '+16195550103', 'doris.n@example.com',     'phone',     'qualified', '11111111-0000-0000-0000-000000000003', '{"hours_per_week": 30, "needed_qualifications": ["Dementia Care","Medication Management"]}'),
  ('33333333-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'Walter Grimes',    '+16195550104', 'wgrimes@example.com',     'website',   'converted', '11111111-0000-0000-0000-000000000001', '{"hours_per_week": 40, "needed_qualifications": ["CNA","Hoyer Lift Certified"]}'),
  ('33333333-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', 'Estelle Ferraro',  '+16195550105', 'estelle.f@example.com',   'referral',  'converted', '11111111-0000-0000-0000-000000000002', '{"hours_per_week": 25, "needed_qualifications": ["HHA","Medication Management"]}'),
  ('33333333-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000001', 'Raymond Cho',      '+16195550106', 'rcho@example.com',        'website',   'lost',      '11111111-0000-0000-0000-000000000003', '{"hours_per_week": 10}')
on conflict do nothing;

-- Clients (two converted from leads, one standalone)
insert into public.clients (id, tenant_id, lead_id, name, phone, email, status, requirements) values
  ('44444444-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', '33333333-0000-0000-0000-000000000004', 'Walter Grimes',   '+16195550104', 'wgrimes@example.com',   'active', '{"hours_per_week": 40, "needed_qualifications": ["CNA","Hoyer Lift Certified"]}'),
  ('44444444-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', '33333333-0000-0000-0000-000000000005', 'Estelle Ferraro', '+16195550105', 'estelle.f@example.com', 'active', '{"hours_per_week": 25, "needed_qualifications": ["HHA","Medication Management"]}'),
  ('44444444-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', null,                                    'Frank Delgado',   '+16195550107', 'fdelgado@example.com',  'paused', '{"hours_per_week": 12, "needed_qualifications": ["HHA"]}')
on conflict do nothing;

-- Resources (caregivers) with overlapping regions/qualifications
insert into public.resources (id, tenant_id, name, phone, email, qualification_ids, region_ids, availability) values
  ('55555555-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Alicia Moreno', '+16195550201', 'alicia.m@example.com',
     '{22222222-0000-0000-0000-000000000001,22222222-0000-0000-0000-000000000004}',
     '{11111111-0000-0000-0000-000000000001,11111111-0000-0000-0000-000000000002}',
     '{"mon":["08:00-16:00"],"tue":["08:00-16:00"],"wed":["08:00-16:00"]}'),
  ('55555555-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Brian Okafor',  '+16195550202', 'brian.o@example.com',
     '{22222222-0000-0000-0000-000000000002,22222222-0000-0000-0000-000000000005}',
     '{11111111-0000-0000-0000-000000000002,11111111-0000-0000-0000-000000000003}',
     '{"thu":["09:00-17:00"],"fri":["09:00-17:00"],"sat":["10:00-14:00"]}'),
  ('55555555-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'Carmen Ruiz',   '+16195550203', 'carmen.r@example.com',
     '{22222222-0000-0000-0000-000000000001,22222222-0000-0000-0000-000000000003,22222222-0000-0000-0000-000000000005}',
     '{11111111-0000-0000-0000-000000000001}',
     '{"mon":["12:00-20:00"],"wed":["12:00-20:00"],"fri":["12:00-20:00"]}'),
  ('55555555-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'Derek Hsu',     '+16195550204', 'derek.h@example.com',
     '{22222222-0000-0000-0000-000000000002,22222222-0000-0000-0000-000000000004}',
     '{11111111-0000-0000-0000-000000000003}',
     '{"tue":["07:00-15:00"],"thu":["07:00-15:00"]}'),
  ('55555555-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', 'Evelyn Park',   '+16195550205', 'evelyn.p@example.com',
     '{22222222-0000-0000-0000-000000000003,22222222-0000-0000-0000-000000000005}',
     '{11111111-0000-0000-0000-000000000002,11111111-0000-0000-0000-000000000003}',
     '{"sat":["08:00-18:00"],"sun":["08:00-18:00"]}')
on conflict do nothing;

-- Schedules (past + upcoming, mixed statuses)
insert into public.schedules (id, tenant_id, resource_id, client_id, start_time, end_time, status) values
  ('66666666-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000001', '44444444-0000-0000-0000-000000000001', now() - interval '7 days'  + interval '8 hours', now() - interval '7 days'  + interval '12 hours', 'completed'),
  ('66666666-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000001', '44444444-0000-0000-0000-000000000001', now() - interval '5 days'  + interval '8 hours', now() - interval '5 days'  + interval '12 hours', 'completed'),
  ('66666666-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000002', '44444444-0000-0000-0000-000000000002', now() - interval '3 days'  + interval '9 hours', now() - interval '3 days'  + interval '14 hours', 'completed'),
  ('66666666-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000004', '44444444-0000-0000-0000-000000000002', now() - interval '2 days'  + interval '7 hours', now() - interval '2 days'  + interval '11 hours', 'no_show'),
  ('66666666-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000003', '44444444-0000-0000-0000-000000000001', now() - interval '1 days'  + interval '12 hours', now() - interval '1 days' + interval '16 hours', 'cancelled'),
  ('66666666-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000001', '44444444-0000-0000-0000-000000000001', now() + interval '1 days'  + interval '8 hours', now() + interval '1 days'  + interval '12 hours', 'scheduled'),
  ('66666666-0000-0000-0000-000000000007', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000002', '44444444-0000-0000-0000-000000000002', now() + interval '2 days'  + interval '9 hours', now() + interval '2 days'  + interval '14 hours', 'scheduled'),
  ('66666666-0000-0000-0000-000000000008', '00000000-0000-0000-0000-000000000001', '55555555-0000-0000-0000-000000000005', '44444444-0000-0000-0000-000000000003', now() + interval '4 days'  + interval '8 hours', now() + interval '4 days'  + interval '18 hours', 'scheduled')
-- Schedule times are relative to now(): refresh them on re-seed so the demo
-- always has past (completed/cancelled) and upcoming (scheduled) visits, rather
-- than freezing at first-seed time and drifting into the past.
on conflict (id) do update set
  start_time = excluded.start_time,
  end_time   = excluded.end_time,
  status     = excluded.status;

-- external_ids (two leads + one client mapped to fake CRM ids)
insert into public.external_ids (id, tenant_id, entity_type, entity_id, source_system, external_id, last_synced_at) values
  ('77777777-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'lead',   '33333333-0000-0000-0000-000000000001', 'crm', 'CRM-LEAD-1001', now() - interval '1 days'),
  ('77777777-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'lead',   '33333333-0000-0000-0000-000000000003', 'crm', 'CRM-LEAD-1003', now() - interval '2 days'),
  ('77777777-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'client', '44444444-0000-0000-0000-000000000001', 'crm', 'CRM-CLIENT-2001', now() - interval '6 hours')
on conflict do nothing;

-- events (plain-language jsonb payloads)
insert into public.events (id, tenant_id, source_system, event_type, entity_type, entity_id, payload) values
  ('88888888-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'crm',    'lead.created',      'lead',   '33333333-0000-0000-0000-000000000001', '{"summary": "New website inquiry from Margaret Ellison"}'),
  ('88888888-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'phone',  'call.received',     'lead',   '33333333-0000-0000-0000-000000000003', '{"summary": "Inbound call from Doris Nakamura", "duration_seconds": 340}'),
  ('88888888-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'crm',    'lead.converted',    'client', '44444444-0000-0000-0000-000000000001', '{"summary": "Walter Grimes converted to active client"}'),
  ('88888888-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'manual', 'schedule.no_show',  'client', '44444444-0000-0000-0000-000000000002', '{"summary": "Estelle Ferraro visit marked no-show"}'),
  ('88888888-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', 'email',  'message.received',  'lead',   '33333333-0000-0000-0000-000000000002', '{"summary": "Harold Byrne replied to intake email"}')
on conflict do nothing;

-- tasks (one linked to an event)
insert into public.tasks (id, tenant_id, title, description, status, priority, originating_event_id, assigned_to, due_at) values
  ('99999999-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Follow up on no-show visit', 'Estelle Ferraro''s caregiver was marked no-show; confirm coverage and reschedule.', 'pending', 'high', '88888888-0000-0000-0000-000000000004', 'coordinator', now() + interval '1 days'),
  ('99999999-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Qualify new lead Margaret Ellison', 'Reach out to Margaret Ellison to complete intake.', 'pending', 'normal', null, 'coordinator', now() + interval '2 days')
on conflict do nothing;

-- pending_actions (one pending approval, so Module 5's gate UI has real data)
insert into public.pending_actions (id, tenant_id, task_id, tool_name, tool_input, status) values
  ('aaaaaaaa-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', '99999999-0000-0000-0000-000000000001', 'send_sms',
     '{"to": "+16195550105", "body": "Hi Estelle, we noticed today''s visit was missed. Can we reschedule for tomorrow morning?"}', 'pending')
on conflict do nothing;

-- Applicants (caregiver-recruiting pipeline, Module 10) — spread across stages so
-- the funnel/metrics have shape. quals/regions reuse the seeded reference ids;
-- one carries availability (copied verbatim on hire). Idempotent update-in-place
-- so re-seeds refresh stage/fields without duplicating.
insert into public.applicants (id, tenant_id, name, phone, email, source, stage, qualification_ids, region_ids, availability, notes) values
  ('dddddddd-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Nadia Owens',     '+16195550301', 'nadia.o@example.com',  'indeed',    'applied',    '{22222222-0000-0000-0000-000000000002}', '{11111111-0000-0000-0000-000000000001}', '{}', 'Applied via Indeed; HHA certified.'),
  ('dddddddd-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Marcus Bell',     '+16195550302', 'marcus.b@example.com', 'referral',  'screening',  '{22222222-0000-0000-0000-000000000001,22222222-0000-0000-0000-000000000005}', '{11111111-0000-0000-0000-000000000002}', '{}', 'Referred by Brian Okafor.'),
  ('dddddddd-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'Priya Raman',     '+16195550303', 'priya.r@example.com',  'website',   'interview',  '{22222222-0000-0000-0000-000000000001,22222222-0000-0000-0000-000000000003}', '{11111111-0000-0000-0000-000000000001,11111111-0000-0000-0000-000000000003}', '{"mon":["08:00-16:00"],"wed":["08:00-16:00"]}', 'Strong dementia-care background.'),
  ('dddddddd-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'Terrence Wolfe',  '+16195550304', 'terrence.w@example.com', 'indeed',  'offer',      '{22222222-0000-0000-0000-000000000002,22222222-0000-0000-0000-000000000004}', '{11111111-0000-0000-0000-000000000003}', '{"tue":["07:00-15:00"],"thu":["07:00-15:00"]}', 'Offer extended; awaiting response.'),
  ('dddddddd-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', 'Grace Lin',       '+16195550305', 'grace.l@example.com',  'referral',  'rejected',   '{22222222-0000-0000-0000-000000000002}', '{11111111-0000-0000-0000-000000000002}', '{}', 'Not enough availability for our needs.')
on conflict (id) do update set
  stage             = excluded.stage,
  qualification_ids = excluded.qualification_ids,
  region_ids        = excluded.region_ids,
  availability      = excluded.availability,
  notes             = excluded.notes;

-- ===========================================================================
-- Probe tenant (00000000-…-0002) — minimal, for cross-tenant RLS tests
-- ===========================================================================
insert into public.leads (id, tenant_id, name, phone, email, source, status, requirements) values
  ('bbbbbbbb-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', 'Probe Lead', '+15625550000', 'probe.lead@example.com', 'manual', 'new', '{}')
on conflict do nothing;

insert into public.tasks (id, tenant_id, title, description, status, priority) values
  ('bbbbbbbb-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000002', 'Probe Task', 'Exists only to test tenant isolation.', 'pending', 'normal')
on conflict do nothing;
