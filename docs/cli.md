# CLI reference

`ra1 <command>` (zero-install via `bin/ra1`, or `python3 <skill>/scripts/readiness/cli.py <command>`).

## `report`

Analyze a repository and emit a readiness report.

| Flag | Default | Description |
|------|---------|-------------|
| `--project PATH` | `.` | Repository to analyze |
| `--format LIST` | `json` | Comma list: `json,markdown,github,junit,sarif` |
| `--out DIR` | — | Write `report.<ext>` + `latest.json` to this directory |
| `--no-github` | off | Disable T2 GitHub API checks (offline / deterministic) |
| `--exec` | off | Opt in to T3 execution: runs the detected test command on an isolated temp copy (allowlisted argv, scrubbed env, hard timeout). Advisory only — never changes the level |
| `--exec-timeout N` | 120 | T3 execution timeout in seconds |
| `--min-level N` | — | Exit non-zero if the achieved level is below N |
| `--fail-on ID …` | — | Exit non-zero if any named criterion id fails; this can deliberately gate advisory criteria |

Exit code is `0` unless a gate (`--min-level` / `--fail-on`) fails. By default, advisory criteria
do not affect the deterministic level; `--fail-on loop.some_id` is an explicit user override.

## `fix`

Plan or apply remediation from the latest report.

| Flag | Default | Description |
|------|---------|-------------|
| `--project PATH` | `.` | Repository to remediate |
| `--apply` | off | Write changes (default is a dry-run plan) |
| `--force` | off | Apply even if the worktree is dirty |
| `--report PATH` | `.agents/readiness/latest.json` | Report to read |

Only safe config scaffolds are written, idempotently, never overwriting non-empty files.

## `detect`

Print project-type detection (type, confidence, signals, application inventory) as JSON.

## `version` / `formats`

`version` prints the engine/registry/detector/schema version stamps. `formats` lists report formats.

## Report formats

- **json** — canonical; the `score` block is authoritative; feeds grounding + `fix`.
- **markdown** — human report (Level, Applications, per-pillar criteria, gating Action Items, and separate Advisory Improvements); also the CI step summary.
- **github** — `::warning::` annotations for gating failures only, plus `::notice::` level summary.
- **junit** — `<testsuites>` with one testcase per gating criterion.
- **sarif** — SARIF 2.1.0 for gating failures with a real source location; advisory failures and repo-level claims are excluded.
