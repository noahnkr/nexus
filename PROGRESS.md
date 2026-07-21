# Progress

The working board for the version being built and the ones queued next. Claude Code reads this at the start of a session to see where the build stands.

- **Ordered version index + backlog:** `ROADMAP.md` (build order = version order).
- **Shipped history:** `CHANGELOG.md`.
- **Architecture each version touches:** `PRD.md`.

Task status: `[ ]` not started · `[-]` in progress · `[x]` done.

## Now

**v1.1.2 shipped (2026-07-21) — nothing mid-build.** The lead funnel now mirrors WelcomeHome stage for stage: seven stages instead of five, with `qualified` retired and every WH-sourced lead placed by a corrective re-sweep. Re-syncs no longer log "updated" for records that didn't change, and funnel blocks render at even width. **Several validations are carried, not clean** — see _Carried-over pending validations_, including a dev-DB data-loss incident during the build. Next is **v1.1.3**. Run `/plan` to plan it.

## Next up

### v1.1.3 — Timeline readability · fix
No plan yet. Legibility pass on entity timelines and event logs: welcome home activitys  icons per activity type, headings/colour, and a best-effort structured renderer for the highly variable JSON detail. Two concrete defects to fix along the way: email bodies render raw HTML tags, and long entries get cut off. Begin with an analysis of real activity payloads to find what structure actually recurs.

### v1.1.4 — One smart summary per entity · fix
No plan yet. Merge the separate communication-profile card into the smart summary so one "at a glance" section covers the record, activity, communication history, and significant facts. Leads and clients lose the second card and its `comm-profile` endpoints; caregivers gain comms coverage in the summary they already have. Seam-local to `services/views/summary.py` — the `entity_summaries.kind` column stays.

## Queued (planned, blocked or later)

### v1.2.0 — WellSky Personal Care sync · new capability
Plan: `.claude/plans/v1.2.0-wellsky-sync.md`. **Blocked: API credentials from a WellSky rep** — build/tests run offline against fixtures; live checks are credential-gated. Rides the v1.0.0 sync loop + ingest seam. Client files land in the **documents** tier; any message/note content goes through `ingest_communication` (v1.1.0), never into `documents`.
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

### v1.5.0 — Cross-source communication identity · new capability
No plan yet. **After v1.3.0 + v1.4.0** — the duplicates don't exist until GoTo and Gmail do, and the merge rules need real payloads to tune. One interaction = one row whatever it came in through: a canonical action shape + fuzzy reconciler in the `ingest_communication` seam, channel classification lifted out of per-adapter maps, and suppression of WelcomeHome's provisional bridge-call legs. The narrow bridge-number guard lands earlier, inside v1.3.0.

## Carried-over pending validations

- **v1.1.2 full-suite failures (8), none caused by this version.** Verified by re-running each at HEAD with v1.1.2 stashed:
  - *Dev-DB volume residue (4, fail identically at HEAD)* — `test_tool_report::test_run_report`, `test_referrals::test_seam_hand_computed_rows`, `test_mcp_server::test_tools_call_list_leads_and_audit`, `test_tools_entities::test_entity_tools`. All assert against the 6-lead seed while the dev DB holds 100 leads (90 WelcomeHome-synced); e.g. Margaret Ellison exists and is `new`, but 37 `new` leads push her off the first page.
  - *External quota (3)* — `test_retrieval_comms` (×2) + `test_tool_communications` fail on a Voyage AI `RateLimitError`. They pass when the quota allows; unrelated to code.
  - *Known flaky (1)* — `test_automation_scheduler::test_cron_fires_once_then_reschedules`; passes in isolation. Same family as the flaky stop-contract test noted under v1.1.1.
- **v1.1.2 dev-DB data loss (repaired by re-sync, history not recoverable).** `test_wh_runner._cleanup` deleted every entity whose external id matched `wh:%` — on this DB that meant the 90 live-synced leads, their contacts, and their start-of-care clients, plus the `welcomehome` `connector_state` row. The Task 6 re-sweep re-imported all 90 from WelcomeHome (source of truth), so the pipeline is whole again, but re-synced leads carry **new entity ids**: the ~8k pre-existing WelcomeHome `events` and 276 `communications` rows still point at the deleted ids and are now orphaned. Immutable by design, so there is no repair — decide whether to prune or leave them. The cleanup is now narrowed to the fixture's own ids so this cannot recur.
- **v1.1.2 browser + CRM spot-checks not done.** The seven-column board walk (Task 5) and the 3-lead Nexus-vs-WelcomeHome stage comparison in the CRM UI (Task 6) both need a human at a browser; the mapping itself is deterministic and unit-covered.

- **v1.1.1 full-suite run never completed** — *superseded:* the v1.1.2 full run above completed all 416 tests with no hang, so the `idle in transaction` blocker is gone. Original note: The targeted suites are green — `test_chat_tools`, `test_chat_stop`, `test_chat_stream_tracing`, `test_chat_api`, `test_chat_schema` pass together (24), the fail-first tripwire was proven (reverting the fix → 9 failures), and `test_leads_api` passes on its own. But `pytest backend/tests` (415 tests) **hung twice at test 213/415, `test_leads_api`**, blocked by a session left `idle in transaction` on `public.leads` by a previously-killed run. Clearing it needs `pg_terminate_backend` on the live DB (not done — permission-gated). Re-run the full suite once that session is cleared. Two pre-existing defects surfaced and are now in the roadmap backlog: the hang itself (dev-DB residue) and a flaky stop-contract test (4/15 on unmodified HEAD, 1/15 with v1.1.1 — not caused by this version).

- **Live in-browser walks** for the v0.10+ surfaces. The auth ops step is **done** — the office user exists, is confirmed, carries the `app_metadata.tenant_id` claim, and last signed in 2026-07-21, so this is no longer blocked; what remains is walking the surfaces in a browser (`uvicorn` + `npm run dev`, sign in at `/login`). Automated suites were green at each ship (`pytest backend/tests`, `npm run test`, `npm run build`).
- **v1.0.0 live steps** (operator actions, not code): a real WelcomeHome write-backfill (imports real PII, leaves immutable `events` rows) and the live incremental walk (change a WH stage → observe the lead update within one poll). As of v1.1.0 the backfill also seeds the communications tier in three passes (store → embed → comm profiles), so a live run now costs embedding and summary API calls it previously didn't.
- **v1.1.0 LangSmith trace confirmation** (operator action, not code): the four new spans are instrumented and exercised by the green gated suite — `ingest_communication` and `embed_communication` (chains), `retrieve_communications` (retriever), `comm_profile` (chain). Eyeballing them in the LangSmith UI needs a running app against a configured `LANGSMITH_API_KEY`; not done from this session.
