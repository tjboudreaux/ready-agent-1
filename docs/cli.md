# CLI reference

`ra1 <command>` (zero-install via `bin/ra1`, or `python3 <skill>/scripts/readiness/cli.py <command>`).

## `report`

Analyze a repository and emit a readiness report.

| Flag | Default | Description |
|------|---------|-------------|
| `--project PATH` | `.` | Repository to analyze |
| `--format LIST` | `json` | Comma list: `json,markdown,github,junit,sarif` |
| `--out DIR` | — | Write `report.<ext>` + `latest.json` to this directory |
| `--require-origin` | off | Fail if the repo has no `origin` remote (matches Droid's `/readiness-report` prerequisite) |
| `--store-history` | off | Write a timestamped local history snapshot keyed by repository identity |
| `--history-dir DIR` | `<out>/history` or `<project>/.agents/readiness/history` | History root used by `--store-history` and `fix --latest` |
| `--no-github` | off | Disable T2 GitHub API checks (offline / deterministic) |
| `--exec` | off | Opt in to T3 execution: runs the detected test command on an isolated temp copy (allowlisted argv, scrubbed env, hard timeout). Advisory only — never changes the level |
| `--exec-timeout N` | 120 | T3 execution timeout in seconds |
| `--min-level N` | — | Exit non-zero if the achieved level is below N |
| `--fail-on ID …` | — | Exit non-zero if any named criterion id fails; this can deliberately gate advisory criteria |

Exit code is `0` unless a gate (`--min-level` / `--fail-on`) fails. By default, advisory criteria
do not affect the deterministic level; `--fail-on loop.some_id` is an explicit user override.

### Repository identity and history

Every report carries a `repository` identity. When an `origin` remote exists it is an
`origin` identity (`host`/`owner`/`name`/`identity_hash`) with the remote URL **redacted** —
credentials embedded in the URL are never serialized. Without an origin (and without
`--require-origin`) RA1 falls back to a `local_path` identity derived from a path hash; the raw
absolute path is never written.

`--store-history` writes `<primary-out>/latest.json` (canonical latest), an immutable
`<history-root>/<identity_hash>/<timestamp>.json` snapshot, and an ordered
`<history-root>/<identity_hash>/index.json`. `<primary-out>` is `--out` or
`<project>/.agents/readiness`. Reports are **schema 2**; schema-1 reports are only usable via an
explicit `fix --report PATH` and are ineligible for `fix --latest` / history / delta.

## `fix`

Plan or apply remediation from the latest report.

| Flag | Default | Description |
|------|---------|-------------|
| `--project PATH` | `.` | Repository to remediate |
| `--apply` | off | Write changes (default is a dry-run plan) |
| `--force` | off | Apply even if the worktree is dirty |
| `--report PATH` | `.agents/readiness/latest.json` | Report to read |
| `--latest` | off | Resolve the latest stored report by repository identity (schema 2 only) |
| `--history-dir DIR` | `<project>/.agents/readiness/history` | History root for `--latest` |
| `--include ID...` | — | Only remediate these criterion ids (authoritative) |
| `--exclude ID...` | — | Never remediate these criterion ids (authoritative) |
| `--instructions TEXT` | — | Focus grammar (`prioritize <pillar>`, `do not touch <pillar>`); unrecognized text is annotated, never silently filtered |

Only safe config scaffolds are written, idempotently, never overwriting non-empty files. Advisory
criteria are auto-scaffolded only when their fix is a registry-declared safe scaffold; other advisory
or prose work requires an explicit `--include`. The plan ends with a **Verify** reminder to re-run
`ra1 report`.

## `detect`

Print project-type detection (type, confidence, signals, application inventory) as JSON.

## `version` / `formats`

`version` prints the engine/registry/detector/schema version stamps. `formats` lists report formats.

## Report formats

- **json** — canonical; the `score` block is authoritative (including `score.recommendations`, the deterministic top next-actions) and every criterion carries `passed_apps`/`evaluated_apps`; feeds grounding + `fix`.
- **markdown** — human report (Level Achieved, Applications Discovered, per-criterion Criteria Results shown as N/M, the top 2-3 gating Action Items, and separate Advisory Improvements); also the CI step summary.
- **github** — `::warning::` annotations for gating failures only, plus `::notice::` level summary.
- **junit** — `<testsuites>` with one testcase per gating criterion.
- **sarif** — SARIF 2.1.0 for gating failures with a real source location; advisory failures and repo-level claims are excluded.
