# Roadmap

The single source of truth for **what version is what**, in build order. Shipped history is summarized in `CHANGELOG.md`; the active version's task board is in `PROGRESS.md`; the architecture each version touches is described in `PRD.md`.

## Versioning

Semantic versioning by **impact**, not by calendar or build number:

- **MAJOR** (`2.0.0`) — a re-template to a new vertical, or a breaking change to the core platform.
- **MINOR** (`1.1.0`) — a new capability or subsystem (a connector, a view, a knowledge tier).
- **PATCH** (`1.0.1`) — a tweak, fix, or refinement to an existing capability.

Two rules keep planning honest:

1. **Build order = version order.** The top unshipped entry under *Planned* is what gets built next. A later version is never built before an earlier one.
2. **Ideas get routed, not built.** A new idea lands in *Backlog* first, then gets slotted into a specific version (or promoted to *Planned*) when it's prioritized — so the dependency order is decided deliberately, not by whatever was thought of first.

Each planned version has a plan at `.claude/plans/vX.Y.Z-<name>.md` before it's built.

## Shipped

The foundation (v0.1.0 → v0.18.0), the first live connector (v1.0.0), and the communications knowledge tier (v1.1.0). Full notes in `CHANGELOG.md`.

| Version | Capability |
|---|---|
| v0.1.0 | Canonical data model |
| v0.2.0 | Foundation chat + ingestion |
| v0.3.0 | Structured data access (tool layer) |
| v0.4.0 | MCP server + connector seam |
| v0.5.0 | Event log |
| v0.6.0 | Approval gate + tasks |
| v0.7.0 | Control center shell + auth |
| v0.8.0 | Core automations framework |
| v0.9.0 | Automations center |
| v0.10.0 | Leads view + marketing funnel |
| v0.11.0 | Caregivers view + hiring |
| v0.12.0 | Automation field tokens |
| v0.13.0 | Smart staffing + scheduling |
| v0.14.0 | Automation builder enhancements |
| v0.15.0 | Finishing touches (chat/tasks/shell/settings) |
| v0.16.0 | Client & care oversight (census + EVV) |
| v0.17.0 | Referral-source dashboard |
| v0.18.0 | Workforce & compliance (roster + credentials) |
| **v1.0.0** | **WelcomeHome CRM sync** — first live external data flowing end-to-end |
| **v1.1.0** | **Communications tier & RAG hygiene** — messages get their own store; per-entity communication profile |
| **v1.1.1** | **Chat streaming/tracing fix** — tool-calling questions no longer fail; chat errors speak plainly |
| **v1.1.2** | **WelcomeHome stage reflection** — seven-stage funnel mirroring the CRM one-to-one; quiet re-syncs; even funnel blocks |
| **v1.1.3** | **Entity timeline readability** — plain-text email bodies, no more clipped entries, per-activity icons, shared structured detail renderer |
| **v1.1.4** | **One smart summary per entity** — the communication profile folds into the one summary; caregivers gain comms coverage; one cache, one Regenerate |
| **v1.1.5** | **Windows startup fix** — the documented `uvicorn` command works; the app sets the selector event-loop policy the DB driver needs |
| **v1.2.0** | **GoTo Connect** — calls + SMS land on the right person's timeline via phone matching; real gated `send_sms`; WH bridge-number guard. *Calls are metadata-only: the account has no call recording* |

## Planned

In build order. The next thing to build is the top of this list.

| Version | Capability | Plan | Notes |
|---|---|---|---|
| v1.3.0 | Gmail & Google Calendar | `v1.3.0-google-workspace.md` | **The authoritative source for email.** Ongoing correspondence into the communications tier + calendar; real `send_email`, gated `create_calendar_event`. Lead intake stays WelcomeHome's job — Gmail never creates leads. One-time OAuth ops step (self-service). |
| v1.3.1 | Retire WelcomeHome as a communications source | *(to plan)* | With GoTo and Gmail live, WelcomeHome goes back to what it is authoritative for — **leads and referrers** — and stops feeding the communications tier. **Measured 2026-07-21 (browser walk): WelcomeHome truncates 484 of 572 email bodies (85%) to ~150 characters before we ever receive them** — the stored `detail.notes` literally ends in `...`, and no other activity type is affected (Call 0/139, Note 0/117, Text 0/37, Assessment 0/22, all full to 3,000-4,500 chars). So the email text simply is not obtainable from this source, which makes those 484 bodies close to worthless in the RAG corpus and means v1.1.3's "nothing gets cut off" fix can only ever be half-true for email. **This is the strongest single argument for the version** — Gmail is not merely better-structured, it is the only way to have the actual email text. Its activity data is also unstructured: `occurred_at` is a `completed_at → scheduled_at → created_at` guess made at mapping time, HTML is baked into note bodies, the schema varies per activity type, and WH's own `/activity_types` list is documented as incomplete. **Gated on v1.2.0 + v1.3.0** — the comms tier is already canonical (`channel`/`direction`/`occurred_at` are real typed columns), so this is about *which sources fill it*, and cutting before the replacements are live would leave a window with no correspondence in RAG at all. **Open scope for the plan:** stop routing WH messaging (Call/Email/Text) through `ingest_communication`; decide *separately* whether the CRM-native activities (Notes, Assessments, Home Visits — nothing else will ever carry them) keep their `lead.activity_logged` timeline entries; and whether existing WH-sourced `communications` rows are pruned or left. Updates the CLAUDE.md knowledge-tier rule that names "CRM activities" as a message source. **Absorbs the former v1.5.0** (see below). |
| v1.4.0 | WellSky Personal Care sync | `v1.4.0-wellsky-sync.md` | Line-of-business system: active clients, hired caregivers, full schedule + EVV, client files → RAG. Rides the v1.0.0 sync loop + ingest seam. **Deferred to last — blocked on API credentials from a WellSky rep**, the only remaining dependency that isn't self-service. Build/tests run offline against fixtures; live checks are credential-gated. |

**Retired: v1.5.0 — Cross-source communication identity.** It existed to merge duplicate legs of one interaction: WelcomeHome's messaging center sends its own texts/emails/calls, so the same conversation would arrive twice (WH activity + Gmail thread / GoTo call), and `content_hash` can't match them — different bodies, timestamps, numbers. It planned a canonical action shape and a fuzzy reconciler in the `ingest_communication` seam.

Choosing **one authoritative source per channel** (v1.3.1) dissolves the problem instead of solving it: GoTo owns calls and SMS, Gmail owns email, and WelcomeHome stops sending messaging at all — so no two sources describe the same interaction and there is nothing to reconcile. The two durable pieces survive elsewhere: the **bridge-number guard** is part of v1.2.0 (GoTo needs it the day it goes live, independent of any of this), and the comms tier is **already canonical** — `channel`, `direction`, `occurred_at`, `subject`, `source`, `content_hash` are typed columns, so the "canonical action shape" was largely built in v1.1.0. If real multi-source payloads later show genuine overlap, re-route it as a new idea rather than reviving this row.

## Backlog

Unslotted ideas. Each gets a version and a plan when prioritized — until then it is deliberately *not* being built.

- **Tidy the inert Windows event-loop fix** — *housekeeping, not a blocker: the documented `--reload` command works.* It works because uvicorn's own `asyncio_setup()` sets the selector policy in subprocess mode, though — not because of v1.1.5's statement in `main.py`, which runs after uvicorn has already built the loop and therefore does nothing. Either move it to an entrypoint script or delete it and document `--reload` as required, so nobody drops the flag and rediscovers a `PoolTimeout` that reads like bad credentials.
- **Call transcripts, if GoTo recording is ever enabled** — v1.2.0 established empirically (2026-07-22) that this account produces **no call recordings**: 100 real calls over 90 days carry zero recording fields, and the recording API has nothing to return. That is a GoTo Admin setting / plan-tier question, not an API one, so calls ship as metadata. **If recording is switched on, this becomes additive**: fetch the transcript, `ingest_communication` it as the call's body, and calls join RAG like texts already do. *Separate lead found the same day: voicemail transcription endpoints (`/voicemail/v1/voicemails/search`, `.../transcriptions`) are real and answer 403 `AUTHZ_INSUFFICIENT_SCOPE` — reachable by adding a voicemail scope and re-consenting. Voicemail ≠ calls, so it does not satisfy the original requirement on its own.*
- **Call transcripts via `REPORT_SUMMARY`, if the `cr.v1.read` scope turns out to be what unlocks it** — the 2026-07-22 fix subscribes calls to `ENDING` because `REPORT_SUMMARY` is rejected with `Events[0] INVALID` on this account. GoTo's Call Events Report guide names `cr.v1.read` as a required scope and **this account has never consented to it**, which is the leading explanation. Confirming costs a re-consent (an ops step), so it was not tested. If it works, `REPORT_SUMMARY` restores the original design — one frame carrying a complete call — and removes the `ENDING`-payload uncertainty below.
- **Verify the GoTo notification frame shape against a live call** — *now more important than when it was written:* the 2026-07-22 subscription fix means calls arrive as **`ENDING`** frames, while every `gt_map` fixture is `REPORT_SUMMARY`-shaped. v1.2.0's `gt_map` fixtures for the call-history record are real (captured from the account), but the WebSocket envelope comes from GoTo's published Call Events Report schema rather than an observed frame. `gt_map` scans structurally for numbers rather than trusting field names, so a shape drift degrades to "no counterpart found → ack-only receipt" rather than bad data on a record — but the first live call is the real test, and the fixture should be replaced with a captured frame once one exists.
- **Manual test runs can send real messages** — `.env` carries live GoTo credentials, so any test exercising `send_sms` unstubbed would put an actual text on an actual phone. Two suites (`test_tools_write`, `test_automation_scheduler`) now stub the provider deliberately, but nothing *enforces* it. Worth a conftest-level guard that fails loudly if an outbound provider is called during tests. The same trap arrives again with `send_email` in v1.3.0.
- **Retention / at-risk roster view** — rule-based flags (declining hours vs 4-week average, repeated no-shows, short tenure via a `hire_date`).
- **Per-credential `credential.expiring` events** — a flagging tool with dedup state so automations trigger per credential; today a daily digest covers it.
- **Credential-based scheduling blocks** — hard-block assigning a caregiver with an expired credential (matching only warns today).
- **`leads.referral_partner_id` FK** — only if partner-rename stability becomes a real problem; enrichment-by-name is the deliberate default.
- **Structured care-plan editor** — goals / ADLs / care tasks as structured data; today care plans are tagged documents + a free-text summary.
- **Billing / payroll export** — export delivered-vs-authorized hours to a billing system (connector-shaped).
- **Home census stat card** — surface the revenue-leakage number on the Home page.
- **Chat document export** — PDF/print export of document-style chat answers.
- **Mobile layouts for the schedule board & automation builder** — the two dense surfaces that stay desktop-first today.
- **Test-suite isolation from dev-DB residue** — several tests assert on exact seed counts (e.g. `run_report` expecting exactly six leads), so rows left behind by an interrupted run fail them spuriously and cost real debugging time. Either seed/teardown per run or assert on relative deltas. **Escalated during v1.1.1 (2026-07-21): this now also hangs the suite outright.** A killed run leaves a session `idle in transaction` on `public.leads` through the pooler, and the next full run blocks forever on `test_leads_api` (test 213/415) instead of failing. Wants a connect/statement timeout in the test harness so residue surfaces as a fast failure, plus teardown that closes transactions on abort. **Update (v1.1.2, 2026-07-21): the hang is gone** — the full suite ran all 416 tests to completion — but the count assertions still fail, now four of them (`test_run_report`, `test_seam_hand_computed_rows`, `test_tools_call_list_leads_and_audit`, `test_entity_tools`), because the dev DB holds 100 leads against a 6-lead seed. Two failure shapes: exact-count equality, and seed rows pushed off a tool's first result page.
- **Flaky cron-scheduler test** — `test_automation_scheduler.py::test_cron_fires_once_then_reschedules` fails inside a full-suite run but passes in isolation (observed at v1.1.2). Same timing-barrier family as the stop-contract test below; likely wants a real await rather than a sleep.
- **Flaky stop-contract test** — `test_chat_stop.py::test_stop_during_tool_loop_keeps_alternation` fails intermittently (measured 4/15 on unmodified HEAD at v1.1.1). It aborts a turn mid-tool-loop and gives the shielded persistence task two `asyncio.sleep(0)` ticks to settle, which is not a reliable barrier; the assertion then sees `['user']` instead of `['user', 'assistant']`. Wants a real await on the persistence task rather than tick-counting.
- **Manual "log a communication" entry** — the communications tier is connector/seed-fed only; an office user can't record a walk-in or a personal-phone call. Would need the write path plus a UI surface.
- **Summary freshness** — summaries are cached until someone hits Regenerate, so they silently go stale as new activity and messages arrive. Options: show what the summary was built from, or invalidate on new events/communications. *(Folded from the old comm-profile freshness item — v1.1.4 collapses the two caches into one, so this covers the single summary.)*
