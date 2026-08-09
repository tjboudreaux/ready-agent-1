# Getting started

## Requirements

- **Linux or macOS** with **Python 3.11+**. No third-party runtime dependencies. The engine
  requires the full POSIX directory-fd/no-follow capability set; on Windows or a deficient
  runtime the operational commands fail closed with
  `ra1: safe_io_unsupported: required POSIX filesystem primitives are unavailable` before any
  repository access or subprocess. `--help`, `version`, `formats`, and `banner` still work.
- A Git repository with a clean, known worktree for `fix --apply` and `answer --apply`.
- Optional: **GitHub.com checks (T2)** are offline by default. Opt in per run with `--github`
  (add `--host-proxy` only together with `--github` to forward a captured host proxy).
  Authentication comes from a bounded startup `GH_TOKEN`/`GITHUB_TOKEN` or a safely copied
  external `gh` config — never repository-local config. GitHub Enterprise Server is not
  supported in this release.

## Policy inputs vs. generated output

- Team-owned, reviewable inputs: `.ra1/config.json` and `.ra1/waivers.json` — commit these.
- Generated reports/history: `.ra1/reports/` only. Your root `.gitignore` must ignore exactly
  `/.ra1/reports/` and must not ignore `.ra1/config.json` or `.ra1/waivers.json`. Report
  persistence proves this boundary with Git before writing; if it cannot, the report is still
  printed in memory and the command exits 1 with
  `ra1 report: .ra1/reports is not safely isolated from versioned .ra1 policy; ignore only .ra1/reports/`.

Migrating from 0.10.x or older: legacy `.agents/readiness/config.json` / `waivers.json` policy
files block scoring with migration guidance — move them to `.ra1/`. Legacy schema-2 history is
readable only through `ra1 history list|diff --mode legacy --root <old history root>`; the
default current mode and `--latest` never search the old tree.

## Generate a report

```bash
ra1 report --project /path/to/repo            # Markdown to stdout (the default format)
```

```bash
ra1 report --project /path/to/repo \
  --format json,markdown,html --out .ra1/reports --store-history
```

The persisted run writes `report.json`, `report.md`, `report.html`, `latest.json`, a
digest-bearing history snapshot, and a `.commit.json` manifest under an exclusive writer lock;
readers take a shared lock and validate the manifest. History is capped at 10,000 entries — at
the cap nothing is pruned or overwritten; archive or remove old snapshots first. A busy writer
or an incomplete previous generation refuses with an exact diagnostic.

Through an agent, just ask: *"run a readiness report on this repo"* — the `ra1-report` skill
runs the vendored engine and adds advisory commentary, without changing the score.

## Remediate (verified)

```bash
# Plan — fresh scan, three buckets: auto-apply scaffolds, propose drafts, GitHub settings
ra1 fix --project /path/to/repo

# Apply — requires a clean, known Git worktree (no bypass)
ra1 fix --project /path/to/repo --apply
```

`--apply` is one contract: fresh baseline → create-only mutation (existing files are skipped;
`.gitignore` is created only if missing) → rescan with the same options → comparable
`history.delta`. It exits 0 only when every written criterion verifies `pass`, nothing newly
fails, no pass regresses to unknown/skipped/waived, and the Level does not decrease. It emits a
`fix_contract` JSON, never rolls back a written scaffold, and never writes
report/history/latest.

The engine only writes missing config files. Documentation (README, AGENTS.md, runbooks) and
tests are **proposed** for you to author — the engine won't fabricate prose. GitHub settings
are a checklist.

## Answer readiness gaps (interview)

The `ra1-interview` skill closes engine-declared gaps one typed answer at a time:

```bash
ra1 gaps --project /path/to/repo --format json          # opaque gap ids, typed choices
ra1 answer --project /path/to/repo --gap-id <id> --choice <id>          # plan
ra1 answer --project /path/to/repo --gap-id <id> --choice <id> --apply  # record
ra1 answer --project /path/to/repo --gap-id config.ci_budget_minutes --minutes 30 --apply
```

An answer performs one bounded merge on `.ra1/config.json` (or exact engine-authored
`.ra1/waivers.json` ids — no free-form reason/owner/expiry) and emits an honest
`answer_contract`: a recorded answer can expose failures or lower the Level; it never implies
the score improved.

## Interpreting the output

- **Level** — highest achieved maturity level (≥80% of each level's gating criteria,
  cumulative). Levels 1–4 are defined; the deterministic ceiling is **Level 4 — Optimized**.
  Level 5 Autonomous is reserved and never awarded. A defined level whose criteria are all
  skipped/waived is not achieved.
- **Status per criterion** — `pass` / `fail` / `skipped` (N/A to this project) / `unknown`
  (evidence unavailable, unreadable, or indeterminate — blocking, never silently absent) /
  `waived`.
- **Decision trace & boundary** — every result carries a deterministic reason code and cited
  evidence; every report discloses the `assessment_boundary` (what T0–T4 evidence can and
  cannot prove) and the unsigned `assessment_provenance`.
- **Applications** — in a monorepo, application-scoped criteria report `X/Y applications pass`,
  and a failing production-facing app is surfaced rather than averaged away.

## Waivers

Waivers live in `.ra1/waivers.json` and are written only by `ra1 answer` for the exact
engine-authored gap (e.g. a non-GitHub host) — there is no free-form reason/owner/expiry.
Waived criteria are excluded from the gate and disclosed; a waiver is never a pass.
