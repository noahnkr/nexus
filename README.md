# Nexus Control Center

An operational control center for small businesses that unifies messy, cross-platform business data — CRM, phone service, line-of-business systems, email — into a single canonical source of truth, exposed through a conversational AI agent and a set of purpose-built interfaces (chat, ingestion, tasks, event log, automations, entity pipeline views).

The core is built to be **business-agnostic**: interfaces, the MCP tool layer, the event/task system, and the automations engine are shared scaffolding. What changes per client is the Postgres entity schema and any domain-specific connectors, pipeline views, or decision harnesses layered on top. This first build validates the architecture against an in-home senior care business.

See [`PRD.md`](./PRD.md) for full scope, target users, and success criteria. See [`CLAUDE.md`](./CLAUDE.md) for build rules and conventions if you're developing this with Claude Code.

## What's Here

- **Chat** — threaded conversations with an AI agent that has retrieval access to unstructured business context (via RAG) and structured business data (via parameterized tools), and can take gated actions (send a message, create a task, trigger an automation)
- **Ingestion** — manual document upload with chunking/embedding status
- **Control Center Home** — landing dashboard: at-a-glance counts, recent activity, quick actions
- **Tasks** — anything needing a human decision, created automatically or manually
- **Event Log** — an immutable audit trail of everything that happened across every connected system and every agent action
- **Automations Center** — a grid of WHEN → IF → THEN automations built on an in-app engine whose steps call the same MCP tools the chat agent uses; created via a recipe builder or described in natural language and drafted by an agent
- **Leads / Caregivers** — pipeline dashboard views: the lead marketing funnel and the caregiver hiring process as pre-defined stages with per-stage automated outreach, plus entity directories with event history and AI smart summaries
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
| Automations | Custom in-app engine (event listeners + cron scheduling, durable runs; no n8n) |
| Observability | LangSmith |

## Architecture at a Glance

```
Frontend (React)
  home · chat · ingestion · tasks · event log · automations · leads · caregivers · settings
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
  create_task / send_message / trigger_automation (gated actions)
        │
        ▼
Data Layer
  Postgres: canonical entities (tenant-scoped, per-vertical schema)
          + pgvector document chunks (tagged to canonical entity IDs)
          + events (immutable) + tasks + pending_actions
          + automations + automation_runs (durable WHEN/IF/THEN runs)
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
7. Core Automations Framework (WHEN/IF/THEN engine: triggers, cron, durable runs, MCP tool steps)
8. Automations Center (grid + recipe builder + agent-drafted automations)
9. Leads View & Marketing Funnel (pipeline dashboard, per-stage outreach sequences)
10. Caregivers View & Hiring Process (pipeline dashboard, accept/deny + scoring)
11. Deterministic Matching/Decision Harness (generic engine, per-client configuration)
12. Advanced RAG & Scale-Up (hybrid search, reranking, multi-format ingestion, sub-agents)

Track live status in [`PROGRESS.md`](./PROGRESS.md).

## Getting Started

### Prerequisites

- Python 3.11+ with `venv`
- Node.js (for the Vite frontend)
- A Supabase project (Postgres + pgvector + Auth + Storage + Realtime)
- Anthropic API key
- Voyage AI API key (embeddings + reranking)
- LangSmith API key

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

Home (default route `/`) is a light landing page — greeting, at-a-glance counts,
recent activity, and quick actions. Chat moved to `/chat`; it streams responses over
SSE with RAG citations and renders assistant replies as GFM markdown. Ingestion
(`/ingestion`) is drag-and-drop upload with live status via Supabase Realtime; Tasks
(`/tasks`) and Event Log (`/events`) round out the shell.

### Auth Setup (Module 6)

Every `/api` route is protected by Supabase Auth: the frontend signs in with email
+ password, and each request (including the chat SSE stream, file uploads, and
Realtime) carries the session's access token. The backend verifies it in
`deps.get_tenant_id` — accepting both the ES256 tokens Supabase Auth issues (via the
project JWKS) and legacy HS256 tokens (via `SUPABASE_JWT_SECRET`) — and scopes every
query to `app_metadata.tenant_id`. No valid token ⇒ 401; a valid token without a
tenant claim ⇒ 403. The two machine paths keep their own credentials and are **not**
JWT-gated: the webhook ingress verifies an HMAC signature, and `/mcp` a static
bearer (`NEXUS_MCP_TOKEN`).

There is no sign-up UI this phase — create the one office user in the Supabase
dashboard (a one-time ops step):

1. **Authentication → Add user**: enter the email + password, enable auto-confirm.
2. Attach the tenant claim so RLS can scope the user (run against `SUPABASE_DB_URL`):

   ```sql
   update auth.users
      set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb)
          || '{"tenant_id": "00000000-0000-0000-0000-000000000001"}'::jsonb
    where email = '<office-user-email>';
   ```

Then sign in at `/login`; the app redirects there automatically when signed out.
`NEXUS_TENANT_ID` is no longer read for the user surface — it remains only for the
machine paths (webhooks, `/mcp`), the seed, and the test harness.

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
`list_leads` tool.

### Connector Webhook Ingress (Module 3b)

External systems deliver events to a single ingress, `POST /api/webhooks/{source}`
(`source` ∈ `welcomehome`, `goto`, `wellsky`, `gmail`, `gcal`). Each inbound
event is verified, written to `events` as a raw receipt, then resolved to a
canonical entity via `external_ids` before anything else is written — matched to
an existing entity, auto-created (e.g. a new `lead.created`), or, when it can't be
resolved, turned into a plain-language review task. Poll/export sources (via
Module 7's scheduled automations, or manual triggers) re-POST into this same
ingress, so the core stays webhook-shaped.

The placeholder adapters verify a shared-secret HMAC: set `NEXUS_WEBHOOK_SECRET`
in `.env`; an unset secret 401s every request (fail closed). Each real connector
later swaps in its platform's own verification without changing the seam — the
real integration flow is documented in each adapter's docstring under
`backend/app/services/connectors/adapters/`.

Simulate a signed event (creates a lead from the WelcomeHome fixture):

```bash
python - <<'PY'
import hashlib, hmac, json, os, urllib.request
secret = os.environ["NEXUS_WEBHOOK_SECRET"].encode()
body = json.dumps({"event": "lead.created", "prospect": {
    "id": "WH-DEMO-1", "name": "Simulated Prospect",
    "email": "sim@example.com", "source": "welcomehome"}}).encode()
sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
req = urllib.request.Request(
    "http://localhost:8000/api/webhooks/welcomehome", data=body,
    headers={"Content-Type": "application/json", "X-Nexus-Signature": sig})
print(urllib.request.urlopen(req).read().decode())
PY
```

Then in Chat ask "any new leads today?" — the agent's `list_leads` answer includes
the webhook-created lead. Every call is auditable: `events` rows for
`webhook.received` and `lead.created`, and a `webhook_ingress` span in LangSmith.

### Approvals & Tasks (Module 5)

State-changing tools (`update_lead_status`, `update_client_status`,
`create_schedule`, `cancel_schedule`, `send_sms`, `send_email`) are **gated**: a
call queues a `pending_action` behind a high-priority review `task` instead of
running, and the model reports that a task was created (a queued call is a success,
not an error). Approving runs the tool through the same audited `execute_tool`
seam; rejecting cancels the task. `create_task` is safe and runs immediately (an
internal to-do with no outside effect). `send_sms`/`send_email` are placeholder
executions this phase (no external delivery until the automation modules wire
real connector credentials).

The gate lifecycle is fully auditable in `events`: `action.queued` → (`action.approved`
+ `tool.called` carrying `pending_action_id`) or `action.rejected`.

Endpoints (all tenant-scoped via RLS):

```
GET   /api/tasks?status=&priority=&cursor=&limit=50   list tasks (status is a
        comma-separated set, e.g. pending,in_progress); keyset pagination; each
        task embeds its pending_actions[]
POST  /api/tasks                                       create a task {title, description?, priority?, due_at?} → 201
PATCH /api/tasks/{id}  {status}                        transition a task (pending↔in_progress,
        either → done|cancelled); 409 on terminal states or while an action is pending
POST  /api/pending-actions/{id}/approve               approve → execute the queued tool
POST  /api/pending-actions/{id}/reject  {note?}       reject → cancel the task
```

No new env vars. `tasks` and `pending_actions` are in the Realtime publication, so
the **Tasks** page (nav → Tasks) live-updates: it lists tasks with status tabs
(Open / Done / Cancelled / All) and a priority filter, inline Approve/Reject cards
for queued actions, status transitions, manual task creation, and a "View history"
drill-down into the Event Log. In Chat, a gated call shows an amber "queued" chip
linking to the Tasks page.

### Automations Engine (Module 7a)

Automations are validated, declarative **WHEN / IF / THEN** recipes stored in the
core `automations` table and executed by an in-app engine (no n8n). A recipe is
JSON with three parts:

- **trigger** (WHEN) — `{"type":"event","event_type":…,"source_system":…?}`,
  `{"type":"cron","expression":"0 9 * * 1"}`, or `{"type":"manual"}`.
- **conditions** (IF) — an AND-list of field comparisons `{"field":…,"op":…,"value":…}`.
  Fields root at `trigger.` (the triggering event), `entity.` (the linked canonical
  row), or `context.` (accumulated step outputs). Operators: `eq neq gt gte lt lte
  contains not_contains exists not_exists`. Conditions are declarative only — no code,
  no LLM in the control path.
- **steps** (THEN, max 20, run in order) — `tool` (runs an MCP tool through the
  audited `execute_tool` seam; gated tools queue for approval and pause the run),
  `delay` (`minutes`/`hours`/`days`, parks the run), `condition` (a mid-sequence
  stop-guard; false ⇒ the run completes early), `function` (a safe pure computation
  from the function registry — `now`, `days_since`, plus vertical scoring fns), and
  `generate` (LLM content into `context`; `"model":"fast"` uses the cheap model).
  `{{path}}` templates in a tool `input`, function `args`, or generate `prompt`
  render from `{trigger, entity, context}`.

Each step commits in its own transaction (durable across waits/crashes), everything
the engine writes carries `source_system='automation'`, and the run lifecycle is
audited: `automation.run_started` → … → `automation.run_completed` /
`automation.run_failed` (fails also raise a plain-language review task) /
`automation.run_skipped` (concurrency: one active run per automation+entity). The
per-step `step_log` is the plain-language run trail. Recipes default to `paused` on
create; only `active` automations fire from triggers (Module 7b's loops).

Endpoints (all tenant-scoped via RLS; a bad recipe is a 422 with a plain-language
message):

```
GET    /api/automations?status=active|paused        list (with active-run counts)
POST   /api/automations                             create {name, description?, trigger,
         conditions?, steps?} → 201 (status defaults to paused)
GET    /api/automations/{id}                        full recipe
PATCH  /api/automations/{id}                        partial update; recipe changes
         revalidate; status flips active/paused
DELETE /api/automations/{id}                        204 (runs cascade)
POST   /api/automations/{id}/run  {entity_type?, entity_id?}
         manual "run now" — starts + advances synchronously, returns the run
         (may be completed / waiting / waiting_approval / failed); 409 if an active
         run already exists for this automation+entity
GET    /api/automations/{id}/runs                   run history (newest first)
GET    /api/automation-runs/{id}                    run detail (status, context, step_log, error)
```

Example — a "welcome a new lead" recipe (curl-runnable once you have a JWT):

```bash
curl -X POST http://localhost:8000/api/automations \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" -d '{
    "name": "Welcome a new lead",
    "trigger": {"type": "event", "event_type": "lead.created", "source_system": "welcomehome"},
    "conditions": [],
    "steps": [
      {"type": "generate", "model": "fast", "save_as": "msg",
       "prompt": "Write a one-line friendly welcome text to {{entity.name}} from Acme Home Care."},
      {"type": "tool", "tool": "send_sms",
       "input": {"to": "{{entity.phone}}", "body": "{{context.msg}}"}, "save_as": "sent"}
    ]
  }'
# then trigger it manually for a specific lead:
curl -X POST http://localhost:8000/api/automations/$ID/run \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"entity_type": "lead", "entity_id": "<lead-uuid>"}'
```

The gated `send_sms` step parks the run at `waiting_approval` with a task in the
Tasks page; approving it resumes the run to completion. No new **required** env vars;
`FAST_MODEL` overrides the cheap model (default `claude-haiku-4-5-20251001`).

**Background engine (Module 7b).** Triggers, scheduling, and wait-wakes run in an
in-process loop started in the FastAPI lifespan (one process, one pool — no worker
deployment). Each cycle runs four phases under the machine tenant:

- **event dispatcher** — polls `events` behind a durable `(created_at, id)` cursor
  (stored in `connector_state._automations`), starts a run for every `active`
  event-trigger automation whose `event_type`/`source_system` matches. On first run
  it initializes the cursor to the latest event (no history replay); automation-emitted
  events are never dispatched (automations can't trigger automations).
- **cron scheduler** — fires `active` cron automations when `next_fire_at ≤ now()`,
  advancing `next_fire_at` (via `croniter`) *before* running so a slow run can't
  double-fire. Activating a cron automation (or changing its expression) via PATCH
  arms `next_fire_at`.
- **waker** — resumes `waiting` runs whose `wake_at` has passed (delay steps).
- **recovery sweep** — re-advances any run stuck in `running` past a staleness
  threshold (a crash mid-advance; per-step transactions make re-entry safe) and
  arms any un-armed active cron automation.

Approving/rejecting a gated automation step resumes/cancels the parked run in the
same request. Optional env (all have defaults):

```
NEXUS_AUTOMATIONS_ENABLED=true        # false disables the loops (API + manual runs still work)
NEXUS_AUTOMATIONS_POLL_SECONDS=5      # cycle interval
NEXUS_AUTOMATIONS_STALE_MINUTES=10    # recovery threshold for stuck `running` runs
```

## Notes on Templating

This repo is designed so that a second deployment, in a different vertical, requires:
- A new entity schema (swap Module 0's business tables — `leads`/`clients`/`resources`/etc. — for the new vertical's equivalents)
- New connector adapters for that business's external systems
- New pipeline-view content (stages, outreach sequences, scoring) on the shared entity-dashboard pattern
- Per-client configuration of the Module 11 decision harness

No changes should be needed to the core interfaces, the MCP tool layer's shape, the event/task system, or the automations engine itself. If a change to those does turn out to be necessary when templating, that's a signal the core wasn't abstracted correctly and worth revisiting.
