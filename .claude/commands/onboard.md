---
description: Onboard Claude Code into the codebase
---

# Context

## Process

1. **Scan structure**
   - Run `git ls-files` to see all tracked files

2. **Read key files**
   - `CLAUDE.md` (how it's built + seam boundaries), `PRD.md` (component/architecture reference)
   - `ROADMAP.md` (ordered versions + backlog), `PROGRESS.md` (active build board), `CHANGELOG.md` (shipped history)
   - Entry points and config files, core schemas/models, the most recent shipped plan in `.claude/plans/`

3. **Check state**
   - Run `git status` and `git log -10 --oneline`

## Output

Provide a brief summary:
- What this project does
- Tech stack
- How it's organised
- Current branch and recent activity
