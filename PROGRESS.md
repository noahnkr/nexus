# Progress

Module-by-module build status for the Nexus Control Center. Claude Code reads this file at the start of a session to understand where the project stands; update the relevant tasks as work completes. Module numbering follows the PRD's module list (0–10).

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
- `[~]` Task 4 — Wrap-up done (docs + tests green); live end-to-end browser/LangSmith walk still pending a running stack (gated action stalls until approved; full `action.queued → approved → tool.called` trail in Event Log + LangSmith)

### Module 6: Control Center Shell & Visual Overhaul
`[ ]` Not started.

### Module 7: Workflow Automation via n8n
`[ ]` Not started.

### Module 8: Deterministic Matching/Decision Harness
`[ ]` Not started. Default 🔴 Complex — break into sub-plans.

### Module 9: Custom Views / Plugin Apps
Deferred — explicitly out of scope for this repo (see PRD Out of Scope).

### Module 10: Advanced RAG & Scale-Up
`[ ]` Not started.
