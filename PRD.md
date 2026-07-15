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

## Subsequent Modules (summary)

1. **Foundation Chat + Ingestion** — chat interface, drag-and-drop upload, chunking/embedding pipeline writing into Module 0's schema
2. **Structured Data Access** — parameterized tools (`get_client`, `list_leads_by_status`, `get_caregiver_availability`, etc.) plus scoped read-only text-to-SQL for reporting
3. **MCP Server & External Connectors** — tool server exposing Modules 1–2, plus CRM/phone/EHR/email webhook adapters normalizing into canonical entities
4. **Event Log** — every tool call, webhook, and agent action writes an immutable event row
5. **Approval Gate & Task System** — gated tools write to `pending_actions`/`tasks` instead of executing; approval triggers real execution
6. **Control Center Shell** — auth, nav, unified needs-attention queue; Chat and Event Log become views inside it
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
