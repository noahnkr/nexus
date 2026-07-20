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

The foundation (v0.1.0 → v0.18.0) and the first live connector (v1.0.0). Full notes in `CHANGELOG.md`.

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

## Planned

In build order. The next thing to build is the top of this list.

| Version | Capability | Plan | Notes |
|---|---|---|---|
| v1.0.1 | Fuzzy referral-source matching | `v1.0.1-fuzzy-matching-dedupe.md` | Patch on the referral join — WelcomeHome sync surfaces messy real-world source strings. |
| v1.1.0 | Communications tier & RAG hygiene | *(to plan)* | Separate messages from documents; store-all/embed-selectively; per-entity comms profile. **Foundational — lands before the messaging connectors so they build into the right substrate.** |
| v1.2.0 | WellSky Personal Care sync | `v1.2.0-wellsky-sync.md` | Line-of-business system: active clients, hired caregivers, full schedule + EVV, client files → RAG. **Blocked on API credentials (WellSky rep).** |
| v1.3.0 | GoTo Connect | `v1.3.0-goto-connect.md` | Calls + SMS via WebSocket bridge; real `send_sms`. One-time OAuth consent ops step. |
| v1.4.0 | Gmail & Google Calendar | `v1.4.0-google-workspace.md` | Correspondence (not lead intake — WelcomeHome owns that) + calendar; real `send_email`, gated `create_calendar_event`. One-time OAuth ops step. |

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
