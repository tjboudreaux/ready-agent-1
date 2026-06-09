---
name: ra1-report
description: Ready Agent 1 scans your repo for agent-readiness — a deterministic, cited score (Level 1–5 across Style & Validation, Build System, Testing, Documentation, Dev Environment, Security, and Task Discovery) plus advisory guidance. Use when the user asks to assess agent readiness, score a repo, check whether a codebase is ready for AI agents, run Ready Agent 1, generate a readiness report, or find out a repo's readiness level. Runs a local pure-stdlib engine; the score is reproducible and the agent only adds non-gating advisory.
license: MIT
compatibility: Python 3.11+; optional authenticated gh CLI for GitHub (T2) checks
metadata:
  version: 0.1.0
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Agent Readiness Report

Produce a trustworthy readiness report for a repository. The **deterministic engine owns the
score**; you (the agent) add **advisory** commentary only. You must never change the engine's score.

## Steps

1. **Run the engine** (it does all the deterministic work — file/config parsing, git history, and,
   if `gh` is authenticated, the GitHub API):

   ```bash
   python3 "$(dirname "$0")/scripts/readiness/cli.py" report \
     --project <repo-path> --format json,markdown --out <repo-path>/.agents/readiness
   ```

   This writes `report.md`, `report.json`, and `latest.json` under `.agents/readiness/` and prints
   the canonical JSON. Read `.agents/readiness/latest.json` from the prior run first, if present, to
   compute a **Δ vs last run** (only when engine + registry versions match; otherwise skip the delta).

2. **Emit the score verbatim.** Your final report MUST contain a fenced ```json block holding the
   engine's `score` object **exactly** — `level`, `level_name`, `pass_rate`, `gating_passed`,
   `gating_total`, `pillars`. Do not change a single number. This is the authoritative, reproducible score.

3. **Add the human summary** from the engine's markdown (Level, Applications, per-pillar criteria with
   their statuses and cited evidence, Action Items).

4. **Add an `## Advisory` section** (non-gating). Here you may, grounded strictly in the engine's
   findings and the cited files:
   - Judge the soft criteria the engine leaves to you (naming consistency, README/AGENTS.md quality,
     code modularization, observability depth) — clearly labelled as advisory opinion.
   - Explain *why* failing criteria matter and the highest-leverage next steps.
   - Note stale or low-quality existing docs the engine can only see as "present".

## Contract (do not violate)

- **Never claim a higher Level than the engine reports.** The fenced score block is the source of truth.
- **Never mark a failing criterion as passing.** If the engine says `fail`/`unknown`, your prose must agree.
- **Never invent criteria, evidence, or passing results.** Cite only what the engine surfaced or files you actually read.
- **Do not assert that a specific criterion is "gating" or "non-gating"** — only the engine's data says so. Don't add caveats absent from the findings.
- Advisory is opinion and is explicitly **non-gating** — it cannot move the Level.

## Notes

- If the engine reports `project_type: unknown`, surface that honestly — type-dependent criteria are
  `unknown`, not silently skipped. Suggest the user pin the type via `.agents/readiness/config.json`.
- T2 (GitHub) criteria are `skipped` when `gh` is unavailable; recommend authenticating `gh` for a fuller score.
- To raise the score, hand off to the **ra1-fix** skill (the Loadout).
