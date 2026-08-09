---
name: ra1-fix
description: Ready Agent 1 gears up your repo (the Loadout) — remediate agent-readiness gaps by applying safe configuration scaffolds, drafting documentation for review, and listing GitHub settings to change. Use when the user asks to fix readiness, remediate readiness findings, raise the readiness level, apply readiness fixes, or scaffold missing config (linters, CI, issue/PR templates, dependabot, devcontainer, .env.example). Applies changes to a local branch only, verifies every write, and never pushes without confirmation.
license: MIT
compatibility: Python 3.11+; git for branch/commit; run ra1-report first
metadata:
  version: 0.11.0
allowed-tools: Bash
---

# Agent Readiness Fix

Raise a repo's readiness by applying only **safe** changes. The engine plans, writes, and
**verifies** every scaffold; you present the canonical `fix_contract`. No direct file writes, no
git branch/commit/push/PR commands, and no prose authoring: the engine owns the remediation
contract and you report it exactly.

## Steps

`<skill-dir>` below is this skill's own directory: the absolute path your runtime reports when it
loads the skill. Substitute it. Do not write `$(dirname "$0")`.

1. **Plan from a fresh scan** (source-less; no stored report is required, and `latest.json` is
   never a dependency — a first-run report-persistence refusal cannot deadlock this skill):

   ```bash
   python3 -I "<skill-dir>/scripts/readiness/cli.py" fix --project . --format json
   ```

   The canonical JSON `fix_contract` carries `operation: "plan"`, the normalized plan buckets
   (`auto` with shared-target `criterion_ids`, `propose`, `github`, `manual`), and
   `verification.status: "not_run"`. Focus the plan deterministically with `--include <id>...` /
   `--exclude <id>...` (authoritative criterion-id filters only — never free-form instructions),
   or the `--instructions` keyword→pillar grammar, which annotates unsupported phrases as a note
   rather than silently filtering. Present the plan and the engine-owned explanation for every
   proposed action; never reconstruct a trace from prose.

2. **Apply only on explicit current-turn mutation request**, using the fixed grammar:

   ```bash
   python3 -I "<skill-dir>/scripts/readiness/cli.py" fix --project . --apply --format json
   ```

   `--apply` always requires a clean/known Git worktree (no bypass), runs a fresh baseline scan,
   refuses on repository-indeterminate or incomplete static/Git evidence (and requested-T2
   incompleteness), performs create-only scaffold writes (existing targets are skipped proposals;
   `.gitignore` is create-if-missing only, never appended), re-scans with identical options, and
   computes the authoritative comparable delta. Append `--github` only when the current-turn
   request explicitly requires online T2 verification, and paired `--host-proxy` only on a
   separate current-turn host-proxy request. `fix` never writes report/history/latest.

3. **Report only the canonical `fix_contract`** — present it verbatim: `operation: "apply"`, the
   fresh plan, the exact `apply_result` (`written`/`skipped` targets with their complete
   `criterion_ids`), and `verification` with `status` (`passed`/`failed`/`not_run`), `errors`,
   `confirmed_ids`, `unresolved`, `regressions`, before/after `level`, and `decision_successful`.

   - `confirmed_ids` are the only IDs you may call fixed/verified — never an unresolved or
     regressed one.
   - On post-write failure the behavior is intentional and explicit: created files are retained
     (never rolled back, never auto-deleted) and every retained creation plus its unresolved or
     regression state is reported so the maintainer can inspect or revert with normal version
     control. Generating or storing a subsequent full report is a separate explicit `ra1 report`
     invocation, never an automatic post-fix step.

## Contract (do not violate)

- **Never push and never open a PR without explicit user confirmation.** You do not run git
  branch/commit/push/merge commands at all — branching and committing are the user's controlled
  action after reviewing the contract.
- **No direct file tools.** You never read/write/edit repository files; every mutation goes
  through the fixed `fix --apply` command.
- **Auto-apply config scaffolds only.** README/AGENTS.md/tests/runbooks are *proposed drafts* the
  user reviews; the engine never auto-writes prose, and neither do you.
- **Never bundle GitHub setting changes** with code commits. `github` bucket items are a manual
  checklist of commands the user runs and confirms — you never execute them.
- **Never claim success without verification.** Only `verification.status: "passed"` plus the
  matching `confirmed_ids` authorizes the words "fixed" or "verified"; everything else stays
  unresolved/regressed as recorded.
- Respect a dirty worktree: `fix --apply` refuses (never bypasses) a dirty or indeterminate Git
  state — there is no force flag.