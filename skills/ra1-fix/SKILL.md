---
name: ra1-fix
description: Ready Agent 1 gears up your repo (the Loadout) — remediate agent-readiness gaps by applying safe configuration scaffolds, drafting documentation for review, and listing GitHub settings to change. Use when the user asks to fix readiness, remediate readiness findings, raise the readiness level, apply readiness fixes, or scaffold missing config (linters, CI, CODEOWNERS, issue/PR templates, dependabot, devcontainer, .env.example). Applies changes to a local branch only and never pushes without confirmation.
license: MIT
compatibility: Python 3.11+; git for branch/commit; run ra1-report first
metadata:
  version: 0.1.0
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Agent Readiness Fix

Raise a repo's readiness by applying only **safe** changes. The engine plans and writes config
scaffolds; you handle git and author any prose. Three buckets, three different safety levels.

## Steps

1. **Ensure a report exists.** If `.agents/readiness/latest.json` is missing, run the ra1-report
   skill first.

2. **Dry-run the plan** to see what would change:
   ```bash
   python3 "$(dirname "$0")/scripts/readiness/cli.py" fix --project <repo-path>
   ```
   It prints three buckets: **Auto-apply** (safe config scaffolds), **Propose** (drafts for review),
   **GitHub settings** (manual).

3. **Create a local branch first** (never work on the default branch):
   ```bash
   git -C <repo-path> checkout -b readiness/fixes
   ```

4. **Apply the safe scaffolds** (idempotent; refuses on a dirty worktree; never overwrites non-empty files):
   ```bash
   python3 "$(dirname "$0")/scripts/readiness/cli.py" fix --project <repo-path> --apply
   ```

5. **Author the "Propose" items yourself** — README sections, a tailored `AGENTS.md`, runbooks, a first
   test. Write these as **drafts for the user to review**; do not invent facts about the codebase.
   `templates/AGENTS.md` is a starting skeleton — fill it from what the repo actually does.

6. **Show the diff and commit locally.** Summarize changes, then:
   ```bash
   git -C <repo-path> add -A && git -C <repo-path> commit -m "chore: raise agent readiness"
   ```
   End the commit message with the required `Co-Authored-By` trailer.

7. **Re-run ra1-report** to show the level delta.

## Contract (do not violate)

- **Never push and never open a PR without explicit user confirmation.** Build and commit locally first.
- **Auto-apply config scaffolds only.** README/AGENTS.md/tests/runbooks are *proposed drafts* the user
  reviews — auto-writing prose risks "documenting fiction."
- **Never bundle GitHub setting changes** (branch protection, secret scanning, labels) with code commits.
  Present them as a checklist of `gh` commands for the user to run and confirm.
- Respect a dirty worktree: do not `--force` over uncommitted work without asking.
