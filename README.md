# Nexus Control Center

An operational control center for small businesses that unifies messy, cross-platform business data — CRM, phone service, line-of-business systems, email — into a single canonical source of truth, exposed through a conversational AI agent and a set of purpose-built interfaces (chat, ingestion, tasks, event log, workflows).

The core is built to be **business-agnostic**: interfaces, the MCP tool layer, the event/task system, and the workflow engine are shared scaffolding. What changes per client is the Postgres entity schema and any domain-specific connectors or decision harnesses layered on top. This first build validates the architecture against an in-home senior care business.

See [`PRD.md`](./PRD.md) for full scope, target users, and success criteria. See [`CLAUDE.md`](./CLAUDE.md) for build rules and conventions if you're developing this with Claude Code.

## What's Here

- **Chat** — threaded conversations with an AI agent that has retrieval access to unstructured business context (via RAG) and structured business data (via parameterized tools), and can take gated actions (send a message, create a task, trigger a workflow)
- **Ingestion** — manual document upload with chunking/embedding status
- **Control Center Home** — a single "needs attention" queue for pending tasks, paused workflow approvals, and flagged events
- **Tasks** — anything needing a human decision, created automatically or manually
- **Event Log** — an immutable audit trail of everything that happened across every connected system and every agent action
- **Workflows / Automations** — n8n-based automation, with custom nodes wrapping the same MCP tools the chat agent uses
- **Settings** — connector and agent configuration (env vars, no admin UI in this phase)

## Stack

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript + Vite + Tailwind + shadcn/ui |
| Backend | Python + FastAPI |
| Database | Supabase (Postgres + pgvector + Auth + Storage + Realtime) |
| LLM | Anthropic Messages API |
| Embeddings / Reranking | Voyage AI |
| Agent tooling | MCP server |
| Workflow automation | n8n |
| Observability | LangSmith |

## Architecture at a Glance

```
Frontend (React)
  chat · ingestion · control center · tasks · event log · workflows · settings
        │
        ▼
Backend (FastAPI)
  /api/chat        → Anthropic Messages API + MCP tools + conversation history
  /api/entities    → canonical business data CRUD
  /api/events       /api/tasks
  webhook receivers → CRM / phone / EHR / email → normalize → canonical entities
        │
        ▼
MCP Server (tool layer)
  search_documents (vector/hybrid search)
  get_<entity> / list_<entity>_by_<field> (parameterized structured reads)
  create_task / send_message / trigger_workflow (gated actions)
        │
        ▼
Data Layer
  Postgres: canonical entities (tenant-scoped, per-vertical schema)
          + pgvector document chunks (tagged to canonical entity IDs)
          + events (immutable) + tasks + pending_actions
  n8n: workflow automation, calling the same MCP tools
```

Every table is scoped by `tenant_id` with Row-Level Security enforced at the Postgres level. Every tool call, webhook, and agent action writes an entry to the immutable event log. Any tool that changes state visible outside the system defaults to a human-approval gate (`pending_actions` → `tasks`) rather than executing immediately.

## Module Sequence

0. Canonical Data Model (entities, tenancy, RLS, mapping/event/task tables)
1. Foundation Chat + Ingestion
2. Structured Data Access (parameterized tools + scoped read-only text-to-SQL)
3. MCP Server & External Connectors
4. Event Log
5. Approval Gate & Task System
6. Control Center Shell
7. Workflow Automation via n8n
8. Deterministic Matching/Decision Harness (generic engine, per-client configuration)
9. Custom Views / Plugin Apps — *out of scope for this repo, future per-client work*
10. Advanced RAG & Scale-Up (hybrid search, reranking, multi-format ingestion, sub-agents)

Track live status in [`PROGRESS.md`](./PROGRESS.md).

## Getting Started

### Prerequisites

- Python 3.11+ with `venv`
- Node.js (for the Vite frontend)
- A Supabase project (Postgres + pgvector + Auth + Storage + Realtime)
- Anthropic API key
- Voyage AI API key (embeddings + reranking)
- LangSmith API key
- n8n instance (self-hosted, for Module 7+)

### Environment Variables

All configuration is via environment variables — there is no admin UI in this phase.
Copy `.env.example` to `.env` and fill in the values from your hosted Supabase
project (Project Settings → API and → Database).

### Database Setup (Module 0)

The canonical schema is applied to a **hosted** Supabase project via the CLI
(local `supabase start` is not required — no Docker needed).

```bash
# 1. Install the Supabase CLI (Windows / scoop)
scoop bucket add supabase https://github.com/supabase/scoop-bucket.git
scoop install supabase

# 2. Link the repo to your hosted project (uses SUPABASE_DB_URL / DB password)
supabase link --project-ref <your-project-ref>

# 3. Apply the four core + entity migrations
supabase db push

# 4. Seed sample data (two tenants; idempotent — safe to re-run)
#    Either via the CLI's seed include, or with psql directly:
psql "$SUPABASE_DB_URL" -f supabase/seed.sql

# 5. Set NEXUS_TENANT_ID in .env to the demo tenant UUID (already the default
#    in .env.example): 00000000-0000-0000-0000-000000000001
```

### Running the Tests (Module 0)

```bash
cd backend
python -m venv venv
source venv/Scripts/activate      # Windows bash;  venv\Scripts\activate on cmd/PowerShell
pip install -r requirements.txt
cd ..
pytest backend/tests              # schema, RLS, events-immutability, vector
```

Tests skip cleanly if the Supabase env vars are absent, so collection is safe
before provisioning. They require the DB to be migrated and seeded first.

### Backend Role Setup (Module 1)

The FastAPI backend connects to Postgres as a dedicated **RLS-subject** login role
`nexus_app` (`nobypassrls`), never as `postgres` (which has `BYPASSRLS`) and never
with the service-role key. The `20260715014927_app_role.sql` migration creates the
role but deliberately sets **no password** (passwords are never committed). After
`supabase db push`, do this one-time ops step:

```bash
# 1. Set a strong password for nexus_app (connect as postgres via SUPABASE_DB_URL)
psql "$SUPABASE_DB_URL" -c "alter role nexus_app with password '<strong-password>';"

# 2. Put the Session Pooler URI (username nexus_app.<project-ref>) in .env:
#    NEXUS_APP_DB_URL=postgresql://nexus_app.<project-ref>:<pw>@<pooler-host>:5432/postgres
#    plus ANTHROPIC_API_KEY, VOYAGE_API_KEY, and (optional) LANGSMITH_API_KEY.
```

The `nexus_app`-gated tests (`test_app_role.py`, ingestion, retrieval, chat) skip
until `NEXUS_APP_DB_URL` is set; `test_app_role.py` proves the role sees zero rows
without the tenant GUC and is rejected on cross-tenant writes — i.e. the
postgres-BYPASSRLS hole is closed.

### Running the App (Module 1)

```bash
# Backend (from backend/, with venv active and .env filled in)
cd backend
python -m uvicorn app.main:app --reload --port 8000
# -> http://localhost:8000/healthz  ->  {"status":"ok"}

# Frontend (separate terminal)
cd frontend
cp .env.example .env          # fill VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY
npm install
npm run dev                   # -> http://localhost:5173  (proxies /api -> :8000)
```

Chat (default route `/`) streams responses over SSE with RAG citations; Ingestion
(`/ingestion`) is drag-and-drop upload with live status via Supabase Realtime.

### Connecting an MCP Client (Module 3a)

The backend exposes its tool registry (the same tools chat uses) over MCP at
`/mcp` (Streamable HTTP, stateless JSON). It's guarded by a static bearer token —
set `NEXUS_MCP_TOKEN` in `.env` (see `.env.example`); an unset token 401s every
request (fail closed). Every MCP-originated tool call writes an `events` audit row
with `source_system='mcp'`, distinguishing it from chat.

With the backend running, register it in Claude Code:

```bash
claude mcp add --transport http nexus http://localhost:8000/mcp \
  --header "Authorization: Bearer $NEXUS_MCP_TOKEN"
```

Then ask it to, e.g., list new leads — the answer comes from seed data via the
`list_leads` tool. (Module 7's n8n custom nodes consume this same endpoint.)

## Notes on Templating

This repo is designed so that a second deployment, in a different vertical, requires:
- A new entity schema (swap Module 0's business tables — `leads`/`clients`/`resources`/etc. — for the new vertical's equivalents)
- New connector adapters for that business's external systems
- Per-client configuration of the Module 8 decision harness

No changes should be needed to the core interfaces, the MCP tool layer's shape, the event/task system, or the workflow engine itself. If a change to those does turn out to be necessary when templating, that's a signal the core wasn't abstracted correctly and worth revisiting.
