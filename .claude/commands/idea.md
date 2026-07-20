---
description: Capture an idea and route it to the right version in the roadmap
argument-hint: [the idea]
---

# Idea

Capture a feature, improvement, or fix and **route it to the correct place in `ROADMAP.md`** — without building it: `$ARGUMENTS`.

This is the antidote to planning-later-work-first: an idea gets *slotted*, so build order stays deliberate. Capturing an idea is never a licence to build it now.

## Process

1. **Classify the impact** — is it a new capability/subsystem (**minor**), a tweak/fix to an existing one (**patch**), or a re-template/breaking change (**major**)? This decides its version shape.

2. **Find its dependencies** — what must exist before it can be built? Read `ROADMAP.md` (Planned + Backlog) and, if needed, the relevant `PRD.md` component. If it depends on unbuilt work, it goes *after* that work — never ahead of it.

3. **Route it** — pick exactly one:
   - **Slot into an existing planned version** if it clearly belongs there (add a bullet to that row/plan).
   - **Give it its own version** and place it in the _Planned_ list **in dependency order** (not at the top unless it's genuinely next).
   - **Park it in _Backlog_** if it's real but not yet prioritized — the default when unsure. A backlog item is deliberately *not* being built.

4. **Write it down** — add the entry to `ROADMAP.md` with a one-line scope and, where useful, its dependency ("after v1.1.0 — needs the comms tier"). If it displaces or reorders anything, say so.

## Output

Report: the idea, its impact class, where it landed (version or backlog), and — if it has dependencies — what must ship first. Do **not** start building it.
