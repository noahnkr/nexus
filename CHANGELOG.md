# Changelog

All notable changes to the Nexus Control Center. The project moved from module-tracking to release versioning on 2026-07-20: each completed module became one minor release (**Module N → v0.N+1.0**), and the remaining connectors module ships as **v1.0.0** — the release where live external data flows end-to-end. Module numbers below use the final (2026-07-20) numbering; see `PRD.md` for each module's full specification. Newest first.

## [Unreleased] — v1.0.0 · Module 18: External Services Connectors

The four real integrations replacing the M3 placeholder adapters: WelcomeHome CRM, WellSky Personal Care, GoTo Connect, Gmail/Google Calendar. In-app connector sync loop + single `ingest_payload` seam; one-way inbound with gated outbound actions. Plans: `.agent/plans/18.external-connectors.md` (+ 18a–18d). Status: 18a mid-build (config + WH client written); 18b planned, blocked on WellSky credentials for live checks; 18c/18d planned, each with a one-time OAuth ops step.

Also in this release cycle (2026-07-20, unreleased):
- Module/versioning refactor: connectors renumbered 14 → 18 (last remaining work), Finishing Touches 15 → 14, Clients 16 → 15, Referrals 17 → 16, Workforce 18 → 17; implemented plan files retired; history transcribed into this changelog.
- Plan 18a revised against the as-built v0.15–v0.18 surfaces (Start-of-Care client promotion, single lead stage-writer, referral-source contract, M15-tag transcription ingestion).
- Plan 18b (WellSky) authored from the public Connect API spec, superseding the 2026-07-18 WellSky-direct deferral.

## v0.18.0 — 2026-07-19 · Module 17: Workforce & Compliance

- Workforce Roster tab on `/caregivers` (Pipeline | Roster): compliance strip (headcount, utilization, expiring/expired counts), roster table with utilization bars and per-credential status badges, credentials editor + active/inactive toggle in the shared caregiver drawer.
- `resource_credentials` table (dated rows per caregiver × qualification) + `resources.status`; credential status (valid / expiring ≤60d / expired) derived at read time — no stored state, no detector loop.
- Inactive caregivers excluded from matching and the schedule board; safe `list_expiring_credentials` tool; daily credential-digest recipe documented in the README and proven by an engine-run test.

## v0.17.0 — 2026-07-19 · Module 16: Referral-Source Dashboard

- `/referrals` dashboard: per-source conversion metrics, hours/week-won revenue proxy (via `clients.lead_id`), hand-rolled monthly trend bars, partner table with one-click Track promotion, partner drawer/dialog.
- `referral_partners` table as enrichment-by-name over free-text `leads.source` (exact match, no FK, no backfill); partner CRUD emits `referral_partner.*` events; chat answers referral questions through read-only `run_report`.

## v0.16.0 — 2026-07-19 · Module 15: Client & Care Oversight

- Clients became the fourth sanctioned vertical surface: `/clients` directory with census strip (authorized vs scheduled vs delivered hours, leakage in warning tone, payer/region breakdowns) and `/clients/{id}` care overview (smart summary, care/contacts/hours/caregivers/visits cards, entity timeline, tagged documents).
- Vertical migration: client oversight fields (payer, authorized hours, region, care summary), statuses → active/hospital_hold/discharged, `client_contacts` table, EVV clock columns on schedules with read-time late/missed flags (15-min grace, no detector loop).
- Core migration: `documents` entity tagging (`entity_type`/`entity_id`, chunks inherit) — care plans RAG-searchable per client.
- Seam `views/clients.py` (`change_status` single writer, deterministic census math); check-in/out via the schedule seam; tools `update_client_status` (seam-rewired), gated `record_visit_check_in/out`, safe `get_census`.

## v0.15.0 — 2026-07-19 · Module 14: Finishing Touches

- Chat: stop/cancel mid-stream with cancellation-safe persistence; document-style output guidance + GFM table rendering.
- Tasks: approve-with-edits (`ToolDef.editable_fields`, edits audited on `action.approved`), task detail drawer with clean labeled fields, event-log readability (type icons, plain labels, source accents).
- Shell: collapsible sidebar, mobile drawer nav + responsive core pages, `/settings` page (profile/workspace/appearance), Ingestion → Knowledge with per-tenant agent Instructions (`tenant_settings` core table + whitelist seam, injected as a second system block).
- Automations: safe `run_automation` tool (manual triggers only, deferred start), `formula` function (hand-rolled safe expression parser — no eval, no LLM) with token-aware `FormulaEditor`; `weighted_score` retired.

## v0.14.0 — 2026-07-19 · Module 13: Automation Builder Enhancements

- Hand-rolled shared `ui/Select` (groups, search, icons, dots, full listbox ARIA) replacing every native `<select>` app-wide; themed `TimePicker`.
- Entry IF gated on event triggers with confirm-and-clear; field-scope verify-and-fix (`lib/fields.ts` + regression suite, hint-bearing `FieldCombobox` that is never silently empty); plain-language event/operator labels.

## v0.13.0 — 2026-07-18 · Module 12: Smart Staffing & Scheduling

- `/schedule` week board (caregivers as rows, pinned Open-shifts row, visit drawer for every action) with repeat-weekly visit creation (≤12 weeks, server-expanded) and a roster-editing caregiver drawer.
- Shift model: nullable `resource_id` (open shifts), `called_out` status with linked replacement rows, required qualifications; client/caregiver address/zip/languages/traits fields.
- Deterministic matching engine (`views/matching.py`): zip geography, language/trait fit, availability, continuity, load balance — plain-language reasons/warnings, no LLM; safe `find_available_caregivers` + gated `record_call_out`/`assign_caregiver`; call-out → ranked candidates → assign → gated SMS notify flow.

## v0.12.0 — 2026-07-18 · Module 11: Automation Field Tokens

- Trigger-aware field catalog on the vocabulary endpoint (labeled trigger fields, per-event payload keys, per-entity fields from the seam, event→entity map); condition values template-rendered (unresolvable → false, never a crash); `days_until` function; draft agent taught the catalog.
- Builder: `{{path}}` references render as atomic labeled chips (`TokenText`) with a grouped searchable `FieldPicker`; recipe JSON format unchanged; read-mode surfaces labelized.

## v0.11.0 — 2026-07-17 · Module 10: Caregivers View & Hiring Process

- `applicants` entity end-to-end (stages applied → screening → interview → offer → hired, terminal rejected) with `/caregivers` directory, profiles, hiring smart summary, and metrics; `move_stage()` single writer; moving to hired atomically promotes onto the `resources` roster (`resource.created`).
- Hiring funnel strip + per-stage sequences (incl. rejected-stage denial email) via the shared view-config registry — no core changes.

## v0.10.0 — 2026-07-17 · Module 9: Leads View & Marketing Funnel

- `/leads` directory + profiles (inline edit, stage select, entity timeline, on-demand AI smart summary — never persisted) and funnel strip + conversion metrics.
- Per-stage outreach sequences as ordinary automations bound via core `automations.binding` jsonb (one per view/stage, partial unique index) with a constrained stage-sequence builder; every stage writer emits `lead.stage_changed`.

## v0.9.0 — 2026-07-17 · Module 8: Automations Center

- `/automations` grid (status, plain-language trigger line, approval chip, run info, Realtime), detail page with recipe read-mode + `step_log` run timeline, run cancellation through the approvals seam, edit guards while runs are in flight.
- Sentence + step-list recipe builder (`/automations/new`, `/{id}/edit`) driven by a vocabulary endpoint; agent drafting returns validated, never-persisted drafts that prefill the builder.

## v0.8.0 — 2026-07-17 · Module 7: Core Automations Framework

- Business-agnostic WHEN/IF/THEN engine: `automations` + `automation_runs` core tables, Pydantic recipe vocabulary with plain-language validation, `{{path}}` templates, function registry; steps execute through `execute_tool` (`source_system='automation'`) so the approval gate parks runs.
- In-process lifespan loops: event dispatcher (durable cursor, no automation→automation cycles), cron scheduler, delay waker, stale-run recovery; approval resolution resumes/cancels paused runs; one active run per (automation, entity).

## v0.7.0 — 2026-07-16 · Module 6: Control Center Shell & Auth

- Supabase Auth (email/password, ES256 + HS256 JWT verification) replacing the env-tenant seam; every `/api` route fails closed; machine paths (webhooks, `/mcp`) on their own seam.
- Home landing page (`/`, stat widgets + quick actions + recent activity via `GET /api/home/summary`), Chat moved to `/chat`, full visual overhaul ("Signal" palette, Inter, semantic status tokens, restyled shell/nav/user menu), chat markdown + streaming QoL.

## v0.6.0 — 2026-07-16 · Module 5: Approval Gate & Task System

- Gated tools queue instead of executing: `action.queued` event + plain-language task + `pending_actions` row; approval executes through the same `execute_tool` seam (bypass reserved to `services/approvals.py`); rejection/failure paths visible.
- First write tools (gated entity writes, placeholder `send_sms`/`send_email`, safe `create_task`); `/tasks` page with approval cards, filters, Realtime; chat marks queued actions with an amber chip.

## v0.5.0 — 2026-07-16 · Module 4: Event Log

- `GET /api/events` (keyset pagination, source/type/date/entity filters) + facets; read-time plain-language summary derivation (`services/event_summaries.py`).
- `/events` page: filterable feed, entity drill-down chips, expandable technical payload, Realtime live tail.

## v0.4.0 — 2026-07-16 · Module 3: MCP Server & Connector Seam

- MCP server mounted at `/mcp` (Streamable HTTP, static bearer) listing the registry dynamically; calls audited with `source_system='mcp'`; live-verified with a real MCP client.
- Webhook ingress `POST /api/webhooks/{source}` (HMAC verify → raw receipt → normalize → resolve): adapter seam with five placeholder adapters (WelcomeHome, GoTo, WellSky, Gmail, GCal), entity resolution via `external_ids` (matched / auto-create / review task), `connector_state` core table.

## v0.3.0 — 2026-07-16 · Module 2: Structured Data Access

- Tool registry + single `execute_tool()` audit seam (events row per call, `safe` flag); seven entity read tools; retrieval became the `search_documents` tool; read-only `run_report` text-to-SQL (validated single SELECT, READ ONLY tx, timeout, row cap).
- Chat became a real agentic loop: `tool_use`/`tool_result` persisted verbatim, extended SSE contract, plain-language tool chips.

## v0.2.0 — 2026-07-14 · Module 1: Foundation Chat + Ingestion

- First runnable app: FastAPI backend on the RLS-subject `nexus_app` role (per-request tenant GUC), Vite/React/Tailwind frontend shell.
- Ingestion: upload → Storage → parse (PDF/DOCX/HTML/MD/TXT) → chunk → Voyage embeddings, live status via Realtime; Chat: persisted threads, SSE streaming with basic RAG + citations, prompt caching; LangSmith tracing end-to-end.

## v0.1.0 — 2026-07-14 · Module 0: Canonical Data Model

- Core schema on hosted Supabase: `tenants`, `documents`/`document_chunks` (pgvector 1024 + HNSW), immutable `events`, `tasks`, `pending_actions`, `external_ids`; four-policy tenant RLS on every table; senior-care entity migration isolated as the re-templating seam (`leads`/`clients`/`resources`/`schedules`/`regions`/`qualifications`); idempotent seeds; 28-test pytest harness.

---

**Known pending validations** (carried across 0.x releases): live in-browser walks for the v0.10–v0.18 surfaces await the one-time Module 6 ops step (create the office user with the tenant claim in the Supabase dashboard); each release's automated suites (pytest/vitest/build) were green at ship time.
