# CLAUDE.md

Nexus Control Center: a business-agnostic operational hub with home (default), chat, ingestion, tasks, event log, an automations center, entity pipeline views (leads/caregivers), and settings interfaces, backed by a canonical entity data model and an MCP tool layer that bridges external systems (CRM, phone, line-of-business, email). First instantiation targets an in-home senior care business; core is built to be re-templated for other verticals by swapping the entity schema, not the surrounding architecture. Config via env vars, no admin UI.

See `PRD.md` for full scope, module breakdown, and success criteria. This file governs *how* to build; the PRD governs *what*.

## Stack

- Frontend: React + Vite + TypeScript + Tailwind + shadcn/ui
- Backend: Python + FastAPI
- Database: Supabase (Postgres, pgvector, Auth, Storage, Realtime)
- LLM: Anthropic Messages API (Claude Sonnet primary; Haiku for cheap high-volume classification/routing)
- Embeddings: Voyage AI
- Reranking: Voyage AI reranker
- Agent tooling: MCP server (custom tools, exposed to the Anthropic API as `tools`)
- Automations: custom in-app engine (no n8n) — event-trigger listeners + cron scheduling, durable run state across waits, steps execute MCP tools through `execute_tool`
- Observability: LangSmith (`wrap_anthropic` + `@traceable` for tool/harness spans)

## Rules

**General**
- Python backend must use a `venv` virtual environment
- No LangChain, no LangGraph — raw Anthropic SDK calls only
- Use Pydantic for structured LLM outputs
- Stream chat responses via SSE
- Use Supabase Realtime for ingestion status and task/event queue updates
- Chat is stateless per the Anthropic Messages API — store and send full conversation history yourself; use `cache_control` on system prompt and tool definitions to control cost as history grows
- Frontend dropdowns use the shared `ui/Select` component (hand-rolled listbox popover — options/groups, icons, dots, search) — never native `<select>` elements; new UI reuses it rather than reinventing per-page pickers

**Data & tenancy**
- Every table needs Row-Level Security scoped by `tenant_id` — this is stricter than "users only see their own data"; it's tenant isolation at the Postgres level, not just per-user filtering, since this system is built to host multiple client businesses over time
- The entity schema (leads/clients/resources/schedules/etc.) is the one thing that changes per deployment — keep it isolated to its own migration files so a new vertical only touches those, never the core tables (`events`, `tasks`, `pending_actions`, `external_ids`, `documents`, `document_chunks`, `communications`, `communication_chunks`, `chat_threads`, `chat_messages`, `connector_state`, `automations`, `automation_runs`, `entity_summaries`, `tenant_settings`)
- The FastAPI backend connects to Postgres as the dedicated `nexus_app` role (`nobypassrls`, member of `authenticated`) and sets the `request.app.tenant_id` GUC per request/transaction — never as `postgres` (has BYPASSRLS) and never with the service-role key for data access. The service-role key is reserved for migrations/ops and Storage uploads only
- Tenant identity for the user-facing API comes from `deps.get_tenant_id()` — the verified Supabase JWT's `app_metadata.tenant_id` claim (HS256 legacy secret and ES256 project JWKS both accepted); every `/api` route fails closed (401/403) without a valid token. The credentialed machine paths — webhook ingress (HMAC signature) and `/mcp` (static bearer) — resolve tenant via `deps.get_machine_tenant_id()` (env `NEXUS_TENANT_ID`) and never via user JWTs; nothing else reads the env tenant at request time. All tenant-dependent code goes through these two seams
- User-facing workspace/agent preferences live in the core `tenant_settings` table (one jsonb row per tenant) behind the `services/settings.py` whitelist seam — env vars remain the only home for infra config and credentials, and `tenant_settings` never holds secrets. Tenant agent instructions are appended to the chat system prompt *after* the core persona: they may shape tone and content, never the gating rules or tool semantics; the `settings.updated` audit event names changed keys, never values
- Every inbound webhook/connector event must resolve to a canonical entity via `external_ids` before writing anywhere else — never let a connector write directly into a business table without entity resolution
- All inbound connector traffic enters through the single ingest seam `services/connectors/ingest.py::ingest_payload` (raw receipt written to `events` before normalization) — the `POST /api/webhooks/{source}` route is verify-then-ingest, and poll/export/bridge-based sources are driven by the in-app connector sync loop (`services/connectors/sync.py`, a lifespan loop under `get_machine_tenant_id()` like the automations engine loops) whose runners fetch → translate → call the same `ingest_payload`; never a second inbound path. Sync cursors/channel state live in `connector_state.state`; credentials and OAuth refresh tokens live in env vars only, never in the database. Connector adapters live in `backend/app/services/connectors/adapters/` (one file per source: `verify` + `normalize` only); `services/connectors/entity_writers.py` is part of the vertical re-templating seam alongside the entity migration and `services/tools/entities.py`. External platforms are source of truth: sync is one-way inbound; outbound effects go through gated tools only (no entity write-back)

**Structured data access**
- No open-ended text-to-SQL against tables that can be written to. Client/schedule/lead writes always go through named, parameterized tools with defined inputs/outputs
- Text-to-SQL, where implemented, is read-only and scoped to analytical/reporting queries only
- All agent-callable tools live in `backend/app/services/tools/` and run through the registry's single `execute_tool()` seam — it writes the audit `events` row and enforces the `safe` flag; never call tool handlers directly from chat/MCP/workflow code
- The MCP server is mounted at `/mcp` inside the FastAPI app (Streamable HTTP, static bearer-token auth — the deliberate choice for machine clients; per-client OAuth waits for a real external need) and lists tools dynamically from the registry — never define an MCP-only tool or add MCP-specific behavior branches inside tool handlers; calls carry `source_system='mcp'` in the audit trail
- Entity-specific tools (`services/tools/entities.py`) are part of the re-templating seam alongside the entity migration; core tools never reference vertical concepts. Tool handlers take the already-tenant-scoped connection and never accept `tenant_id` as an input — RLS does the filtering

**Agent actions & safety**
- Any MCP tool that changes state in a way visible outside the system (send SMS/email, update a client record, trigger a workflow with external effects) must default to gated: write to `pending_actions`/`tasks` instead of executing, unless explicitly marked safe in its tool definition. A queued gated call is a *successful* tool result (the model reports it plainly), not an error
- Approval resolution executes through the same `execute_tool()` seam via its approved-action bypass (`services/approvals.py` is the only caller allowed to pass it) — never by invoking a handler directly; gate lifecycle uses the core event types `action.queued` / `action.approved` / `action.rejected`, and gated tools provide a `gate_describe` so task titles name entities in plain language. An approval may execute with human-edited `tool_input`, but only for keys the tool declares in `ToolDef.editable_fields`, and the edit (fields + original input) is recorded on the `action.approved` event — never a second resolution path
- Every tool call, webhook event, and gated-action resolution writes a row to the immutable `events` table — this is the audit trail, not optional logging
- New `events` writers set a plain-language `payload.summary`; the Event Log derives summaries at read time (`services/event_summaries.py`) for types that lack one — never backfill or mutate stored events to fix a summary
- Task/event surfaces shown to end users must be plain-language summaries — no raw JSON or tool-call payloads in user-facing views; that detail belongs in LangSmith traces and the Event Log's technical detail only

**Automations**
- Automation recipes are validated, declarative JSON (WHEN/IF/THEN): conditions are field comparisons only — no code and no LLM output in the control path; the only LLM surface is the `generate` step's content. Tool steps run exclusively through `execute_tool` with `source_system='automation'`, so auditing and the approval gate apply unchanged (a gated step parks the run `waiting_approval`; only `services/approvals.py` resumes/cancels it)
- Automation functions (`services/automations/functions.py` registry) are safe-by-definition pure computations — anything with external effect must be a *tool*, never a function; vertical functions (e.g. scoring) register into this seam without core changes, and `services/automations/entities.py` is the vertical entity-lookup seam alongside the other re-templating files
- The engine loops (event dispatcher, cron, waker, recovery) run in-process in the FastAPI lifespan under `get_machine_tenant_id()`; the events dispatcher never re-dispatches events with `source_system='automation'` (automations must not trigger automations — the same rule extends to the tool layer: `run_automation` only starts manual-trigger automations and refuses calls with `source_system='automation'`); the default concurrency rule is one active run per (automation, entity)
- Agent-drafted recipes are never persisted directly — drafting endpoints return unsaved, validated drafts for human review in a builder; the standard validated create path is the only writer of `automations` rows
- Pipeline-view stage sequences are ordinary automations tagged via the core `automations.binding` jsonb (`{"view": …, "stage": …}`; one per (tenant, view, stage) via partial unique index) — core validates binding *shape* only and never interprets vertical stage names; no parallel linkage tables, no second execution path

**Vertical views**
- The entity-pipeline views (Leads/Caregivers) are pattern-core, content-seam: `backend/app/services/views/` (stage configs, metrics, smart summaries) and the vertical routers/pages (`routers/leads.py`, the view's frontend pages/components) are re-templating-seam members alongside the entity migration, `services/tools/entities.py`, and the connector writers. Stage sets live in `leads.status`-style entity columns + seam config — never in new core tables
- View pages write entities through their own human REST routes (`source_system='user'`, entity events logged per write — e.g. `lead.stage_changed` with a plain `payload.summary`); the approval gate is for agent-initiated effects, not a human clicking their own UI. Every writer that moves an entity's stage (REST route or tool handler) emits the stage-changed event so sequences and timelines see it. The human-UI exemption covers entity record writes only — outbound messaging triggered from a page (e.g. the schedule board's notify-by-SMS) still runs through `execute_tool` and its gate
- The Schedule board is the third sanctioned vertical surface: `services/views/schedule.py` (the transition seam — the only writer of schedule state; REST routes, tool handlers, and the connector sync writers all delegate) and `services/views/matching.py` (deterministic ranker; weights are in-seam constants) are re-templating-seam members. Matching stays deterministic and explainable — plain-language reasons/warnings, no LLM in the ranking
- The Clients view is the fourth sanctioned vertical surface: `services/views/clients.py` (status transitions via the single `change_status` writer, census math, EVV read-time flags) and `routers/clients.py` are re-templating-seam members. Census/hours numbers are deterministic seam SQL — no LLM near the metrics; EVV late/missed flags are computed at read time from rule constants, never stored or written by a detector loop
- The Referrals dashboard rides the Leads surface (not a fifth surface): `services/views/referrals.py` + `routers/referrals.py` are seam members. Referral partners are enrichment-by-name over the free-text `leads.source` (exact match, no FK, no backfill) — lead write paths and connector adapters never link to partner rows, they just keep writing source strings
- The workforce Roster rides the Caregivers surface: `services/views/workforce.py` + `routers/workforce.py` are seam members. Credentials are dated rows per (caregiver, qualification) layered over the undated `qualifications` vocabulary — `resources.qualification_ids` stays the matching input, and credential expiry status is derived at read time from in-seam constants (no stored status, no detector loop). Resources with `status='inactive'` are excluded from matching candidates and the schedule board; the Roster tab is the one surface that lists everyone

**Ingestion**
- Manual file upload and webhook/event-triggered ingestion only — no scheduled/cron-based re-ingestion pipelines
- Multi-format support (PDF, DOCX, HTML, Markdown) via the ingestion pipeline; every chunk tagged with `tenant_id` and, where applicable, the canonical `entity_type`/`entity_id` it relates to
- Uploads may carry an optional canonical-entity tag (`documents.entity_type`/`entity_id`, validated against the entity map) that chunks inherit — the one sanctioned way to associate a document with an entity (e.g. a client's care plan); untagged uploads stay tenant-general

**Knowledge tiers**
- Knowledge is three deliberately-separate tiers so a high-volume, low-value stream never pollutes the curated corpus: **Documents** (uploaded/connector *files* — `documents`/`document_chunks`, retrieved by `search_documents`), **Communications** (messages/calls/emails — `communications`/`communication_chunks`, retrieved by `search_communications`), **Derived knowledge** (per-entity summaries/comm-profiles via the `entity_summaries` seam). Never route message/call/email content into `documents` — it belongs in the communications store
- Every message source (CRM activities, the messaging connectors) writes through the single `services/communications.py::ingest_communication` seam — **store-all, embed-selectively**: every message is stored and timeline-linked (via a `source_event_id` events spine + `content_hash` cross-source dedup); only long-form correspondence is chunked/embedded (the `should_embed` policy — short messages like SMS stay store-only). `communications` rows are durable; `communication_chunks` is a derived, rebuildable cache (no scheduled re-embed job — the same no-cron rule as document ingestion)
- `entity_summaries` is keyed by `(tenant_id, entity_type, entity_id, kind)` — one entity holds both a `smart_summary` and a `comm_profile`; the summary seam (`services/views/summary.py`) threads `kind` through its cache. Tone/style/responsiveness is a summary problem, never retrieval

**Scale discipline**
- This deployment's corpus is small (low hundreds of documents). Hybrid search and reranking should be implemented per the PRD, but tuned for correctness at this scale — don't add retrieval complexity that's solving a document-volume problem this client doesn't have

## Planning

- Save all plans to `.claude/plans/`, one per version
- Naming convention: `vX.Y.Z-{name}.md` (e.g. `v1.0.0-welcomehome-sync.md`, `v1.2.0-goto-connect.md`)
- Plans should be detailed enough to execute without ambiguity
- Each task in the plan must include at least one validation test to verify it works
- Assess complexity and single-pass feasibility — can an agent realistically complete this in one go?
- Include a complexity indicator at the top of each plan:
  - ✅ **Simple** — Single-pass executable, low risk
  - ⚠️ **Medium** — May need iteration, some complexity
  - 🔴 **Complex** — Break into sub-plans before executing
- Versions touching the MCP tool layer, the approval-gate pattern, or the automations framework default to 🔴 Complex and should be broken into ordered sub-parts rather than attempted single-pass

## Versioning & Workflow

Semantic versioning **by impact** (see `ROADMAP.md`): **minor** = a new capability/subsystem, **patch** = a tweak/fix to an existing one, **major** = a re-template or breaking change. Two rules: **build order = version order** (never build a later version before an earlier one), and **ideas get routed before they get built** (a new idea lands in the roadmap's backlog or a version slot, never straight into the code).

The lifecycle is encoded as commands — use them rather than reinventing the flow:

1. **`/idea`** — capture a feature/fix and route it to the right version (or backlog) in `ROADMAP.md`, in dependency order. Never starts building.
2. **`/plan`** — plan the next version (top of the roadmap's _Planned_ list): explore → clarify → write `.claude/plans/vX.Y.Z-*.md` → sync docs.
3. **`/build`** — execute the current version's plan top-to-bottom, ticking `PROGRESS.md`, running every validation (incl. the LangSmith trace for agent/tool work).
4. **`/tweak`** — a small patch-version change without a full plan.
5. **`/document`** — after a green build, ship the version: append to `CHANGELOG.md` (high-level, never rewrite past entries), move it to _Shipped_ in `ROADMAP.md`, advance `PROGRESS.md`.

The four docs have distinct jobs: `ROADMAP.md` = ordered versions + backlog (what's next) · `PROGRESS.md` = the active build board (task checkboxes) · `CHANGELOG.md` = shipped history (high-level) · `PRD.md` = the component/architecture reference (not a build timeline).

## Commits

Conventional commits — `type(scope): subject` — always. This ties commits to the versioning system: the **type** signals the version impact, and the **scope** names the component (drawn from the architecture, so scoping stays consistent rather than ad hoc).

**Type** (decides the version bump):

| Type | Meaning | Bump |
|---|---|---|
| `feat` | a new capability/subsystem | minor |
| `fix` | a bug fix | patch |
| `perf` / `refactor` | performance / behavior-preserving change | patch |
| `test` | tests only | — |
| `docs` | docs, plans, comments only | — |
| `chore` | tooling, deps, config, file moves, housekeeping | — |

A `BREAKING CHANGE:` footer forces a **major** bump regardless of type.

**Scope** — the component the change centers on. Pick the closest from the standing vocabulary; omit the scope only for a genuinely repo-wide change.

- **Core platform:** `data-model` · `chat` · `knowledge` · `tools` · `mcp` · `events` · `tasks` · `approvals` · `automations` · `connectors` · `auth` · `obs`
- **Vertical seam:** `leads` · `caregivers` · `schedule` · `clients` · `referrals` · `workforce` · `entities`
- **A specific connector** when clearer than `connectors`: `welcomehome` · `wellsky` · `goto` · `google`
- **Meta:** `docs` · `plans` · `roadmap` · `readme` · `workflow` · `deps` · `seed` · `migrations` · `config`

**Subject**: imperative mood, lowercase after the colon, no trailing period, ≤ 72 chars. An optional wrapped body explains *why*, not what. End every AI-assisted commit with a `Co-Authored-By:` trailer naming the assisting model, e.g. `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

Examples:
- `feat(welcomehome): sync prospects to leads with stage mapping`
- `fix(schedule): keep the called-out shift's original row on reassign`
- `refactor(leads): extract change_stage as the single stage-writer`
- `docs(prd): restructure into a component reference`
- `chore(workflow): add /idea → /plan → /build → /document commands`

## Vertical surfaces

The Leads and Caregivers pipeline views, the Schedule board, and the Clients view are the four sanctioned vertical surfaces (the Referrals dashboard rides the Leads surface; the workforce Roster rides the Caregivers surface) — their patterns are core, their content (stages, sequences, matching weights, board semantics, census/EVV rules, referral metrics, credential/utilization rules) belongs to the re-templating seam; business-specific views beyond those stay out of scope for this repo.
