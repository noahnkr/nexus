# Progress

The working board for the version being built and the ones queued next. Claude Code reads this at the start of a session to see where the build stands.

- **Ordered version index + backlog:** `ROADMAP.md` (build order = version order).
- **Shipped history:** `CHANGELOG.md`.
- **Architecture each version touches:** `PRD.md`.

Task status: `[ ]` not started · `[-]` in progress · `[x]` done.

## Now

**v1.1.0 shipped (2026-07-21) — nothing mid-build.** The communications tier is in: messages have their own store and search, and lead/client profiles carry a Communication profile card. Next is **v1.2.0 — WellSky Personal Care sync**, which is **blocked on API credentials from a WellSky rep**; the build and its tests run offline against fixtures, so it can start before the credentials land — only the live checks are gated. Run `/build` to begin.

## Next up

### v1.2.0 — WellSky Personal Care sync · new capability
Plan: `.claude/plans/v1.2.0-wellsky-sync.md`. **Blocked: API credentials from a WellSky rep** — build/tests run offline against fixtures; live checks are credential-gated. Rides the v1.0.0 sync loop + ingest seam. Client files land in the **documents** tier; any message/note content goes through `ingest_communication` (v1.1.0), never into `documents`.
- `[ ]` Config (`WELLSKY_*`) + `ws_client.py` (token cache, pagination, retries) + fixtures; offline + credential-gated live token test
- `[ ]` `ws_map.py` (active-clients-only, deactivation→discharge, hired-caregivers-only, appointments/encounters/contacts); offline mapping tests
- `[ ]` People sync: link-or-create writers (phone→name match vs promoted/manual rows, ambiguity → review task); gated tests
- `[ ]` Schedule seam `sync_upsert_visit` + EVV `check_in`/`check_out` (idempotent re-sweeps); gated seam tests
- `[ ]` Window sweeps (per-client horizon, encounter lookback) with DB diffing; offline two-cycle tests
- `[ ]` Client files (DocumentReference) → RAG, entity-tagged; offline + gated retrieval tests
- `[ ]` Wrap-up: README scope table, `.env.example`, event accent; full pytest + build green

## Queued (planned, blocked or later)

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

- **Live in-browser walks** for the v0.10+ surfaces. The auth ops step is **done** — the office user exists, is confirmed, carries the `app_metadata.tenant_id` claim, and last signed in 2026-07-21, so this is no longer blocked; what remains is walking the surfaces in a browser (`uvicorn` + `npm run dev`, sign in at `/login`). Automated suites were green at each ship (`pytest backend/tests`, `npm run test`, `npm run build`).
- **v1.0.0 live steps** (operator actions, not code): a real WelcomeHome write-backfill (imports real PII, leaves immutable `events` rows) and the live incremental walk (change a WH stage → observe the lead update within one poll). As of v1.1.0 the backfill also seeds the communications tier in three passes (store → embed → comm profiles), so a live run now costs embedding and summary API calls it previously didn't.
- **v1.1.0 LangSmith trace confirmation** (operator action, not code): the four new spans are instrumented and exercised by the green gated suite — `ingest_communication` and `embed_communication` (chains), `retrieve_communications` (retriever), `comm_profile` (chain). Eyeballing them in the LangSmith UI needs a running app against a configured `LANGSMITH_API_KEY`; not done from this session.
