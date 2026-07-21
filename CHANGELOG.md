# Changelog

Notable changes to the Nexus Control Center, newest first. Each entry is a high-level summary of what the version delivers; implementation detail lives in the plans (`.claude/plans/`) and the code. Versioning follows `ROADMAP.md` (semantic, by impact).

## v1.1.4 — One summary per person · 2026-07-21

Lead and client profiles used to carry two AI cards: a summary of the record and activity, and a separate communication profile describing how the person communicates. Two cards meant two Regenerate buttons and a split read of the same person. Now there's one:

- **A single "at a glance" summary** covering who they are, what has happened, and — when their correspondence shows it — how they communicate: preferred channel, tone, how readily they reply.
- **Caregiver profiles gained correspondence coverage** they never had. The old communication profile only ever existed for leads and clients; the merged summary is generic, so applicants pick it up automatically.
- **One cache, one Regenerate.** The summary is built on demand and refreshed when you ask for it.
- After a WelcomeHome history import, touched leads' summaries are **cleared rather than rebuilt**, so the next time you open a profile it reflects the newly-imported messages — at no cost for leads nobody opens.

## v1.1.3 — Timelines you can actually read · 2026-07-21

Entity timelines are the office's read on what has been happening with a lead, and most of what's on them comes from the CRM. They were a wall of one-line summaries with a raw-JSON expander. Now:

- **Emails read as text, not markup.** Email activity that arrived as HTML showed its tags in the timeline (`<b>Come See Us…</b><br><br>`). It now renders as plain, readable text — both for the ~330 emails already stored and for everything synced from here on.
- **Nothing is cut off any more.** Long calls and notes were clipped mid-sentence at a fixed limit even though the full text was recorded. Rows now show a short preview and expand to the complete text, however long — the longest note in the corpus runs about 4,500 characters.
- **Every row says what it is at a glance** — a mail, phone, message, note, or assessment icon per activity, and a colour bar keying the row to where it came from. Client and caregiver timelines got real icons too, instead of the warning symbol they were falling back to.
- **Expanding an entry shows the entry, not a data dump.** The full text comes first, then the details that matter (direction, when it happened, which stage moved, which fields changed) in plain labels. The raw record is still there, one click further down.

## v1.1.2 — The funnel now mirrors WelcomeHome stage for stage · 2026-07-21

A lead's position in Nexus used to be blurrier than what the office sees in the CRM: "Contact Attempted" and "Contact Made" both showed as *Contacted*, and both home-visit stages collapsed into *Qualified*. Now every WelcomeHome stage has its own:

- **Seven stages instead of five** — New, Contact Attempted, Contacted, Visit Scheduled, Visit Completed, Converted, and Lost. *Qualified* is gone; those leads moved to Visit Scheduled, and the WelcomeHome re-sync then placed each one exactly where the CRM has it. Lost stays what it was: a terminal archive for don't-contact and not-applicable inquiries, with no sequence attached.
- **Sequences on all six worked stages**, so the two newly-visible stages can be automated like any other.
- **A re-sync no longer clutters timelines.** Polled sources re-send whole records every sweep; an unchanged record now records nothing, instead of logging an "updated" entry for an edit nobody made.
- **Even funnel blocks.** Stage widths were proportional to lead count, which squeezed quieter stages — worse at seven stages. Every stage now gets equal width; share is still shown as a count and a percentage.

Deliberately sequenced before the WellSky connector, so that integration begins writing into a settled stage set rather than one reshaped underneath it.

## v1.1.1 — Chat turns no longer fail on tool-calling questions · 2026-07-21

- **Fixed: some questions failed outright in chat** with a `'NoneType' object has no attribute 'outputs'` message — including "what is my most recent touch point with…" and phone-number lookups. The fault was in the observability layer, not the questions: the LangSmith tracing wrapper crashed while recording the model's streamed reply. Chat now reads the reply in a way that avoids that path, with tracing intact and unchanged.
- **Errors in chat now speak plainly.** A failed turn says so in ordinary language instead of printing an internal error into the conversation; the technical detail goes to the server log, where it can actually be diagnosed.

## v1.1.0 — Communications tier & RAG hygiene · 2026-07-21

Conversations and documents are now two different things. Messages get their own home instead of being mixed into the document corpus, so the curated knowledge base stays clean as message volume grows:

- **Calls, emails, texts, and notes are stored as communications** — every one of them, linked to the entry it created on the lead's timeline. Previously only long narratives were kept, as documents; short messages left no searchable record at all.
- **Long-form correspondence is searchable in chat**, through its own search that's kept separate from document search — so asking "what did we discuss with the Ellisons?" looks at conversations, while "what does her care plan say?" looks at files. Short messages are stored but not indexed, since a two-line text is a record, not a reference.
- **A Communication profile on lead and client profiles** — an on-demand read of how someone communicates: tone, how responsive they are, which channel they prefer, and topics that keep coming up. It sits alongside the existing Smart summary.
- **The Knowledge view is now files-only.** WelcomeHome call and note transcripts no longer appear there as pseudo-documents.
- Groundwork for the messaging connectors: the upcoming GoTo (calls/SMS) and Gmail integrations write into this store rather than each inventing their own.

## v1.0.0 — WelcomeHome CRM sync · 2026-07-20

First live external data flowing end-to-end. Nexus now polls the WelcomeHome CRM and mirrors its sales pipeline into the canonical model:

- Prospects become leads (create + update) with stages mapped onto the Nexus funnel; family and decision-makers become lead contacts; referral sources are preserved verbatim so the referrals dashboard attributes conversions correctly.
- CRM activities (calls, emails, notes, visits) land on the lead's timeline; long call/note narratives become searchable in chat.
- Reaching "Start of Care" promotes a lead into an active client record automatically — connecting the sales funnel to the client census for the first time.
- Shared connector infrastructure the remaining integrations plug into: an in-app sync loop, a single ingest path used by both webhooks and polling, and a one-time history backfill. One-way inbound; outbound stays gated.

## v0.18.0 — Workforce & compliance · 2026-07-19

- Caregiver roster with a compliance view: headcount, utilization, and credential status (valid / expiring / expired) at a glance.
- Credential tracking with expiry surfaced weeks ahead; a daily digest automation names exactly whose credentials need attention.
- Deactivating a caregiver removes them from scheduling and matching while their history stays intact.

## v0.17.0 — Referral-source dashboard · 2026-07-19

- A referrals view ranking partners (hospitals, senior-living, discharge planners) by conversion rate and hours-per-week won, so relationship time goes where it pays off.
- Any lead source can be promoted to a tracked partner in one click; chat answers referral questions directly.

## v0.16.0 — Client & care oversight · 2026-07-19

- Clients directory with an active **census**: authorized vs scheduled vs delivered hours, and the revenue-leakage gap in one number, broken down by payer and region.
- Per-client care overview: smart summary, care plan documents (searchable in chat), family contacts, assigned caregivers, and visit history.
- In-app visit verification (EVV): clock-in/out on visits with automatic late/missed flags.

## v0.15.0 — Finishing touches · 2026-07-19

- Chat is interruptible (stop mid-answer) and renders document-style answers with tables.
- Tasks are completable in place: edit a drafted text/email right in the approval; clean labeled task detail, no raw data in the UI.
- A real Settings page, a collapsible + mobile-friendly shell, and per-tenant agent instructions (tone and guidance the assistant follows).

## v0.14.0 — Automation builder enhancements · 2026-07-19

- One consistent, searchable dropdown component across the whole app.
- The condition builder only appears when it can do something and always offers the trigger's real fields, with plain-language labels.

## v0.13.0 — Smart staffing & scheduling · 2026-07-18

- A weekly schedule board (caregivers as rows, open shifts pinned) with repeat-weekly visits.
- Deterministic caregiver matching — geography, language/trait fit, availability, continuity, load — with plain-language reasons, no black box.
- Call-out flow: a caregiver calls out, ranked replacements appear, assign in one click, notify by gated SMS.

## v0.12.0 — Automation field tokens · 2026-07-18

- The automation builder shows the trigger's real fields as labeled, searchable tokens — no more typing dotted paths by hand.

## v0.11.0 — Caregivers view & hiring · 2026-07-17

- Caregiver hiring pipeline (applied → hired, with automated accept/deny emails per stage); moving an applicant to Hired creates their caregiver record automatically.

## v0.10.0 — Leads view & marketing funnel · 2026-07-17

- Leads directory and pipeline with per-stage outreach sequences, funnel metrics, and on-demand AI summaries per lead.

## v0.9.0 — Automations center · 2026-07-17

- A monday.com-style grid to see, manage, and build automations — compose a recipe in a sentence builder, or describe it and let the agent draft it for review.

## v0.8.0 — Core automations framework · 2026-07-17

- The WHEN → IF → THEN engine everything automation-shaped runs on: event/cron/manual triggers, durable runs across delays, steps that execute through the audited/gated tool seam.

## v0.7.0 — Control center shell & auth · 2026-07-16

- Real login (Supabase Auth, tenant-scoped); a Home landing page; and a full visual overhaul into a professional, cohesive product shell.

## v0.6.0 — Approval gate & tasks · 2026-07-16

- State-changing actions queue as human-reviewable tasks instead of firing; approve/reject/edit with the whole trail in plain language. The Tasks interface for clearing that queue.

## v0.5.0 — Event log · 2026-07-16

- A filterable, live audit feed of everything that happened across every connected system and every agent action — plain-language summaries, raw detail one click away.

## v0.4.0 — MCP server & connector seam · 2026-07-16

- An MCP server exposing the same governed tools chat uses, and the inbound webhook seam that normalizes external events into canonical entities.

## v0.3.0 — Structured data access · 2026-07-16

- The agent gains governed access to structured data: parameterized read tools and a read-only reporting query tool, wired into chat as a real tool-using loop.

## v0.2.0 — Foundation chat + ingestion · 2026-07-14

- The first runnable app: upload documents, watch them process, and hold a streamed, cited chat over them.

## v0.1.0 — Canonical data model · 2026-07-14

- The tenant-isolated schema every other capability reads and writes: canonical entities, cross-system ID mapping, immutable event log, tasks, and the approval-gate table — with row-level security on every table and seed data.

---

**Pending live validations** (carried across releases): in-browser walks for the v0.10+ surfaces await the one-time auth ops step (create the office user with the tenant claim in the Supabase dashboard). Each release's automated suites (backend tests, frontend tests, build) were green at ship time.
