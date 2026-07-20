# Progress

Release-by-release build status for the Nexus Control Center. Claude Code reads this file at the start of a session to understand where the project stands; update tasks as work completes. **Shipped history lives in `CHANGELOG.md`** (one release per module, Module N → v0.N+1.0, all of v0.1.0–v0.18.0 code complete); this file tracks only the current release. Module numbering follows the PRD (renumbered 2026-07-20: External Connectors moved to Module 18 as the final module; Finishing Touches/Clients/Referrals/Workforce shifted to 14–17; implemented plan files retired).

## Convention
- `[ ]` = Not started
- `[-]` = In progress
- `[x]` = Completed

## Current release: v1.0.0 — Module 18: External Services Connectors

`[-]` In progress — 🔴 Complex, four sub-plans. Parent: `.agent/plans/18.external-connectors.md`. Build order 18a → 18b → 18c → 18d (18a lands the shared sync loop + ingest seam the other three plug runners into). Planning research ran against the live WelcomeHome/GoTo APIs (2026-07-18) and the public WellSky Connect spec (2026-07-20). One-way inbound + gated outbound actions only; in-app connector sync loop; creds in root `.env` (`WELCOMEHOME_API_KEY`, `GOTO_CONNECT_CLIENT_ID/SECRET` present; WellSky + Google credentials are blocking ops steps).

**18a — WelcomeHome CRM sync** (`.agent/plans/18a.welcomehome-sync.md`) — *plan revised 2026-07-20 against the as-built v0.15–v0.18 surfaces (surface-coverage audit added; Start-of-Care client promotion + referral-source contract + single lead stage-writer + M15-tag transcription ingestion)*:
- `[x]` Task 1 — Config + `wh_client.py` (export CSV pager w/ Link cursors + rate respect, JSON reference endpoints) + offline fixtures; gated live ping test **(live ping verified: account_id 18754)**
- `[x]` Task 2 — Migration `20260730000000_entities_crm_sync.sql` (leads `zip`/`address`/`background`, `lead_contacts` mirroring `client_contacts` + RLS) **pushed** + seed rows + seam threading (SQL_SCHEMA_DOC, sql_guard allowlist); gated schema tests green
- `[x]` Task 3 — Ingest-seam refactor (`ingest.py::ingest_payload`; route = verify → ingest); existing connector tests pass unmodified + new direct-call parity test
- `[x]` Task 4 — Connector sync loop (`sync.py`, SyncRunner registry, cursors in `connector_state`, `connector.sync_failed` isolation, `after_commit` hook, lifespan + `NEXUS_CONNECTORS_*`); gated loop tests + lifespan boot smoke (on/off)
- `[x]` Task 5 — `views/leads.change_stage` extraction (single stage-writer; REST + tool delegate, existing tests pass unmodified) + WH mapping (`wh_map.py`, system-activity skip discovered live) + resolution update path (`updates_entity`, `UPDATERS`) + Start-of-Care client promotion (`clients.lead_id`, contacts copy, `client.created`) + verbatim `leads.source` (M16 contract); offline + gated tests green
- `[x]` Task 6 — WH runner (`wh_runner.py`, per-table cursors, refs cache, transcript queue) + `backfill_welcomehome.py` (idempotent, resumable, promotion-bounded + discharge-sweep runbook) + `ingest_text` via the M15 document entity tag; offline runner + gated tests green. **Live dry-run verified against the real account (70 prospects / 406 activities, read-only). Live write-backfill deferred as an ops step — it writes real PII and leaves immutable `events` rows in the demo tenant.**
- `[x]` Task 7 — Wrap-up: README connectors section + `.env.example`; frontend map entries (`welcomehome` accent, `lead.activity_logged` label) + `npm run build` clean; full pytest 382 passed (1 pre-existing flaky waker test unrelated to 18a). **Remaining ops step: live incremental walk (change a WH stage → observe lead status + `lead.stage_changed` within one poll) requires a running server + a human editing WelcomeHome.**

**18b — WellSky Personal Care sync** (`.agent/plans/18b.wellsky-sync.md`) — planned 2026-07-20 from the public Connect API spec (OAuth client-credentials, watermark search on people, windowed appointment/encounter sweeps, certificates API disabled → no credential sync). User-locked: active clients only; hired caregivers only; full schedule + EVV sync through the M12/M15 seams; DocumentReference → RAG. No migration, no new tools, no new event types. **Blocking: `WELLSKY_CLIENT_ID`/`SECRET` from a WellSky rep** — all live checks credential-gated; requires 18a Tasks 1–4 first.
- `[ ]` Task 1 — Config (`WELLSKY_*`) + `ws_client.py` (token cache, `_search`/`search_all` pagination, trailing-slash rules, retries) + sanitized fixtures; offline client tests + credential-gated live token test
- `[ ]` Task 2 — `ws_map.py` (patient active-only filter + deactivation→discharge, practitioner `is_hired`, appointment/encounter/relatedperson); offline mapping tests
- `[ ]` Task 3 — People sync: link-or-create writers (phone→name match vs promoted/manual rows, ambiguity → review task), contact upserts, runner watermark stages via `ingest_payload`, real adapter normalize; gated link/create/two-cycle tests
- `[ ]` Task 4 — Schedule seam `sync_upsert_visit` (single-writer rule holds) + EVV via seam `check_in`/`check_out` (idempotent re-sweeps); gated seam tests incl. census delivered-hours delta
- `[ ]` Task 5 — Window sweeps (per-client `weekNo` horizon, encounter lookback) with DB diffing; offline two-cycle diff tests
- `[ ]` Task 6 — DocumentReference → RAG (download, entity-tagged ingestion, `documents_ingested` ledger, format/size caps); offline + gated retrieval tests
- `[ ]` Task 7 — Wrap-up: README (scope table, divergence caveat, subscriptions upgrade path, live-walk checklist), `.env.example`, `lib/events.ts` wellsky accent; full pytest + build green

**18c — GoTo Connect** (`.agent/plans/18c.goto-connect.md`):
- `[ ]` Task 1 — OAuth bootstrap (`scripts/goto_oauth.py`) + shared `oauth.py` refresh helper; **blocking ops: one-time browser consent → `GOTO_CONNECT_REFRESH_TOKEN` in .env**; gated live token test
- `[ ]` Task 2 — WS channel + call-events subscription manager (state/renewal in `connector_state`); empirically settle SMS-on-channel vs Messaging-API-poll fallback
- `[ ]` Task 3 — WebSocket bridge runner (`websockets` dep, reconnect/backoff → `ingest_payload`); fake-WS offline test + **live call → `call.completed` on the lead timeline**
- `[ ]` Task 4 — Real `send_sms` (`services/messaging/goto_sms.py`, gate unchanged); mocked tests + **live approved SMS delivery**
- `[ ]` Task 5 — Wrap-up: README bootstrap runbook; full pytest; live walks recorded

**18d — Gmail & Google Calendar** (`.agent/plans/18d.google-workspace.md`):
- `[ ]` Task 1 — Google OAuth bootstrap + `google_client.py` (httpx, shared TokenSource); **blocking ops: GCP OAuth client + consent → `GOOGLE_*` in .env**; gated live profile test
- `[ ]` Task 2 — Gmail poll runner (historyId cursor, no backfill, SENT filtered) + attributed-sender attachments → ingestion; offline + **live email w/ PDF → timeline + RAG**
- `[ ]` Task 3 — Real `send_email` (`gmail_send.py`, gate unchanged, `email.sent` event); mocked + live approved delivery
- `[ ]` Task 4 — Calendar poll runner (syncToken, 410 resync); offline + live event-change walk
- `[ ]` Task 5 — Calendar tools: safe `list_calendar_events`, gated `create_calendar_event` (+ `calendar.event.created`, calendar `external_ids`); gated tool tests + live chat-scheduled tour
- `[ ]` Task 6 — Wrap-up: README Google runbook; full pytest; LangSmith `connector_sync` spans verified

## Carried-over pending validations (pre-v1.0.0)

- Live in-browser walks for the v0.10–v0.18 surfaces await the one-time Module 6 ops step: create the office user with the `app_metadata.tenant_id` claim in the Supabase dashboard (documented in the README auth section). Automated suites were green at each ship: `pytest backend/tests` 319 passed, `npm run test` 101, `npm run build` clean as of v0.18.0.
