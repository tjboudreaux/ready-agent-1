# Getting started

## Requirements

- **Python 3.11+** (for `tomllib`). No third-party runtime dependencies.
- Optional: an authenticated **`gh`** CLI — enables the T2 GitHub checks (branch protection, secret
  scanning, labels, backlog, CI status). Without it, those criteria are `skipped` (never failed).

## Generate a report

```bash
python3 skills/readiness-report/scripts/readiness/cli.py report \
  --project /path/to/repo --format markdown,json --out /path/to/repo/.agents/readiness
```

This writes `report.md`, `report.json`, and `latest.json` under `.agents/readiness/` (gitignored —
reports may contain code excerpts) and prints the Markdown. The JSON `score` block is the
authoritative, reproducible result.

Through an agent, just ask: *"generate an agent readiness report for this repo"* — the
`readiness-report` skill runs the engine and adds advisory commentary, without changing the score.

## Remediate

```bash
# Dry run — shows three buckets: auto-apply scaffolds, propose drafts, GitHub settings
python3 skills/readiness-fix/scripts/readiness/cli.py fix --project /path/to/repo

# Apply the safe config scaffolds (idempotent; refuses on a dirty worktree)
git -C /path/to/repo checkout -b readiness/fixes
python3 skills/readiness-fix/scripts/readiness/cli.py fix --project /path/to/repo --apply
```

The engine only writes missing config files. Documentation (README, AGENTS.md, runbooks) and tests
are **proposed** for you to author — the engine won't fabricate prose. GitHub settings are a checklist.

## Interpreting the output

- **Level** — highest fully-achieved maturity level (≥80% of each level's gating criteria, cumulative).
- **Status per criterion** — `pass` / `fail` / `skipped` (N/A to this project) / `unknown` (type
  undetermined) / `waived`.
- **Applications** — in a monorepo, application-scoped criteria report `X/Y applications pass`, and a
  failing production-facing app is surfaced rather than averaged away.

## Waivers

To intentionally exempt a criterion, add `.agents/readiness/waivers.json`:

```json
[{"id": "security.branch_protection", "reason": "mirror repo", "owner": "you", "expires": "2026-12-31"}]
```

Waived criteria are excluded from the gate. Expiry is only enforced when a `now` date is supplied
(so the default score stays time-independent and reproducible).
