---
description: Plan the next module (explore → clarify → plan → sync docs)
argument-hint: [module-number-or-name (optional — defaults to the next unstarted module)]
---

# Plan Module

Plan the next phase of the project: `$ARGUMENTS` (if empty, pick the first module in `PROGRESS.md` that is not started and not deferred).

Prerequisite: run the `/onboard` process first if you don't already have current context on the repo and its progress.

## Process

1. **Scope the module**
   - Read the module's section/summary in `PRD.md`, the rules in `CLAUDE.md`, and the previous module's plan in `.agent/plans/` for house style
   - Explore the existing code the module builds on (schema/migrations, backend services, test fixtures, env vars) — prefer reusing existing patterns and fixtures over inventing new ones
   - Verify assumptions empirically where cheap (e.g., query the DB for a role/column that a design decision hinges on) rather than trusting docs

2. **Ask clarifying questions**
   - Before designing, surface the decisions that genuinely belong to the user: ambiguous scope boundaries (what's in this module vs. deferred to a later one), competing approaches with real trade-offs, and anything the PRD leaves open
   - Ask them as concrete either/or options with a recommendation — not open-ended questions
   - Don't ask about things CLAUDE.md/PRD already decide, or that have an obvious conventional answer; lock those in yourself and record them as design decisions

3. **Design and write the plan** to `.agent/plans/{sequence}.{plan-name}.md`, matching the house style of the existing plans:
   - Complexity indicator at top (✅ / ⚠️ / 🔴 per CLAUDE.md; Modules 3, 5, 8 default 🔴 and must be broken into sub-plans) with a one-line justification and, for large plans, explicit fallback split points
   - **Context** section: why this module, what exists already, what the user locked in, and explicit non-goals (name the module each deferred item lands in)
   - **Key design decisions (locked in — do not re-litigate during build)**: numbered, concrete (DDL sketches, endpoint contracts, file-by-file layout), each with a one-line justification
   - **File layout after this module** (fenced tree)
   - **Tasks** in strict execution order, each with at least one concrete, runnable **Validation** (a pytest file + assertion, a curl check, a browser check); flag any blocking user input (secrets, ops steps) at the task where it's needed
   - **Execution notes**: blocking inputs, existing fixtures/patterns to reuse (with file paths), scope discipline reminders
   - The plan must be self-contained — `/build` executes it top-to-bottom without re-reading this conversation

4. **Sync the project docs** (only where the plan actually changes them):
   - `PRD.md` — promote the module from the "Subsequent Modules" summary list into its own `## Module N: <Name>` section (Goal → feature groups → infrastructure introduced → Deliverable, ending with the plan path), styled like the existing module sections; remove its line from the summary list
   - `PROGRESS.md` — mark the module `[-]` Planned with the plan path, and list the plan's tasks as individual `[ ]` checkboxes so `/build` can tick them off
   - `CLAUDE.md` — add/amend rules **only** for durable, cross-module conventions the plan establishes (e.g., a new DB-access pattern, a new core table, a new seam); never copy module-specific detail that belongs in the plan
   - `README.md` — update only if the module changes how someone runs or sets up the project (new services, env vars, getting-started steps); otherwise leave it alone

## Output

Report: the plan path, the complexity rating, the user decisions collected, which docs were updated and why, and any blocking inputs the user must provide before `/build` can complete.
