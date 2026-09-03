# AGENTS.md

Briefing for agents working in **agent-readiness**.

## Build & Test
- No build step; pure Python standard library (3.11+).
- Tests: `python3 -m unittest discover -s tests -t .`
- Coverage (gate is >90%): `python3 -m coverage run --branch --source=engine/readiness,evals,scripts,ci -m unittest discover -s tests -t . && python3 -m coverage report --fail-under=90`
- One-shot verification: `make check` (ruff + tests + vendor parity + release matrix); requires the dev group: `uv sync --group dev` or `pip install ruff==0.16.1`.

## Architecture
- `engine/readiness/` — canonical pure-stdlib engine. Flow: `detect` → `collectors` (T0 static / T1 git /
  T2 gh) → `checks/` (typed `fn(ctx)->Verdict`) → `score` (applicability, aggregation, level gating,
  decision traces) → `report` (json/markdown/html/junit/sarif/github from one canonical dict).
- `safe_io.py` — the sole bounded retained-handle authority for repository/artifact reads, discovery,
  safe creates/replaces, isolated copies, and primary/reciprocal-linked-worktree Git metadata admission.
- `process.py` — the sole bounded child-process launcher (closed tool IDs, startup/auth/proxy
  authorities, output/timeout caps, Linux hard-AS and Darwin CPU/core Git containment).
- `criteria/registry.json` — criteria **metadata only**; logic lives in `checks/`.
- `fix/recipes.py` — always-verified remediation: fresh baseline → create-only mutation → same-option
  rescan → comparable regression decision. No `--force`, no bypass, no unverified success claims.
- `gaps.py` / `answers.py` — typed interview questions and one-answer verified policy recording.
- `skills/` — three agentskills.io skills (`ra1-report`, `ra1-fix`, `ra1-interview`); each is
  self-contained (engine + templates **vendored** in) and grants **Bash only** with fixed CLI grammars.
- `evals/` — three-skill deterministic contracts + advisory LLM judge + criterion-graduation benchmark.

## Conventions
- The engine is the single source of truth for the **deterministic gating score**. The agent layer is
  **advisory only** and must never change the score.
- Pure stdlib only in `engine/` (no third-party imports). `coverage` is dev-only.
- After editing the engine or templates, run `python3 scripts/vendor.py` and commit the vendored skills
  (CI runs `scripts/vendor.py --check`).
- New criteria start `gating:false` (advisory) and graduate only through the labeled-corpus benchmark,
  a maintainer ADR, and a reviewed release change (see `docs/criterion-graduation.md`).
- Every production repository read/write and subprocess goes through `safe_io`/`process` — never
  pathname opens, `shutil`, or `subprocess` call sites in `engine/`.

## Security
- Reports may contain code excerpts → they live under gitignored `.ra1/reports/`. Never commit `.env`.
  Team-owned policy inputs are `.ra1/config.json` and `.ra1/waivers.json` (unignored, reviewable).
- Generated output vs policy boundary: `/.ra1/reports/` is ignored; `.ra1/` policy files are not.
- `ra1-fix` never pushes or opens PRs without explicit user confirmation; `fix --apply` and
  `answer --apply` require clean/known Git and complete T0/T1 evidence, and never write history.

## Concurrent work
- Use a separate worktree or task branch for each concurrent agent task, and own your files.
- Coordinate with other agents before touching the same or overlapping files.
- Re-read files before editing and preserve unexpected user or concurrent changes; never overwrite them
  silently.
- After merging or integrating, run the full test suite to verify the combined state.

## Git Workflow
- Branch for changes; scoped commits; end commit messages with a `Co-Authored-By` trailer when agent-authored.