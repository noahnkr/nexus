---
description: Build the current version from its plan
argument-hint: [plan path — optional; defaults to the version under "Next up" in PROGRESS.md]
---

# Build

Execute a version's plan: `$ARGUMENTS`. If empty, take the version under **Next up** in `PROGRESS.md` and its plan path (build order = version order — confirm it's the next unshipped version in `ROADMAP.md` before starting).

## Process

1. **Read the entire plan** — all tasks, dependencies, and validations. If the plan targets a version that isn't next in `ROADMAP.md`, stop and flag the ordering.

2. **Execute tasks in order** — implement each following project conventions and the seam boundaries in `CLAUDE.md`. Verify syntax and imports after each change. Tick each task `[x]` in `PROGRESS.md` as it lands.

3. **Run the validations** — every task's test/curl/browser check. Fix issues before moving on. For agent/tool-calling work, verify the LangSmith trace shows the expected step sequence, not just the final output.

4. **Report** — tasks completed, files created/modified, test results, any deviations from the plan and why, and any blocking ops steps that remain (secrets, live walks).

When the build is complete and green, run `/document` to fold it into `CHANGELOG.md` and mark the version shipped — don't hand-edit the shipped history.
