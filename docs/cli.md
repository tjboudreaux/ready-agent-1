# CLI reference

`ra1 <command>` (zero-install via `bin/ra1`, or `python3 <skill>/scripts/readiness/cli.py <command>`).
Operational commands require Linux/macOS with the full POSIX directory-fd capability set; on Windows or a
deficient runtime they fail closed with the exact `safe_io_unsupported` diagnostic before any repository
access (help/`version`/`formats`/`banner` remain available).

## `report`

Analyze a repository and emit a readiness report. Markdown is the default format on stdout.

| Flag | Default | Description |
|------|---------|-------------|
| `--project PATH` | `.` | Repository to analyze |
| `--format LIST` | `markdown` | Comma list: `json,markdown,html,github,junit,sarif` (machine consumers name `json` explicitly) |
| `--detail {actionable,all}` | `actionable` | Markdown/HTML trace expansion: `actionable` expands fail/unknown, `all` expands every result |
| `--out DIR` | — | Generated artifacts root beneath which `report.<ext>` is written |
| `--github` | off | Opt in to T2 GitHub.com API checks (offline by default) |
| `--host-proxy` | off | Forward captured host proxy env to `gh`; requires `--github`, else exit 2 |
| `--require-origin` | off | Fail if the repo has no `origin` remote |
| `--store-history` | off | Write a timestamped local history snapshot keyed by repository identity |
| `--exec` | off | Opt in to T3 execution: runs the detected test command on an isolated copy (allowlisted argv, scrubbed env, hard timeout). Advisory only — never changes the level |
| `--exec-timeout N` | 120 | T3 execution timeout in seconds (1..3600) |
| `--min-level N` | — | Exit non-zero if the achieved level is below N |
| `--fail-on ID …` | — | Exit non-zero if any named criterion id fails |

Persistence writes under the output root (default `<project>/.ra1/reports`): selected presentation
files, `latest.json`, immutable history snapshots, a digest-bearing `history/<identity_hash>/index.json`,
and a final `.commit.json` manifest under an exclusive writer lock. In-repository persistence requires
authoritative root-`.gitignore` proof that `/.ra1/reports/` is ignored while `.ra1/config.json` and
`.ra1/waivers.json` are **not**; otherwise the report still prints in memory and exits 1 with the exact
isolation error. A busy writer, a history cap (10,000 entries), or an incomplete/mixed generation refuses
with an exact diagnostic and creates/replaces nothing.

When `--github` is requested, incomplete T2 collection still renders and persists the partial report,
then exits 1 with `requested GitHub evidence was incomplete`; likewise `--exec` reports
`requested execution evidence was unsuccessful` unless every allowlisted run succeeded.

### Repository identity and history

Every report carries a `repository` identity. When an `origin` remote exists it is an
`origin` identity (`host`/`owner`/`name`/`identity_hash`) with the remote URL **redacted** —
credentials, query, and fragment are never serialized. A present *malformed* origin fails identity
resolution and never falls back. Without an origin RA1 uses a `local_path` identity; the raw
absolute path is never written, and `identity_hash` is an unsigned local comparison key, not
anonymization (human/GitHub renderers omit it).

History is **schema 3**. `history list` and `history diff` accept `--mode current|legacy` plus
`--root`; `diff` also takes the all-or-none quartet `--from-mode/--from-root/--to-mode/--to-root`
(mutually exclusive with `--mode/--root`) so an explicit legacy schema2 id can compare to a current
schema3 id without parent traversal. Legacy mode requires an explicit root and never resolves `latest`.

### Comparability

`history diff` compares only after strict validation and (schema 3) the full identity/input/scope
contract: canonical `repository` inputs, valid `generated_at` order, complete static and Git
evidence of the same metadata class, equal GitHub/execution requested scope, and **live commit
ancestry** proven through the safe Git authority (`merge-base --is-ancestor`). Every refusal returns
`comparable:false` with an authored reason — outages or divergence can never masquerade as
repository change. Cross-schema (2↔3) is always `version mismatch: schema_version`.

## `fix`

Plan and verified-apply safe remediation from a **fresh in-memory scan** (source-less; a stored
report is optional transparency only).

| Flag | Default | Description |
|------|---------|-------------|
| `--project PATH` | `.` | Repository |
| `--apply` | off | Verified apply: fresh baseline → create-only mutation → same-option rescan → comparable delta |
| `--github` | off | Online T2 verification; only with `--apply` |
| `--host-proxy` | off | Forward captured proxy env; only with `--apply --github` |
| `--report PATH` | — | Schema1/2/3 transparency source (never selects writes); schema1 is plan-only |
| `--latest` | off | Resolve the latest **current-schema** stored report by identity |
| `--reports-dir DIR` | `<project>/.ra1/reports` | Output root for `--latest` |
| `--format {markdown,json}` | markdown | `json` emits the canonical `fix_contract` |
| `--include/--exclude ID …` | — | Authoritative criterion filters |
| `--instructions TEXT` | — | Focus grammar: `prioritize <pillar>` / `do not touch <pillar>` |

Apply requires `worktree_dirty == False` (no bypass), a repository-determinate baseline with
complete static/Git evidence (and requested T2 completeness), and refuses before any scan/write
otherwise. It writes only missing scaffold targets (exclusive create; existing targets are skipped
proposals — `.gitignore` create-if-missing only, never appended), then rescans with identical options
and computes the authoritative `history.delta`. A written target that does not verify `pass`, any
newly-failing criterion, any baseline `pass → unknown/skipped/waived` regression, an incomparable
delta, or a Level decrease returns 1 **with** a valid failed `fix_contract` on stdout; created files
are retained and never rolled back, and fix never writes report/history/latest. Plan mode is read-only
and prints the plan or the plan `fix_contract`.

## `gaps`

List inputs the scan could not determine. `--format json` emits the canonical list: every item has
`gap_id`, `kind`, `question`, `why`, `recordable`, `input_kind`, `choices` (opaque ids + display-only
labels + `record`/`external_action`/`leave_unanswered` effects), `value`, `blocked_ids`,
`blocked_gating`, `levels`, `evidence`. Offline by default; `--github`/`--host-proxy` behave as in
`report`. Exits 0 even when gaps exist; incomplete requested T2 exits 1.

## `answer`

Record one typed interview answer. `--gap-id <canonical-id>` plus `--choice <canonical-choice-id>`
(repeatable only for the multi-enum surfaces gap) or `--minutes <1..1440>` (CI budget only), with
`--format json` emitting one canonical `answer_contract`. Plan mode (no `--apply`) writes nothing.
Apply requires clean/known Git and a complete T0/T1 baseline, rederives the gap mapping, performs one
bounded create/merge on `.ra1/config.json` or exact engine-authored `.ra1/waivers.json` entries,
rescans offline, and reports honestly — an answer can expose failures or lower the Level. Never pass
free-form text; never recommend `gh auth login`.

## `history`

`list` / `diff` with typed `--mode current|legacy` and `--root` (see above).

## `detect` / `version` / `formats` / `banner`

`detect` prints project-type detection; `version` prints the engine/registry/detector/schema stamps;
`formats` lists report formats; `banner` prints the banner. All four work on any platform.

## Exit codes

`0` success (or offline gaps present); `1` operational failure/refusal (dirty tree, incomplete
evidence, gate below `--min-level`, requested-T2 incompleteness, verification failure — each with an
exact diagnostic); `2` usage errors (unknown flags, invalid pairings, bad formats) before any scan
or write. Removed legacy flags (`--no-github`, `--verify`, `--force`, `--history-dir`) exit 2 with no
aliases.