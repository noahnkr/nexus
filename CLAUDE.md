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

**Data & tenancy**
- Every table needs Row-Level Security scoped by `tenant_id` — this is stricter than "users only see their own data"; it's tenant isolation at the Postgres level, not just per-user filtering, since this system is built to host multiple client businesses over time
- The entity schema (leads/clients/resources/schedules/etc.) is the one thing that changes per deployment — keep it isolated to its own migration files so a new vertical only touches those, never the core tables (`events`, `tasks`, `pending_actions`, `external_ids`, `documents`, `document_chunks`, `chat_threads`, `chat_messages`, `connector_state`)
- The FastAPI backend connects to Postgres as the dedicated `nexus_app` role (`nobypassrls`, member of `authenticated`) and sets the `request.app.tenant_id` GUC per request/transaction — never as `postgres` (has BYPASSRLS) and never with the service-role key for data access. The service-role key is reserved for migrations/ops and Storage uploads only
- Tenant identity for the user-facing API comes from `deps.get_tenant_id()` — the verified Supabase JWT's `app_metadata.tenant_id` claim (HS256 legacy secret and ES256 project JWKS both accepted); every `/api` route fails closed (401/403) without a valid token. The credentialed machine paths — webhook ingress (HMAC signature) and `/mcp` (static bearer) — resolve tenant via `deps.get_machine_tenant_id()` (env `NEXUS_TENANT_ID`) and never via user JWTs; nothing else reads the env tenant at request time. All tenant-dependent code goes through these two seams
- Every inbound webhook/connector event must resolve to a canonical entity via `external_ids` before writing anywhere else — never let a connector write directly into a business table without entity resolution
- All inbound connector traffic enters through the single `POST /api/webhooks/{source}` ingress (signature-verified per adapter, raw receipt written to `events` before normalization) — poll/export-based sources are handled by external pollers (scheduled automations/manual) that re-post into this same ingress, never by a second inbound path. Connector adapters live in `backend/app/services/connectors/adapters/` (one file per source: `verify` + `normalize` only); `services/connectors/entity_writers.py` is part of the vertical re-templating seam alongside the entity migration and `services/tools/entities.py`

**Structured data access**
- No open-ended text-to-SQL against tables that can be written to. Client/schedule/lead writes always go through named, parameterized tools with defined inputs/outputs
- Text-to-SQL, where implemented, is read-only and scoped to analytical/reporting queries only
- All agent-callable tools live in `backend/app/services/tools/` and run through the registry's single `execute_tool()` seam — it writes the audit `events` row and enforces the `safe` flag; never call tool handlers directly from chat/MCP/workflow code
- The MCP server is mounted at `/mcp` inside the FastAPI app (Streamable HTTP, static bearer-token auth — the deliberate choice for machine clients; per-client OAuth waits for a real external need) and lists tools dynamically from the registry — never define an MCP-only tool or add MCP-specific behavior branches inside tool handlers; calls carry `source_system='mcp'` in the audit trail
- Entity-specific tools (`services/tools/entities.py`) are part of the re-templating seam alongside the entity migration; core tools never reference vertical concepts. Tool handlers take the already-tenant-scoped connection and never accept `tenant_id` as an input — RLS does the filtering

**Agent actions & safety**
- Any MCP tool that changes state in a way visible outside the system (send SMS/email, update a client record, trigger a workflow with external effects) must default to gated: write to `pending_actions`/`tasks` instead of executing, unless explicitly marked safe in its tool definition. A queued gated call is a *successful* tool result (the model reports it plainly), not an error
- Approval resolution executes through the same `execute_tool()` seam via its approved-action bypass (`services/approvals.py` is the only caller allowed to pass it) — never by invoking a handler directly; gate lifecycle uses the core event types `action.queued` / `action.approved` / `action.rejected`, and gated tools provide a `gate_describe` so task titles name entities in plain language
- Every tool call, webhook event, and gated-action resolution writes a row to the immutable `events` table — this is the audit trail, not optional logging
- New `events` writers set a plain-language `payload.summary`; the Event Log derives summaries at read time (`services/event_summaries.py`) for types that lack one — never backfill or mutate stored events to fix a summary
- Task/event surfaces shown to end users must be plain-language summaries — no raw JSON or tool-call payloads in user-facing views; that detail belongs in LangSmith traces and the Event Log's technical detail only

**Ingestion**
- Manual file upload and webhook/event-triggered ingestion only — no scheduled/cron-based re-ingestion pipelines
- Multi-format support (PDF, DOCX, HTML, Markdown) via the ingestion pipeline; every chunk tagged with `tenant_id` and, where applicable, the canonical `entity_type`/`entity_id` it relates to

**Scale discipline**
- This deployment's corpus is small (low hundreds of documents). Hybrid search and reranking should be implemented per the PRD, but tuned for correctness at this scale — don't add retrieval complexity that's solving a document-volume problem this client doesn't have

## Planning

- Save all plans to `.agent/plans/` folder
- Naming convention: `{sequence}.{plan-name}.md` (e.g., `0.canonical-data-model.md`, `1.foundation-chat-ingestion.md`)
- Plans should be detailed enough to execute without ambiguity
- Each task in the plan must include at least one validation test to verify it works
- Assess complexity and single-pass feasibility — can an agent realistically complete this in one go?
- Include a complexity indicator at the top of each plan:
  - ✅ **Simple** — Single-pass executable, low risk
  - ⚠️ **Medium** — May need iteration, some complexity
  - 🔴 **Complex** — Break into sub-plans before executing
- Modules involving the MCP tool layer, the approval-gate pattern, the automations framework, or the matching/decision harness (Modules 3, 5, 7, 11) should default to 🔴 Complex and be broken into sub-plans rather than attempted single-pass

## Development Flow

1. **Plan** — Create a detailed plan and save it to `.agent/plans/`
2. **Build** — Execute the plan to implement the feature
3. **Validate** — Test and verify the implementation works correctly. Use browser testing where applicable via an appropriate MCP. For agent/tool-calling features, verify the LangSmith trace shows the expected step sequence, not just the final output
4. **Iterate** — Fix any issues found during validation

## Progress

Check `PROGRESS.md` for current module status. Update it as you complete tasks. Module numbering follows the PRD's module list (0 through 12). The Leads and Caregivers views (Modules 9–10) are the two sanctioned vertical views — their dashboard/pipeline *pattern* is core, their content (stages, sequences, scoring) belongs to the re-templating seam; business-specific views beyond those two stay out of scope for this repo.
