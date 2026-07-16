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
`[-]` Built (2026-07-14) — see `.agent/plans/1.foundation-chat-ingestion.md`. Code complete; live validation pending the one-time `nexus_app` ops step + API keys.

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
`[ ]` Not started.

### Module 3: MCP Server & External Connectors
`[ ]` Not started. Default 🔴 Complex — break into sub-plans.

### Module 4: Event Log
`[ ]` Not started.

### Module 5: Approval Gate & Task System
`[ ]` Not started. Default 🔴 Complex — break into sub-plans.

### Module 6: Control Center Shell
`[ ]` Not started.

### Module 7: Workflow Automation via n8n
`[ ]` Not started.

### Module 8: Deterministic Matching/Decision Harness
`[ ]` Not started. Default 🔴 Complex — break into sub-plans.

### Module 9: Custom Views / Plugin Apps
Deferred — explicitly out of scope for this repo (see PRD Out of Scope).

### Module 10: Advanced RAG & Scale-Up
`[ ]` Not started.
