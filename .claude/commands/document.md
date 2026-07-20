---
description: Sync the docs after a build — ship the version, update the record
argument-hint: [version — optional; defaults to the version just built]
---

# Document

Fold a completed build into the project docs and mark the version shipped: `$ARGUMENTS` (defaults to the version whose tasks are now `[x]` in `PROGRESS.md`).

Run this after `/build` is green. It is the only sanctioned writer of shipped history — never hand-edit past `CHANGELOG.md` entries.

## Process

1. **Confirm it's done** — the version's tasks are complete and validations pass. Note any ops steps still outstanding (they get carried, not hidden).

2. **`CHANGELOG.md`** — add a `## vX.Y.Z — <title> · <date>` entry at the top (newest first). Keep it **high-level and user-facing**: what the version delivers, not how it's implemented. A few bullets. Convert relative dates to absolute.

3. **`ROADMAP.md`** — move the version from _Planned_ to _Shipped_ (add its row to the table). If new follow-up ideas surfaced during the build, append them to _Backlog_.

4. **`PROGRESS.md`** — clear the shipped version from _Next up_ / _Queued_, update **Now** to point at the next version, and carry any outstanding ops steps into _Carried-over pending validations_.

5. **`PRD.md`** — only if the build changed the architecture: update the affected **component** description to match what now exists (drop "planned" qualifiers, correct stale detail). Never add a build-timeline section.

6. **`README.md` / `CLAUDE.md`** — update only if setup/usage or a durable cross-version rule changed.

## Output

Report: the version shipped, which docs were updated, and any ops steps still pending.
