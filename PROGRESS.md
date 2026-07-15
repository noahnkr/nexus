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
`[-]` Planned (2026-07-14) — see `.agent/plans/1.foundation-chat-ingestion.md`. Build not started.

- `[x]` Plan written and scope locked (basic RAG in chat, persisted threads, 4-format lightweight parsers, single ordered plan)
- `[ ]` Task 1 — Migrations: `nexus_app` RLS-subject role, `chat_threads`/`chat_messages` + RLS, Storage bucket + Realtime publication (blocking ops step: role password + new `.env` keys)
- `[ ]` Task 2 — Backend app skeleton: FastAPI, psycopg pool, tenant-scoped connection dependency, `/healthz`
- `[ ]` Task 3 — Parsing layer (PDF/DOCX/HTML/MD behind swappable interface) + chunking
- `[ ]` Task 4 — Voyage embeddings service + LangSmith wiring
- `[ ]` Task 5 — Ingestion pipeline + documents API (upload → Storage → BackgroundTasks → status transitions + `events` rows)
- `[ ]` Task 6 — Retrieval service (pgvector cosine top-8, RLS-filtered)
- `[ ]` Task 7 — Chat API: threads CRUD + SSE streaming with RAG, prompt caching, realtime-token endpoint
- `[ ]` Task 8 — Frontend scaffold: Vite + React + Tailwind + shadcn/ui, router, AppShell, api/sse/supabase libs
- `[ ]` Task 9 — Ingestion page: dropzone, document table, live status via Supabase Realtime
- `[ ]` Task 10 — Chat page: thread list, streaming messages, source citations, history restore
- `[ ]` Task 11 — Wrap-up: README getting-started, full test suite green, commit

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
