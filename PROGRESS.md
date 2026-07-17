# Progress

Module-by-module build status for the Nexus Control Center. Claude Code reads this file at the start of a session to understand where the project stands; update the relevant tasks as work completes. Module numbering follows the PRD's module list (0–12; renumbered 2026-07-16 when Module 7 was expanded into Modules 7–10 and n8n was dropped).

## Convention
- `[ ]` = Not started
- `[-]` = In progress
- `[x]` = Completed

## Modules

### Module 0: Canonical Data Model
`[x]` Complete (2026-07-14) — live on hosted Supabase project ref `csiwxltfzodnlywuykdh`.

- `[x]` Core foundation migration — `tenants`, `app.current_tenant_id()` (JWT claim → GUC → deny), shared trigger functions
- `[x]` Core tables migration — `documents`, `document_chunks` (vector(1024), HNSW), `events`, `tasks`, `pending_actions`, `external_ids`
- `[x]` Core RLS migration — four-policy tenant isolation on every table, `events` SELECT+INSERT only
- `[x]` Senior-care entity migration — `leads`, `clients`, `resources`, `schedules`, `regions`, `qualifications` (re-templating seam isolated to this file)
- `[x]` Idempotent `seed.sql` applied — demo tenant + RLS probe tenant
- `[x]` pytest harness green, 28/28 — schema/constraints/triggers, tenant RLS isolation over PostgREST, events immutability both ways, pgvector HNSW nearest-neighbour

### Module 1: Foundation Chat + Ingestion
`[x]` Built (2026-07-14) — see `.agent/plans/1.foundation-chat-ingestion.md`. Code complete; live validation with chat interface and file ingestion with basic RAG.
- `[x]` Plan written and scope locked (basic RAG in chat, persisted threads, 4-format lightweight parsers, single ordered plan)
- `[x]` Task 1 — Migrations pushed: `nexus_app` RLS-subject role, `chat_threads`/`chat_messages` + 4-policy RLS, Storage bucket + Realtime publication. Schema/RLS tests green over PostgREST; `test_app_role.py` written (skips until `NEXUS_APP_DB_URL` set — **blocking ops step: role password, documented in README/.env.example**)
- `[x]` Task 2 — Backend app skeleton: FastAPI, psycopg async pool on `nexus_app`, tenant-scoped connection dependency + `tenant_tx` context manager, `deps.get_tenant_id()` seam, `/healthz`. Health test green.
- `[x]` Task 3 — Parsing layer (PDF/DOCX/HTML/MD/TXT behind swappable registry) + chunking. 12 tests green (offline).
- `[x]` Task 4 — Voyage embeddings service (batched ≤128, dim-1024 assert) + LangSmith `wrap_anthropic`/`@traceable` wiring (no-ops without key). Batching tests green.
- `[x]` Task 5 — Ingestion pipeline + documents API (upload → Storage → BackgroundTasks parse→chunk→embed → status transitions + `events` rows). Test written (nexus_app-gated).
- `[x]` Task 6 — Retrieval service (pgvector cosine top-8, RLS-filtered). Test written (nexus_app-gated, proves cross-tenant isolation).
- `[x]` Task 7 — Chat API: threads CRUD + SSE streaming with RAG, two-block system w/ prompt caching, realtime-token endpoint. SSE-sequence + persistence test written (nexus_app-gated).
- `[x]` Task 8 — Frontend scaffold: Vite + React + TS + Tailwind + shadcn-style UI, router, AppShell, api/sse/supabase libs. `npm run build` green.
- `[x]` Task 9 — Ingestion page: dropzone, document table, live status via Supabase Realtime (`postgres_changes` + `setAuth`). Browser check pending running stack.
- `[x]` Task 10 — Chat page: thread list, streaming messages (fetch+ReadableStream SSE), source citations, history restore. Browser check pending running stack.
- `[x]` Task 11 — Wrap-up: README getting-started (role ops step, run backend+frontend), `.env.example` updated, `pytest backend/tests` = 52 passed / 10 skipped (key-gated), `npm run build` clean.

**Remaining for full live validation** (needs `nexus_app` password + `NEXUS_APP_DB_URL` + `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY` in `.env`, and `frontend/.env`): run the 10 skipped backend tests; browser-test drag-drop status flow and streaming chat; confirm a LangSmith trace shows retrieve→generate spans.

### Module 2: Structured Data Access
`[x]` Complete (2026-07-16) — see `.agent/plans/2.structured-data-access.md`. Reads-only tool layer + read-only text-to-SQL, wired into chat as an agentic tool loop (retrieval becomes a `search_documents` tool). No new migrations or env vars.

- `[x]` Task 1 — Tool core: `ToolDef`, registry, `execute_tool` audit seam (events row per call, `safe` flag refusal)
- `[x]` Task 2 — Entity read tools (vertical seam `entities.py`): 7 read tools + `SQL_SCHEMA_DOC`
- `[x]` Task 3 — `search_documents` tool (retrieval as a tool, turn-global citation numbering)
- `[x]` Task 4 — SQL guard + `run_report` (validation + READ ONLY tx + timeout + 200-row cap)
- `[x]` Task 5 — Agentic chat loop (tool_use/tool_result persisted verbatim, extended SSE contract)
- `[x]` Task 6 — Frontend tool activity (chips, plain-language labels, history reload)
- `[x]` Task 7 — Wrap-up + live validation. Automated: full `pytest backend/tests` green (97 passed, incl. nexus_app-gated tool/report/chat-loop tests), `npm run build` clean. Live (2026-07-16): tool calls validated in LangSmith and basic tool tests exercised — chat agentic loop, structured tools, and trace spans confirmed working.

### Module 3: MCP Server & External Connectors
`[x]` Complete (2026-07-16) — 🔴 Complex, split per the planning rule into two independently executable sub-plans (both done). Parent: `.agent/plans/3.mcp-and-connectors.md` (includes the real-system integration research for WelcomeHome / GoTo Connect / WellSky / Gmail / Google Calendar). Build order 3a → 3b.

**3a — MCP server** (`.agent/plans/3a.mcp-server.md`) — `[x]` Complete (2026-07-16), code + tests + live validation:
- `[x]` Task 1 — `mcp==1.28.1` dep (sse-starlette pinned <3.x to keep fastapi's starlette<0.42); `services/mcp_server.py` (low-level Server over the registry, Streamable HTTP stateless JSON, bearer-token ASGI wrapper); mounted at `/mcp` + `session_manager.run()` in lifespan
- `[x]` Task 2 — Gated end-to-end green: `tools/call list_leads {status:new}` over JSON-RPC → Margaret Ellison from seed + `events` row with `source_system='mcp'`; unknown tool → clean `isError`
- `[x]` Task 3 — README + `.env.example` (`NEXUS_MCP_TOKEN`); `pytest backend/tests` green (104, incl. 7 MCP tests). Live-verified against a running backend with the real `streamablehttp_client`: initialize → list_tools (9) → `list_leads` (Margaret Ellison) + `run_report` (5 rows), both `isError=false`; two `source_system='mcp'` audit rows; LangSmith trace shows `mcp_call` chain span with the `execute_tool` tool span nested under it

**3b — Connector ingress & entity resolution** (`.agent/plans/3b.connector-ingress.md`) — `[x]` code + tests complete (2026-07-16); live chat/trace check pending running backend:
- `[x]` Task 1 — Migration pushed (`20260716000000_connector_infra.sql`): `connector_state` core table + 4-policy RLS + `set_updated_at`; `external_ids` category CHECK gains `'calendar'`. Verified live on the remote DB.
- `[x]` Task 2 — Adapter framework (`services/connectors/base.py`): `NormalizedEvent`/`NormalizedResult`, `ConnectorAdapter` (default HMAC-SHA256 verify + `sign` helper), `registry.py`, bootstrap `__init__`
- `[x]` Task 3 — Resolution router (`resolution.py`, three outcomes) + vertical-seam `entity_writers.py` (lead writer; no-writer → task, never a 500); `log_event` now returns the receipt id
- `[x]` Task 4 — `POST /api/webhooks/{source}` ingress (verify-before-DB → raw receipt event → async normalize → route), `webhook_ingress` trace span
- `[x]` Task 5 — Five placeholder adapters (welcomehome/goto/wellsky/gmail/gcal) with real-flow docstrings + signed fixtures; one end-to-end test per source (matched / auto-create / task / ack-only, base64 round-trip)
- `[x]` Task 6 — README + `.env.example` (`NEXUS_WEBHOOK_SECRET`); `pytest backend/tests` green (115, +11 connector tests); `npm run build` clean. Live-verified against a running backend: signed `welcomehome` `lead.created` → `{received:1, created:1}`; `list_leads` (the chat path) returns the new lead; `events` rows `webhook.received` + `lead.created` under `source_system='welcomehome'`; LangSmith `webhook_ingress` chain span (status success).

### Module 4: Event Log
`[x]` Code complete (2026-07-16) — ✅ Simple, see `.agent/plans/4.event-log.md`. Read-only surface over the existing `events` table: filtered/paginated API + Event Log page with entity drill-down and Realtime live tail. No new env vars.

- `[x]` Task 1 — Migration pushed (`20260716000001_eventlog_realtime.sql`): `events` added to the `supabase_realtime` publication (guarded/idempotent). Verified live on the remote DB.
- `[x]` Task 2 — `services/event_summaries.py`: read-path plain-language summary derivation (`payload.summary` → core templates → humanized fallback). 15 offline cases green.
- `[x]` Task 3 — Events API: `GET /api/events` (keyset pagination; source/type/date/entity filters, limit capped at 100) + `GET /api/events/facets`. Gated tests green (pagination, each filter, RLS isolation, cap, server-derived summary).
- `[x]` Task 4 — Frontend: `/events` page (filters ↔ URL params, entity drill-down chips, expandable payload JSON, Realtime live tail, Load more). `npm run build` clean.
- `[-]` Task 5 — Wrap-up: full `pytest backend/tests` green (130 passed, incl. 15 new event tests); `npm run build` clean. Also refreshed the time-relative schedule seed (now `on conflict (id) do update`) so it no longer drifts into the past. Live browser check (feed/drill-down/live-tail in a running stack) pending.

### Module 5: Approval Gate & Task System
`[x]` Code complete (2026-07-16) — 🔴 Complex, split per the planning rule into two sub-plans. Parent: `.agent/plans/5.approval-gate-and-tasks.md`. Build order 5a → 5b (5b consumes 5a's API). User-locked: entity writes + placeholder `send_sms`/`send_email` as the gated tool set; full simple task management UI; safe `create_task` tool. Backend `pytest` green (138); frontend `npm run build` clean. Remaining: live browser + LangSmith end-to-end walk on a running stack.

**5a — Approval gate backend** (`.agent/plans/5a.approval-gate-backend.md`): ✅ code complete (2026-07-16), `pytest backend/tests` green (137 passed)
- `[x]` Task 1 — Migration: `pending_actions` +`source_system`/`resolved_by`/`result` columns; `tasks` + `pending_actions` into the Realtime publication (guarded/idempotent)
- `[x]` Task 2 — Gate path in `execute_tool`: `ToolDef.gate_describe`, queue path (event → task → action, non-error result), `approved_action_id` bypass
- `[x]` Task 3 — Approvals engine (`services/approvals.py`): approve → execute via the bypass (action `executed`/`failed`, task coupling), reject, double-resolve conflict
- `[x]` Task 4 — Write tools: 4 gated entity writes (vertical seam), safe `create_task`, placeholder `send_sms`/`send_email`; bootstrap order
- `[x]` Task 5 — Tasks & approvals API: `GET/POST /api/tasks`, `PATCH /api/tasks/{id}`, `POST /api/pending-actions/{id}/approve|reject`
- `[x]` Task 6 — Chat PERSONA/TOOL_LABELS + README endpoints + full pytest green

**5b — Tasks interface** (`.agent/plans/5b.tasks-interface.md`): ✅ code complete (2026-07-16), `npm run build` clean; live browser + LangSmith walk pending a running stack
- `[x]` Task 1 — `lib/api.ts` task/action calls + types
- `[x]` Task 2 — `/tasks` page: tabs/filters ↔ URL, task cards w/ transitions, approval cards, create dialog, Realtime, nav entry
- `[x]` Task 3 — Chat queued surfacing: additive `queued` flag on `tool_result` SSE + amber chip linking to `/tasks`
- `[x]` Task 4 — Wrap-up done (docs + tests green); live end-to-end browser/LangSmith walk still pending a running stack (gated action stalls until approved; full `action.queued → approved → tool.called` trail in Event Log + LangSmith)

### Module 6: Control Center Shell & Visual Overhaul
`[x]` Planned (2026-07-16) — 🔴 Complex, split per the planning rule into two sub-plans. Parent: `.agent/plans/6.control-center-shell.md`. Build order 6a → 6b (6b renders 6a's session and sits behind its route guard). User-locked: Home lands at `/` as a *light* widget landing page (not a needs-attention queue — Tasks stays the triage surface) with Chat moving to `/chat`; email + password sign-in; `/mcp` keeps the static bearer token.

**6a — Supabase Auth & tenant identity** (`.agent/plans/6a.supabase-auth.md`): code complete (2026-07-16), `pytest backend/tests` green (139), `npm run build` clean. Remaining: blocking ops step + live browser walk.
- `[x]` Task 1 — Backend JWT verification (ES256/RS256 via JWKS + HS256 via secret) in `get_tenant_id`; `get_current_user`; `get_machine_tenant_id` seam for webhooks/MCP; realtime-token dev seam (`routers/auth.py`) deleted; `cryptography` added
- `[x]` Task 2 — Test-harness sweep: `bearer_headers`/`auth_headers` fixtures + `email` kwarg on `mint_tenant_jwt`, every data-route API test authenticated, full pytest green (139)
- `[x]` Task 3 — `resolved_by` from the verified user (`get_current_user`) on approve/reject
- `[x]` Task 4 — Frontend session: `AuthProvider`/`RequireAuth`, `/login` page, `authFetch` on every API call (incl. SSE + upload), Realtime via session (three `setAuth` seams removed), temporary sign-out in shell footer
- `[x]` Task 5 — README auth section + `.env.example` MCP note done; **blocking: create office user + tenant claim SQL in the Supabase dashboard**; live browser walk pending that user

**6b — Shell, Home & visual overhaul** (`.agent/plans/6b.shell-home-overhaul.md`): code complete (2026-07-16), `pytest backend/tests` green (140, incl. gated home-summary test), `npm run build` clean. Remaining: live browser walk in both themes (pending 6a's office user + running stack).
- `[x]` Task 1 — Design foundation: "Signal" palette (teal-cyan accent, light+dark), semantic status tokens (`--warning`/`--success`/`--info`), Inter Variable self-hosted, `@tailwindcss/typography`, `PageHeader`/`EmptyState` primitives
- `[x]` Task 2 — Shell & routes: Home at `/`, Chat at `/chat`, catch-all → `/`, nav reordered (Home/Chat/Tasks/Ingestion/Event Log), restyled sidebar w/ brand mark + active bar, `UserMenu` popover (email/theme/sign-out); temporary footer + `ThemeToggle.tsx` removed
- `[x]` Task 3 — `GET /api/home/summary` counts endpoint (`routers/home.py`, `HomeSummary`/`DocumentCounts` schemas, wired in `main.py`) + `api.getHomeSummary`; gated `test_home_api.py` (delta-based counts, RLS isolation, 401) green
- `[x]` Task 4 — Home page: greeting hero, four `StatCard`s (semantic tones, deep-links), `QuickActions` (New chat/Upload/`?create=1`), `RecentActivity` (last 6 events, relative time); TasksPage honors `?create=1`
- `[x]` Task 5 — Chat QoL: `Markdown.tsx` (react-markdown + remark-gfm, assistant-only, `.prose-chat`), rAF-buffered SSE deltas in `ChatPage.send`, pinned-aware autoscroll + jump-to-latest in `MessageList`
- `[x]` Task 6 — Polish sweep: `PageHeader`/`EmptyState` across Ingestion/Tasks/Event Log + ThreadList; status badges consolidated onto semantic tokens (Badge `success`/`warning`/`info`, StatusBadge, TaskCard, ApprovalCard, ToolActivity); README routes note. Full pytest + build green; live browser walk pending

### Module 7: Core Automations Framework
`[x]` Complete (2026-07-17) — 🔴 Complex, split per the planning rule into two sub-plans (both done, live-validated). Parent: `.agent/plans/7.core-automations-framework.md`. Build order 7a → 7b. The business-agnostic WHEN/IF/THEN engine (n8n dropped); engine + REST only — all UI and agent surfaces are M8+. User-locked: declarative-only IF conditions (function steps compute values into context); step failure ⇒ fail run + review task (no retries); one active run per (automation, entity), re-triggers skipped; no agent tools this module. `pytest backend/tests` = 181 passed; `npm run build` clean; end-to-end live walk passed.

**7a — Recipe model, tables & synchronous engine core** (`.agent/plans/7a.recipe-model-and-engine.md`): ✅ code complete (2026-07-17), `pytest backend/tests` green (172 passed, +32 automations tests); `npm run build` clean
- `[x]` Task 1 — Migration pushed (`20260717000000_automations_infra.sql`): `automations` + `automation_runs` core tables (4-policy RLS, concurrency partial-unique index, waker index), `pending_actions.automation_run_id`, Realtime publication. Verified live on the remote DB.
- `[x]` Task 2 — Recipe vocabulary (`recipe.py`, Pydantic discriminated unions + plain-language `validate_recipe`), `{{path}}` templates (`templates.py`, type-preserving full-value refs, fail-loud on missing path), function registry (`functions.py`, core `now`/`days_since`), vertical entity-lookup seam (`entities.py`). 20 offline tests green.
- `[x]` Task 3 — Engine core (`engine.py`): `start_run`/`advance_run` (one `tenant_tx` per step), gate pause (`waiting_approval` + `automation_run_id` stamp), delay parking (`waiting` + `wake_at`), fail path + high-priority review task + `automation.run_failed`, concurrency guard + `automation.run_skipped`, `step_log` trail, `@traceable` chain span. 8 gated tests green.
- `[x]` Task 4 — Automations & runs REST API (`routers/automations.py`): CRUD w/ 422 plain errors, manual run-now (skip-conditions override → 409 on concurrency), run history/detail; RLS + 401 tests green.
- `[x]` Task 5 — Wrap-up: README automations section (endpoints + curl-runnable "welcome a new lead" recipe); `croniter` + `fast_model` added; full `pytest backend/tests` green (172); `npm run build` clean.

**7b — Triggers, scheduler & durable runs** (`.agent/plans/7b.triggers-scheduler-durability.md`): ✅ code complete + live-validated (2026-07-17), `pytest backend/tests` green (181 passed, +9 scheduler tests); `npm run build` clean
- `[x]` Task 1 — Engine loop skeleton (`scheduler.py` `run_cycle` + four `*_once()` ticks) in lifespan + settings (`NEXUS_AUTOMATIONS_ENABLED`, `_POLL_SECONDS`, `_STALE_MINUTES`); uvicorn smoke green enabled + disabled; `test_cycle_runs_clean` green
- `[x]` Task 2 — Event dispatcher: keyset `(created_at, id)` poll of `events` behind a durable `connector_state._automations` cursor; loop guard (automation-sourced events never dispatched); no history replay on first run. Gated tests green.
- `[x]` Task 3 — Cron triggers: `next_fire_at` bookkeeping (croniter, `next_fire`), `for update skip locked` claims, advance-before-run, PATCH recompute on activation/expression change. Gated tests green.
- `[x]` Task 4 — Waker for due `waiting` runs + stale-`running` recovery sweep (+ arms un-armed active cron). Gated tests green.
- `[x]` Task 5 — Approval resume/cancel hook in `services/approvals.py` (approved→resume in-request via `resume_after_approval`; rejected→`cancel_after_rejection`; post-approval failure→run failed, no second review task). Gated tests green.
- `[x]` Task 6 — Wrap-up: README engine-loops section + `.env.example` entries. **Live walk PASSED** (running server): seeded `lead.created` → dispatcher started the run → live `generate` (fast model) produced a welcome message → gated `send_sms` parked `waiting_approval` with a task → API approve resumed and completed the run; full plain-language Event Log trail (`lead.created → automation.run_started → action.queued → action.approved → tool.called → automation.run_completed`, all `source_system='automation'`).

### Module 8: Automations Center
`[ ]` Planned (2026-07-16) — 🔴 Complex, split per the planning rule into two sub-plans. Parent: `.agent/plans/8.automations-center.md`. Build order 8a → 8b; requires Module 7 built first. User-locked: sentence + step-list builder layout; dedicated builder pages (`/automations/new`, `/{id}/edit`); agent drafting = draft → review in builder (drafts never persisted by the agent); no starter templates. Plan 7a amended at planning time: `automation_runs.step_log` + `FunctionDef.input_schema` (both consumed here).

**8a — Grid, run history & management** (`.agent/plans/8a.center-management.md`):
- `[ ]` Task 1 — Backend: `POST /api/automation-runs/{id}/cancel` (waiting_approval cancels via the approvals seam), definition-edit guard (409 w/ runs in flight), list enrichment (`active_runs`/`last_run`/`requires_approval`), Home summary automations block
- `[ ]` Task 2 — `/automations` grid page + nav entry: cards w/ status pill, plain-language trigger line (`describeRecipe`), approval chip, pause/resume, delete
- `[ ]` Task 3 — `/automations/{id}` detail: read-mode recipe components, run history, `step_log` timeline drawer, cancel-run
- `[ ]` Task 4 — Realtime live updates (both tables), Home StatCard, README + wrap-up

**8b — Recipe builder & agent drafting** (`.agent/plans/8b.recipe-builder.md`):
- `[ ]` Task 1 — `GET /api/automations/vocabulary` (tools + schemas + safety, functions, event types, operators) + cron-preview helper
- `[ ]` Task 2 — `POST /api/automations/draft`: LLM → Pydantic-validated recipe, one retry on validation failure, never persisted, `automation_draft` trace span
- `[ ]` Task 3 — Builder page (create): WHEN/IF sentence chips, THEN step cards w/ schema-driven forms + `{{path}}` template insert, live sentence preview
- `[ ]` Task 4 — Edit mode + guard UX (409 banner → cancel runs → retry)
- `[ ]` Task 5 — Draft-review flow (DraftBox → prefill builder → explanation banner → normal save)
- `[ ]` Task 6 — Wrap-up + live walk: describe → draft → activate → webhook → live run → approve → complete, in both themes

### Module 9: Leads View & Marketing Funnel
`[ ]` Not started. First vertical dashboard view (entity-dashboard/pipeline pattern is core; lead content is the re-templating seam): stage funnel, per-stage outreach sequence builder (SMS/email/call tasks, delays, waits, conditionals, content generation on the M7 framework), lead directory with expanded profiles (basic info, entity event log, AI smart summary), funnel metrics. Depends on Module 7.

### Module 10: Caregivers View & Hiring Process
`[ ]` Not started. Same dashboard pattern for caregiver recruiting: hiring-stage pipeline, automated accepted/denied emails, scoring functions, applicant directory with smart summaries, hiring metrics. Depends on Module 7 (and reuses Module 9's view pattern).

### Module 11: Deterministic Matching/Decision Harness
`[ ]` Not started. Default 🔴 Complex — break into sub-plans. (Formerly Module 8.)

### Module 12: Advanced RAG & Scale-Up
`[ ]` Not started. (Formerly Module 10. The former Module 9 "Custom Views / Plugin Apps" placeholder is retired — Modules 9–10 now carry the vertical-view pattern in scope; anything beyond them stays out of scope.)

### Future Plans

* Settings View
* Content generation and output files e.g., formatted dynamic care plan
* Stop / cancel streaming. Abort chat strea mid rresponse. Also fix send button positioning and text box. Button height does not match text input and text input not centered.
* Sidebarr collapse to icons
* Calender date input improvement that matches theme. Current one is ugly.
* Home page dashboard with census, billable hours week-over-week, new starts, caregiver headcount, coverage rate (% of visits filled), AR/unbilled, and the top open alerts.
* Referral-source dashboard — which partners (hospitals, senior-living, discharge planners) send leads that actually convert. Referral ROI drives where the owner spends relationship time; this is the highest-value net-new growth view not already on the roadmap.
* Client & care oversight: 
    * Active census — count of active clients, by region/payer, plus authorized hours vs scheduled vs delivered. The gap between authorized and delivered is direct revenue leakage — owners obsess over it.
    * Per-client care overview — care plan, assigned caregivers, schedule, family contacts, status (active / hospital-hold / discharged). Care plans and visit notes flow through your ingestion + RAG so they're searchable in chat.
    * Visit verification (EVV) — worth flagging even if you hadn't considered it: Electronic Visit Verification (clock-in/out, missed/late visits) is legally mandated for Medicaid-funded home care in most states. It's connector-shaped and you already have telephony/EHR placeholder adapters (GoTo Connect, WellSky) to hang it on.
* Scheduling system:  the daily fire in home care, so it's the flagship. Three linked pieces:
    * Schedule board — a week calendar, caregivers as rows and visits as blocks (or flip to client-rows). Color by state: confirmed / unfilled / call-out / overtime-risk. This is a direct render of schedules joined to resources and clients.
    * Coverage / open-shift view — the "who's not covered tomorrow" list. Unfilled or at-risk visits, sorted by how soon. This is the single view an owner opens first every morning.
    * Caregiver–client matching tool — the smart part. Given an open shift, rank available caregivers by: qualification match (qualifications), region proximity (regions), availability, continuity (has this caregiver served this client before — owners care enormously about this), and overtime/conflict avoidance. This is almost exactly what the planned M11 matching harness is for, exposed as an MCP tool like find_available_caregivers so you can also just ask in chat: "who can cover Margaret's Tuesday 9am?"
    * The call-out → replacement flow ties it together and shows off your existing plumbing: caregiver calls out → matching tool ranks replacements → gated send_sms offers the shift → owner approves in Tasks → creal event.
* Workforce & Compliance 
    * Caregiver roster / utilization — headcount, active vs inactive, hours-this-week, utilization %, availability. Overlaps M10.
    * Credential expiry tracker — CPR, TB test, background check, license, all with expiry dates on qualifications. This is a killer automations use case: WHEN a credential is within 30/60 days of expiry, THEN queue a task + notify. In this industry an expired credential can mean a caregiver legally can't work a shift — surfacing it before it bites is high-value and cheap given the engine exists.
    * Retention / at-risk view — turnover in home care runs 70–80%/yr; a view flagging declining hours, missed shifts, or short tenure lets the owner intervene before someone quits.
    The scheduling system (your example, built out)
