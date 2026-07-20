---
description: Plan the next version (explore → clarify → plan → sync docs)
argument-hint: [version or name — optional; defaults to the top unshipped entry in ROADMAP.md]
---

# Plan

Plan a version: `$ARGUMENTS`. If empty, take the **top unshipped entry under _Planned_ in `ROADMAP.md`** (build order = version order — never plan a later version before an earlier one; if the target is out of order, stop and say so).

Prerequisite: run `/onboard` first if you don't already have current context on the repo.

## Process

1. **Scope the version**
   - Read its row in `ROADMAP.md` and any existing plan/backlog note. Read the relevant component in `PRD.md`, the rules in `CLAUDE.md`, and the most recent shipped plan in `.claude/plans/` for house style.
   - Explore the existing code it builds on (schema/migrations, services, fixtures, env vars). Prefer reusing existing patterns and fixtures over inventing new ones.
   - Verify assumptions empirically where cheap (query the DB for a column/role a decision hinges on) rather than trusting docs.
   - Confirm the version number fits its impact: **minor** = a new capability/subsystem, **patch** = a tweak/fix to an existing one. Adjust the number if the scope doesn't match.

2. **Ask clarifying questions** — only the decisions that genuinely belong to the user: ambiguous scope boundaries (in this version vs. deferred to a later one), competing approaches with real trade-offs, anything the PRD leaves open. Ask as concrete either/or options with a recommendation. Lock in anything CLAUDE.md/PRD already decide yourself.

3. **Write the plan** to `.claude/plans/vX.Y.Z-<name>.md`, matching house style:
   - Complexity indicator at top (✅ / ⚠️ / 🔴 per CLAUDE.md) with a one-line justification and, for large plans, explicit fallback split points. Break 🔴 plans into ordered sub-parts.
   - **Context**: why now, what exists, what the user locked in, explicit non-goals (name the version each deferred item lands in).
   - **Key design decisions (locked in — do not re-litigate during build)**: numbered, concrete (DDL sketches, endpoint contracts, file-by-file layout), each with a one-line justification.
   - **File layout after this version** (fenced tree).
   - **Tasks** in strict execution order, each with at least one concrete, runnable **Validation** (a pytest file + assertion, a curl check, a browser check). Flag blocking user input (secrets, ops steps) at the task where it's needed.
   - Self-contained — `/build` executes it top-to-bottom without re-reading the planning conversation.

4. **Sync the docs** (only where the plan changes them):
   - `ROADMAP.md` — if scope/order/version changed, update the row; move it to the top of _Planned_ if it's next. Slot in any newly-deferred items to _Backlog_.
   - `PROGRESS.md` — put the version under **Next up** with its plan path and every task as a `[ ]` checkbox so `/build` can tick them off.
   - `PRD.md` — update the affected **component** description only if the plan changes the architecture (a new subsystem, table, or seam). Never add a build-timeline section; PRD describes components, not sequence.
   - `CLAUDE.md` — add/amend rules only for durable, cross-version conventions the plan establishes (a new DB pattern, core table, or seam). Never copy version-specific detail that belongs in the plan.
   - `README.md` — update only if the plan changes how someone runs or sets up the project.

## Output

Report: the plan path, the version + complexity, user decisions collected, which docs were updated and why, and any blocking inputs the user must provide before `/build`.
