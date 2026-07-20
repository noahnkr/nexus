# Progress

The working board for the version being built and the ones queued next. Claude Code reads this at the start of a session to see where the build stands.

- **Ordered version index + backlog:** `ROADMAP.md` (build order = version order).
- **Shipped history:** `CHANGELOG.md`.
- **Architecture each version touches:** `PRD.md`.

Task status: `[ ]` not started · `[-]` in progress · `[x]` done.

## Now

**v1.0.0 — WelcomeHome CRM sync — shipped** (merged 2026-07-20). Nothing is actively mid-build. Next up is **v1.0.1** (a patch, plan written) or **v1.1.0** (the foundational knowledge rework); run `/plan` on whichever is chosen to populate its tasks below.

## Next up

### v1.0.1 — Fuzzy referral-source matching · patch
Plan: `.claude/plans/v1.0.1-fuzzy-matching-dedupe.md`
- `[ ]` Tasks to be enumerated by `/plan` (alias/fuzzy match of `leads.source` → tracked referral partners, replacing today's exact-name join).

### v1.1.0 — Communications tier & RAG hygiene · new capability
Plan: *(to write — `/plan v1.1.0`)*. Foundational: lands before the messaging connectors so they build into the right substrate.
- `[ ]` Communications store (channel/direction/timestamp/body/entity link, optional embedding) separate from `documents`
- `[ ]` Store-all / embed-selectively policy; `kind`/`source` discriminator on searchable chunks + retention
- `[ ]` Per-entity communication profile via the `entity_summaries` seam (tone/style as summary, not retrieval)
- `[ ]` Event-as-spine linkage + cross-source de-duplication; migrate the v1.0.0 transcript path off `documents`
- `[ ]` Split history-seed: structured pass / batched embed pass / summary pass

## Queued (planned, blocked or later)

### v1.2.0 — WellSky Personal Care sync · new capability
Plan: `.claude/plans/v1.2.0-wellsky-sync.md`. **Blocked: API credentials from a WellSky rep** — build/tests run offline against fixtures; live checks are credential-gated. Rides the v1.0.0 sync loop + ingest seam.
- `[ ]` Config (`WELLSKY_*`) + `ws_client.py` (token cache, pagination, retries) + fixtures; offline + credential-gated live token test
- `[ ]` `ws_map.py` (active-clients-only, deactivation→discharge, hired-caregivers-only, appointments/encounters/contacts); offline mapping tests
- `[ ]` People sync: link-or-create writers (phone→name match vs promoted/manual rows, ambiguity → review task); gated tests
- `[ ]` Schedule seam `sync_upsert_visit` + EVV `check_in`/`check_out` (idempotent re-sweeps); gated seam tests
- `[ ]` Window sweeps (per-client horizon, encounter lookback) with DB diffing; offline two-cycle tests
- `[ ]` Client files (DocumentReference) → RAG, entity-tagged; offline + gated retrieval tests
- `[ ]` Wrap-up: README scope table, `.env.example`, event accent; full pytest + build green

### v1.3.0 — GoTo Connect · new capability
Plan: `.claude/plans/v1.3.0-goto-connect.md`. **Ops step: one-time browser OAuth consent → refresh token in `.env`.**
- `[ ]` OAuth bootstrap script + shared refresh helper; gated live token test
- `[ ]` WebSocket channel + call/SMS subscription manager (state/renewal in `connector_state`)
- `[ ]` WebSocket bridge runner (reconnect/backoff → `ingest_payload`); fake-WS test + live call → timeline
- `[ ]` Real `send_sms` behind the existing gated tool; mocked tests + live approved delivery
- `[ ]` Wrap-up: README bootstrap runbook; full pytest; live walks recorded

### v1.4.0 — Gmail & Google Calendar · new capability
Plan: `.claude/plans/v1.4.0-google-workspace.md`. **Ops step: GCP OAuth client + consent → `GOOGLE_*` in `.env`.** Scope: ongoing correspondence + calendar — **lead intake stays WelcomeHome's job; Gmail never creates leads.**
- `[ ]` Google OAuth bootstrap + `google_client.py` (shared TokenSource); gated live profile test
- `[ ]` Gmail poll runner (historyId cursor, no backfill, SENT filtered); aggregator-notification senders skipped, human correspondence → comms; live email → timeline/RAG
- `[ ]` Real `send_email` (gate unchanged, `email.sent` event); mocked + live approved delivery
- `[ ]` Calendar poll runner (syncToken, 410 resync); offline + live event-change walk
- `[ ]` Calendar tools: safe `list_calendar_events`, gated `create_calendar_event`; gated tests + live chat-scheduled tour
- `[ ]` Wrap-up: README Google runbook; full pytest; `connector_sync` spans verified

## Carried-over pending validations

- **Live in-browser walks** for the v0.10+ surfaces await the one-time auth ops step (create the office user with the `app_metadata.tenant_id` claim in the Supabase dashboard — README → Auth setup). Automated suites were green at each ship (`pytest backend/tests`, `npm run test`, `npm run build`).
- **v1.0.0 live steps** (operator actions, not code): a real WelcomeHome write-backfill (imports real PII, leaves immutable `events` rows) and the live incremental walk (change a WH stage → observe the lead update within one poll).
