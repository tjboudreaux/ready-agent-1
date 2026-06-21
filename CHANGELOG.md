# Changelog

All notable changes to Ready Agent 1. The deterministic **gating score** and the **advisory** layer
are tracked separately: advisory additions never change a repo's Level, GitHub annotations, JUnit, or
SARIF.

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
