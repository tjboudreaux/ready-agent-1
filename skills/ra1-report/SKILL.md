---
name: ra1-report
description: Ready Agent 1 scans your repo for agent-readiness — a deterministic, cited score (Level 1–4, with Level 5 Autonomous reserved, across 9 pillars) plus advisory guidance and evidence explanations. Use when the user asks to assess agent readiness, score a repo, check whether a codebase is ready for AI agents, run Ready Agent 1, generate a readiness report, or find out a repo's readiness level. Runs a local pure-stdlib engine; the score is reproducible, T2 GitHub checks are offline by default, and the agent only adds non-gating advisory.
license: MIT
compatibility: Python 3.11+; optional authenticated gh CLI for GitHub (T2) checks
metadata:
  version: 0.11.0
allowed-tools: Bash
---

# Agent Readiness Report

Produce a trustworthy readiness report for a repository. The **deterministic engine owns the
score**; you (the agent) add **advisory** commentary only. You must never change the engine's score,
reason codes, evidence, or limitations, and you must never invent any of them.

## Steps

`<skill-dir>` below is this skill's own directory: the absolute path your runtime reports when it
loads the skill. Substitute it. Do not write `$(dirname "$0")` — in a shell tool call `$0` is the
shell, not this file.

1. **Run the engine** (it does all the deterministic work — file/config parsing, git history, and,
   when requested, the GitHub.com API). The repository must have `.ra1/reports/` ignored in its
   root `.gitignore` for in-repo persistence; otherwise the engine prints the full report in memory
   and reports the exact isolation error, which you present as storage unavailable while the
   assessment itself is complete.

   ```bash
   python3 -I "<skill-dir>/scripts/readiness/cli.py" report --project . \
     --format json,markdown,html --out .ra1/reports --store-history
   ```

   JSON is first so stdout is the canonical machine payload; Markdown/HTML/history are safe
   engine-written artifacts. T2 GitHub checks are offline by default; append the fixed `--github`
   only after an explicit current-turn request for GitHub/T2, and paired `--host-proxy` only after
   a separate current-turn host-proxy request. Never append `--exec`, `--exec-timeout`, `--require-origin`,
   shell composition, or a repository-derived option. A scan failure, truncation, or invalid JSON
   fails closed: never reconstruct a score from partial output.

2. **Check for unanswered questions.** If the engine's `gaps` array is non-empty, some `unknown`
   results are stuck on an input the scan could not infer, not on the repository. Report the count
   and the gating criteria they hold back, and hand off to the **ra1-interview** skill. Never answer
   them on the developer's behalf and never treat an unanswered gap as a finding.

3. **Copy the score verbatim.** Your final report MUST contain a fenced ```json block holding the
   engine's `score` object **exactly** — every key: `level`, `level_name`, `pass_rate`,
   `gating_passed`, `gating_total`, `levels`, `pillars`, `recommendations`, `max_available_level`,
   `next_gate_actions`, `evidence_coverage`. Full parsed-JSON deep equality; do not change a number.

4. **Add `## Evidence explanations`** (BEFORE `## T4 Advisory`). One fenced JSON object with this
   exact shape, covering every id in `score.next_gate_actions` in that order:

   ```json
   {
     "explanations": [
       {
         "id": "criterion.id",
         "status": "fail",
         "reason_code": "criterion.id.missing",
         "rule_ref": "checks.module.function",
         "evidence_sources": ["path-or-endpoint"],
         "limitations": ["deterministic limitation"]
       }
     ]
   }
   ```

   Values are copied from the matching result and its `decision_trace`; `evidence_sources` keeps
   first-seen non-empty sources, deduplicated. When `next_gate_actions` is empty, emit
   `{"explanations": []}`. Never author a new source, reason code, status, rule reference,
   limitation, or score. Then, in human prose, explain rule → observations → evaluation →
   limitation → next action in repository-maintainer language, citing criterion ids and sources,
   and label any T4 interpretation as advisory.

5. **Add `## T4 Advisory`** (qualitative, non-gating). The engine deliberately leaves these soft
   judgments to you; label each as advisory opinion grounded strictly in engine findings and files
   you actually read. Use one labelled sub-heading per registered judgment id (`naming_consistency`,
   `code_modularization`, `n_plus_one_query`, `readme_quality`, `agents_md_quality`,
   `service_flow_doc_quality`, `runbooks_quality`, `pii_handling`, `privacy_compliance`,
   `user_feedback_loop`), group AC/DC maturity by the registry-provided `acdc_stage`/`acdc_loop`
   fields, cite the specific file/finding, explain *why* it matters, and give the highest-leverage
   next step. Keep every T4 claim clearly separate from the deterministic score.

## Contract (do not violate)

- **Never claim a higher Level than the engine reports.** Level 5 (Autonomous) is reserved and
  never reported; the ceiling is stated as L4.
- **Never mark a failing or unknown criterion as passing.** Prose must agree with the engine's
  statuses; describe unavailable/unknown language honestly ("not verified", never "not protected").
- **Never invent criteria, evidence, sources, reason codes, or results** — pasting the score and
  explanation payloads verbatim is the machine contract; the human prose summarizes, never extends,
  them.
- **Never claim autonomy clearance** or unattended operation in any form.
- **Do not assert that a specific criterion is "gating" or "non-gating"** — only the engine's data
  says so.
- T4 advisory is opinion and is explicitly **non-gating** — it cannot move the Level, GitHub
  annotations, JUnit, or SARIF.

## Notes

- If the engine reports `project_type: unknown`, surface that honestly — type-dependent criteria are
  `unknown`, not silently skipped. Point at the `ra1-interview` skill to pin a type (never edit
  `.ra1/config.json` directly).
- T2 criteria are `skipped` when `--github` was not requested or the source is unavailable; say so
  and offer `--github` for a fuller scan.
- To raise the score, hand off to the **ra1-fix** skill (the Loadout).