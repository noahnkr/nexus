# Nexus Control Center - PRD

## What We're Building

A control center application — the operational nexus for a small business — that unifies messy, cross-platform business data (CRM, phone service, line-of-business system, email) into a single canonical source of truth, exposed through a conversational AI agent and a set of purpose-built interfaces. The core is deliberately business-agnostic: the interfaces, MCP tool layer, event/task system, and workflow engine are shared scaffolding; what changes per client is the Postgres entity schema (Module 0) and any domain-specific connectors or harnesses built on top of it. This first build validates the architecture against an in-home senior care business, but no core interface should assume care-specific concepts.

**8 interfaces:**

1. **Chat** (default view) — Threaded conversations with the AI agent; retrieval-augmented responses over unstructured business context, plus structured data lookups and action-taking via MCP tools
2. **Ingestion** — Upload files manually, track processing status, manage documents, view chunking/embedding results
3. **Control Center Home** — Unified "needs attention" queue: pending tasks, paused workflow approvals, and flagged events in one place, regardless of origin
4. **Tasks** — Pending/in-progress/done items, created automatically (agent, workflow, harness) or manually; each links back to its originating event(s)
5. **Event Log** — Immutable, append-only audit feed of everything that happened across every connected system and every agent/tool action
6. **Workflows / Automations** — n8n embedded/linked, with custom nodes wrapping MCP tools (send SMS, trigger on CRM webhook, etc.)
7. **Settings** — Connector configuration, user preferences, agent behavior toggles (config primarily via env vars for this phase — see Out of Scope)
8. **Auth / Login** — Session-based auth, tenant-scoped from the data layer up

Business-specific views (e.g., a caregiver scheduler, a marketing hub) are plugin views layered on top of this core once it's validated — deliberately excluded from this PRD so the core stays portable across clients. See Out of Scope.

## Target Users

- **Primary**: a small business's office staff/owner-operator — non-technical, needs to triage leads, resolve exceptions, and trust the system enough to act on what it surfaces, without writing queries or reading logs
- **Implicit tertiary user**: the builder (you) — this is a reference implementation meant to be re-templated for future small-business clients across different verticals, so architectural decisions favor reusability over hardcoding any one business's domain specifics

## Scope

### In Scope

- ✅ Document ingestion and processing (manual upload; webhook-triggered ingestion from connected systems)
- ✅ Canonical entity data model (business-specific entities — e.g. leads, clients, resources, schedules — defined per deployment) with cross-system ID mapping
- ✅ Vector search with pgvector
- ✅ Hybrid search (keyword + vector)
- ✅ Reranking
- ✅ Metadata extraction
- ✅ Record management (deduplication, entity resolution across CRM/phone/EHR/email sources)
- ✅ Multi-format support (PDF, DOCX, HTML, Markdown)
- ✅ Data connectors (APIs, webhooks, websockets) for CRM, phone service, EHR, email
- ✅ Structured-data tool layer (parameterized read/write tools — see Constraints on text-to-SQL scope)
- ✅ Text-to-SQL tool, restricted to read-only analytical/reporting queries only
- ✅ Web search fallback
- ✅ Sub-agents with isolated context
- ✅ Chat with threads and memory
- ✅ Streaming responses
- ✅ Auth with RLS, schema designed tenant-aware (single active tenant this phase)
- ✅ MCP server exposing all agent-callable tools (structured queries, vector search, connector actions, task creation)
- ✅ Immutable Event Log across all systems and agent/tool actions
- ✅ Task system with approval-gate pattern for external-facing/state-changing actions
- ✅ n8n-based workflow automation with custom connector nodes
- ✅ Deterministic multi-phase harness *pattern* (generic engine: phase → programmatic check → human review on ambiguous cases), implemented against this client's actual matching problem as the first reference case, but built as a reusable engine, not care-specific logic
- ✅ LLM observability/tracing (LangSmith)
- ✅ Prompt caching for repeated system prompt/tool-definition context

### Out of Scope

- ❌ Knowledge graphs / GraphRAG
- ❌ Code execution / sandboxing
- ❌ Image/audio/video processing
- ❌ Fine-tuning
- ❌ Multi-tenant **admin tooling** (schema is tenant-aware; building UI/workflows to provision and manage *multiple* live tenant businesses is deferred until after this client is validated)
- ❌ Billing/payments
- ❌ Scheduled/automated ingestion (cron-based re-scans); ingestion is manual-upload or event-triggered only
- ❌ Admin UI (configuration via env vars and direct DB access for this phase)
- ❌ Open-ended text-to-SQL against state-changing tables (client/schedule writes always go through parameterized tools, never generated SQL)
- ❌ PDF bounding-box citation grounding
- ❌ Full HIPAA compliance certification (system is designed with audit logging, access control, and data-flow discipline in mind, but formal compliance review/BAA execution is a separate legal/business workstream, not an engineering deliverable of this PRD)
- ❌ Business-specific plugin views (e.g., a caregiver scheduler, a marketing hub) — these read/write through the core MCP tools but are built and scoped separately, per client, once the core here is validated; keeping them out of this PRD is what keeps the core templatable

## Stack

| Layer | Choice |
|-------|--------|
| Frontend | React + TypeScript + Vite + Tailwind + shadcn/ui |
| Backend | Python + FastAPI |
| Database | Supabase (Postgres + pgvector + Auth + Storage + Realtime) |
| LLM | Anthropic Messages API (Claude Sonnet primary; Haiku for cheap high-volume classification/routing subtasks) |
| Embeddings | Voyage AI |
| Reranking | Voyage AI reranker |
| Agent Tooling | MCP server (custom tools) |
| Workflow Automation | n8n (self-hosted), custom nodes wrapping MCP tools |
| Observability | LangSmith (Anthropic wrapper + `@traceable` for tool/harness spans) |

## Constraints

- **Cost, not scope, is the limiting factor.** Building "more than needed" for this client is acceptable and intended — it's a deliberate investment in a reusable architecture for future, larger clients. Optimize for API/infra spend, not for minimizing feature surface.
- **Data sensitivity**: this business touches health-adjacent information. Every table involved in client/care data needs a tenant_id, every agent/tool action touching client data must write to the Event Log, and any tool that can change client-affecting state must be gated through the Task approval system by default unless explicitly marked safe.
- **No open-ended write access via LLM-generated queries.** Text-to-SQL is read-only and reporting-scoped; all structured writes go through named, parameterized tools with defined inputs/outputs.
- **Scale target for this phase**: low hundreds of documents, not thousands — do not over-invest in retrieval sophistication (hybrid search/reranking should be present per Scope, but tuned for correctness at small scale, not premature optimization for corpora this client doesn't have yet).
- **Non-technical end users.** Every interface that surfaces agent or workflow output to office staff must be understandable without technical background — plain-language task descriptions, no raw JSON/tool-call output in user-facing views (that belongs in LangSmith traces and the Event Log's technical detail, not the Task queue's summary line).
- **Single-tenant deployment this phase**, tenant-aware schema throughout, to keep the templating path open without building multi-tenant admin surface prematurely.

---

## Module 0: Canonical Data Model

**Goal**: establish the single source of truth every other module reads from and writes to, before any ingestion, chat, or connector work begins. Everything in this module except the entity tables themselves is meant to be identical across deployments — only the entity schema below changes per client/vertical.

**Core entity tables — business-specific, defined per deployment** (all include `tenant_id`, `created_at`, `updated_at`):

This first build's instantiation (in-home senior care), given as a concrete example — a different vertical would swap these tables for its own equivalents (e.g., a home-services business might have `jobs`/`technicians`/`service_areas` instead):

- `leads` — id, tenant_id, name, contact info, source, status, region, requirements, created_at
- `clients` — id, tenant_id, lead_id (nullable FK), name, contact info, requirements, status
- `resources` (caregivers, in this instantiation) — id, tenant_id, name, qualifications[], regions[], availability_ref
- `schedules` — id, tenant_id, resource_id, client_id, start_time, end_time, status
- `regions` — id, tenant_id, name, boundary definition (zip codes/geo)
- `qualifications` — id, tenant_id, name, description (reference table joined to resource capabilities/lead requirements)

The naming above (`resources`, `regions`, `qualifications`) is intentionally generic where reasonable, so the pattern — not just the care-specific names — is what should be reused for the next client.

**Cross-system identity mapping**:

- `external_ids` — entity_type, entity_id (FK to canonical table), source_system (crm/phone/ehr/email), external_id, last_synced_at
- This table is what makes entity resolution possible: every inbound webhook event resolves to a canonical entity via this table before writing anywhere else

**Unstructured content**:

- `document_chunks` — id, tenant_id, document_id, chunk_text, embedding (pgvector), entity_type (nullable), entity_id (nullable FK), source_system, created_at
- The entity_id/entity_type tag is what lets a query like "what's going on with client X" join structured record + relevant notes by canonical ID rather than by semantic similarity alone

**Operational tables** (used by later modules, defined here since they're foundational):

- `events` — id, tenant_id, source_system, event_type, entity_type, entity_id, payload (jsonb), created_at — immutable, append-only
- `tasks` — id, tenant_id, title, description, status, priority, originating_event_id (FK, nullable), assigned_to, due_at, created_at, resolved_at
- `pending_actions` — id, tenant_id, task_id (FK), tool_name, tool_input (jsonb), status (pending/approved/rejected), created_at, resolved_at — the approval-gate mechanism: a gated tool call writes here instead of executing immediately

**Row-Level Security (RLS)**: every table scoped by `tenant_id` matching the authenticated session's tenant, enforced at the Postgres level via Supabase RLS policies — not just application-layer filtering, so a bug in the API layer can't leak cross-tenant data.

**Deliverable for this module**: schema migrations, RLS policies, and a seed script with representative fake data (a handful of leads/clients/caregivers/schedules) so every subsequent module has something real to build and test against immediately.

---

## Module 1: Foundation Chat + Ingestion

**Goal**: stand up the first two user-facing interfaces — Chat and Ingestion — on top of Module 0's schema, proving the full loop end to end: upload a document, watch it chunk and embed, then ask the agent about it and get a streamed, cited answer. This module also introduces the running application itself (the first FastAPI app and the first frontend), so its infrastructure decisions — tenant-scoped DB access, the SSE contract, the parser seam — are load-bearing for every later module.

**Chat**:

- Threaded conversations persisted in new core tables: `chat_threads` and `chat_messages`, with messages stored as Anthropic content-block JSON verbatim so Module 2's `tool_use`/`tool_result` blocks need no schema change
- Responses streamed via SSE (`start` → `citations` → `text` deltas → `done`/`error`)
- Basic RAG: query embedding → plain pgvector cosine top-k over `document_chunks` → retrieved context injected into the system prompt with numbered citations; hybrid search and reranking stay in Module 10
- Prompt caching (`cache_control`) on the static system block; full conversation history sent per call (stateless Messages API)

**Ingestion**:

- Drag-and-drop upload → Supabase Storage → background chunking/embedding pipeline (FastAPI BackgroundTasks — no worker queue at this scale)
- All four formats (PDF, DOCX, HTML, Markdown/TXT) via lightweight parsers behind a swappable parser interface; the Docling upgrade in Module 10 replaces parser registry entries only
- Voyage AI embeddings (1024-dim, matching the `document_chunks` column), batched
- Document status transitions (`uploaded → processing → ready/failed`) surfaced live to the frontend via Supabase Realtime; every transition also writes an immutable `events` row

**Infrastructure introduced here**:

- Dedicated RLS-subject Postgres role (`nexus_app`) for the backend, with per-request tenant scoping via the `request.app.tenant_id` GUC — closing the RLS-bypass hole of connecting as `postgres`/service-role
- Tenant-identity seam (`get_tenant_id()`): env-configured single tenant this phase, replaced by the verified JWT claim in Module 6
- Vite + React + Tailwind + shadcn/ui frontend shell with Chat (default) and Ingestion pages
- LangSmith tracing wired end to end (retrieve → generate spans)

**Deliverable for this module**: a runnable app where a user can upload documents in the four supported formats, watch processing status update live, and hold a threaded, streamed chat that answers from those documents with citations — with all tests green and every ingestion/chat action visible in the Event Log's `events` table and LangSmith. Plan: `.agent/plans/1.foundation-chat-ingestion.md`

---

## Module 2: Structured Data Access

**Goal**: give the agent governed access to the structured side of the canonical data model — named, parameterized read tools over the entity schema plus a scoped read-only text-to-SQL reporting tool — and wire them into Chat as a real agentic tool loop, so a question routes to structured tools, vector search (now a tool itself), or both. This module establishes the tool registry that Module 3's MCP server, Module 5's approval gate, and Module 7's n8n nodes all build on.

**Tool layer**:

- A registry of tool definitions (`name`, `description`, JSON Schema input, handler, `safe` flag) with a single `execute_tool()` execution seam: every call writes an immutable `events` row (plain-language summary + technical payload) and unsafe tools are refused until Module 5's gate exists
- Entity read tools — this instantiation: `list_leads`, `get_lead`, `list_clients`, `get_client`, `list_resources`, `get_resource_availability`, `list_schedules` — kept in one vertical-seam file mirroring the entity migration; generic naming so the pattern re-templates
- `search_documents` — document retrieval becomes a tool the model chooses to call; per-turn context injection is retired
- `run_report` — read-only text-to-SQL for analytical/reporting questions only: statement validation (single SELECT, allowlisted tables), executed inside a `READ ONLY` transaction with a statement timeout, RLS-scoped like everything else. Reads only this module; all write tools deferred to Module 5's approval gate

**Chat integration**:

- Multi-step agentic turns: `tool_use`/`tool_result` content blocks persisted verbatim in `chat_messages` (the Module 1 schema anticipated this), bounded loop, prompt caching over the static system block and tool definitions
- SSE contract extended additively with plain-language `tool`/`tool_result` progress events; citations aggregate across all `search_documents` calls in a turn
- Frontend shows tool activity as human-readable chips — no raw JSON in user-facing views

**Deliverable for this module**: chat that answers structured questions from live entity data ("which caregivers can handle dementia care?"), document questions via retrieval-as-a-tool with citations, and aggregate questions via read-only reporting SQL — every tool call visible as an `events` row and as a tool span in the LangSmith trace. Plan: `.agent/plans/2.structured-data-access.md`

---

## Module 3: MCP Server & External Connectors

**Goal**: open the system in both directions — outward, an MCP server exposing the Module 2 tool registry to external clients (Claude clients now, n8n custom nodes in Module 7); inward, a webhook ingress and connector-adapter seam that normalizes events from external systems into canonical entities via `external_ids` before anything else is written. Built as two sub-plans per the complexity rule.

**MCP server**:

- Official MCP Python SDK, Streamable HTTP transport, mounted at `/mcp` inside the existing FastAPI app — one process, one connection pool, one tenant seam
- Tools listed dynamically from the Module 2 registry; every call dispatches through the same audited `execute_tool()` seam with `source_system='mcp'`, so MCP calls appear in the Event Log and LangSmith exactly like chat calls
- Static bearer-token auth (`NEXUS_MCP_TOKEN`) until Module 6 introduces real auth

**Connector ingress & entity resolution**:

- Single ingress `POST /api/webhooks/{source}` with per-adapter signature verification (raw receipt written to `events` for every accepted call); poll-based sources (via n8n in Module 7, or manual triggers) re-post into this same ingress so the core stays webhook-shaped
- Adapter seam per source: `verify()` + async `normalize()` → canonical `NormalizedEvent`s; five adapters shipped as placeholders documenting the researched real integration flows — WelcomeHome (CRM, webhook subscriptions), GoTo Connect (VoIP/SMS, notification channels), WellSky Personal Care (EHR, FHIR webhooks/poll fallback), Gmail (Pub/Sub push + history fetch-back), Google Calendar (watch channels + syncToken fetch-back); real adapters later replace only adapter-file internals, never the seam
- Resolution routing per normalized event: matched via `external_ids` → link + record; unmatched but explicitly new (e.g. `lead.created`) → auto-create canonical row + mapping via the vertical-seam entity writers; unmatched reference → plain-language review task linked to the originating event (fuzzy matching stays in Module 8)
- New core table `connector_state` (tenant-scoped, RLS) for durable connector cursors — Gmail `historyId`, Calendar `syncToken`, channel renewals

**Deliverable for this module**: an MCP client (e.g. Claude Code) can connect to `/mcp` with a bearer token and call the same governed tools as chat, fully audited; a simulated signed webhook for each of the five sources flows through ingress → normalization → entity resolution, auto-creating a lead, matching known external ids, and stalling unknowns as review tasks — every step visible in `events` and LangSmith. Plans: `.agent/plans/3.mcp-and-connectors.md` (+ `3a.mcp-server.md`, `3b.connector-ingress.md`)

---

## Module 4: Event Log

**Goal**: surface the audit trail. Every module already writes immutable `events` rows (document lifecycle, chat turns, tool calls from chat and MCP, webhook receipts, connector resolutions); this module adds the business-facing read surface — PRD interface #5 — so office staff can see everything that happened across every connected system without reading logs or LangSmith.

**Events API**:

- `GET /api/events` — keyset-paginated feed (newest first) with filters: source system, event type, date range, and canonical entity (`entity_type` + `entity_id`) for entity drill-down ("everything that happened to this lead"), all RLS-scoped like every other read
- `GET /api/events/facets` — distinct source systems/event types feeding the filter UI, kept dynamic so the surface stays business-agnostic
- Plain-language summaries derived at read time: events that self-describe (`payload.summary` — tool calls, connector events) pass through; core lifecycle events get templates; unknown types humanize gracefully. No backfill — events are immutable

**Event Log interface**:

- Chronological feed with source badges, plain-language summary lines, and entity chips that apply the drill-down filter (URL-addressable for future deep links)
- Expandable per-row technical detail (pretty-printed payload JSON) — the sanctioned home for raw detail per the non-technical-user constraint; summaries stay plain everywhere else
- Live tail via Supabase Realtime (`events` added to the publication), same token pattern as ingestion status

**Deliverable for this module**: an Event Log page where a chat turn, an MCP tool call, and a simulated webhook each appear as readable feed entries within moments of happening, filterable down to a single lead's history — with raw payloads one click away but never in the summary line. Plan: `.agent/plans/4.event-log.md`

---

## Module 5: Approval Gate & Task System

**Goal**: close the loop the tool layer has been pointing at since Module 2 — state-changing tools stop being refused and start being *governed*. A gated tool call queues as a human-reviewable task instead of executing; approval triggers the real execution through the same audited seam; and office staff get the Tasks interface (PRD interface #4) to clear that queue alongside review tasks from connectors and their own manual to-dos. Built as two sub-plans per the complexity rule.

**Approval gate (backend)**:

- `execute_tool()`'s unsafe-tool refusal becomes the queue path: an `action.queued` event, a high-priority task titled in plain language (each gated tool provides a `gate_describe` that names the affected entities), and a `pending_actions` row holding the exact tool input — the model is told the action is queued (not an error) so it reports honestly
- Approval executes synchronously through `execute_tool` with an approved-action bypass — one seam for every execution, so the post-approval run writes the standard `tool.called` audit row plus an `action.approved` outcome event; rejection cancels the task with an `action.rejected` event; failed executions stay visible (`failed` action, task remains open)
- First write tools: vertical-seam entity writes (`update_lead_status`, `update_client_status`, `create_schedule`, `cancel_schedule`, all gated), core gated `send_sms`/`send_email` with placeholder log-only execution documenting the real GoTo/Gmail flows (credentials arrive with Module 7's connector work), and a safe `create_task` so the agent can create internal coordination tasks immediately
- Tasks & approvals API: keyset-paginated task list with embedded pending actions, manual task creation, validated status transitions, approve/reject endpoints — `tasks` and `pending_actions` join the Realtime publication

**Tasks interface**:

- `/tasks` page: status tabs and priority filter (URL-addressable), task cards with plain-language descriptions, status transitions, manual creation, and drill-down links into the Event Log via each task's originating event
- Inline approval cards: what will happen in plain language, Approve/Reject (with optional note), outcome shown immediately; raw tool input only behind an expandable technical-detail toggle; live updates via Supabase Realtime
- Chat marks queued actions distinctly (additive `queued` flag on the SSE `tool_result` event, amber chip linking to `/tasks`)

**Deliverable for this module**: the PRD success criterion made demonstrable — asking chat to change a lead's status visibly *stalls* (record unchanged, task created) until a human approves it in the Tasks page, at which point the change lands and the Event Log shows the whole `action.queued → action.approved → tool.called` trail in plain language; rejection and failure paths equally visible. Plans: `.agent/plans/5.approval-gate-and-tasks.md` (+ `5a.approval-gate-backend.md`, `5b.tasks-interface.md`)

---

## Subsequent Modules (summary)

6. **Control Center Shell & Visual Overhaul** — auth, nav, unified needs-attention queue; Chat and Event Log become views inside it. Establish a visually stunning, sleek, professional control center shell with a frontend design overhaul. Improve the UI and UX for existing views with quality of life features and design improvementsthat make it feel more polished and user-friendly (chat, ingestion, event log). Use the frontend-design plugin.
7. **Workflow Automation via n8n** — custom nodes calling MCP tools, embedded/linked editor
8. **Deterministic Matching/Decision Harness** — generic phase-pipeline engine (check → check → human review on ambiguous cases), instantiated against this client's actual matching problem (e.g., can we serve this lead) using the Module 5 approval gate for ambiguous cases; the engine is core, the specific checks are per-client configuration
9. **Custom Views / Plugin Apps** *(explicitly out of scope for this PRD — see Out of Scope)* — future domain-specific views (e.g., a scheduler) would land here, reading/writing exclusively through Module 2–3 tools
10. **Advanced RAG & Scale-Up** — hybrid search, reranking, multi-format ingestion (Docling), sub-agents, validated against this client's small corpus before applying to a larger future client

---

## Success Criteria

- A new inbound lead (via webhook or manual entry) resolves to a canonical entity, and the agent can correctly answer "can we serve this lead?" by calling the matching/decision harness rather than reasoning freeform over documents
- A client/care question in Chat correctly routes to structured tools, vector search, or both, and joins results by canonical entity ID when both apply
- Every tool call — read, write, or gated — appears in both the Event Log (business-facing) and LangSmith (developer-facing trace)
- Any tool marked as requiring approval never executes without a human clearing it in the Task queue first, verified by attempting to trigger one and confirming it stalls at `pending_actions` until approved
- The system runs correctly against this client's actual document/data volume without hybrid search or reranking tuning that was optimized for a scale this client doesn't have
- The MCP tool layer, event/task system, workflow engine, and interface shell are documented well enough that standing up a second tenant business — in a *different* vertical — requires no changes to the core interfaces or Modules 1–7 code, only a new entity schema (Module 0 equivalent), new connector adapters, and per-client harness configuration
