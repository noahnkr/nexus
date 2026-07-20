# Plan 18: External Services Connectors (parent)

**Complexity: 🔴 Complex** — real integrations with four external platforms, new in-app sync-loop infrastructure, a vertical schema migration, two OAuth bootstrap flows, and real implementations of the gated messaging tools. Per the CLAUDE.md planning rule (connector/MCP modules default 🔴) it is **broken into four sub-plans** (re-lettered 2026-07-20 when the WellSky direct connector was un-deferred into the 18b slot):

- **`18a.welcomehome-sync.md`** (⚠️ Medium) — the shared connector sync loop + single-ingest seam, the CRM-sync schema additions (`lead_contacts`, lead zip/address/background), the WelcomeHome export poller with stage/field/activity mapping, the one-time backfill, and call-transcription ingestion into RAG.
- **`18b.wellsky-sync.md`** (⚠️ Medium) — WellSky Personal Care (formerly ClearCare) direct sync: the line-of-business system's clients/caregivers/schedules feeding the M12/M15/M17 surfaces through the 18a sync loop. **Supersedes the 2026-07-18 deferral** (user decision 2026-07-20); planned against the public API docs (`https://apidocs.clearcareonline.com/`) — no API key yet, so live validations are credential-gated.
- **`18c.goto-connect.md`** (⚠️ Medium) — GoTo OAuth bootstrap (authorization-code → refresh token), WebSocket notification-channel bridge for call/SMS events, and the real `send_sms` implementation behind the existing gated tool.
- **`18d.google-workspace.md`** (⚠️ Medium) — Google OAuth bootstrap, Gmail poll (historyId) with attachments→ingestion and the real `send_email`, Google Calendar poll (syncToken) plus safe/gated calendar tools.

Build order: **18a → 18b → 18c → 18d** — 18a lands the sync-loop + ingest seam the other three plug runners into. If a sub-plan stalls, each has an internal fallback split noted at the top.

**Requires Modules 3b, 5, 7 built** (adapter/resolution seam, approval gate for outbound sends, the lifespan-loop precedent and event dispatcher that automations trigger from). Module 12/13 are independent of this work.

## Context

Module 3b built the connector *seam* — HMAC-verified ingress, adapters (verify+normalize), entity resolution via `external_ids`, `connector_state` — with placeholder adapters. Module 18 makes WelcomeHome, WellSky, GoTo Connect, Gmail, and Google Calendar real. ~~The WellSky **direct** adapter is deferred (user decision 2026-07-18): patient data will arrive through the announced WellSky↔WelcomeHome platform integration, i.e. via the WelcomeHome sync; the placeholder foundation stays.~~ **Superseded 2026-07-20**: the WellSky direct connector is un-deferred as sub-plan 18b — the M12 schedule board, M15 clients/EVV, and M17 roster (all built after the deferral) need line-of-business data that the WelcomeHome sync cannot carry; see `18b.wellsky-sync.md`.

**Planning-time empirical findings (2026-07-18, live credentials verified):**
- **WelcomeHome**: OpenAPI spec at `https://crm.welcomehomesoftware.com/api-docs/v1/swagger.yaml` (5,354 lines, downloaded and reviewed). Auth is `Authorization: Token token={key}`; `GET /api/ping` verified **HTTP 200** against the real account (`account_id` 18754, single community 65648 "Seniors Helping Seniors Greater Naperville"). **The API has no webhook endpoints** — polling is confirmed as the only inbound mechanism. The sync backbone is `GET /api/exports/community/{community_id|all}/table/{table}` (live paginated CSV; tables include `Prospects`, `ProspectFields`, `Residents`, `Influencers`, `Activities`, `ExternalReferences`, `MarketingTouchpoints`, `Referrers`, `Traits`, `Users`; `filters[updated_at_after]`, `sort_by=updated_at` default, `limit` ≤ documented max, `Link`-header cursor pagination with a **cursor-reuse limit of 3/minute**). The spec's own guidance: initial full pull per table, then periodic re-poll with `updated_at_after`. JSON REST endpoints also exist (`/prospects/{id}`, `/prospects/search`, `/activities` with the same filters, `/stages`, `/activity_types`, `/prospect_fields`, `/lead_sources`, `/relationships`, `/influencers/{id}`, `/residents/{id}`, `/prospects/{prospect_id}/attachments`).
- **Live WelcomeHome config**: stages `Inquiry` (`system_type=new_lead`, pos 0) → `Contact Attempted` → `Contact Made` → `Home Visit Scheduled` → `Home Visit Completed` (`visit`) → `Start of Care` (`move_in`); lead sources include the aggregators the PRD mentioned (A Place For Mom, Caring.com, CareInHomes, Care Patrol) with categories; prospect records carry `stage_id`, `lead_source_id`, `score_id`, `notes`, `status`, plus nested `residents_attributes` (the actual care recipients — names/contact live there), `influencers_attributes` (family/related contacts), and `prospect_field_values_attributes` (custom fields).
- **GoTo Connect**: the provided OAuth client **rejects `client_credentials`** (verified live: 401 "Unauthorized grant type") — the authorization-code flow with a one-time browser consent is required; the refresh token then drives everything. Notification channels support **webhook or WebSocket** delivery; call events subscribe via `POST /call-events/v1/subscriptions`; outbound SMS via the Messaging API. Channels expire and need renewal.
- **Gmail / Google Calendar**: push (Pub/Sub / watch channels) requires a public HTTPS endpoint; polling (`users.history.list` from a stored `historyId`; `events.list` with a stored `syncToken`, HTTP 410 ⇒ full resync) needs none and calls the same fetch code push would.

**User-locked decisions (2026-07-18):**
1. **Scope**: WelcomeHome + GoTo + Gmail/GCal in; WellSky direct adapter out (deferred until Connect access has a real driver; its data path is the WelcomeHome sync). **Amended 2026-07-20 (user decision)**: WellSky direct is back in as 18b, planned from the public ClearCare API docs; credentials are a blocking ops step for its live validations.
2. **Direction**: one-way inbound sync (external systems stay source of truth) + outbound **actions** only (gated `send_sms`/`send_email`/calendar create). No entity write-back — that's a future module if the office starts editing records in Nexus.
3. **WelcomeHome data scope**: prospects + stage mapping + one-time backfill; activities/messages/calls → lead timelines; call transcriptions → RAG ingestion; clients (residents) + influencers/relationships (schema additions).
4. **Poller infrastructure**: an in-app **connector sync loop** (lifespan task beside the M7 engine loops), not scheduled automations. CLAUDE.md's poll/export rule is amended accordingly.
5. **GoTo delivery**: WebSocket bridge (works on localhost, no tunnel); the HTTP webhook adapter stays for a future deployed setup.
6. **Google delivery**: polling with sync cursors; upgrading to Pub/Sub push later is additive (same fetch-back code).
7. **Credentials** live in the root `.env`: `WELCOMEHOME_API_KEY`, `GOTO_CONNECT_CLIENT_ID`, `GOTO_CONNECT_CLIENT_SECRET` are present and verified; Google + GoTo refresh tokens are produced by the bootstrap scripts (blocking ops steps flagged in 18b/18c).

**Non-goals** (name the destination): WellSky direct FHIR integration (Future Plans / a later module); entity write-back to any platform (future module on demand); inbound-SMS conversation loop & broadcast offers (Future Plans, per M12); a Connectors/Settings UI (Future Plans — config stays env-based per PRD); Pub/Sub push + webhook-channel production hardening (activation notes documented, built when the backend deploys); calendar view in the UI (Future Plans); EVV (Future Plans).

## Shared conventions (all sub-plans)

1. **Single ingest seam**: webhook processing is factored out of `routers/webhooks.py` into `services/connectors/ingest.py::ingest_payload(source, payload, headers, *, receipt_extra=None)` — raw receipt event first, then normalize → resolve, exactly today's semantics. The HTTP route becomes verify → `ingest_payload`; sync runners do fetch → translate → `ingest_payload` directly (they are the trusted fetcher; no self-HTTP, no signature). One path, so the CLAUDE.md "never a second inbound path" rule keeps meaning what it says.
2. **Sync loop**: `services/connectors/sync.py` runs in the FastAPI lifespan under `get_machine_tenant_id()`, gated by `NEXUS_CONNECTORS_ENABLED` (default true) with interval `NEXUS_CONNECTORS_POLL_SECONDS` (default 120). A registry of `SyncRunner`s; a runner is active only when its credentials are configured. Each cycle, per runner: read cursor from `connector_state.state` (keyed per source; the existing `(tenant_id, source_system)` row), fetch increments, ingest, advance the cursor in the same transaction as the last successful batch. Failures write a `connector.sync_failed` event (plain `payload.summary`) and never kill the loop (M7 recovery discipline). Every cycle wraps in a `connector_sync` `@traceable` span.
3. **Secrets in env only**: `connector_state` holds cursors, channel ids, and expiries — never tokens. OAuth refresh tokens land in `.env` via the bootstrap scripts.
4. **OAuth bootstraps** are one-time scripts under `backend/app/scripts/` (`goto_oauth.py`, `google_oauth.py`): spin a localhost redirect listener, print the consent URL, exchange the code, and print the `NAME=value` line for the operator to paste into `.env`. Token refresh at runtime goes through one shared helper `services/connectors/oauth.py` (httpx POST, in-process access-token cache with expiry slack).
5. **HTTP via httpx** with hand-rolled token refresh — no `google-api-python-client`, no GoTo SDK. The only new dependency is `websockets` (18b).
6. **Canonical events**: new inbound types are declared in the seam (`EVENT_ENTITY_TYPES` / `EVENT_PAYLOAD_FIELDS`) and `_KNOWN_EVENT_TYPES` so automations and the M11 field catalog see them immediately: `lead.activity_logged` (18a), `email.sent`, `calendar.event.created` (18c); existing `call.completed` / `sms.received` / `email.received` / `calendar.event.updated` / `lead.created` / `lead.updated` / `lead.stage_changed` are reused. Sync-written events carry `source_system='<connector>'` — dispatcher-eligible by design (automations *should* trigger on them; the M7 loop guard only excludes `automation`-sourced events).
7. **Tests**: translators/mappers/clients get offline tests against sanitized recorded fixtures (never real PII); anything needing live credentials is an env-gated skip, the established pattern. Live walks are explicit wrap-up tasks.
8. **Outbound stays gated**: `send_sms`/`send_email`/`create_calendar_event` keep their tool names, schemas, and gate semantics — only the placeholder internals become real. Approval flow, audit events, and automations behavior are unchanged.

## File layout after this module

```
supabase/migrations/20260730000000_entities_crm_sync.sql   # vertical: leads zip/address/background,
                                                           #   lead_contacts table (+RLS)
                                                           # (renamed 2026-07-20: must sort after M17's 20260729000000)
backend/app/
  config.py                    # + welcomehome/goto/google/connector-loop settings
  scripts/goto_oauth.py  scripts/google_oauth.py  scripts/backfill_welcomehome.py
  services/connectors/
    ingest.py                  # NEW: the single ingest seam (route + sync runners)
    sync.py                    # NEW: connector sync loop + SyncRunner registry
    oauth.py                   # NEW: shared OAuth refresh helper
    wh_client.py  wh_map.py  wh_runner.py          # 18a
    ws_client.py  ws_map.py  ws_runner.py          # 18b (WellSky — see 18b.wellsky-sync.md)
    goto_client.py  goto_bridge.py                 # 18c
    google_client.py  gmail_runner.py  gcal_runner.py  # 18d
    entity_writers.py          # + lead update writer, lead_contacts writer
    adapters/…                 # gmail/gcal accept the decoded (poll-produced) shape too
  services/messaging/goto_sms.py  services/messaging/gmail_send.py
  services/tools/entities.py   # + calendar tools; send_sms/send_email internals real
  routers/webhooks.py          # thinned: verify → ingest_payload
backend/tests/ test_wh_*  test_goto_*  test_google_*  test_connector_sync.py
```

Near-zero frontend changes in this module (events/timelines/tasks already render connector activity). Exception per the 18a revision (2026-07-20): two map entries in `lib/events.ts` (`welcomehome` source accent) and `lib/recipe.ts` (`lead.activity_logged` label) — the M13/M14a readability maps postdate the original plan; fallbacks are graceful but the entries keep the Event Log's source accents meaningful.

## Sub-plans

1. **`18a.welcomehome-sync.md`** *(revised 2026-07-20 against as-built M18–17 — see its surface-coverage audit)* — Tasks: (1) config + WH client + live smoke; (2) CRM-sync migration + seam threading; (3) ingest-seam refactor; (4) sync loop core; (5) single lead stage-writer extraction + WH mapping + resolution update-path + Start-of-Care client promotion + referral-source discipline; (6) WH runner + backfill script + transcription ingestion (M15 entity tag); (7) wrap-up + live incremental-sync walk.
2. **`18b.wellsky-sync.md`** — Tasks: (1) config + WS client (OAuth client-credentials token cache) + fixtures; (2) mapping (active clients / hired caregivers only); (3) people sync w/ link-or-create identity vs existing rows; (4) schedule seam `sync_upsert_visit` + encounter EVV clock; (5) appointment/encounter window sweeps (no modified-since on those resources); (6) DocumentReference → RAG; (7) wrap-up + credential-gated live-walk checklist (**blocking: WellSky Connect API credentials from an account rep**). No migration, no new tools, no new event types.
3. **`18c.goto-connect.md`** — Tasks: (1) OAuth bootstrap + refresh helper (**blocking: one-time browser consent**); (2) channel + call-events subscription manager; (3) WebSocket bridge runner; (4) real `send_sms`; (5) wrap-up + live call/SMS walk.
4. **`18d.google-workspace.md`** — Tasks: (1) Google OAuth bootstrap (**blocking: GCP OAuth client + consent**); (2) Gmail poll runner + attachments→ingestion; (3) real `send_email`; (4) Calendar poll runner; (5) calendar tools; (6) wrap-up + live email/calendar walk.

## Doc sync performed at planning time

- `PRD.md`: Module 18 promoted from the summary list to a full section (goal → sync architecture / per-platform groups → deliverable, plan paths); WellSky noted as deferred-direct.
- `PROGRESS.md`: Module 18 marked planned, sub-plan tasks listed as checkboxes.
- `CLAUDE.md`: the poll/export-pollers rule amended — pollers are the in-app connector sync loop entering through the single `ingest_payload` seam; secrets-in-env / cursors-in-`connector_state` convention recorded.
- `README.md`: not updated at planning time — each sub-plan's wrap-up documents its env vars, bootstrap runbooks, and the backfill procedure.
