# Changelog

All notable changes to Ready Agent 1. The deterministic **gating score** and the **advisory** layer
are tracked separately: advisory additions never change a repo's Level, GitHub annotations, JUnit, or
SARIF.

## 0.4.0 — Factory/Droid parity

Engine/registry/detector → `0.4.0`; report **schema → 2**.

### Deterministic (may affect the score/output)
- **Report schema 2**: every report now carries a redacted `repository` identity and `generated_at`;
  each criterion result carries `passed_apps`/`evaluated_apps` (N/M); the score block carries
  `recommendations` (the top 2-3 gating next-actions). The gating criteria set is **unchanged at 32**.
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
