# Nexus Control Center

An operational control center for small businesses that unifies messy, cross-platform business data — CRM, phone service, line-of-business systems, email — into a single canonical source of truth, exposed through a conversational AI agent and a set of purpose-built interfaces (chat, ingestion, tasks, event log, automations, entity pipeline views).

The core is built to be **business-agnostic**: interfaces, the MCP tool layer, the event/task system, and the automations engine are shared scaffolding. What changes per client is the Postgres entity schema and any domain-specific connectors, pipeline views, or decision harnesses layered on top. This first build validates the architecture against an in-home senior care business.

See [`PRD.md`](./PRD.md) for full scope, target users, and success criteria. See [`CLAUDE.md`](./CLAUDE.md) for build rules and conventions if you're developing this with Claude Code.

## What's Here

- **Chat** — threaded conversations with an AI agent that has retrieval access to unstructured business context (via RAG) and structured business data (via parameterized tools), and can take gated actions (send a message, create a task, trigger an automation)
- **Knowledge** — manual document upload with chunking/embedding status, plus the free-text instructions that shape how the assistant writes
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
npm run build                 # type-check + production build
npm run test                  # vitest unit tests (the template tokenizer, Module 11)
```

Home (default route `/`) is a light landing page — greeting, at-a-glance counts,
recent activity, and quick actions. Chat moved to `/chat`; it streams responses over
SSE with RAG citations and renders assistant replies as GFM markdown — including
document-style answers (headings, lists, tables) when you ask for one, with wide
tables scrolling inside the bubble. The send button becomes a **stop** button while a
reply streams; stopping keeps whatever was written (annotated "— stopped") and
persists it, so the conversation stays valid and the next question answers normally.
Knowledge
(`/knowledge`, formerly `/ingestion`, which now redirects) is drag-and-drop upload
with live status via Supabase Realtime; Tasks (`/tasks`), Event Log (`/events`), and
Settings (`/settings`) round out the shell.

The sidebar collapses to an icon rail (remembered per browser in
`localStorage["nexus.sidebar"]`). Below `md` it becomes an overlay drawer behind a
hamburger in a slim top bar, and the core pages — Home, Chat, Tasks, Leads,
Caregivers, Event Log, Knowledge, Settings — lay out for small screens: grids
collapse, filter rows wrap, tables scroll inside their own containers, and drawers
go full-width. The schedule board and automation builder stay desktop-first; they
scroll horizontally rather than reflow.

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

### Workspace Settings & Agent Instructions (Module 15b)

`tenant_settings` is a core table holding one jsonb row per tenant of *user-facing*
preferences. It is deliberately not a config store: infra config and credentials
stay in env vars, nothing here is a secret, and the machine paths (`/mcp`, webhooks)
never read it.

```
GET   /api/settings     every whitelisted key, defaults filled in
PATCH /api/settings     partial update; 422 on an unknown key or invalid value
```

Keys are whitelisted in `services/settings.py`, which owns each one's default,
validation, and audit label — adding a preference needs no migration:

| key | v1 rule |
| --- | --- |
| `workspace_name` | text ≤ 80; shown in the Home greeting |
| `agent_instructions` | text ≤ 4000; appended to the chat system prompt |
| `agent_tone` | `balanced` (default) \| `professional` \| `friendly` \| `concise` |

Every write logs a `settings.updated` event naming the changed **keys only** —
never their values, since instructions are free text an owner may treat as private.

**How instructions reach the model.** `build_system()` returns the system array for
a turn: `PERSONA` first and unmodified, then — only when instructions or a
non-balanced tone are set — a second block framed as *"Follow these preferences
where they don't conflict with the rules above"*. `cache_control` sits on the last
block so the whole prefix is cached. The ordering is the safety property: tenant
text can shape tone and content, never the approval gate or tool semantics.

Edit instructions at `/knowledge?tab=instructions`; `/settings` covers profile
(display name and password, both via Supabase Auth), workspace name, and theme.

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
POST  /api/pending-actions/{id}/approve  {tool_input?}  approve → execute the queued
        tool; the optional tool_input carries approver edits (see below)
POST  /api/pending-actions/{id}/reject  {note?}       reject → cancel the task
```

No new env vars. `tasks` and `pending_actions` are in the Realtime publication, so
the **Tasks** page (nav → Tasks) live-updates: it lists tasks with status tabs
(Open / Done / Cancelled / All) and a priority filter, status transitions, manual
task creation, and a drill-down into the Event Log. In Chat, a gated call shows an
amber "queued" chip linking to the Tasks page.

**Task drawer & approve-with-edits (Module 15a).** Task cards are summaries —
type icon and label ("Text message", "Scheduling"), status, and an "awaiting your
approval" chip. Clicking one opens a right-side drawer that renders the queued call
as labeled fields (*To*, *Message*, *Subject*) instead of raw JSON; the payload
survives only in a collapsed "technical detail" expander at the bottom of the drawer.

A tool may declare `editable_fields` on its `ToolDef` — currently `send_sms.body`
and `send_email.subject`/`body`. Those fields render as inputs in the drawer, so an
office user can fix a typo in a drafted message and click **Approve with edits**
instead of rejecting and re-asking. The API re-validates every edit against the
tool's `editable_fields` (422 on any other key, or on blank text, with the action
left pending), applies it to the stored `tool_input` *before* execution — one
execution path, unchanged — and records the change on the `action.approved` event as
`edited`, `edited_fields`, and the agent's `original_input`.

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

### Automations Center (Module 8)

The Automations Center is the UI over the engine — nav → **Automations**. It only
*manages* recipes; every effect still runs behind the M7 API.

- **Grid** (`/automations`) — a card per automation with a plain-language trigger
  line (`describeRecipe()`), an amber "requires approval" chip when any step calls a
  gated tool, active-run and last-run lines, pause/resume, and delete. Live via
  Realtime on `automations` + `automation_runs`.
- **Detail** (`/automations/{id}`) — the recipe rendered read-mode (WHEN sentence,
  IF chips, THEN step cards; raw JSON behind a toggle) plus run history. A run row
  opens a timeline drawer built from `step_log`, with the accumulated `context`
  behind a technical expander and a **Cancel run** button for active runs.
- **Builder** (`/automations/new`, `/automations/{id}/edit`) — the monday.com-style
  **sentence + step-list** builder: an editable WHEN line (event, or a **schedule
  built from dropdowns** — frequency + day + time, emitting a standard cron
  expression — or manual), IF condition chips with a **field-path autocomplete**
  (server-provided `entity.<col>` / `trigger.*` suggestions + this run's context
  keys), and a reorderable list of THEN step cards whose forms are generated from
  each tool/function's JSON Schema (with a `{{template}}` inserter on text fields).
  Step types: run a tool, write with AI, wait a fixed delay, **wait until an event
  happens** (with optional timeout), only-continue-if, and compute a value — see
  the **`formula`** function under Module 15c for lead-value / applicant-fit
  scoring. The server is the validator of
  record — a 422 renders inline; editing a definition with runs in flight returns a
  409 with a "cancel runs & save" path.
- **Describe → draft → review** — on the create page, describe the automation in
  plain language and the agent drafts a complete recipe that prefills the builder
  for review. **Agent drafts are never persisted** (CLAUDE.md): the draft endpoint is
  read-only w.r.t. the database and the standard create path is the only writer.

Backend additions (all tenant-scoped via RLS):

```
POST /api/automation-runs/{id}/cancel   cancel an active run (waiting_approval routes
        through the approvals seam so action + task + run resolve together); 409 terminal
GET  /api/automations/vocabulary        tools (+schema/safety/label), functions, operators,
        event types (observed ∪ core-known, automation-sourced excluded), field roots,
        and field_suggestions (entity.<col> + trigger.* paths for the builder autocomplete)
POST /api/automations/draft {description}
        agent-drafted, Pydantic-validated, UNSAVED recipe (one retry on validation
        failure); 503 without ANTHROPIC_API_KEY, 422 if it can't produce a valid recipe
```

`GET /api/automations` list rows are enriched with `active_runs`, `last_run`, and
`requires_approval`; `GET /api/home/summary` gains an `automations` block
(`active`, `runs_today`, `failed_today`) driving a Home StatCard. Drafting needs
`ANTHROPIC_API_KEY`; no other new env vars.

#### Field tokens & calculations (Module 11)

The builder's field surface is trigger-aware and plain-language. `GET
/api/automations/vocabulary` also returns a **`field_catalog`**: the five core
trigger fields (labeled), observed `trigger.payload.*` keys **grouped per event
type**, `entity.*` fields **per entity type** (with a seam-supplied record label —
"Lead", "Applicant"), and an event→entity map. From it, every template-accepting
input renders `{{path}}` references as **atomic, labeled chips** (a field picker
grouped by *the selected trigger's* actual fields inserts them at the caret — the
user never types a dotted path), and read-mode surfaces show "…to Phone" instead of
`{{trigger.payload.phone}}`. The stored recipe format is unchanged — chips are a
view over the same `{{path}}` strings, so existing recipes and the draft agent are
untouched. The `function` step is presented as **"Run a calculation"** (its editor
landed in Module 15c, below — M11b shipped the step type but left the args on the
generic schema form). New function: **`days_until`** (credential-expiry /
upcoming-date automations). Condition *values*
are now template-rendered by the engine (an unresolvable value makes the condition
false, never a run failure). Frontend adds a `vitest` unit suite for the tokenizer
(`npm run test`); no new backend env vars or migrations.

### Leads View (Module 9)

The first vertical dashboard view — nav → **Leads**. The *pattern* (entity
directory + profile + funnel strip + per-stage sequences + metrics) is core and
M10 re-instantiates it for caregivers; the *content* (stages, outreach steps, the
router) is the re-templating seam (`backend/app/services/views/`,
`routers/leads.py`, `frontend/src/lib/leads.ts`, the leads pages/components).

- **Directory** (`/leads`) — a clickable **funnel strip** (per-stage counts + a
  sequence chip) filtering the table below, conversion **metrics** widgets (in
  pipeline, conversion rate, new this week, avg days-to-convert, top sources), a
  source filter and search, and a **New lead** dialog. Live via Realtime on
  `leads`. Stage moves happen in the profile, not the table.
- **Profile** (`/leads/{id}`) — a **cached AI smart summary** (the first open
  generates + persists it in the core `entity_summaries` table; later opens serve
  the cached row instantly; a **Regenerate** button refreshes on demand; a quiet
  notice without `ANTHROPIC_API_KEY`), an inline-editable info card with a **stage
  selector**, requirements behind a technical expander, and a compact **entity
  timeline** of the lead's events.
- **Per-stage sequences** (`/leads/stages/{stage}/sequence`) — a constrained
  outreach builder that composes M8's step/condition components with a **fixed**
  trigger sentence ("When a lead enters *Contacted*"). A sequence is an ordinary
  M7 automation tagged with the core `automations.binding` jsonb; the engine,
  approval gate, run history, and Automations Center all apply unchanged. The
  Center shows a binding chip ("Leads · Contacted") and routes a bound recipe's
  Edit back to this builder. New sequences start **paused**. **Advancing a lead's
  stage cancels the prior stage's in-flight sequence** (a generic, binding-driven
  supersede), so a lead that moves up quickly never receives a colder stage's
  message.

Stages are `leads.status` values (no new table); the label/order/terminal config
lives in the seam. Lead writes are human REST writes (`source_system='user'`) —
create + stage moves + basic-field edits; every stage move emits
`lead.stage_changed` (which per-stage sequences trigger on). **No delete** by
design (a lead ends as converted/lost, keeping funnel history honest).

The binding is a **core**, business-agnostic mechanism: an `automations.binding`
jsonb column (`{"view":…,"stage":…}`) with a partial unique index enforcing one
sequence per `(tenant, view, stage)`. Core validates binding *shape* only and
never interprets stage names — M10 binds `{"view":"caregivers",…}` with zero
schema work.

Backend additions (all tenant-scoped via RLS):

```
GET   /api/leads?status=&source=&q=&limit=&offset=  directory list + total (offset paging)
GET   /api/leads/facets                 distinct sources + regions for filters/selectors
GET   /api/leads/metrics                funnel conversion metrics (all five stages, rate,
        new-this-week, avg-days-to-convert, top sources)
POST  /api/leads {name,phone?,email?,source?,region_id?}   create (status always 'new');
        emits lead.created; 422 on missing name / bad region
GET   /api/leads/{id}                   full row + region_name
PATCH /api/leads/{id}                   partial edit; a status change emits lead.stage_changed,
        other fields emit one lead.updated; no-op emits nothing; NO delete route
GET   /api/leads/{id}/summary           cached AI smart summary (generates + caches on
        first call, then serves the cache); 503 without a cache and no API key
POST  /api/leads/{id}/summary/regenerate  force a fresh summary and overwrite the cache
GET   /api/automations?view=leads       bound sequences for a view; AutomationOut.binding
        returned everywhere; create/PATCH accept binding (409 on a duplicate stage)
```

No new frontend deps or env vars. Smart summaries need `ANTHROPIC_API_KEY`.

### Caregivers View (Module 10)

The second and final sanctioned vertical view — nav → **Caregivers** — the
caregiver-recruiting pipeline. It re-instantiates the Module 9 pattern (directory +
profile + funnel strip + per-stage sequences + metrics) for a **new entity type**,
proving the pattern is core while content is seam. The structural difference from
Leads: applicants don't exist in the base schema at all, so M10 adds the
`applicants` entity end-to-end plus an **atomic hire-promotion** onto the caregiver
roster (`resources`). Seam files: `services/views/caregivers.py`,
`routers/applicants.py`, `frontend/src/lib/caregivers.ts`, the caregivers
pages/components, and the `applicants` entity migration.

- **Directory** (`/caregivers`) — a clickable **funnel strip** (six stages, each
  with a sequence chip) filtering the table, hiring **metrics** widgets (in
  pipeline, hire rate, new this week, avg days-to-hire, top sources), a source
  filter and search, and a **New applicant** dialog with qualification/region
  multi-selects. Live via Realtime on `applicants`.
- **Profile** (`/caregivers/{id}`) — a **cached AI hiring summary**, an
  inline-editable info card (contact/source + quals/regions chips + notes) with a
  **stage selector**, availability behind a technical expander, an **entity
  timeline**, and — after a hire — a success banner naming the created caregiver.
- **Stages**: `applied → screening → interview → offer → hired`, terminal
  `rejected`. **Moving an applicant to `hired` atomically creates a `resources`
  (caregiver) row** — copying name/contact/qualifications/regions/availability,
  stamping `resources.applicant_id` provenance, and emitting `resource.created` —
  in the same transaction as the stage move (the leads→clients precedent). Re-hiring
  never duplicates the caregiver. Both the human REST route and the gated
  `update_applicant_stage` tool go through the single `move_stage()` path, so a
  chat/MCP-approved move and a UI move are indistinguishable in the timeline.
- **Per-stage sequences** (`/caregivers/stages/{stage}/sequence`) — the *same*
  shared stage-sequence builder as Leads, driven by the caregivers view config.
  **Every stage — including `rejected` — carries a sequence chip** (the deliberate
  divergence from leads' chip-less `lost`): the PRD's automated accepted/denied
  emails are the marquee use case. Sequences are ordinary bound automations
  (`{"view":"caregivers","stage":…}`), so the engine, approval gate, and Center
  apply unchanged.

Applicant writes are human REST writes (`source_system='user'`) — create + stage
moves + basic/quals/regions/notes edits; every stage move emits
`applicant.stage_changed`. **No delete** by design. Scoring is deferred to Module 11
(no score column/function/UI this module).

Backend additions (all tenant-scoped via RLS):

```
GET   /api/applicants?stage=&source=&q=&limit=&offset=  directory list + total
GET   /api/applicants/facets            distinct sources + regions + qualifications
GET   /api/applicants/metrics           hiring metrics (all six stages, hire rate,
        new-this-week, avg-days-to-hire, top sources)
POST  /api/applicants {name,phone?,email?,source?,qualification_ids?,region_ids?}
        create (stage always 'applied'); emits applicant.created; 422 on missing name / bad ref
GET   /api/applicants/{id}              full row + resolved qualification/region names
PATCH /api/applicants/{id}              partial edit; a stage change routes through
        move_stage() (emits applicant.stage_changed + hired-promotion), other fields emit
        one applicant.updated; no-op emits nothing; NO delete route
GET   /api/applicants/{id}/summary      cached AI hiring summary; 503 without cache and no key
POST  /api/applicants/{id}/summary/regenerate   force a fresh summary
```

New agent tools: `list_applicants`, `get_applicant` (read), and gated
`update_applicant_stage` (delegates to `move_stage()`). No new frontend deps or env
vars. Smart summaries need `ANTHROPIC_API_KEY`.

### Smart Staffing — Scheduling backend (Module 12a)

The scheduling API turns the one `schedules` table into an operational board. A visit
is a caregiver–client assignment over a window; the same table now also holds
**open shifts** (`status='open'`, `resource_id` null — a visit nobody holds yet) and
**called-out** visits (`status='called_out'` — the original is retained so "who called
out" stays queryable, and a linked `open` replacement is created via
`replaces_schedule_id`). Coherence CHECKs keep status and caregiver-presence honest:
`open ⇒ no caregiver`, `scheduled/called_out/completed/no_show ⇒ a caregiver`.

Every status change goes through one transition seam
(`services/views/schedule.py`) — REST routes and gated tools both delegate, so a board
click and a chat/MCP-approved action leave the same events
(`schedule.created` / `assigned` / `called_out` / `cancelled` / `updated`). Nothing
else writes schedule state.

Matching (`services/views/matching.py`) is deterministic and explainable — no LLM in
the ranking. `rank_candidates` disqualifies caregivers missing a required
qualification, holding an overlapping visit, or being the one who just called out,
then scores the rest with plain-language reasons/warnings:

| Signal | Weight |
| --- | --- |
| Availability fit (window inside a declared weekday range) | +30 |
| Same ZIP as the client | +20 |
| Client ZIP within one of the caregiver's regions | +12 |
| Continuity (per completed past visit with this client, capped) | +5 each, cap +20 |
| Shares a language with the client | +10 |
| Matches a client preference tag (per match, capped) | +5 each, cap +10 |
| Light schedule this week (< 20h) | +5 |
| Would push the caregiver over 40h this week | −15 |

Weights are constants in the seam file (one client, explainability over tunability —
a re-template swaps the file wholesale). ISO-week hours (`hours_this_week`, the load
component) share one SQL definition between the roster payload and the matcher.

```
GET   /api/schedule?week=YYYY-MM-DD   board for the Mon–Sun window:
        {week_start, visits[] (client/resource + resolved qual names), caregivers[]
         (full roster + hours_this_week)}; cancelled visits omitted
POST  /api/schedules {client_id,resource_id?,start_time,end_time,
        required_qualification_ids?,notes?,repeat_weekly_until?}
        create a visit (assigned) or open shift; repeat weekly (≤12 extra rows,
        all-or-nothing); 201 with every created row
PATCH /api/schedules/{id}             edit window/notes/required-quals on open/scheduled,
        or record an outcome (completed|no_show via set_outcome); other statuses refused
POST  /api/schedules/{id}/call-out    scheduled → called_out + linked open replacement
POST  /api/schedules/{id}/assign {resource_id}   fill/reassign; warnings on qual/
        availability gaps (non-blocking), 409 on a hard time conflict
POST  /api/schedules/{id}/cancel      terminal verb (there is NO delete)
GET   /api/schedules/{id}/candidates  ranked caregivers for an open shift; 409 otherwise
GET   /api/roster?week=YYYY-MM-DD     caregiver roster + hours_this_week
PATCH /api/roster/{id}                edit contact/address/zip/languages/traits/
        availability; emits one resource.updated naming changed fields
POST  /api/schedules/{id}/notify {resource_id,message}   text a caregiver
```

`notify` is **gated even from a human click**: `send_sms` is a system-executed
external effect, so it runs through `execute_tool` and its approval gate (one seam,
one audit trail) and returns the queued action id — the human-UI exemption covers
entity record writes, not outbound messaging.

New agent tools: safe `find_available_caregivers` (ranks for a shift or an ad-hoc
window — usable from chat/MCP and as an automation step), gated `record_call_out` and
`assign_caregiver`; `create_schedule`/`cancel_schedule` now delegate to the seam. The
`schedule.called_out` event (payload carries `replacement_schedule_id`) is the trigger
for call-out automations. No new frontend deps or env vars.

### Formula steps & manual runs (Module 15c)

**`formula` function.** The "Run a calculation" step now takes a real arithmetic
expression instead of the `weighted_score` weights/inputs objects that fell through
to raw-JSON textareas:

```
({{trigger.record.hourly_rate}} + 2) * 1.5
round({{entity.visits_last_month}} / 4, 1)
```

Grammar: decimal numbers, `+ - * /`, parentheses, unary minus, and
`round(value[, digits])`. Field references are ordinary `{{templates}}` — the
engine substitutes them before the function runs, so every referenced field must
hold a number.

It is evaluated by a hand-rolled tokenizer + recursive-descent parser
(`services/automations/formula.py`) — **no `eval`, no `ast`**. The expression comes
from a recipe a non-technical user typed, so it is untrusted input on the
automation control path; a parser can only ever produce a number. Errors are plain
language ("'pending' is not a number", "Division by zero") and fail the run per M7
semantics. `lib/formula.ts` mirrors the grammar in the builder for live validation
only — the backend parser is the authority at run time.

`weighted_score` — the old weights/inputs function — was **retired** in the same
change. No stored recipe referenced it, so nothing needed migrating; a recipe that
still names it now fails validation rather than running silently against a missing
function.

**Manual runs.** A manual-trigger automation has no trigger to be "active" for —
`POST /api/automations/{id}/run` has always ignored `status` — so the grid and
detail page now show a neutral **Manual** badge and a **Run** button instead of a
pause toggle that did nothing.

**`run_automation` tool** lets chat and MCP start one: *"run the Score this lead
automation"*. It is **safe** (starting a run has no direct external effect; a gated
step inside it still parks for approval) and refuses three ways, all in plain
language: unknown name (the message lists what *can* be run), a non-manual trigger,
and — extending the automations-don't-trigger-automations rule to the tool layer —
any call arriving with `source_system='automation'`. It is deliberately absent from
the builder's step palette for that last reason, while chat and MCP see it normally.

The run is created **deferred** (`status='waiting'`, `wake_at=now()`): a tool
handler executes inside `execute_tool`'s savepoint on an uncommitted transaction,
and `advance_run` opens its own per-step transactions that wouldn't see the row. The
M7b waker picks it up on its next poll, so the run starts a few seconds later on
machinery that already exists.

### Schedule board (Module 12b)

`/schedule` (nav **Schedule**) is the week board over the 12a API — caregivers as
rows, a pinned **Open shifts** row on top, visits as status-tinted day-column chips
(no hour-scaled geometry — visits are 2–10h). The week round-trips to
`?week=YYYY-MM-DD` (Monday-normalized) and refetches on Supabase Realtime `schedules`
changes. Everything reads from one `GET /api/schedule?week=` fetch.

Clicking a visit opens the **visit drawer**, whose actions follow the visit's state:

- **Open shift** → a ranked **candidate list** (name, score badge, plain reason
  chips, amber warnings). *Assign* fills it, then offers a prefilled **"Text ⟨name⟩
  about this shift?"** prompt → *Queue text* runs `send_sms` through the approval gate
  and shows an amber "queued for approval" chip linking to `/tasks`.
- **Scheduled (future)** → **Call out** (confirm → opens a replacement shift and the
  drawer follows to it), **Reassign** (same candidate list), **Cancel**.
- **Scheduled (past)** → **Mark completed** / **No-show**.
- **Called-out / terminal** → read-only, with a link chip to the replacement (or the
  original it covers).

The **New visit** dialog creates an assigned visit or, with the caregiver left blank,
an open shift; **Repeat weekly until** expands the series server-side (≤12 extra
visits, mirrored with a client-side cap). Clicking a caregiver's name opens the
**caregiver drawer** — the one roster-editing surface (contact, address/ZIP,
languages/traits tags, and per-day availability in the `{"mon":["08:00-16:00"]}`
shape); saving emits one `resource.updated`. Home gains an **Open shifts** stat card
(`open_shifts` = future unfilled visits) deep-linking to the board.

**Call-out automation recipe** (build it in the M8 builder — recipes aren't seeded).
The `schedule.called_out` event fires when a caregiver drops a visit, and its payload
carries `replacement_schedule_id` (the new open shift). A basic dispatch recipe:

- **WHEN** event `schedule.called_out`
- **THEN** ①  tool `find_available_caregivers` with `schedule_id` =
  `{{trigger.payload.replacement_schedule_id}}` (safe — its result lands in run
  context), then ②  `create_task` titled e.g. *"Cover {{entity.client_id}}'s shift —
  try {{steps.1.candidates.0.name}}"* so a coordinator sees the top candidate.

The "AI dispatch" extension swaps step ②'s `create_task` for a gated `send_sms` to
`{{steps.1.candidates.0.phone}}` — still queued for approval, so a human confirms
before any text goes out. The M11 token picker resolves the
`{{trigger.payload.replacement_schedule_id}}` and `{{steps.N.candidates.0.*}}` paths.

### Client & care oversight — backend (Module 16a)

The clients surface is the fourth sanctioned vertical view (after Leads,
Caregivers, and the Schedule board). Its content seam is
`backend/app/services/views/clients.py` + `backend/app/routers/clients.py`.

**Statuses.** Clients are `active` / `hospital_hold` / `discharged`. This replaces
the M0 `active`/`paused`/`ended` set (data-migrated in
`20260727000001_entities_client_oversight.sql`; the old values are now rejected by
the CHECK). A client is never deleted — a status ends them and the history stays.
Every status write goes through the seam's `change_status()`, which emits
`client.status_changed`; the REST `PATCH /api/clients/{id}` and the gated
`update_client_status` tool both delegate, so a UI click and a chat-approved change
are indistinguishable in the timeline.

**Census math** (`census_metrics`, one seam function, deterministic SQL — no LLM
anywhere near the numbers). The window is the Monday week the Schedule board uses:

| Number | Definition |
| --- | --- |
| `authorized_hours` | Σ `clients.authorized_hours_per_week` over **active** clients |
| `scheduled_hours` | Σ scheduled duration of the week's `scheduled`/`completed`/`no_show`/`called_out` visits |
| `delivered_hours` | Σ over **completed** visits of the *actual* clocked duration when both EVV stamps exist, else the scheduled window |
| `open_hours` | Σ duration of the week's unfilled `open` shifts (reported separately, so a staffing gap can't hide inside "scheduled") |
| `leakage_hours` | `max(authorized − delivered, 0)` — hours the business is paid for but did not deliver |
| `delivery_rate` | delivered ÷ authorized as a %, `null` when authorized is 0 |

`client_week_hours()` is the same math scoped to one client, so the profile can
never disagree with the census strip.

**EVV-lite.** `schedules.check_in_at` / `check_out_at` are the clock stamps, written
only by the schedule seam's `check_in` / `check_out`. Check-**out** also completes
the visit (a caregiver clocking out *is* the visit finishing, and the clocked
duration becomes its delivered hours); `set_outcome` remains for manual bookkeeping
when no clock data exists. Late/missed are **computed at read time** by
`views/clients.evv_flag()` — a `scheduled` visit with no check-in reads `late` after
a 15-minute grace and `missed` past its end time. There is no stored flag and no
detector loop, so a badge can never survive the caregiver finally clocking in.
`no_show` stays the explicit human-recorded terminal status. Connector-fed clock-ins
(telephony, WellSky) land in the same columns via Module 14's ingest path.

**Endpoints** (all JWT tenant-scoped; writes are `source_system='user'`):

```
GET    /api/clients                       list + filters (status/payer/region_id/q), limit/offset
GET    /api/clients/metrics[?week=]       the census strip
GET    /api/clients/facets                observed statuses/payers + all regions
POST   /api/clients                       201 + client.created
GET    /api/clients/{id}                  care overview: client + contacts + caregivers
                                          + hours_this_week + tagged documents
PATCH  /api/clients/{id}                  basic fields -> one client.updated;
                                          status -> change_status
POST   /api/clients/{id}/contacts         201; PATCH/DELETE .../contacts/{contact_id}
GET    /api/clients/{id}/summary          cached AI care summary (503 without a key)
POST   /api/clients/{id}/summary/regenerate
POST   /api/schedules/{id}/check-in       optional {time}; omitted means now
POST   /api/schedules/{id}/check-out      also completes the visit
```

Contact writes emit `client.updated` **on the client** ("Family contact 'Susan
Grimes (daughter)' added for Walter Grimes") — a contact has no timeline of its own.
Setting `is_primary` clears the previous primary in the same transaction, so there
is never a moment with two. The board feed (`GET /api/schedule`) now carries
`check_in_at`, `check_out_at`, and the derived `evv` field on every visit.

**Tagged uploads.** `POST /api/documents` accepts optional `entity_type` +
`entity_id` form fields — the one sanctioned way to associate a document with a
record (a client's care plan). The type must be a key of the vertical entity map and
the row must exist under the tenant's RLS, else 422. The tag is stored on the
document row and stamped on every chunk, so retrieval and chat citations work
unchanged and the profile's document list is one query. `GET /api/documents` takes
the same pair as a filter. **Untagged uploads are unchanged** — tenant-general
knowledge, chunk entity columns left NULL.

**New agent tools:** gated `record_visit_check_in` / `record_visit_check_out` (an
agent asserting when a caregiver arrived changes a billing record, so it goes
through the approval gate even though a coordinator's own drawer click does not),
and safe `get_census`. `update_client_status` was rewired to the seam and
`get_client` now returns the care picture (payer label, contacts, this week's
hours). Three new event types — `client.status_changed`, `schedule.checked_in`,
`schedule.checked_out` — are registered as automation triggers, so a recipe can fire
on "WHEN a visit is checked out".

No new environment variables.

### Clients view — frontend (Module 16b)

The `/clients` directory and `/clients/{id}` care overview are the fourth vertical
surface's UI, reusing the Leads/Caregivers directory + profile patterns and the M12b
schedule drawer wholesale. Frontend seam: `lib/clients.ts` (status/payer meta,
`fmtHours`/`fmtDuration`, vitest-covered) + `components/clients/*` +
`pages/ClientsPage.tsx` / `ClientProfilePage.tsx`. Nav entry **Clients**
(`HeartPulse`) sits between Caregivers and Schedule.

**Directory** (`/clients`): a **census strip** on top — four stat tiles (Active
clients, Authorized/wk, Scheduled this week with unfilled hours as the subline, and
Delivered this week which turns **amber whenever `leakage_hours > 0`**, because the
revenue-leakage gap is the point of the census) plus by-payer / by-region chip rows
that apply the filter on click. Below: status filter chips (from the status meta),
payer + region `Select`s, search — all round-tripped through the URL — and a table
(name, status pill, payer, region, authorized/wk, contact) with row-click to the
profile. Realtime on `clients` refetches the directory + census; Realtime on
`schedules` refetches just the census (delivered/open hours move as visits do). Every
number comes straight from `GET /api/clients/metrics` — no client-side census math.

**Care overview** (`/clients/{id}`): SmartSummary (shared component, `client`
entity), then cards — contact/address/zip inline edit with languages/preferences tag
editors; a Care card (status/payer/region `Select`s with a **discharge-confirm**
dialog, authorized hours, care-summary textarea); an hours card (authorized /
scheduled / delivered bars, delivered amber when short, the leakage line); family
contacts (primary-star first, add/edit/delete, one-primary swap); assigned caregivers
linking to the board on their next visit's week; a visits card (next 5 upcoming + last
5 past, status pills + amber EVV badges, actual clocked duration once checked out,
"open in schedule"); a documents card; and the `client` EntityTimeline.

**Care plans** (`ClientDocumentsCard`): reuses the ingestion upload with
`entity_type='client'`/`entity_id` **preset invisibly** — the coordinator just picks a
file — with live status via Realtime and a confirmed delete. The Knowledge/Ingestion
page is untouched; this is the only place the tag is set from the UI.

**Schedule board EVV surfaces.** `VisitBlock` shows a compact amber `late`/`missed`
badge from the feed's server-computed `evv` field (no client-side rule math).
`VisitDrawer` gains state-driven **Check in** / **Check out** actions on scheduled
visits (check-out completes the visit and the drawer reflects the transition), shows
the clocked line ("Checked in 8:04 · out 12:14 · 4h 10m") once recorded, and keeps the
existing outcome buttons for unclocked bookkeeping. The EVV badge + `evvLabel` live in
`lib/schedule.ts` / `components/schedule/EvvBadge.tsx`, shared by the board and the
profile's visits card so the two never disagree.

**One backend addition (deviation from the plan's "no backend work" note).** The
visits card needs a client-scoped visit list, which 16a did not ship, so 16b adds one
read-only seam route: `GET /api/clients/{id}/visits?upcoming=&past=` returns the next
upcoming and last past visits in the board's `ScheduleVisitOut` shape (same resolved
names, same read-time `evv` flag). It reuses the schedule router's visit shaping and is
RLS-scoped like every `/api` route; both routers are vertical-seam members.

### Referrals dashboard (Module 17)

Which referral partners (hospitals, senior-living communities, discharge planners)
send leads that actually convert — referral ROI drives where the owner spends
relationship time. The dashboard **rides the Leads surface** (it is not a fifth
sanctioned vertical surface): its seam is `services/views/referrals.py` +
`routers/referrals.py` on the backend and `lib/referrals.ts` +
`components/referrals/*` + `pages/ReferralsPage.tsx` on the frontend. Nav entry
**Referrals** (`Handshake`) sits between Leads and Caregivers.

**Enrichment by name (no FK, no backfill).** A referral *source* is just the
free-text `leads.source` written by every lead path. A `referral_partners` row
(`entities_referral_partners` migration — the vertical seam; name unique per
tenant) enriches a source by **exact name match** — `join referral_partners p on
p.name = l.source`. Nothing links leads to partners: connector adapters keep
writing plain source strings, a rename simply re-joins, and deleting a partner
only un-enriches its source (the leads keep their `source` and funnel history).
An unmatched source shows as *untracked* and can be promoted in one click.

**Metrics** (`GET /api/referrals/metrics?months=6`, deterministic seam SQL — no
LLM near the numbers): one row per distinct non-empty `leads.source` **unioned
with every tracked partner** (so a tracked-but-quiet partner still shows — that
silence is itself the relationship signal), each with leads / in-pipeline /
converted / lost, conversion rate, avg days-to-convert, `last_lead_at`, a
zero-filled monthly lead-count series, and **`hours_won`** — the summed
`authorized_hours_per_week` of every client whose `lead_id` traces to that source
(all linked clients; a discharged client was still won business). `totals` carries
tracked-partner count, leads in the last 30 days, total hours-won, and the
best-converter (highest conversion rate among sources with ≥ 3 leads; null below
the bar). `?months=` is clamped 1–24, not rejected.

**Partner CRUD** (`GET/POST /api/referrals/partners`, `PATCH`/`DELETE
/api/referrals/partners/{id}`) is human REST (`source_system='user'` — an owner
curating their own list is the approver, so no approval gate), emitting
`referral_partner.created` / `updated` / `deleted` with plain-language summaries
(`updated` names the changed fields; a no-op PATCH emits nothing; a duplicate name
is a 409). No new agent tools — chat answers referral questions through the
existing read-only `run_report`, since `referral_partners` joins `SQL_SCHEMA_DOC`.

**UI** (`/referrals`): a metrics strip (Tracked partners, Leads last 30 days, Best
converter, Hours/wk won), a hand-rolled monthly lead-volume bar row (no chart
library — user decision; `MonthlyTrendBars`, theme-token bars, reused per-partner
in the drawer), and a client-side sortable partner table — one row per source with
its category chip (tracked) or a muted **Track** button (opens the create dialog
prefilled with the source name), leads / converted / conversion / hours-won / last
lead, and a 6-month sparkline. Row click opens the `PartnerDrawer` (contact card +
Edit/Delete for tracked, a Track CTA for untracked, per-partner trend, and that
source's recent leads linking to `/leads/{id}`). Realtime on `referral_partners` +
`leads` debounce-refetches the single metrics call. `lib/referrals.ts` (category
meta + dot tones, `fmtHoursWon`/`fmtRate`, month-bucket fill, sort helpers) is
vitest-covered.

**Note (best-converter threshold).** The demo seed has three `website` leads, so
the ≥ 3-lead bar surfaces `website` (33.3%) as the best converter — the plan's note
that it would stay null on the seed was a miscount; the code follows the explicit
≥ 3 contract.

## Notes on Templating

This repo is designed so that a second deployment, in a different vertical, requires:
- A new entity schema (swap Module 0's business tables — `leads`/`clients`/`resources`/etc. — for the new vertical's equivalents)
- New connector adapters for that business's external systems
- New pipeline-view content (stages, outreach sequences, scoring) on the shared entity-dashboard pattern
- Per-client configuration of the Module 11 decision harness

No changes should be needed to the core interfaces, the MCP tool layer's shape, the event/task system, or the automations engine itself. If a change to those does turn out to be necessary when templating, that's a signal the core wasn't abstracted correctly and worth revisiting.
