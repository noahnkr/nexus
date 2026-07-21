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

## Planned

In build order. The next thing to build is the top of this list.

| Version | Capability | Plan | Notes |
|---|---|---|---|
| v1.1.3 | Entity timeline readability | *(to plan)* | Structure and legibility pass on entity timelines: per-activity-type icons, headings/colour, a best-effort renderer for the variable JSON detail, plus two defects — **email bodies render raw HTML tags**, and **long timeline entries get cut off**. Start with an analysis of real activity payloads. |
| v1.1.4 | One smart summary per entity | *(to plan)* | Merge the separate communication-profile card into the smart summary: one "at a glance" section covering the record, activity, communication history, and significant facts. Leads + clients lose the second card and its `comm-profile` endpoints; caregivers gain comms coverage in the summary they already have. Seam-local (`services/views/summary.py`) — the `entity_summaries` `kind` column stays. Retires the *Comm-profile freshness* backlog item (one cache, one Regenerate). |
| v1.2.0 | WellSky Personal Care sync | `v1.2.0-wellsky-sync.md` | Line-of-business system: active clients, hired caregivers, full schedule + EVV, client files → RAG. **Blocked on API credentials (WellSky rep).** |
| v1.3.0 | GoTo Connect | `v1.3.0-goto-connect.md` | Calls + SMS via WebSocket bridge; real `send_sms`. One-time OAuth consent ops step. **Plan must include a known-numbers guard** so WelcomeHome's provisional bridge number doesn't ingest as real client calls — the narrow half of v1.5.0, needed the day GoTo goes live. |
| v1.4.0 | Gmail & Google Calendar | `v1.4.0-google-workspace.md` | Correspondence (not lead intake — WelcomeHome owns that) + calendar; real `send_email`, gated `create_calendar_event`. One-time OAuth ops step. |
| v1.5.0 | Cross-source communication identity | *(to plan)* | One interaction = one row, whatever it came in through. WelcomeHome's messaging center sends texts/emails/calls of its own, so the same conversation arrives twice (WH activity + Gmail thread / GoTo call+SMS) and today's `content_hash` can't match them — different bodies, different timestamps, different numbers. Adds a canonical action shape (channel · direction · participants · occurred_at) + a fuzzy reconciler in the `ingest_communication` seam that merges legs and keeps the richest source, and pushes channel classification out of per-adapter maps (`wh_map.ACTIVITY_CHANNELS`). Also suppresses **WelcomeHome bridge legs**: a WH-initiated call dials the office first, so GoTo logs a call to/from WH's provisional number that is plumbing, not correspondence. **After v1.3.0 + v1.4.0** — the duplicates don't exist until those sources do, and the merge rules need real payloads to tune. |

## Backlog

Unslotted ideas. Each gets a version and a plan when prioritized — until then it is deliberately *not* being built.

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
