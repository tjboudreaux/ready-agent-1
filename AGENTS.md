# AGENTS.md

Briefing for agents working in **agent-readiness**.

## Build & Test
- No build step; pure Python standard library (3.11+).
- Tests: `python3 -m unittest discover -s tests -t .`
- Coverage (gate is >90%): `python3 -m coverage run --branch --source=engine/readiness,evals -m unittest discover -s tests -t . && python3 -m coverage report`

## Architecture
- `engine/readiness/` — canonical pure-stdlib engine. Flow: `detect` → `collectors` (T0 static / T1 git / T2 gh) → `checks/` (typed `fn(ctx)->Verdict`) → `score` (applicability, aggregation, level gating) → `report` (json/markdown/junit/sarif/github).
- `criteria/registry.json` — criteria **metadata only**; logic lives in `checks/`.
- `fix/recipes.py` — safe remediation (scaffolds only; never overwrites; refuses dirty worktree).
- `skills/` — two agentskills.io skills; each is self-contained (engine + templates **vendored** in).
- `evals/` — agent-contract evals (deterministic contracts + LLM-as-judge).

## Conventions
- The engine is the single source of truth for the **deterministic gating score**. The agent layer is
  **advisory only** and must never change the score.
- Pure stdlib only in `engine/` (no third-party imports). `coverage` is dev-only.
- After editing the engine or templates, run `python3 scripts/vendor.py` and commit the vendored skills
  (CI runs `scripts/vendor.py --check`).
- New criteria start `gating:false` (advisory) and graduate only after passing evals.

## Security
- Reports may contain code excerpts → they live under gitignored `.agents/`. Never commit `.env`.
- `ra1-fix` never pushes or opens PRs without explicit user confirmation.

## Git Workflow
- Branch for changes; scoped commits; end commit messages with a `Co-Authored-By` trailer when agent-authored.
