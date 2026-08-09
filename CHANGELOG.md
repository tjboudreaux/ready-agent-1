# Changelog

All notable changes to Ready Agent 1. The deterministic **gating score** and the **advisory** layer
are tracked separately: advisory additions never change a repo's Level, GitHub annotations, JUnit, or
SARIF.

## 0.11.0 — Agentic workflow coverage

Package/engine and all three skills (`ra1-report`, `ra1-fix`, `ra1-interview`) → `0.11.0`;
registry → `0.8.0`; detector unchanged at `0.6.0`; report **schema → 3**; the Claude plugin is
retained as a versioned distribution surface at `0.3.0`. Release tag `v0.11.0`; stable
`vMAJOR.MINOR.PATCH` tags are now the only publication trigger (prerelease, build, and floating
tags are rejected). The registry grows to **115 criteria = 32 gating + 83 advisory** across nine
pillars.

### Deterministic score fixes
- **Zero-evidence Level correction**: a defined Level whose gating criteria are all
  skipped/waived is no longer achieved, and an undefined Level is never achieved. The
  deterministic ceiling is **Level 4 — Optimized**; Level 5 Autonomous remains reserved and is
  never awarded.
- **Dead `api` project type removed** from all 26 registry rows; `service` is the canonical
  detector type.
- **Applicability migration**: `build.release_automation` and the new
  `security.supply_chain_provenance` share a conservative three-state artifact-publication
  signal — absent intent skips both, indeterminate blocks as `unknown`.
- **`security.gitignore_comprehensive`** now requires the authoritative root `.gitignore` to
  ignore exactly `/.ra1/reports/` while leaving `.ra1/config.json` and `.ra1/waivers.json`
  unignored.
- Repository-controlled evidence that is unsafe, unreadable, oversize, overflow, or unstable now
  maps to blocking `unknown` instead of collapsing to pass/fail — including malformed or
  unreadable T2 observations.

### Schema 3: traces, boundary, provenance
- Every result carries a deterministic **decision trace** (rule → observation → evaluation →
  conclusion) with typed reason codes and evidence cited by index; explanation payloads never
  affect the score.
- Every report discloses the canonical **`assessment_boundary`** — the finite allowlist of
  checks, declarative-credit semantics, and the unscored runtime-isolation/egress,
  credential-scope, human-approval, prompt-injection, observability, cost/concurrency, and
  organizational-outcome categories.
- **`assessment_provenance`** is engine-recorded **unsigned** metadata borrowing SLSA/in-toto
  field structure only — never an attestation or an authenticity/integrity claim.
  `identity_hash` is an unsigned local-history comparison key; `project_path` is never
  serialized; raw policy/command/waiver/owner text is never copied.

### Safe I/O and bounded processes
- Every production repository read/write/subprocess now goes through `safe_io.py` /
  `process.py` (no pathname opens, no `shutil`, no direct `subprocess`) with bounded outputs,
  timeouts, and process-group kill. Supported hosts are **Linux/macOS** with the full POSIX
  directory-fd/no-follow capability set; Windows and deficient runtimes fail closed with
  `ra1: safe_io_unsupported` before repository access (help/version/formats/banner still work).
- Automatic Git runs only against sanitized immutable snapshots of a primary checkout or a
  standard reciprocal current-user linked worktree — no helpers, no network, no
  includes/alternates/promisor. Linux Git gets a hard address-space cap plus CPU/core caps;
  macOS gets CPU/core/wall/output/command/snapshot caps and **no hard memory cap** (deferred
  and disclosed).

### Commands and paths
- `ra1 report` now defaults to **Markdown on stdout**; JSON is explicit (`--format json`).
  `--format json,markdown,html --out .ra1/reports --store-history` persists
  report.json/md/html + `latest.json` + digest-bearing history + a `.commit.json` manifest
  under an exclusive writer lock, after proving the `.ra1` ignore boundary — otherwise the
  report prints in memory and exits 1. History caps at 10,000 entries (no pruning); busy
  writers and incomplete generations refuse with exact diagnostics.
- **`.ra1` migration**: policy inputs moved to `.ra1/config.json` / `.ra1/waivers.json`;
  generated output lives only under gitignored `.ra1/reports/`. Legacy `.agents/readiness`
  policy files block with migration guidance; legacy schema-2 history is readable only via
  `ra1 history list|diff --mode legacy --root <old root>` and never resolves `latest`.
- **T2 is offline by default** and targets GitHub.com only: opt in with `--github`;
  `--host-proxy` requires `--github`. Authentication is a bounded startup
  `GH_TOKEN`/`GITHUB_TOKEN` or a safely copied external `gh` config — never repository-local.
  GHES is deferred.
- Removed flags `--no-github`, `--verify`, `--force`, and `--history-dir` exit 2 with no
  aliases.
- `ra1 history diff` treats schema 2↔3 as explicitly incomparable
  (`version mismatch: schema_version`); schema-3 deltas additionally require matching repository
  identity, inputs, evidence scope, and proven commit ancestry.

### Verified remediation and typed interview
- `ra1 fix --apply` is one always-verified contract: clean/known Git (no bypass) → fresh
  baseline → create-only mutation (existing targets skipped; `.gitignore` create-if-missing
  only) → same-option rescan → comparable `history.delta`. Exit 0 only when every written id
  verifies `pass`, nothing newly fails, no pass regresses to unknown/skipped/waived, and the
  Level does not decrease. Emits `fix_contract`; never rolls back; never writes
  report/history/latest.
- New `ra1 gaps` / `ra1 answer`: opaque gap ids with typed choices (`command.<sha256>` ids for
  verification commands — no repo text in argv), one answer at a time, one bounded merge on
  `.ra1/config.json` or exact engine-authored `.ra1/waivers.json` ids, and an honest
  `answer_contract` — recording an answer can expose failures or lower the Level.

### New advisory criteria (all `gating: false` pending the labeled-corpus benchmark)
- `docs.agent_context_map`, `taskdisc.pr_evidence_contract`,
  `taskdisc.concurrent_agent_protocol`, `security.branch_protection_depth`,
  `security.agent_config_ownership`, `security.supply_chain_provenance`. Graduation follows the
  manual policy in `docs/criterion-graduation.md` (`evals/criterion_benchmark.py`).

### Deliberately deferred
- Multi-repo portfolio aggregation, an MCP server, runtime sandbox/identity integration,
  organizational surveys, DORA/DevEx/outcome measurement, GHES, and any Level 5 awarding. No
  telemetry; agent/LLM prose remains advisory — the deterministic engine owns the score.

## 0.8.1 — Facet keyboard-order fix

Engine → `0.8.1`; registry stays `0.7.0`. Reporting only; the gating set is unchanged at 32.

- **HTML facet bar**: visible groups now render Status → AC/DC loop → Pillar, matching the input
  DOM order the filter's sibling chains require. The focusable controls are the visually-hidden
  checkboxes, so the previous Status → Pillar → AC/DC loop layout made tab focus jump forward
  then backward across the bar.

## 0.8.0 — AC/DC loop facet in the HTML report

Engine → `0.8.0`; registry stays `0.7.0`; detector stays `0.5.0`; report schema unchanged at **2**.
The deterministic **gating set is unchanged at 32** — this release is reporting only.

### Reporting (never changes the score)
- **AC/DC loop facet group** (Inner / Outer / Both) in the HTML report's criteria filter, built
  from the registry `acdc` metadata. Criteria without an `acdc` mapping carry no loop class and
  stay visible under every loop selection.
- **Pillar visibility inverted**: sections now default to hidden and appear exactly when one of
  their live (status, loop) pairs has every chip on. With three facet axes, "this pillar is empty"
  is a disjunction of conjunctions that sibling-combinator hide rules cannot express; the
  inversion keeps empty headings and the "No criteria match these filters" message exact for any
  status × pillar × loop selection — still script-free, still no `:has()`.

## 0.7.0 — AC/DC stage/loop metadata

Engine/registry → `0.7.0`; detector stays `0.5.0`; report schema unchanged at **2**. The deterministic
**gating set is unchanged at 32** — this release is metadata and reporting only.

### Reporting (never changes the score)
- **`acdc` registry metadata**: criteria covered by the AC/DC verification-loop model now declare
  `{"stage": "guide|verify|solve", "loop": "inner|outer|both"}` (both keys required; `both` is the
  explicit both-loops classification). Eleven criteria mapped: Guide (`docs.agents_md`,
  `docs.agent_verify_contract`, `docs.architecture_doc`, `docs.agents_md_ci_validation`),
  inner-loop Verify (`build.check_command`, `devenv.agent_hooks`), outer-loop Verify
  (`build.ci_runs_tests`, `testing.coverage_threshold`, `testing.new_code_quality_gate`,
  `security.branch_protection`), Solve (`style.precommit_hooks`).
- **Surfaced everywhere**: `acdc_stage`/`acdc_loop` on each criterion result in JSON; an
  "inner loop · verify" label on criterion rows and advisory items in the markdown and HTML reports.
- **`ra1-report`** now groups its AC/DC maturity narrative by the engine-provided fields instead
  of a hardcoded id-to-stage list, so the mapping can no longer drift from the registry.

## 0.6.0 — Verification loop (AC/DC) advisory cluster

Engine/registry → `0.6.0`; detector stays `0.5.0`; report schema unchanged at **2**. The deterministic
**gating set is unchanged at 32** — every addition below is advisory and never moves a repo's Level.

### Advisory (T0 — never changes the score)
- **Verification loop (4)**: `build.check_command`, `docs.agent_verify_contract`,
  `devenv.agent_hooks`, `testing.new_code_quality_gate` (Sonar AC/DC Guide→Verify→Solve inner/outer loop).
- **`acdc` config block** in `.agents/readiness/config.json`: `verify_command`, `instruction_files`,
  `hook_files` — vendor-agnostic, maintainer-declared, always config-cited in evidence.
- **Vendor-agnostic AC/DC pack** `templates/acdc/` (workflow directive + guide/verify/solve skills;
  RA1's answer to Sonar's downloadable pack, allowlisted + vendored).
- Skill advisory mapping in `skills/ra1-report` / `skills/ra1-fix` (metadata 0.6.0).

### Tests/fixtures
- `TestAcdcVerificationLoop` branch coverage; ten labeled fixtures for the new criteria including
  config-driven passes (0 FP/FN/applicability).

## 0.5.0 — Factory parity gap closure

Engine/registry/detector → `0.5.0`; report schema unchanged at **2**. The deterministic **gating set
is unchanged at 32** — every addition below is advisory and never moves a repo's Level.

### Advisory (T0/T1/T2 — never changes the score)
- **Style code-health (6)**: `style.naming_convention_rule`, `style.complexity_budget`,
  `style.dead_code_detection`, `style.duplicate_code_detection`, `style.large_file_guard`,
  `style.tech_debt_tracking`. A capable linter installed is not enough — the rule/budget/scan must be
  configured or actually wired (CI/pre-commit/scripts).
- **Observability depth (5)**: `observability.error_tracking`, `runbooks`, `profiling`,
  `circuit_breakers`, `deployment_markers` (two-part evidence / non-placeholder content).
- **Security depth (4)**: `security.dependency_min_age`, `log_scrubbing`, `secrets_management`, `dast`.
- **Build/dev-env hygiene (8)**: `build.unused_dependencies`, `version_drift`, `monorepo_tooling`,
  `single_command_setup`, `release_notes_automation`, `dependency_weight_budget`;
  `devenv.local_services`, `devenv.database_schema`.
- **Docs/product (4)**: `docs.auto_generation`, `docs.agents_md_ci_validation`,
  `docs.architecture_doc`, `product.error_to_insight`.

### T4 judgments + ESLint-style ignore
- Nine `judgment.*` agent-graded criteria (`decide:"agent"`), **structurally barred from gating** —
  the scorer coerces `gating:false` for `decide:"agent"` regardless of the registry flag.
- A `judgments` block in `.agents/readiness/config.json` silences a judgment like an ESLint rule:
  `off | advisory` severities, a `*` default, and `judgment_overrides` path globs. `error` is rejected
  (downgraded to advisory) — no config path turns a judgment into score-affecting credit. Silenced
  judgments are `WAIVED` and disclosed in the report (`Ignored judgments (N): …`); never a pass.

### T3 execution (advisory, opt-in via `--exec`, behind the sandbox contract)
- `testing.behavioral_smoke` (declared `npm run smoke` / `make smoke`) and
  `devenv.devcontainer_runnable` (`devcontainer build`) run under the existing isolated-copy /
  scrubbed-env / command-allowlist / timeout contract. T3 stays non-gating; CI status (T2) substitutes.

### Tests/fixtures
- 100% branch coverage on every changed module; new pass/fail corpus fixtures (`gap-criteria-rich`,
  `gap-criteria-bare`) keep the eval corpus at 0 FP/FN/applicability; parity unchanged (1.0).

## 0.4.0 — Factory/Droid parity

Engine/registry/detector → `0.4.0`; report **schema → 2**.

### Deterministic (may affect the score/output)
- **Report schema 2**: every report now carries a redacted `repository` identity and `generated_at`;
  each criterion result carries `passed_apps`/`evaluated_apps` (N/M); the score block carries
  `recommendations` (the top 2-3 gating next-actions). The gating criteria set is **unchanged at 32**.
  The raw absolute project path is no longer serialized anywhere (JSON, history snapshots, or the
  markdown subtitle) — the redacted `repository` identity is the only location reference.
- **Detection/app discovery**: Go `cmd/*` binaries, Maven `<modules>`, and Gradle `include` modules
  are discovered as deployable apps (with false-app guards for `examples/`, `vendor/`, tests, etc.);
  Go/Ruby manifest dependencies are parsed for honest classification. This can change app-scoped N/M
  denominators for monorepos — the `detector_version` bump signals it and suppresses stale app deltas.
- Markdown report aligned with Droid concepts (Applications Discovered, Criteria Results as N/M,
  top-3 Action Items, Advisory Improvements). GitHub/JUnit/SARIF remain gating-only.

### Advisory (never changes the score)
- Six deterministic advisory criteria: `build.agentic_development`, `build.build_command_documented`,
  `testing.coverage_threshold`, `testing.flake_quarantine`, `taskdisc.actionable_backlog_items`,
  `build.ci_duration_budget`. `build.reproducible_build` remains deferred (T3, not registered).
- Nine observability/product advisory criteria (two-part evidence — configuration AND wiring — so
  import-only/config-only/doc-only never pass): structured logging, tracing, metrics, health
  endpoints, alerting rules, dashboards-as-code; analytics instrumentation, feature flags, experiment
  config. Applicable to service/api/frontend/monorepo-root only.
- A `## T4 Advisory` skill layer (naming, modularization, doc quality, runbooks, autonomy maturity)
  with judge contracts that reject score inflation, fabricated passes, and autonomy over-claims.

### Workflow / commands
- `ra1 report`: `--require-origin`, `--store-history`, `--history-dir`; repository identity
  (origin-redacted or local-path hash, never a raw token/absolute path); local timestamped history.
- `ra1 fix`: `--latest` (resolve the latest stored report by repository identity), `--include` /
  `--exclude` / `--instructions` focus controls, a Verify reminder, and an unchanged safety model
  (no push/PR/GitHub-settings mutation).
- `ra1 history list` / `ra1 history diff` — local, API-shaped readiness progression.

### Engineering
- Touched-file coverage gate (`scripts/coverage_gate.py`): >90% total **and** 100% branch coverage
  for every changed module, enforced in CI against the PR diff.
- Canned-`gh` fixtures for T2 criteria; observability/product pass/fail fixture corpus (0 FP/FN).
