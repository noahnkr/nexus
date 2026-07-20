---
description: Make a small change and route it to a patch version
argument-hint: [what to change]
---

# Tweak

A small, self-contained change to an existing capability — a fix, a refinement, a rough edge — that doesn't warrant a full `/plan`: `$ARGUMENTS`.

A tweak is a **patch** version (`x.y.Z`) on the line of whatever capability it touches. If the change is actually a new capability/subsystem, stop — that's a **minor**; use `/idea` then `/plan` instead.

## Process

1. **Locate and size it** — find the code and the seam it lives in (`CLAUDE.md`). Confirm it's genuinely a tweak (bounded, no new subsystem/table/tool). Pick the patch version: the next `x.y.Z` on the affected capability's line (e.g. a referral fix on the v1.0 line → `v1.0.2`).

2. **Make the change** — follow existing conventions; keep the blast radius small. For anything non-trivial, write the fix test-first (a failing test that reproduces the issue, then the fix).

3. **Validate** — run the affected tests (and `npm run build` if frontend). Green before done.

4. **Record it** — for a substantive tweak, add a one-line `## vX.Y.Z — <title> · <date>` entry to `CHANGELOG.md` (high-level, user-facing) and note it in `ROADMAP.md` under _Shipped_ if it's worth tracking. A truly trivial fix (typo, lint) needs no version — just say so.

## Output

Report: what changed, the patch version assigned (or "no version — trivial"), files touched, test results.
