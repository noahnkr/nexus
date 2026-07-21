# Nexus Control Center — Product & Architecture Reference

This document describes **what Nexus is made of** — its components and how they fit together — as a standing reference, not a build timeline. For what's shipped, in progress, and planned next, see `CHANGELOG.md`, `PROGRESS.md`, and `ROADMAP.md`. For the rules governing *how* it's built, see `CLAUDE.md`.

## What Nexus Is

Nexus is an operational hub for a small business — the single place its owner and office staff go to see, ask about, and act on everything happening across the disconnected tools the business already runs on (a CRM, a phone system, a line-of-business/EHR system, email). It pulls that scattered data into one canonical model, exposes it through a conversational agent and a set of purpose-built views, and turns cross-system follow-ups into automations and reviewable tasks.

Two things define the product:

- **It is a system of intelligence, not a system of record.** The external tools stay authoritative for their own data; Nexus mirrors them one-way and adds the layer none of them can — reasoning and action *across* all of them at once. The value is the connective tissue (agent, automations, tasks, unified memory), not a re-implementation of any single tool.
- **The core is business-agnostic; the vertical is a seam.** Everything except the entity schema and a handful of clearly-marked seam files is shared scaffolding meant to be re-templated for other verticals. The first instantiation is an in-home senior-care business, but no core component assumes care-specific concepts.

## Who It's For

- **Primary user** — a small business's owner-operator and office staff. Non-technical. They need to triage leads, resolve exceptions, and trust the system enough to act on what it surfaces, without writing queries or reading logs.
- **Implicit second user** — the builder. This is a reference implementation intended to be re-templated for future clients across verticals, so architecture favors reusability over hardcoding any one domain.

## The Central Idea: Core Platform vs. Vertical Seam

```
┌─────────────────────────────────────────────────────────────┐
│  CORE PLATFORM  (business-agnostic, shared across verticals) │
│                                                              │
│  Canonical data model · Chat & agent · Knowledge/RAG ·       │
│  Tool layer & MCP · Event log · Tasks & approval gate ·      │
│  Automations engine & center · Connectors & sync ·           │
│  Auth & tenancy · Observability                              │
├─────────────────────────────────────────────────────────────┤
│  VERTICAL SEAM  (swapped per deployment — senior care here)  │
│                                                              │
│  Entity schema · pipeline views (Leads, Caregivers) ·        │
│  Schedule board & matching · Clients/census/EVV · Referrals ·│
│  Workforce roster · connector adapters & entity writers      │
└─────────────────────────────────────────────────────────────┘
```

Re-templating for a new vertical touches only the seam: the entity migration, the seam service files (`services/views/*`, `services/tools/entities.py`, `services/automations/entities.py`, `services/connectors/entity_writers.py` + adapters), and the vertical routers/pages. Core tables and core code never change.

---

## Core Platform Components

### Canonical Data Model & Tenancy

The single source of truth every other component reads and writes. Core operational tables (identical across deployments): `events`, `tasks`, `pending_actions`, `external_ids`, `documents`/`document_chunks`, `communications`/`communication_chunks`, `chat_threads`/`chat_messages`, `connector_state`, `automations`/`automation_runs`, `entity_summaries`, `tenant_settings`.

- **Cross-system identity** — `external_ids` maps every external record to its canonical entity. Every inbound connector event resolves through it before anything else is written, so one real-world thing is one entity no matter how many systems report it.
- **Tenancy** — every table carries `tenant_id` and is protected by Postgres row-level security (four policies each), so isolation is enforced at the database, not just in application code. The backend connects as a dedicated RLS-subject role (`nexus_app`, no bypass) and sets the tenant per request; the service-role key is reserved for migrations/ops and storage. Single active tenant this phase, tenant-aware throughout to keep the multi-tenant path open.

### Chat & the Agent

The conversational front door. Threaded conversations, streamed over SSE, answered by the Anthropic Messages API as a real tool-using loop: a question routes to structured tools, document retrieval, reporting queries, or action tools, and the answer cites its sources. Conversation history is stored and re-sent per call (stateless Messages API) with prompt caching on the system prompt and tool definitions to control cost. Per-tenant instructions and tone are appended *after* the core persona — they shape voice and content, never the gating rules or tool semantics.

### Knowledge & Retrieval

How unstructured context becomes answerable. Documents (PDF/DOCX/HTML/Markdown) are uploaded or connector-fed, parsed, chunked, embedded (Voyage), and retrieved by hybrid search + reranking, tuned for correctness at this corpus's scale (low hundreds of documents) rather than for volume this business doesn't have. A document can be tagged to a canonical entity so "this client's documents" is one query, and chunks inherit the tag.

Knowledge is organized as **three tiers**, kept deliberately separate so a high-volume, low-value stream never pollutes the curated corpus:

1. **Documents** — uploaded or connector-fed *files* (care plans, assessments, contracts). The curated RAG corpus, retrieved by `search_documents`. Untouched by the communications tier.
2. **Communications** — messages, calls, emails: their own store (`communications`/`communication_chunks`), timeline-linked always via an `events` spine, embedded *selectively* (store ≠ embed — short messages are stored but not embedded; long-form correspondence is chunked into its own index). Retrieved by a separate `search_communications` tool so a high-volume stream never pollutes the curated corpus. `ingest_communication` is the one entry every message source (CRM activities, and the v1.3/v1.4 messaging connectors) writes through. *(Built in v1.1.0, ahead of the messaging connectors.)*
3. **Derived knowledge** — per-entity summaries and communication profiles (tone, responsiveness, preferred channel) generated from history via the `entity_summaries` seam (discriminated by `kind`). Tone/style is a summary problem, not a retrieval one.

### Tool Layer & MCP

The one governed path to structured data and actions. Every agent-callable tool lives in a registry and runs through a single `execute_tool()` seam that writes an audit event and enforces the `safe` flag. Named, parameterized read/write tools cover the entity model; a read-only, validated text-to-SQL tool answers analytical questions (never writes). The same registry is exposed to external machine clients over an MCP server (`/mcp`, static-bearer auth) — MCP calls run through the identical seam and audit trail. Open-ended write access via generated SQL is never allowed.

### Event Log & Audit

The immutable spine. Every tool call, webhook/connector event, automation step, and gated-action resolution appends a row to `events`. The Event Log surface renders these as plain-language summaries (derived at read time for types that lack one), filterable down to a single entity's history, with raw technical detail one click away — never in the summary line.

Because events are immutable, **display is the only place a badly-written summary can be corrected**: the expanded view of an event is a shared best-effort renderer (used by the Event Log and every entity timeline alike) that derives what it shows from the payload's structured fields rather than trusting the stored one-liner — long text first, then a labeled field grid, then the raw JSON behind a toggle. Payload shapes are deliberately heterogeneous (each writer chooses its own), so the renderer reads what it recognizes, shows the rest generically, and never fails on a shape it hasn't seen.

### Tasks & the Approval Gate

How the system acts safely. Any tool that changes state visible outside Nexus (send SMS/email, update a record, trigger an external effect) defaults to **gated**: instead of executing, it writes a plain-language, human-reviewable task and a `pending_actions` row. Approval executes the *same* call through the *same* audited seam (optionally with human-edited fields the tool declares editable); rejection and failure stay visible. A queued gated call is a successful result the agent reports plainly, not an error. The Tasks interface is where staff clear that queue alongside connector review-tasks and their own to-dos.

### Automations Engine & Center

The cross-system brain. A business-agnostic **WHEN → IF → THEN** engine: event/cron/manual triggers, declarative field-comparison conditions (no code or LLM in the control path), and THEN steps that run tools through the audited/gated seam, wait/delay durably across restarts, guard on mid-sequence conditions, compute via safe registered functions, or generate LLM content. Runs advance one transaction per step; gated steps park the run for approval. The **Automations Center** is the surface over it — a grid to see and manage automations, a sentence + step-list builder, and agent drafting (describe it in English, review a validated draft; the agent never persists directly). Automations cannot trigger automations.

### Connectors & Sync

How external systems flow in. A single **ingest seam** (`ingest_payload`) sits behind both the webhook route (verify → ingest) and an in-app **connector sync loop** (a lifespan task, like the automations engine) that polls sources with no webhooks. Every inbound event follows the same path: raw receipt → normalize (per-source adapter) → resolve to a canonical entity via `external_ids` (match / auto-create / review-task). Sync is **one-way inbound** — external platforms stay source of truth; outbound effects go only through gated tools. Cursors/channel state live in `connector_state`; credentials live in env vars only, never the database. A source being down degrades to one `connector.sync_failed` event and a skipped cycle — never a stalled loop.

### Auth & Observability

- **Auth** — Supabase Auth (email/password), tenant resolved from the verified JWT's `app_metadata.tenant_id`; every `/api` route fails closed. The two machine paths keep their own credentials (webhook HMAC, MCP static bearer) and never use user JWTs.
- **Observability** — LangSmith tracing end to end (chat turns, tool spans, connector-sync spans, automation runs), so any behavior is inspectable beyond the user-facing Event Log.

---

## The Vertical Layer (senior-care instantiation)

Everything below is content in the re-templating seam. Another vertical replaces these and nothing else.

### Entity Schema

The business's own records, defined per deployment (all tenant-scoped, audited): `leads`, `clients` (with `lead_id` provenance), `resources` (caregivers), `applicants` (hiring), `schedules`, `regions`, `qualifications`, `referral_partners`, plus contact tables (`lead_contacts`, `client_contacts`) and dated `resource_credentials`. Naming stays generic where reasonable so the *pattern* re-templates, not just the care-specific names.

### Sanctioned Vertical Surfaces

Four dashboard patterns are core; their content is seam. Business-specific views beyond these stay out of scope.

- **Leads** — marketing-funnel pipeline: stage board, per-stage outreach sequences (ordinary automations bound to a stage), lead directory with profiles and on-demand AI summaries, funnel metrics. A single stage-writer emits `lead.stage_changed` from every path.
- **Caregivers** — the same pipeline pattern for hiring (applied → hired, automated accept/deny emails); moving an applicant to Hired atomically creates their caregiver record. A **Roster** tab layers on workforce oversight (below).
- **Schedule board & matching** — a weekly board with open shifts and a call-out → replacement flow, backed by a **deterministic** caregiver matcher (geography, language/trait fit, availability, continuity, load) that gives plain-language reasons and warnings — no LLM in the ranking. The schedule seam is the single writer of schedule state; connector sync delegates to it.
- **Clients, census & EVV** — a clients directory with an active **census** (authorized vs scheduled vs delivered hours, revenue leakage, payer/region breakdowns — deterministic seam SQL), a per-client care overview, and in-app **EVV** (visit clock-in/out with read-time late/missed flags). A single `change_status` writer owns the client lifecycle.

Two more surfaces ride the above rather than adding new ones:

- **Referrals** — rides Leads: partners as enrichment-by-name over free-text `leads.source` (exact match, no FK, no backfill), ranked by conversion and hours-won.
- **Workforce roster** — rides Caregivers: headcount/utilization and credential expiry (valid / expiring / expired) derived at read time; inactive caregivers drop out of matching and the board.

### Connector Adapters & Entity Writers

The per-source translation layer — `services/connectors/adapters/*` (verify + normalize only) and `services/connectors/entity_writers.py` (auto-create/update canonical rows, promotion) — is a seam member alongside the entity migration. External platforms are source of truth; adapters never write a business table without entity resolution, and never link across the referral-partner or other enrichment tables.

---

## Principles & Constraints

- **Cost, not scope, is the limiting factor.** Building "more than needed" for this client is intended — a deliberate investment in reusable architecture for larger future clients. Optimize for API/infra spend, not for minimizing feature surface.
- **Health-adjacent data discipline.** Every table touching client/care data has `tenant_id`; every action touching it writes to the Event Log; anything that changes client-affecting state is gated by default unless explicitly marked safe.
- **No open-ended LLM writes.** Text-to-SQL is read-only and reporting-scoped; all structured writes go through named, parameterized tools.
- **Non-technical users.** Every surface that shows agent/workflow output to staff is plain language — no raw JSON or tool payloads in user-facing views (that belongs in traces and the Event Log's technical detail).
- **Scale discipline.** Low hundreds of documents, not thousands — retrieval sophistication is present but tuned for correctness at this scale, not premature optimization.
- **Single-tenant deployment, tenant-aware schema** — keeps the templating path open without building multi-tenant admin surface prematurely.

## Out of Scope

Knowledge graphs / GraphRAG · code execution/sandboxing · image/audio/video processing · fine-tuning · multi-tenant admin tooling (provisioning UI for multiple live businesses) · billing/payments · scheduled/automated re-ingestion (ingestion is manual-upload or event-triggered) · open-ended text-to-SQL against writable tables · PDF bounding-box citations · formal HIPAA certification (designed with audit/access discipline in mind; BAA/compliance review is a separate legal workstream) · entity write-back to external platforms · inbound-SMS conversation loops · business-specific views beyond the four sanctioned surfaces.

## Stack

| Layer | Choice |
|-------|--------|
| Frontend | React + TypeScript + Vite + Tailwind + shadcn/ui |
| Backend | Python + FastAPI |
| Database | Supabase (Postgres + pgvector + Auth + Storage + Realtime) |
| LLM | Anthropic Messages API (Claude Sonnet primary; Haiku for cheap high-volume classification/routing) |
| Embeddings / Reranking | Voyage AI |
| Agent tooling | MCP server (custom tools) |
| Automations | Custom in-app engine — event listeners + cron, durable runs, steps via the tool seam (no n8n) |
| Observability | LangSmith |
