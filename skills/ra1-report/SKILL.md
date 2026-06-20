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
   if `gh` is authenticated, the GitHub API). Mirroring Droid's `/readiness-report`, require an
   `origin` remote and persist local history:

   ```bash
   python3 "$(dirname "$0")/scripts/readiness/cli.py" report \
     --project <repo-path> --format json,markdown \
     --require-origin --store-history --out <repo-path>/.agents/readiness
   ```

   This requires a git repo with an `origin` remote (drop `--require-origin` to scan an arbitrary
   local path — an RA1 extension). It writes `report.md`, `report.json`, and the canonical
   `latest.json` under `.agents/readiness/`, plus an immutable timestamped snapshot and index under
   `.agents/readiness/history/<identity_hash>/`, and prints the canonical JSON. The report carries a
   redacted `repository` identity (no remote credentials, no absolute paths). Read the prior
   `latest.json` first, if present, to compute a **Δ vs last run** (only when the schema, engine,
   registry, and detector versions match; otherwise skip the delta).

2. **Emit the score verbatim.** Your final report MUST contain a fenced ```json block holding the
   engine's `score` object **exactly** — `level`, `level_name`, `pass_rate`, `gating_passed`,
   `gating_total`, `pillars`. Do not change a single number. This is the authoritative, reproducible score.

3. **Add the human summary** from the engine's markdown (Level, Applications, per-pillar criteria with
   their statuses and cited evidence, Action Items).

4. **Add a `## T4 Advisory` section** (qualitative, non-gating). The engine deliberately leaves these
   soft judgments to you; label each clearly as advisory opinion grounded strictly in engine findings
   and files you actually read. Use these labelled sub-headings:
   - **Naming consistency** · **Code modularization** · **README quality** · **AGENTS.md quality** ·
     **Service-flow docs** · **Runbook usefulness** · **Autonomy workflow maturity**.
   For each, cite the specific file/finding, explain *why* it matters, and give the highest-leverage
   next step. Example (good): "AGENTS.md quality (advisory): the build section names `make test` but
   the repo uses `pytest` (see AGENTS.md L12 vs pyproject) — align them so an agent picks the right
   command." Note stale or low-quality docs the engine can only see as "present".

## Contract (do not violate)

- **Never claim a higher Level than the engine reports.** The fenced score block is the source of truth.
- **Never mark a failing criterion as passing.** If the engine says `fail`/`unknown`, your prose must agree.
- **Never invent criteria, evidence, or passing results.** Cite only what the engine surfaced or files you actually read.
- **Never claim autonomy clearance.** Do not describe the repo as ready for unattended/autonomous
  operation unless the engine reports **Level 5 (Autonomous)**; T4 commentary is advice, not clearance.
- **Do not assert that a specific criterion is "gating" or "non-gating"** — only the engine's data says so. Don't add caveats absent from the findings.
- T4 advisory is opinion and is explicitly **non-gating** — it cannot move the Level, GitHub annotations, JUnit, or SARIF.

## Notes

- If the engine reports `project_type: unknown`, surface that honestly — type-dependent criteria are
  `unknown`, not silently skipped. Suggest the user pin the type via `.agents/readiness/config.json`.
- T2 (GitHub) criteria are `skipped` when `gh` is unavailable; recommend authenticating `gh` for a fuller score.
- To raise the score, hand off to the **ra1-fix** skill (the Loadout).
