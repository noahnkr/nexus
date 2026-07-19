# Progress

Module-by-module build status for the Nexus Control Center. Claude Code reads this file at the start of a session to understand where the project stands; update the relevant tasks as work completes. Module numbering follows the PRD's module list (0–14; renumbered 2026-07-16 when Module 7 was expanded into Modules 7–10 and n8n was dropped).

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
`[x]` Complete (2026-07-17) — 🔴 Complex, split into two sub-plans (both done, live-validated). Parent: `.agent/plans/8.automations-center.md`. Build order 8a → 8b. User-locked: sentence + step-list builder layout; dedicated builder pages (`/automations/new`, `/{id}/edit`); agent drafting = draft → review in builder (drafts never persisted by the agent); no starter templates. `pytest backend/tests` = 189 passed; `npm run build` clean; live draft→create→activate→run→approve walk passed.

**8a — Grid, run history & management** (`.agent/plans/8a.center-management.md`): ✅ code complete (2026-07-17)
- `[x]` Task 1 — Backend: `POST /api/automation-runs/{id}/cancel` (waiting_approval cancels via `reject_action`; running/waiting via engine `cancel_run`), definition-edit guard (409 w/ runs in flight), list enrichment (`active_runs`/`last_run`/`requires_approval`), Home summary automations block. Gated tests green.
- `[x]` Task 2 — `/automations` grid page + nav entry (Zap): cards w/ status pill, `describeRecipe` trigger line, approval chip, active-run/last-run lines, pause/resume (optimistic), overflow menu (View/Edit/Delete), Realtime debounced refetch
- `[x]` Task 3 — `/automations/{id}` detail: read-mode recipe components (`TriggerSentence`/`ConditionChips`/`StepCard`), recipe-JSON toggle, run history (`RunList`), `step_log` timeline drawer (`RunTimeline`) w/ context expander + cancel-run
- `[x]` Task 4 — Realtime on both tables, Home StatCard (Zap, warning tone on `failed_today`), `lib/recipe.ts` (`describeRecipe` + run-status meta + gated-tool helper)

**8b — Recipe builder & agent drafting** (`.agent/plans/8b.recipe-builder.md`): ✅ code complete + live-validated (2026-07-17)
- `[x]` Task 1 — `GET /api/automations/vocabulary` (tools+schema+safety+label, functions, operators, event types [observed ∪ core-known, automation-sourced excluded], field roots) + `GET /cron-preview` (auth-gated). `TOOL_LABELS` lifted to `services/tools/labels.py`. Gated tests green.
- `[x]` Task 2 — `POST /api/automations/draft`: forced-tool-use structured output → `AutomationDraft` → `validate_recipe`, one retry on failure, never persisted, 503 without key, `automation_draft` trace span. Offline-mocked + gated-live tests green.
- `[x]` Task 3 — Builder page (create): editable `TriggerSentence` (event/cron/manual + cron preview), `ConditionChips`, `StepList`/`StepCard` (5 types), `SchemaForm` (JSON-Schema→forms, raw-JSON degrade), `TemplateInsert`, live sentence preview
- `[x]` Task 4 — Edit mode (`/{id}/edit`) + guard UX (409 banner → cancel runs → retry via `cancelRunsAndRetry`)
- `[x]` Task 5 — Draft-review flow (`DraftBox` → prefill builder → explanation banner → normal save; dirty-confirm on replace)
- `[x]` Task 6 — Wrap-up: README Automations Center section. **Live walk passed** (running server): vocabulary (16 tools, send_sms gated) → cron-preview → real-LLM draft ("WelcomeHome Lead Welcome Text": event trigger + generate + gated send_sms) → create-from-draft (paused) → activate → seeded `lead.created` → dispatcher run → gated park → approve → completed. Browser builder UI type-checked via clean build; full in-browser walk (both themes) pending the Module 6 office user.

### Module 9: Leads View & Marketing Funnel
`[x]` Built (2026-07-17) — 🔴 Complex, split per the planning rule into two sub-plans. Parent: `.agent/plans/9.leads-view.md`. Build order 9a → 9b. User-locked: funnel strip + directory on one page (no kanban/dnd); sequence↔stage linkage via core `automations.binding` jsonb (one sequence per stage); smart summary on-demand with no persistence; lead writes = create + stage moves + basic-field edits (no delete). Requires Modules 7–8 built. Backend fully test-covered (195 pytest green); frontend builds clean. Browser/live-walk validations (9a T3–4, 9b T3–5) need the running stack + Module 6 office user — no browser MCP available in this environment.

**9a — Leads API, directory & profiles** (`.agent/plans/9a.leads-api-directory.md`):
- `[x]` Task 1 — Migration (`leads` → Realtime publication) + `routers/leads.py` (list/facets/create/patch/detail, `lead.stage_changed`/`lead.updated` events, no delete) + `services/views/leads.py` stage config + `update_lead_status` emits `lead.stage_changed` (via a tool-invocation contextvar so the caller's `source_system` rides the event — the M7 loop guard depends on it); gated `test_leads_api.py` green
- `[x]` Task 2 — Smart summary: `GET /api/leads/{id}/summary` (fast model over lead + recent event summaries, `lead_summary` trace span, 503 without key, no persistence; generic helper `services/views/summary.py` for M10 reuse); `test_lead_summary.py` green (incl. gated-live)
- `[x]` Task 3 — `/leads` directory page: table + stage-chip/source/search filters ↔ URL, create dialog, Realtime, nav entry (Leads, Filter icon); build clean *(browser check pending running stack)*
- `[x]` Task 4 — `/leads/{id}` profile: SmartSummary card, inline info edit, StageSelect, `EntityTimeline` (shared component); build clean *(browser check pending running stack)*
- `[x]` Task 5 — Wrap-up: README Leads section; full pytest green (195); build clean *(live `lead_summary` LangSmith span verified via gated-live test)*

**9b — Funnel, metrics & per-stage sequences** (`.agent/plans/9b.funnel-and-stage-sequences.md`):
- `[x]` Task 1 — Core migration (`automations.binding` jsonb + partial unique index) + binding API (accept/validate, 409 on duplicate, `?view=` filter, `AutomationOut.binding`); gated `test_automation_binding.py` green
- `[x]` Task 2 — `GET /api/leads/metrics` (stage counts, conversion rate, new-last-7-days, avg days to convert, top sources); gated metrics tests green (`test_lead_metrics`)
- `[x]` Task 3 — `FunnelStrip` (clickable segments + sequence chips) + `LeadMetrics` tiles replacing the 9a chip row; build clean *(browser check pending running stack)*
- `[x]` Task 4 — `/leads/stages/{stage}/sequence` constrained builder (fixed stage trigger convention, restricted tool palette, saves via standard API with binding, starts paused) + Center binding chips/edit rerouting (`lib/pipeline.ts` view-config map); convention validated by `test_automation_binding` recipe *(browser check pending running stack)*
- `[x]` Task 5 — Wrap-up + README; full pytest (195) + build green *(live end-to-end walk pending running stack)*

### Module 10: Caregivers View & Hiring Process
`[x]` Built (2026-07-17) — 🔴 Complex, split per the planning rule into two sub-plans. Parent: `.agent/plans/10.caregivers-view.md`. Build order 10a → 10b. User-locked: new `applicants` table + promotion to `resources` on hire (leads→clients precedent); stages `applied → screening → interview → offer → hired` + terminal `rejected`; promotion is automatic and atomic on the hired stage move (emits `resource.created`); **scoring deferred to Module 11**. Requires Module 9 built (generic pipeline components + binding). Backend fully test-covered (new `test_applicants_api.py` incl. metrics, `test_applicant_summary.py`, caregivers-convention cases in `test_recipe.py`); frontend builds clean. Browser/live-walk validations (10a T4–5, 10b T2–4) need the running stack + Module 6 office user — no browser MCP in this environment.

**10a — Applicants model, API, directory & profiles** (`.agent/plans/10a.applicants-api-directory.md`):
- `[x]` Task 1 — Migration (`20260719000000_entities_applicants.sql`: `applicants` table + RLS + Realtime, `resources.applicant_id`) pushed live; seeds (5 applicants across stages); seams (`ENTITY_TABLES["applicant"]`, `list_applicants`/`get_applicant`, gated `update_applicant_stage`, `SQL_SCHEMA_DOC`, labels, `_KNOWN_EVENT_TYPES`). Gated tool tests green (`test_applicant_tools`)
- `[x]` Task 2 — `routers/applicants.py` (list/facets/create/patch/detail, no delete) + `views/caregivers.py` `move_stage()` (single event/promotion path; hired → atomic caregiver auto-create + `resource.created`, idempotent re-hire; supersedes in-flight sequences). Gated `test_applicants_api.py` green incl. hire-promotion, idempotent re-hire, and the shared approved-execution path (`test_update_applicant_stage_approved`)
- `[x]` Task 3 — Hiring smart summary (`GET /api/applicants/{id}/summary` via shared `views/summary.py`, `applicant_summary` span, 503 without key); `test_applicant_summary.py` green (offline + gated-live)
- `[x]` Task 4 — `/caregivers` directory page: table + stage-chip/source/search filters ↔ URL, create dialog w/ qualification/region multi-selects, Realtime, nav entry (Caregivers, Users icon); build clean *(browser check pending running stack)*
- `[x]` Task 5 — `/caregivers/{id}` profile: generalized `SmartSummary` (now view-agnostic), editable `ApplicantInfoCard` (contact/quals/regions/notes) + availability expander, stage select w/ hire-confirm dialog + hired banner, `EntityTimeline` (`entityType="applicant"`); build clean *(browser check pending running stack)*
- `[x]` Task 6 — Wrap-up: README Caregivers section; full pytest + build green *(live `applicant_summary` LangSmith span verified via gated-live test)*

**10b — Hiring funnel, metrics & per-stage sequences** (`.agent/plans/10b.hiring-funnel-and-sequences.md`):
- `[x]` Task 1 — `GET /api/applicants/metrics` (`views/caregivers.hiring_metrics`: six stage counts, hire rate, new-last-7-days, avg days to hire, top sources); gated `test_applicant_metrics` green (incl. empty-tenant zeroes/nulls)
- `[x]` Task 2 — Generic `FunnelStrip` + `HiringMetrics` StatCards on `/caregivers` (chip row replaced; rejected carries a sequence chip — per-view config); build clean *(browser check pending running stack)*
- `[x]` Task 3 — Caregivers `PipelineViewConfig` (`lib/caregivers.ts`, `sequenceStages` = all six) feeding the shared `StageSequencePage` route `/caregivers/stages/{stage}/sequence`; Center binding chips/Edit rerouting work via the view-config registry (no core changes). Convention validated by `test_recipe.py` caregivers cases
- `[x]` Task 4 — Wrap-up + README hiring funnel/sequences section; full pytest + build green *(live end-to-end walk — denial sequence park→approve, hire→caregiver — pending running stack + M6 office user)*

### Module 11: Automation Field Tokens & Calculations
`[x]` Built (2026-07-18) — 🔴 Complex (touches the automations framework), split per the planning rule into two sub-plans. Parent: `.agent/plans/11.automation-field-tokens.md`. Build order 11a → 11b. Replaces the formerly planned matching/decision harness + scheduling system in this slot (deferred to Future Plans, user decision 2026-07-17). User-locked: score results context-only (no score column/display/tool); `days_until` is the only new calculation function; recipe JSON format unchanged (tokens are a view over the same `{{path}}` strings). Backend fully test-covered (`pytest backend/tests` = 220 passed, +8); frontend builds clean and adds a `vitest` tokenizer suite (13 tests green). Browser/live-walk validations (11b T2–5) need the running stack + Module 6 office user — no browser MCP in this environment.

**11a — Field catalog & calculation backend** (`.agent/plans/11a.field-catalog-backend.md`):
- `[x]` Task 1 — Entity-catalog seam (`ENTITY_LABELS` + `entity_catalog()`, shared `humanize`/`_entity_columns`, `entity_field_suggestions` re-derived — no drift) + trigger-aware `field_catalog` on the vocabulary endpoint (5 labeled trigger fields, per-event payload keys grouped, per-entity fields, observed∪prefix-heuristic event→entity map); gated `test_automations_center_api.py` cases green
- `[x]` Task 2 — `days_until` function (mirror of `days_since`); offline `test_functions.py` cases green
- `[x]` Task 3 — Condition-value template rendering (`_eval_condition` renders `value`, unresolvable → condition false, never a crash; `wait_until` frozen via `_freeze_conditions`); gated `test_automation_engine.py` cases green (match / unresolvable-false / entry-context-value-skips)
- `[x]` Task 4 — Draft prompt teaches the catalog (`_catalog_prompt`); `test_automation_draft.py` prompt-includes-catalog case; full pytest (220) + build green

**11b — Token inputs & builder UX** (`.agent/plans/11b.token-builder-frontend.md`):
- `[x]` Task 1 — `lib/template.ts` pure tokenizer (parse/serialize/labelForPath/labelizeTemplate) + vitest wiring; `npm run test` green (13 tests)
- `[x]` Task 2 — `TokenText` chip input (controlled contenteditable, atomic contentEditable=false chips, caret insertion via ref, paste-reparse; single + multiline); build clean *(browser check pending running stack)*
- `[x]` Task 3 — `FieldPicker` + `TokenField` wrapper (grouped/labeled/searchable, entity group named for the trigger's record, cron/manual hint, custom-path escape hatch) + integration sweep (SchemaForm/StepCard/ConditionChips/FieldCombobox via one `FieldContext` prop; `TemplateInsert` deleted); build clean
- `[x]` Task 4 — "Run a calculation": `CalculationEditor` for `weighted_score` (field × weight rows, auto-slugged keys, live formula, `isSimpleWeightedScore` raw-JSON fallback), add-menu relabel + `FUNCTION_LABELS`; build clean
- `[x]` Task 5 — Read-mode token labels (`describeStep`/`describeCondition`/`readDetail` labelized via the catalog on the detail page) + README `npm run test` note; vitest/build/pytest green *(live draft→builder→run regression walk pending running stack)*

### Module 12: Smart Staffing & Scheduling
`[-]` Planned (2026-07-18) — 🔴 Complex, split per the planning rule into two sub-plans. Parent: `.agent/plans/12.smart-staffing-scheduling.md`. Build order 12a → 12b (12b renders 12a's API). User-locked: week-board UI (caregivers as rows, pinned Open-shifts row); address/zip/languages/trait fields added to clients+resources for the match score (zip-level geography — no geocoding); call-out flow = owner picks from ranked candidates, then gated `send_sms` notify approved in Tasks; repeat-weekly visit creation expanded server-side (≤12 weeks). The generic decision-harness stays deferred — matching is a deterministic seam service + safe tool. Requires Modules 7–11 built.

**12a — Scheduling backend & matching engine** (`.agent/plans/12a.scheduling-backend-matching.md`): `[x]` Code complete (2026-07-18) — migration pushed live + re-seeded; full pytest green (226), frontend build clean.
- `[x]` Task 1 — Migration `20260723000000_entities_scheduling.sql` (nullable `resource_id`, `open`/`called_out` statuses + coherence CHECKs, `required_qualification_ids`/`replaces_schedule_id`/`notes`, client/resource address+zip+languages+traits, Realtime) pushed live + re-seeded (open shift `…0009`, called-out pair `…000a`/`…000b`); seam threading (`ENTITY_TABLES["schedule"]` already present, `SQL_SCHEMA_DOC`, `_KNOWN_EVENT_TYPES` + `EVENT_ENTITY_TYPES` gain the five `schedule.*` types, `SCHEDULE_STATUSES`). Coherence/RLS/vocabulary tests green (`test_schedule_api.py::test_schedule_schema_and_seeds`/`test_schedule_in_vocabulary`)
- `[x]` Task 2 — Transition seam `services/views/schedule.py` (`create_visits` w/ ≤12-week expansion + all-or-nothing overlap reject, `assign` w/ soft qual/availability warnings, `call_out` → linked replacement row + `replacement_schedule_id` payload, `cancel`, `set_outcome`; one event per transition, caller's tx; `ScheduleError` w/ `not_found`/`conflict` flags). `test_schedule_seam` green
- `[x]` Task 3 — Matching engine `services/views/matching.py` (`rank_candidates`: disqualifiers + weighted components w/ plain-language reasons/warnings, top 10, deterministic; shared `availability_covers`/`missing_qualification_names`/`week_hours*` helpers). `test_matching` green
- `[x]` Task 4 — Tools: safe `find_available_caregivers`, gated `record_call_out`/`assign_caregiver`, `create_schedule`/`cancel_schedule` rewired to the seam (create gains optional `resource_id`/`required_qualification_ids`/`repeat_weekly_until`/`notes`); labels; read-tool joins left-joined so open shifts surface. `test_schedule_tools` green
- `[x]` Task 5 — REST `routers/schedule.py` (wired in `main.py`): week board feed, visit create/expand + PATCH (edits/outcomes) + call-out/assign/cancel/candidates, roster list/PATCH (`resource.updated`), gated `notify` via `execute_tool`. `test_schedule_api` green (401 + RLS isolation covered)
- `[x]` Task 6 — Wrap-up: README Scheduling section; full pytest green (226 passed), frontend `npm run build` clean

**12b — Schedule board & call-out flow** (`.agent/plans/12b.schedule-board.md`): `[x]` Code complete (2026-07-18) — full pytest green (226), frontend build clean. Live browser + automation-run walk pending a running stack.
- `[x]` Task 1 — `lib/schedule.ts` (status meta, Monday week math, time fmt) + `lib/api.ts` types/methods + `/schedule` page shell (week nav ↔ `?week=`, one-fetch feed, Realtime debounce, nav entry `CalendarDays`, empty state)
- `[x]` Task 2 — `ScheduleBoard` grid (sticky name column + Mon–Sun, pinned Open-shifts row, hours-this-week, horizontal scroll) + `VisitBlock` chips (status tones from semantic tokens, "covering" hint) — day columns, no hour geometry
- `[x]` Task 3 — `VisitDrawer` (state-driven actions: call-out confirm → follows to replacement, `CandidateList` w/ reason chips + amber warnings, assign → gated-SMS notify prompt → queued chip → `/tasks`; reassign, cancel, outcomes; related-visit links; technical expander) + `VisitCreateDialog` (client/caregiver picker, date+times, required-qual chips, notes, repeat-weekly w/ client-side ≤12 cap)
- `[x]` Task 4 — `CaregiverDrawer` (contact/address/zip, language/trait tag editors, per-day availability editor, hours readout → `patchRosterMember` → one `resource.updated`) + Home `open_shifts` StatCard; `home.py` count + `test_home_api.py` delta/RLS case
- `[x]` Task 5 — Wrap-up: README Schedule board section + call-out recipe (WHEN `schedule.called_out` → `find_available_caregivers` → `create_task`/gated `send_sms`). Backend extension: `/candidates` now ranks `scheduled` visits too (reassign) — 409 only on terminal/called-out; `ScheduleBoard` payload gains `clients` for the create picker. Live automation-run + LangSmith walk pending a running stack

### Module 13: Automation Builder Enhancements
`[-]` Planned (2026-07-18) — ⚠️ Medium, single plan: `.agent/plans/13.automation-builder-enhancements.md` (fallback split: Tasks 1–4 builder work / Tasks 5–6 app sweep). Builds **after** Module 12 (user decision 2026-07-18) so the dropdown sweep also covers the schedule board's selects. User-locked: gate the entry IF section on event triggers; plain-language event labels (raw type as secondary text); verify-and-fix the reported empty IF-field-dropdown bug live; hand-rolled `ui/Select` (no new deps); full sweep of every native `<select>` (~28 sites incl. ScheduleBuilder, SchemaForm enums, DateTimePicker). No migrations, no env vars, no engine/recipe-format changes.

- `[ ]` Task 1 — `ui/Select.tsx` (options/groups, search, clearable, icons/dots/descriptions/mono, listbox ARIA + keyboard nav) + `eventTypeLabel`/`operatorLabel` in `lib/recipe.ts`; vitest `recipe.test.ts` + build clean
- `[ ]` Task 2 — Field-scope verify & fix: extract `fieldGroups` → `lib/fields.ts` + vitest scope suite; gated catalog-content pytest on the vocabulary; `FieldCombobox` hints + empty state; **live browser check of the IF field dropdown** (the reported bug) with root-cause fix if it reproduces
- `[ ]` Task 3 — Entry IF gated on event triggers (confirm-and-clear on trigger-type switch, hint line, edit-mode warning for saved non-event conditions) + `TriggerSentence`/`ScheduleBuilder` selects → `Select`
- `[ ]` Task 4 — Builder step sweep: `StepCard` (tool grouped Safe/Requires-approval, delay unit, function, wait event, model), `ConditionChips` operator plain labels, `SchemaForm` enum; `rg '<select'` clean in builder files
- `[ ]` Task 5 — App-wide sweep: tasks (priority dots), event log (labeled filters), leads/caregivers (filters, dialogs, info cards, stage dots), `DateTimePicker` month/year, Module 12b schedule components; `rg '<select' frontend/src` → zero
- `[ ]` Task 6 — Wrap-up: full pytest + vitest + build green; live draft→builder→save→run regression walk

### Module 14: External Services Connectors
`[-]` Planned (2026-07-18) — 🔴 Complex, split per the planning rule into three sub-plans. Parent: `.agent/plans/14.external-connectors.md`. Build order 14a → 14b → 14c (14a lands the shared sync loop + ingest seam). Planning-time research ran against the **live APIs**: WelcomeHome token verified (`/api/ping` 200, account 18754), full OpenAPI spec reviewed (no webhooks — export-CSV polling confirmed; live stage list captured for the stage map); GoTo client verified to reject `client_credentials` (auth-code bootstrap required). User-locked: WelcomeHome + GoTo + Gmail/GCal in scope, WellSky direct adapter deferred (data arrives via the WelcomeHome sync); one-way inbound + gated outbound actions only; in-app connector sync loop; GoTo via WebSocket bridge; Google via polling with sync cursors; creds in root `.env` (`WELCOMEHOME_API_KEY`, `GOTO_CONNECT_CLIENT_ID/SECRET` present).

**14a — WelcomeHome CRM sync** (`.agent/plans/14a.welcomehome-sync.md`):
- `[ ]` Task 1 — Config + `wh_client.py` (export CSV pager w/ Link cursors + rate respect, JSON reference endpoints) + offline fixtures; gated live ping test
- `[ ]` Task 2 — Migration `20260724000000_entities_crm_sync.sql` (leads `zip`/`address`/`background`, `lead_contacts` + RLS) + seam threading; gated schema tests
- `[ ]` Task 3 — Ingest-seam refactor (`ingest.py::ingest_payload`; route = verify → ingest); existing connector tests pass unmodified
- `[ ]` Task 4 — Connector sync loop (`sync.py`, SyncRunner registry, cursors in `connector_state`, `connector.sync_failed` isolation, lifespan + `NEXUS_CONNECTORS_*`); gated loop tests + uvicorn smoke
- `[ ]` Task 5 — WH mapping (`wh_map.py`: stage map, prospect/resident/influencer/activity mapping) + resolution update path (`updates_entity`, `UPDATERS`, stage moves via `update_lead_status`); offline + gated tests
- `[ ]` Task 6 — WH runner + `backfill_welcomehome.py` (idempotent, resumable) + `ingest_text` transcription ingestion; offline runner tests + **live backfill against the real account**
- `[ ]` Task 7 — Wrap-up: README/.env.example; full pytest; **live incremental walk** (WH stage change → lead status + `lead.stage_changed` + sequence fires within one poll)

**14b — GoTo Connect** (`.agent/plans/14b.goto-connect.md`):
- `[ ]` Task 1 — OAuth bootstrap (`scripts/goto_oauth.py`) + shared `oauth.py` refresh helper; **blocking ops: one-time browser consent → `GOTO_CONNECT_REFRESH_TOKEN` in .env**; gated live token test
- `[ ]` Task 2 — WS channel + call-events subscription manager (state/renewal in `connector_state`); empirically settle SMS-on-channel vs Messaging-API-poll fallback
- `[ ]` Task 3 — WebSocket bridge runner (`websockets` dep, reconnect/backoff → `ingest_payload`); fake-WS offline test + **live call → `call.completed` on the lead timeline**
- `[ ]` Task 4 — Real `send_sms` (`services/messaging/goto_sms.py`, gate unchanged); mocked tests + **live approved SMS delivery**
- `[ ]` Task 5 — Wrap-up: README bootstrap runbook; full pytest; live walks recorded

**14c — Gmail & Google Calendar** (`.agent/plans/14c.google-workspace.md`):
- `[ ]` Task 1 — Google OAuth bootstrap + `google_client.py` (httpx, shared TokenSource); **blocking ops: GCP OAuth client + consent → `GOOGLE_*` in .env**; gated live profile test
- `[ ]` Task 2 — Gmail poll runner (historyId cursor, no backfill, SENT filtered) + attributed-sender attachments → ingestion; offline + **live email w/ PDF → timeline + RAG**
- `[ ]` Task 3 — Real `send_email` (`gmail_send.py`, gate unchanged, `email.sent` event); mocked + live approved delivery
- `[ ]` Task 4 — Calendar poll runner (syncToken, 410 resync); offline + live event-change walk
- `[ ]` Task 5 — Calendar tools: safe `list_calendar_events`, gated `create_calendar_event` (+ `calendar.event.created`, calendar `external_ids`); gated tool tests + live chat-scheduled tour
- `[ ]` Task 6 — Wrap-up: README Google runbook; full pytest; LangSmith `connector_sync` spans verified
