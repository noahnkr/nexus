# Nexus Control Center

An operational hub for a small business — one place to see, ask about, and act on everything happening across the tools the business already runs on (CRM, phone, line-of-business/EHR, email). Nexus pulls that scattered data into a single canonical model and exposes it through a conversational AI agent and a set of purpose-built views.

It's a **system of intelligence, not a system of record**: the external tools stay authoritative for their own data; Nexus mirrors them one-way and adds the layer none of them can — reasoning and action *across* all of them at once. The core is business-agnostic and built to be re-templated for other verticals by swapping the entity schema; the first instantiation is an in-home senior-care business.

> **Docs map:** this README is the getting-started + usage guide. `PRD.md` is the component/architecture reference · `ROADMAP.md` is the ordered version plan · `CHANGELOG.md` is shipped history · `PROGRESS.md` is the active build board · `CLAUDE.md` governs how it's built.

## What You Can Do With It

- **Ask anything about the business** in chat — leads, clients, schedules, documents — and get streamed, cited answers. The agent uses governed tools, so it can also *take* actions (draft a text, update a record) that queue for your approval.
- **See every surface in one product**: Home dashboard, Chat, Knowledge (documents), Tasks, Event Log, Automations, Leads, Caregivers (+ Roster), Schedule board, Clients (census + visit verification), Referrals, Settings.
- **Automate cross-system follow-ups** with a WHEN → IF → THEN builder (or by describing it and letting the agent draft it) — a new lead triggers a welcome text, a call-out triggers a replacement search, a daily digest names expiring credentials.
- **Trust what it does**: anything that changes state visible outside Nexus (send a text/email, change a record) is *gated* — it becomes a plain-language task you approve, and every action is in an immutable Event Log.
- **Sync real systems in**: the WelcomeHome CRM flows in live today; WellSky, GoTo, and Google are on the roadmap.

## Architecture at a Glance

- **Core platform** (business-agnostic): canonical data model + tenancy, chat & agent, knowledge/RAG, the tool layer + MCP server, event log, tasks & the approval gate, the automations engine + center, connectors & sync, auth, observability.
- **Vertical seam** (swapped per deployment): the entity schema and the seam service/router/page files for the senior-care views (Leads, Caregivers, Schedule, Clients, Referrals, Roster) and connector adapters.

Re-templating for a new vertical touches only the seam — core tables and core code never change. See `PRD.md` for the full component reference.

## Stack

| Layer | Choice |
|-------|--------|
| Frontend | React + TypeScript + Vite + Tailwind + shadcn/ui |
| Backend | Python + FastAPI |
| Database | Supabase (Postgres + pgvector + Auth + Storage + Realtime) |
| LLM | Anthropic Messages API (Claude Sonnet primary; Haiku for cheap high-volume routing) |
| Embeddings / Reranking | Voyage AI |
| Agent tooling | MCP server (custom tools over Streamable HTTP) |
| Automations | Custom in-app engine (no n8n) |
| Observability | LangSmith |

---

## Getting Started

### Prerequisites

- Python 3.11+ with `venv`
- Node.js (for the Vite frontend)
- A Supabase project (Postgres + pgvector + Auth + Storage + Realtime)
- Anthropic API key · Voyage AI API key · (optional) LangSmith API key

### Environment

All configuration is via environment variables — there is no admin UI. Copy `.env.example` to `.env` and fill in the values from your hosted Supabase project (Project Settings → API and → Database). `.env.example` documents every variable, including the connector credentials.

### Database

The canonical schema is applied to a **hosted** Supabase project via the CLI (no local Docker needed):

```bash
# 1. Install the Supabase CLI (Windows / scoop)
scoop bucket add supabase https://github.com/supabase/scoop-bucket.git
scoop install supabase

# 2. Link the repo to your hosted project
supabase link --project-ref <your-project-ref>

# 3. Apply all migrations
supabase db push

# 4. Seed sample data (two tenants; idempotent — safe to re-run)
psql "$SUPABASE_DB_URL" -f supabase/seed.sql

# 5. NEXUS_TENANT_ID in .env defaults to the demo tenant:
#    00000000-0000-0000-0000-000000000001
```

### Backend role (one-time ops step)

The backend connects to Postgres as a dedicated **RLS-subject** role `nexus_app` (`nobypassrls`) — never as `postgres` (which bypasses RLS) or with the service-role key. The role is created by a migration with **no password** (passwords are never committed), so set one and point the app at it:

```bash
# 1. Set a strong password (connect as postgres via SUPABASE_DB_URL)
psql "$SUPABASE_DB_URL" -c "alter role nexus_app with password '<strong-password>';"

# 2. Put the Session Pooler URI in .env, plus your API keys:
#    NEXUS_APP_DB_URL=postgresql://nexus_app.<project-ref>:<pw>@<pooler-host>:5432/postgres
#    ANTHROPIC_API_KEY=... VOYAGE_API_KEY=... (LANGSMITH_API_KEY optional)
```

### Run it

```bash
# Backend (from backend/, venv active, .env filled in)
cd backend
python -m venv venv
source venv/Scripts/activate         # Windows bash; venv\Scripts\activate on cmd/PowerShell
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000     # -> http://localhost:8000/healthz

# Frontend (separate terminal)
cd frontend
cp .env.example .env                  # fill VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY
npm install
npm run dev                           # -> http://localhost:5173 (proxies /api -> :8000)
```

Tests and build:

```bash
pytest backend/tests    # backend suite (skips cleanly when env/creds absent)
npm run test            # frontend unit tests
npm run build           # type-check + production build
```

### Sign-in (one-time ops step)

Every `/api` route is protected by Supabase Auth; the two machine paths (webhook ingress, `/mcp`) keep their own credentials. There is no sign-up UI — create the one office user in the Supabase dashboard:

1. **Authentication → Add user**: email + password, auto-confirm on.
2. Attach the tenant claim so RLS can scope them (run against `SUPABASE_DB_URL`):

   ```sql
   update auth.users
      set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb)
          || '{"tenant_id": "00000000-0000-0000-0000-000000000001"}'::jsonb
    where email = '<office-user-email>';
   ```

Then sign in at `/login`. *(This step is the one gating live in-browser walks of the app — see `PROGRESS.md`.)*

---

## Using It

### Chat, Knowledge, and the shell

Home (`/`) is a light landing page — greeting, at-a-glance counts, recent activity, quick actions. Chat (`/chat`) streams answers over SSE with citations and renders markdown (including document-style tables); the send button becomes a **stop** button mid-answer, and stopping keeps a valid, resumable thread. Knowledge (`/knowledge`) is drag-and-drop document upload with live status. Tasks, Event Log, and Settings round out the shell; the sidebar collapses and the core pages are mobile-friendly (the schedule board and automation builder stay desktop-first).

**Knowledge comes in three tiers**, kept separate so a high-volume stream never dilutes the curated corpus. **Documents** are uploaded or connector-fed *files* — the `/knowledge` view — searched by `search_documents`. **Communications** are calls, emails, texts, and notes: every one is stored and linked to its timeline event, but only long-form correspondence is embedded (a text message is stored, not indexed), and chat searches them with a separate `search_communications`. **Derived knowledge** is the per-entity AI cards on a profile — the Smart summary, and a **Communication profile** describing how someone communicates (tone, responsiveness, preferred channel, recurring topics). All of it works with no extra configuration; the embedding threshold is an in-code policy, not an env var.

### The approval gate

State-changing tools don't fire — they queue. Ask chat to change a lead's status and it visibly *stalls* (record unchanged, a task created) until you approve it in **Tasks**, at which point the change lands and the Event Log shows the whole `action.queued → action.approved → tool.called` trail in plain language. You can edit a drafted text/email right in the approval before sending.

### Connecting an MCP client

The backend exposes its tool registry (the same tools chat uses) over MCP at `/mcp` (Streamable HTTP), guarded by a static bearer token (`NEXUS_MCP_TOKEN`; unset ⇒ every request 401s). Register it in Claude Code:

```bash
claude mcp add --transport http nexus http://localhost:8000/mcp \
  --header "Authorization: Bearer $NEXUS_MCP_TOKEN"
```

Every MCP tool call writes an `events` audit row with `source_system='mcp'`.

### Connectors: how external data flows in

Two inbound shapes share one path (verify/receipt → normalize → resolve to a canonical entity via `external_ids`):

- **Webhooks** — `POST /api/webhooks/{source}`, HMAC-verified (`NEXUS_WEBHOOK_SECRET`; unset ⇒ 401). Simulate a signed event:

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

- **Polling** — for sources with no webhooks. An in-app sync loop (a FastAPI lifespan task) sweeps each configured source on `NEXUS_CONNECTORS_POLL_SECONDS` (default 120) and feeds the *same* ingest path; polled receipts log as `connector.received`. A source being down degrades to one `connector.sync_failed` event, never a stalled loop.

Sync is **one-way inbound** — external platforms stay source of truth; outbound effects only ever go through gated tools.

### WelcomeHome CRM sync (live)

WelcomeHome has no webhooks, so it's polled. It mirrors the CRM's sales pipeline into the canonical model:

| WelcomeHome | Nexus |
|---|---|
| Prospect (care recipient = its primary Resident) | `leads` row (create + update) |
| Stage | `leads.status`, via the single stage-writer |
| Lead source | `leads.source` **verbatim** — the referral-partner join key |
| Influencers + extra Residents | `lead_contacts` rows |
| Activities (calls, emails, notes, visits) | `lead.activity_logged` timeline events |
| Calls, emails, texts, notes | stored in the **communications tier**, linked to the timeline event; long-form is embedded for chat retrieval (short messages are stored, not embedded) |
| **Start of Care** | promotes the lead to an **active client** (`lead_id` set) |

Stage mapping keys on WelcomeHome's stable stage `system_type` (falling back to position), so a renamed stage keeps working and a genuinely new stage leaves the lead's status unchanged with a warning — never a guess.

Configure it in `.env` (`WELCOMEHOME_API_KEY`, `WELCOMEHOME_COMMUNITY_ID`, `NEXUS_CONNECTORS_*`), then import existing history once with the operator-run backfill:

```bash
# from backend/, venv active, .env filled in
python -m app.scripts.backfill_welcomehome --dry-run --since 2026-01-01   # preview counts
python -m app.scripts.backfill_welcomehome --since 2026-01-01             # for real
```

It's idempotent, resumable, and prints per-table counts only (never record contents). **After it finishes:** WelcomeHome has no discharge signal, so every historical Start-of-Care prospect imports as an **active** client — bound the reach with `--since`, then open `/clients` and discharge the ones that have ended before trusting the census.

### Automations

Build a WHEN → IF → THEN automation in the **Automations Center** (`/automations/new`) with a sentence + step-list builder, or describe it in plain English and review the agent's draft. Triggers are events (anything in the audit trail), cron, or manual; steps run tools through the audited/gated seam, wait durably across delays, guard on conditions, compute via safe functions, or generate content. A gated step parks the run for approval in Tasks. Automations can't trigger automations. Per-stage outreach sequences on the Leads/Caregivers views are ordinary automations bound to a stage.

---

## Templating for Another Vertical

Nexus is built to be re-pointed at a different small business by swapping the seam, not the architecture. A new vertical replaces: the entity migration (`supabase/migrations/*entities*`), the seam service files (`services/views/*`, `services/tools/entities.py`, `services/automations/entities.py`, `services/connectors/entity_writers.py` + adapters), and the vertical routers/pages. Core tables (`events`, `tasks`, `pending_actions`, `external_ids`, `documents`, `automations`, …) and all core code stay untouched. See `CLAUDE.md` for the seam boundaries in detail.
