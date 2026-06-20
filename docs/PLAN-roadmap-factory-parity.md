# Plan — RA1 Factory/Droid parity roadmap

Status: goal-ready plan, adversarially reviewed and tightened
Date: 2026-06-20
Owner: RA1 maintainers

## Purpose

Bring Ready Agent 1 from a deterministic local readiness engine to a Factory/Droid-compatible readiness workflow while preserving RA1's stricter contract: the engine owns deterministic scoring, agent prose stays advisory, and criteria only graduate after fixture-backed proof.

This plan is written to be executable with `/goal`: each phase has concrete file targets, implementation steps, coverage requirements, and acceptance commands. It intentionally separates **command-surface parity** from **criteria expansion** so the CLI and skills can behave like Droid before every deferred criterion exists.

## Primary sources

- Factory Agent Readiness overview: Droid CLI `/readiness-report`, dashboard, API, and remediation surfaces; five levels; nine pillars; repository/application scopes; 80% level progression.
- Factory CLI readiness report docs: origin remote prerequisite, language detection, sub-application discovery, criteria evaluation, report storage, summary output, numerator/denominator criteria, 2-3 action items, and `/readiness-fix` latest-report remediation semantics.
- RA1 command docs: `docs/cli.md` defines `ra1 report`, `ra1 fix`, formats, advisory split, `--min-level`, and `--fail-on`.
- RA1 skill surfaces: `skills/ra1-report/SKILL.md` and `skills/ra1-fix/SKILL.md` define the slash-command-like agent contracts and safety rules.
- RA1 roadmap: `references/pillars.md` defines tiering, current registry version `0.3.0`, advisory graduation rules, and deferred criteria.

## Existing RA1 test seams to extend

Use the current tests as the first extension points instead of building a parallel harness:

| Seam | Current coverage | Roadmap use |
|---|---|---|
| `tests/test_cli.py` | report JSON, `--out` writing `report.json`/`latest.json`, markdown-capable CLI path, gates, loop advisory JSON behavior | Add `--require-origin`, `--store-history`, history/delta output, N/M display, and recommendation cap tests |
| `tests/test_fix.py` | missing/latest report behavior through `.agents/readiness/latest.json`, dry-run default, apply safety, dirty worktree refusal, loop scaffold allowlist | Add origin-history latest lookup, `--instructions` filtering, include/exclude filters, and no-prior-report parity errors |
| `tests/test_detect.py` | Python, FastAPI, Express, CLI, frontend, infra, ambiguous unknown, npm workspaces, config pinning, loop opt-in serialization | Add Rust/Go/Java/Ruby, richer JS/TS workspaces, deployable-app discovery, and false-app regression fixtures |
| `tests/test_vendor.py` | engine/template vendoring, real repo sync, loop templates, unallowlisted cruft ignored | Extend whenever skill docs, templates, or vendored engine behavior change |
| `tests/test_report.py` | markdown/advisory split, GitHub and SARIF gating-only behavior | Add Droid-shaped headings, N/M criterion display, recommendation ordering, and local-history deltas |

Every phase below names additional tests, but these seams are mandatory because they are the repo's existing gates for command behavior, fix semantics, detection, and vendored skill sync.

## Non-negotiable invariants

1. **Deterministic score boundary** — only engine results determine score, level, gating totals, GitHub warnings, JUnit, and SARIF. Skills may add advisory prose only.
2. **Graduation discipline** — new criteria start `gating:false` unless they are T0/T1/T2 and have pass + fail fixtures with zero false positives, false negatives, and applicability errors.
3. **T3 honesty** — execution checks stay advisory until RA1 can prove fresh source materialization, no-network isolation, scrubbed env, timeout, and allowlisted commands in tests.
4. **T4 never gates** — qualitative naming/modularization/doc-quality judgments remain skill-only advisory.
5. **No fake parity** — RA1 may provide local equivalents for Factory dashboard/API behavior, but must label them as local history, not cloud dashboard storage.
6. **Full coverage for new work** — every new or changed Python module in this roadmap must have 100% branch coverage or explicit, reviewed `# pragma: no cover` exclusions. CI must enforce both total coverage and new/touched-file coverage.
7. **Pure stdlib engine** — no third-party runtime dependencies in `engine/readiness`.
8. **Safe remediation** — `ra1 fix` and `ra1-fix` never push, merge, deploy, publish releases, or mutate GitHub settings without explicit human confirmation.

## Current gap summary

| Factory/Droid behavior | Current RA1 behavior | Gap |
|---|---|---|
| `/readiness-report` requires git repo with `origin` remote | `ra1 report` can scan any path; no origin association requirement | Add opt-in/skill-enforced origin resolution and local report identity |
| Report is persisted for dashboard/history | `--out` writes `latest.json`; no timestamped history/index | Add local report history and diffable indexes |
| Language detection covers JS/TS, Python, Rust, Go, Java, Ruby | Current detector is strongest for Python/JS-ish repo shapes; parity coverage is incomplete | Extend detector and app discovery fixtures |
| Sub-application discovery identifies independently deployable apps | RA1 has app inventory but needs richer monorepo parity | Add deployable-app heuristics and denominator tests |
| Criteria output uses numerator/denominator per criterion | RA1 has per-result statuses; markdown does not consistently present N/M parity | Add scope-aware score display for repository/app criteria |
| Report ends with top 2-3 highest-impact recommendations | RA1 action items can list more; ordering is not explicitly next-level optimized | Add deterministic recommendation selection |
| `/readiness-fix` fetches latest stored report by origin and starts remediation session | `ra1 fix` reads `.agents/readiness/latest.json` or explicit `--report` | Add report lookup by origin/history and skill instructions passthrough |
| Dashboard/API expose historical progression | RA1 has no local API-compatible history schema | Add local history schema, diff command/output, and API-shaped JSON |
| Factory includes observability/product pillars | RA1 roadmap defers these | Add evidence-specific advisory criteria, then graduate cautiously |

## Phase 0 — Contract, coverage, and fixture harness first

### Goal

Make it impossible for later roadmap work to accidentally inflate score, reduce test rigor, or ship untested command behavior.

### Files

- `pyproject.toml`
- `engine/readiness/coverage_gate.py` plus optional thin `scripts/coverage_gate.py` wrapper
- `.github/workflows/ci.yml`
- `tests/test_coverage_gate.py`
- `evals/fixtures.py`
- `evals/contracts.py`
- `tests/test_eval_fixtures.py`
- `evals/thresholds.json`
- `tests/test_contracts.py`
- `references/pillars.md`
- `CONTRIBUTING.md` if contributor-facing coverage commands are introduced

### Implementation steps

1. Add a coverage gate in `engine/readiness/coverage_gate.py` that enforces:
   - repository-wide branch coverage fail-under at the current baseline or higher;
   - 100% branch coverage for files listed in a `--changed-files` input;
   - failure when a changed Python file is not present in coverage JSON, unless it is an explicitly documented thin wrapper with no logic;
   - explicit allowance for reviewed `# pragma: no cover` branches only when the test asserts they are intentional.
2. If a `scripts/coverage_gate.py` entrypoint is added, keep all logic in the covered engine module; the script may only import `main` and call it under a reviewed no-cover wrapper.
3. Update `.github/workflows/ci.yml` so the existing coverage step (`coverage report --fail-under=90`) also emits `coverage json` and calls the touched-file gate. If local contributor commands change, document them in `CONTRIBUTING.md` or the closest existing contributor guide.
4. Add fixture helpers that can define pass-shaped and fail-shaped repositories per criterion without duplicating setup boilerplate.
5. Add canned GitHub runner support to fixture evals so T2 criteria can eventually prove pass/fail behavior in `python3 -m evals.fixtures`; until this exists for a criterion, T2 pass/fail unit tests are not enough for graduation.
6. Add contract tests proving:
   - advisory failures do not change deterministic level or gating totals;
   - skill-generated prose cannot raise levels above engine score in eval contracts;
   - unknown or unsupported evidence yields `skipped`/`unknown`, never pass.
7. Add a roadmap note that any criterion phase is blocked until its fixture pair and coverage assertions exist.

### Tests

- `python3 -m unittest tests.test_coverage_gate tests.test_contracts tests.test_eval_fixtures -t .`
- `python3 -m coverage run --branch --source=engine/readiness,evals,scripts -m unittest discover -s tests -t . && python3 -m coverage json -o .coverage.json && python3 -m coverage report --fail-under=90 && python3 scripts/coverage_gate.py --coverage .coverage.json --changed-files <phase-files>`
- `python3 -m evals.fixtures`

### Acceptance

- CI fails if any newly touched module in later phases lacks 100% branch coverage.
- Existing report score for this repo remains unchanged after Phase 0.
- `.github/workflows/ci.yml` invokes the equivalent gate in CI, and `pyproject.toml` records any shared coverage policy needed by local runs.
- T2 criterion graduation is blocked unless the fixture corpus can run that criterion against canned GitHub responses.

## Phase 1 — Command-surface parity for `/readiness-report`

### Goal

Make RA1's report command and `ra1-report` skill match the documented Droid workflow where useful, while preserving local/offline operation as an explicit RA1 extension.

### Files

- `engine/readiness/cli.py`
- `engine/readiness/run.py`
- `engine/readiness/report.py`
- `engine/readiness/model.py`
- `engine/readiness/collectors/git.py` or new `engine/readiness/history.py`
- `engine/readiness/version.py`
- `docs/cli.md`
- `skills/ra1-report/SKILL.md`
- `tests/test_cli.py`
- `tests/test_report.py`
- `tests/test_history.py`
- `scripts/vendor.py`

### Implementation steps

1. Add repository identity resolution:
   - detect whether `--project` is inside a git repo;
   - when an `origin` remote exists, read `git remote get-url origin` through the existing git collector path or a stdlib subprocess helper;
   - keep `raw_origin_url` in memory only; serialize a stable origin identity containing `identity_kind: "origin"`, `redacted_origin_url`, `host`, `owner`, `name`, and `identity_hash`;
   - when no origin exists and `--require-origin` is false, derive a local-only identity from the resolved project path: serialize `identity_kind: "local_path"`, `name`, `project_path_hash`, and `identity_hash`, but do not serialize the absolute raw path;
   - when `--require-origin` is true, local-only identity is disabled and missing origin exits non-zero.
2. Add report flags:
   - `--require-origin`: fail if no origin remote exists, matching Droid's prerequisite;
   - `--store-history`: write timestamped local history in addition to the primary report files; history requires a repository identity, using origin identity when available and local-path identity only when origin is absent and `--require-origin` is false;
   - `--history-dir DIR`: canonical history root; default is `<out>/history` when `--out` is set, otherwise `<project>/.agents/readiness/history`;
   - `--out DIR`: primary artifact directory for `report.<ext>` and `latest.json`; when `--store-history` is enabled, `latest.json`, history snapshots, and history index must all describe the same report and repository identity;
   - keep current default local scan behavior for backwards compatibility, but update `ra1-report` skill to run with `--require-origin --store-history --out <repo>/.agents/readiness` when emulating `/readiness-report`.
3. Bump `SCHEMA_VERSION` to `2` because `repository`, `generated_at`, and history metadata become canonical consumer inputs for `fix --latest`, history indexing, and deltas. Schema-1 reports remain valid only when passed explicitly with `ra1 fix --report PATH`; they are ineligible for latest/history/delta commands and should produce a clear “rerun report with schema 2” message.
4. Write local history artifacts:
   - `<primary-out>/latest.json` is the canonical latest report, where `<primary-out>` is `--out` when provided and otherwise `<project>/.agents/readiness`;
   - `<history-root>/<identity_hash>/<timestamp>.json` stores immutable snapshots, where `<history-root>` follows the `--history-dir` rule above;
   - `<history-root>/<identity_hash>/index.json` stores ordered metadata for diffs and points back to the matching `<primary-out>/latest.json` identity.
5. Add deterministic delta support only when the full stale-state contract matches: `schema_version`, `engine_version`, `registry_version`, and `detector_version`. Detector mismatches must skip N/M and application deltas because Phase 2 discovery changes can alter denominators without any repository change:
   - score delta;
   - criteria status changes;
   - newly passing/failing/unknown criteria;
   - do not compare advisory to gating as if equivalent.
6. Update `skills/ra1-report/SKILL.md` so the skill explicitly mirrors Droid's `/readiness-report`: origin prerequisite, report storage, human summary, score block verbatim, and local history caveat.
7. Vendor skill updates after docs and engine changes.

### Tests

- CLI tests for no-git, git-without-origin, git-with-origin, `--store-history` with local-path identity, and `--require-origin` rejecting local-path fallback.
- History tests proving immutable timestamped writes, `latest.json` update, and index ordering.
- Delta tests for full-version-contract comparison and mismatch skip across schema, engine, registry, and detector versions, including old schema-1 reports missing repository identity.
- Skill vendor test proving updated `SKILL.md` and engine copies are synced.
- Repository identity tests covering HTTPS remotes with embedded tokens, GitHub SSH remotes, non-GitHub SSH remotes, malformed remotes, and local-path fallback. No serialized report or history index may contain the raw tokenized URL or raw absolute local path.
- Storage contract tests for `--out`, `--store-history`, and `--history-dir`, including `ra1 fix --latest` resolving the same canonical history root that `ra1 report` wrote.
- Backward-compatibility tests proving explicit `ra1 fix --report <old-latest.json>` still works for safe scaffolds, while `fix --latest` refuses old reports that cannot be associated with a repository identity.

### Acceptance

- `ra1 report --project <repo> --require-origin` exits non-zero without origin and succeeds with origin.
- `ra1 report --project <repo-with-origin> --store-history --out <repo>/.agents/readiness --format json,markdown` writes `latest.json`, `report.json`, `report.md`, and timestamped origin-identity history.
- `ra1 report --project <repo-without-origin> --store-history --out <repo>/.agents/readiness --format json` writes timestamped local-path-identity history without serializing the absolute project path; `ra1 fix --latest --project <repo-without-origin>` resolves that same local identity.
- Old `latest.json` reports without repository identity have a documented path: explicit `--report` works, but latest/history/delta commands require rerunning `ra1 report --store-history`.
- `--out`, `--store-history`, and `--history-dir` have one documented canonical lookup rule, and `ra1 fix --latest` uses that same rule.
- `python3 scripts/vendor.py --check` passes.

## Phase 2 — Detection and application-discovery parity

### Goal

Match Factory's documented language and sub-application discovery envelope: JavaScript/TypeScript, Python, Rust, Go, Java, Ruby, single app, monorepo root, and independently deployable apps.

### Files

- `engine/readiness/detect.py`
- `engine/readiness/model.py`
- `engine/readiness/score.py`
- `tests/test_detect.py`
- `tests/test_score.py`
- `evals/fixtures.py`
- `evals/fixtures/*.json`
- `docs/extending.md`
- `references/pillars.md`

### Implementation steps

1. Add detection signals for:
   - JS/TS: `package.json`, lockfiles, workspace files, framework configs;
   - Python: `pyproject.toml`, `requirements*.txt`, `setup.cfg`, `setup.py`;
   - Rust: `Cargo.toml`, workspace members;
   - Go: `go.mod`, `go.work`, `cmd/*` apps;
   - Java: `pom.xml`, `build.gradle`, `settings.gradle`, `src/main`;
   - Ruby: `Gemfile`, `*.gemspec`, Rails app markers.
2. Add application discovery rules that identify deployable roots without inflating libraries into apps:
   - workspace package with scripts/build or service markers;
   - Python service packages with app entrypoints;
   - Go `cmd/*` binaries;
   - Java modules with application plugin or main source layout;
   - Rails apps and engines differentiated conservatively.
3. Preserve explicit `.agents/readiness/config.json` type pinning and app overrides as higher priority than heuristics.
4. Add numerator/denominator metadata in the scorer, not the renderer: app-scoped aggregation must populate authoritative `passed_apps` and `evaluated_apps` fields on criterion results before report formatting.
5. Add fixtures for monorepos with mixed app/library packages and unknown repos.

### Tests

- Unit tests for every language signal and app discovery shape.
- Regression tests for false app inflation: docs-only packages, shared libraries, vendored examples, and test fixtures.
- Fixture evals covering language/app applicability.
- Score aggregation tests asserting `passed_apps/evaluated_apps` for mixed-pass monorepos and proving report display never parses ratios back out of rationale text.

### Acceptance

- A monorepo report lists discovered applications with stable paths and descriptions.
- App-scoped criteria can report `passed_apps/evaluated_apps` consistently.
- Unknown repos remain honest `unknown`/`skipped`; no detector guesses to improve score.

## Phase 3 — Output parity: report shape and recommendations

### Goal

Make RA1's markdown and JSON report easy to compare with Droid output: achieved level, applications discovered, per-pillar criteria results as N/M, and 2-3 highest-impact next actions.

### Files

- `engine/readiness/report.py`
- `engine/readiness/score.py`
- `engine/readiness/model.py`
- `tests/test_report.py`
- `tests/test_score.py`
- `docs/cli.md`
- `skills/ra1-report/SKILL.md`

### Implementation steps

1. Add a `display_score` per criterion:
   - repository-scoped pass/fail/unknown/skip renders `1/1`, `0/1`, or `0/0` when not applicable;
   - app-scoped criteria render passed/evaluated apps;
   - advisory criteria render N/M but remain visually marked advisory.
2. Add a deterministic recommendation selector:
   - only gating failures/unknowns by default;
   - prioritize the next locked level before later-level polish;
   - cap at 3 action items;
   - include effort labels and exact criterion ids;
   - keep advisory improvements in a separate section.
3. Add JSON fields for `recommendations` and app-scoped criterion counts.
4. Update markdown headings to align with Droid concepts: Level Achieved, Applications Discovered, Criteria Results, Action Items, Advisory Improvements.
5. Keep GitHub warnings, JUnit, and SARIF gating-only.

### Tests

- Markdown snapshot-style tests for single-app, monorepo, advisory failures, and unknown GitHub criteria.
- JSON schema tests for `recommendations` and N/M fields.
- SARIF/GitHub/JUnit regression tests for advisory omission.

### Acceptance

- Human report ends with 2-3 highest-impact gating recommendations.
- Criteria display includes N/M without changing scoring math.
- Advisory loop failures never appear as merge-blocking action items.

## Phase 4 — `/readiness-fix` parity and safe remediation workflow

### Goal

Make `ra1 fix` and the `ra1-fix` skill mirror Droid's `/readiness-fix`: resolve the latest stored report for the repo, plan remediation from failing criteria, accept natural-language focus instructions, and verify via a new report run.

### Files

- `engine/readiness/fix/recipes.py`
- `engine/readiness/cli.py`
- `engine/readiness/history.py`
- `skills/ra1-fix/SKILL.md`
- `docs/cli.md`
- `tests/test_fix.py`
- `tests/test_cli.py`
- `tests/test_history.py`
- `scripts/vendor.py`

### Implementation steps

1. Add `ra1 fix --latest` behavior that resolves the latest report by repository identity from the same canonical store defined in Phase 1: explicit `--history-dir` first, otherwise `<project>/.agents/readiness/history`. A custom report store created with `ra1 report --out <custom> --store-history` must be read with `ra1 fix --latest --history-dir <custom>/history`; keep `--report PATH` as an explicit override.
2. Add deterministic focus controls:
   - `--include ID...` and `--exclude ID...` are authoritative filters;
   - `--instructions TEXT` may only use a small documented keyword grammar that maps phrases to criteria/pillars, such as `prioritize <pillar>` and `do not touch <pillar>`;
   - unsupported free-form text must be printed as an advisory note and must not silently filter the remediation plan.
3. Add plan filtering:
   - include only failing/unknown criteria from the selected report;
   - respect explicit include/exclude filters before instruction-derived filters;
   - auto-apply registry-declared `autofixable` scaffolds even when the criterion is advisory, preserving the existing safe loop scaffold behavior;
   - require explicit inclusion for any non-scaffold advisory/prose work.
4. Keep current safety buckets:
   - Auto-apply: idempotent scaffolds only;
   - Propose: tailored docs/tests/runbooks for human review;
   - GitHub settings: checklist/commands only, never direct mutation from `ra1 fix`.
5. Add post-fix verification guidance that runs `ra1 report` again with the same origin/history settings.
6. Update `ra1-fix` skill to describe Droid parity and RA1 safety deviations.

### Tests

- Latest-report resolution by origin hash.
- Canonical-store interaction tests where `ra1 report --out <custom> --store-history` is followed by `ra1 fix --latest --history-dir <custom>/history`, plus default `.agents/readiness` lookup. Without `--history-dir`, custom stores must fail with the exact default path checked.
- No prior report error matching Droid's “run report first” behavior.
- Instruction parsing tests for supported include/exclude/pillar negation forms, unsupported free-form fallback, and precedence between `--include`, `--exclude`, and `--instructions`.
- Dirty worktree refusal and non-overwrite regressions.
- No push/PR/GitHub-settings mutation paths in fix code.

### Acceptance

- `ra1 fix --project <repo> --latest` fails clearly when no stored report exists.
- With a stored report, dry-run output groups Auto-apply, Propose, and GitHub settings from that report.
- A report written with custom `--out` cannot strand `latest.json` silently: `fix --latest` either resolves the explicitly supplied `--history-dir` or fails with the exact default path it checked.
- `ra1 fix --instructions "do not touch CI"` excludes CI remediations only if `CI` is part of the documented grammar; unsupported phrases annotate the plan rather than changing auto-apply behavior.

## Phase 5 — Criteria parity expansion: deterministic candidates

### Goal

Add missing Factory-aligned criteria that can be checked deterministically without execution or agent judgment.

### Candidate criteria

| Criterion | Tier | Initial status | Evidence requirement | Notes |
|---|---:|---|---|---|
| `build.agentic_development` | T1 | advisory | recent commits with accepted agent co-author trailers | Existing check remains unregistered until fixtures cover history shapes |
| `build.build_command_documented` | T0 | advisory | package/workspace config or docs with explicit build command | Do not infer Makefile targets in v1 |
| `testing.coverage_threshold` | T0/T2 | advisory, then gating candidate | coverage config plus CI enforcement | Must detect config-only without CI as partial/fail |
| `testing.flake_quarantine` | T0 | advisory | documented quarantine/retry policy or CI flake labeling | Do not reward blind retries alone |
| `taskdisc.actionable_backlog_items` | T2 | advisory | GitHub issues with labels/milestones and actionable bodies | Requires canned gh runner tests |
| `build.ci_duration_budget` | T2 | advisory | recent CI runs under configured threshold | Skip without GitHub API or runs |

### Implementation steps

1. For each criterion, write pass and fail fixtures before registration.
2. Register with `gating:false` and `engine_min_version` bumped.
3. Implement checks with evidence-specific rationales and no substring-only passes.
4. Add fix recipes only when a safe scaffold is possible.
5. Graduate one criterion at a time only after evals prove zero FP/FN/applicability errors. For T2 criteria, that proof must come from canned GitHub responses in the fixture corpus, not only unit tests.

### Special rule for `build.reproducible_build`

Do not register as gating in this phase. A future T3 advisory version must:

- materialize fresh source via `git archive HEAD` where available;
- fall back to copied source only as `unknown`, never pass;
- exclude `.git`, `.agents`, caches, build artifacts, `node_modules`, `.venv`, `target`, and coverage outputs;
- run only allowlisted commands from explicit repo config;
- prove no-network sandboxing, scrubbed env, timeout, and deterministic cleanup;
- return `unknown` if isolation cannot be proven.

### Tests

- One unit test class per new check.
- Pass/fail/applicability fixtures before any graduation.
- Canned T2 runner tests for GitHub-backed criteria in both unit tests and `python3 -m evals.fixtures`.
- Coverage gate over every touched module.

### Acceptance

- New criteria are visible as advisory and do not change this repo's deterministic level unless deliberately graduated after fixtures.
- No criterion passes from generic keywords alone.
- T2 criteria remain advisory unless their pass/fail behavior is exercised through fixture-level canned GitHub responses.

## Phase 6 — Observability and product pillars, evidence-specific only

### Goal

Fill the largest Factory parity gaps without creating false confidence from superficial imports.

### Candidate criteria

| id | title | pillar | level | scope | check | applicability | initial gating | positive/negative fixture shapes |
|---|---|---|---:|---|---|---|---|---|
| `observability.structured_logging` | Structured Logging | Debugging & Observability | 3 | application | `observability.structured_logging` | service, api, frontend, monorepo-root | false | pass: logger config plus app usage; fail: print/import-only/config-only |
| `observability.tracing` | Distributed Tracing | Debugging & Observability | 3 | application | `observability.tracing` | service, api, frontend, monorepo-root | false | pass: exporter/resource config plus instrumentation wiring; fail: OTel/vendor import only |
| `observability.metrics` | Metrics Collection | Debugging & Observability | 3 | application | `observability.metrics` | service, api, frontend, monorepo-root | false | pass: metrics endpoint/exporter plus service wiring; fail: metrics dependency only |
| `observability.health_endpoints` | Health and Readiness Endpoints | Debugging & Observability | 3 | application | `observability.health_endpoints` | service, api, frontend, monorepo-root | false | pass: route/platform config exposing health/readiness semantics; fail: README mention only |
| `observability.alerting_rules` | Alerting Rules | Debugging & Observability | 4 | repository | `observability.alerting_rules` | service, api, frontend, monorepo-root | false | pass: alert config with service ownership; fail: dashboard only or unowned alert text |
| `observability.dashboards_as_code` | Dashboards as Code | Debugging & Observability | 4 | repository | `observability.dashboards_as_code` | service, api, frontend, monorepo-root | false | pass: dashboard JSON/Terraform/etc. linked to service metrics; fail: screenshot/doc only |
| `product.analytics_instrumentation` | Analytics Instrumentation | Product & Experimentation | 3 | application | `product.analytics_instrumentation` | frontend, service, api, monorepo-root | false | pass: analytics config plus named events/tracking plan; fail: SDK dependency only |
| `product.feature_flags` | Feature Flags | Product & Experimentation | 4 | application | `product.feature_flags` | frontend, service, api, monorepo-root | false | pass: provider/config plus app-side flag evaluation; fail: flag docs without code |
| `product.experiment_config` | Experiment Configuration | Product & Experimentation | 4 | repository | `product.experiment_config` | frontend, service, api, monorepo-root | false | pass: experiment registry with owner, variants, and success metric; fail: analytics events without experiment ownership |

Registry rows must use these exact applicability templates:

```json
{
  "scope": "application",
  "decide": "deterministic",
  "gating": false,
  "applies_when": {
    "project_types": ["service", "api", "frontend", "monorepo-root"],
    "languages": ["*"],
    "requires": []
  }
}
```

For repository-scoped observability rows, keep the same `applies_when` object and set `"scope": "repository"`. For product rows, use `project_types: ["frontend", "service", "api", "monorepo-root"]`. Every row must set `engine_min_version` to the release version that introduces the row.

Required fixture files before registration:

| criterion | pass fixture | fail fixture |
|---|---|---|
| `observability.structured_logging` | `observability-structured-logging-pass.json` | `observability-structured-logging-fail.json` |
| `observability.tracing` | `observability-tracing-pass.json` | `observability-tracing-import-only-fail.json` |
| `observability.metrics` | `observability-metrics-pass.json` | `observability-metrics-dependency-only-fail.json` |
| `observability.health_endpoints` | `observability-health-endpoints-pass.json` | `observability-health-docs-only-fail.json` |
| `observability.alerting_rules` | `observability-alerting-rules-pass.json` | `observability-alerting-unowned-fail.json` |
| `observability.dashboards_as_code` | `observability-dashboards-as-code-pass.json` | `observability-dashboards-screenshot-only-fail.json` |
| `product.analytics_instrumentation` | `product-analytics-instrumentation-pass.json` | `product-analytics-sdk-only-fail.json` |
| `product.feature_flags` | `product-feature-flags-pass.json` | `product-feature-flags-docs-only-fail.json` |
| `product.experiment_config` | `product-experiment-config-pass.json` | `product-experiment-events-only-fail.json` |

### Files

- `engine/readiness/checks/observability.py`
- `engine/readiness/checks/product.py`
- `engine/readiness/criteria/registry.json`
- `tests/test_observability_checks.py`
- `tests/test_product_checks.py`
- `tests/test_score.py`
- `evals/fixtures.py`
- `evals/fixtures/*.json`
- `references/pillars.md`
- `docs/extending.md`

### Implementation steps

1. Add applicability detectors per app type so libraries are skipped when runtime observability/product criteria do not apply.
2. Require two-part evidence for each pass: configuration plus usage/wiring.
3. Return partial/fail rationales that explain which evidence half is missing.
4. Keep all criteria advisory for at least one release.
5. Add docs explaining that RA1 checks configuration evidence, not production telemetry quality.

### Tests

- Positive fixtures for common stacks.
- Negative fixtures for import-only, docs-only, and config-only false positives.
- Applicability tests for libraries/CLIs where runtime telemetry may not apply.

### Acceptance

- Import-only OpenTelemetry, Segment, LaunchDarkly, or metrics packages do not pass.
- Advisory report explains evidence limitations clearly.

## Phase 7 — T4 advisory skill layer

### Goal

Add Factory-like qualitative guidance without contaminating deterministic scoring.

### Scope

- Naming consistency
- Code modularization
- README quality
- AGENTS.md quality
- Service-flow docs quality
- Runbook usefulness
- Autonomy workflow maturity commentary

### Files

- `skills/ra1-report/SKILL.md`
- `evals/contracts.py`
- `evals/judge.py`
- `tests/test_contracts.py`
- optional `evals/fixtures/*` advisory prose expectations

### Implementation steps

1. Define advisory-only output sections with explicit labels.
2. Add LLM-as-judge contracts that fail if advisory prose:
   - changes engine score;
   - claims unattended autonomy clearance;
   - marks failed criteria as passing;
   - invents evidence not present in engine output or read files.
3. Add examples for high-quality advisory feedback grounded in engine findings.
4. Keep the engine unaware of T4 scores.

### Acceptance

- Skill contract evals reject score inflation and evidence invention.
- T4 output is useful but cannot affect `score.level`, GitHub annotations, JUnit, or SARIF.

## Phase 8 — Local dashboard/API-compatible history

### Goal

Provide a local, API-shaped history surface that approximates Factory dashboard/API workflows without claiming cloud parity.

### Files

- `engine/readiness/history.py`
- `engine/readiness/cli.py`
- `engine/readiness/report.py`
- `docs/cli.md`
- `tests/test_history.py`
- `tests/test_cli.py`

### Implementation steps

1. Add `ra1 history list --project <repo>` showing timestamp, level, pass rate, gating counts, and registry version.
2. Add `ra1 history diff --project <repo> --from <id> --to <id>` showing score and criterion deltas.
3. Add `--format json|markdown` for history commands.
4. Shape JSON so a future API wrapper can expose reports without schema translation.
5. Document that Factory dashboard/API are external surfaces; RA1 stores local `.agents/readiness` history only.

### Tests

- List/diff commands over synthetic history directories.
- Corrupt history entry handling.
- Full-version-contract mismatch behavior, including detector-version changes that invalidate app-denominator deltas.

### Acceptance

- A user can run repeated reports and see progression locally.
- Corrupt or mismatched history does not crash normal reporting.

## Phase 9 — Docs, vendoring, release, and final verification

### Goal

Ship roadmap work as a coherent RA1 release with synced skills and reproducible verification.

### Files

- `engine/readiness/version.py`
- `pyproject.toml`
- `docs/cli.md`
- `docs/extending.md`
- `references/pillars.md`
- `skills/ra1-report/SKILL.md`
- `skills/ra1-fix/SKILL.md`
- `scripts/vendor.py`
- `README.md` if command examples materially change

### Implementation steps

1. Bump engine, registry, detector, schema, and package versions when registry/schema behavior changes; Phase 1 specifically requires `SCHEMA_VERSION = "2"`.
2. Update CLI docs for new report/fix/history flags.
3. Update pillars roadmap with shipped/advisory/graduated status.
4. Run `python3 scripts/vendor.py` after engine/templates/skill changes.
5. Run full verification matrix.
6. Prepare release notes that distinguish deterministic score changes from advisory-only additions.

### Final verification matrix

Run from repository root:

```bash
python3 -m unittest discover -s tests -t .
python3 -m coverage run --branch --source=engine/readiness,evals,scripts -m unittest discover -s tests -t .
python3 -m coverage json -o .coverage.json
python3 -m coverage report --fail-under=90
python3 scripts/coverage_gate.py --coverage .coverage.json --changed-files <all-python-files-touched-by-roadmap>
python3 -m evals.fixtures
python3 -m evals.parity
python3 scripts/vendor.py --check
./bin/ra1 report --project . --format json,markdown --out .agents/readiness --store-history
./bin/ra1 fix --project . --latest
```

Expected:

- Unit tests pass.
- Coverage total meets fail-under and touched modules have 100% branch coverage or reviewed pragmas.
- Fixture evals report zero violations.
- Parity meets threshold.
- Vendored skills are in sync.
- Report command writes current + historical artifacts.
- Fix command reads the latest stored report and defaults to dry-run.

## Adversarial review: likely failure modes and mitigations

| Failure mode | Why it matters | Mitigation in this plan |
|---|---|---|
| Score inflation through prose | Users trust the readiness level as deterministic | Skill contracts require exact score block and reject higher claims |
| Advisory criteria become accidental gates | Would make roadmap work break CI without graduation proof | Reporter/JUnit/SARIF/GitHub remain gating-only; `--fail-on` is explicit override |
| Origin parity breaks local/offline RA1 usage | RA1's current value includes scanning arbitrary local repos | `--require-origin` is opt-in for CLI, default in skill emulation of Droid |
| History stores secrets from identity sources | Remote URLs may contain tokens and local paths may reveal workstation details | `raw_origin_url` and raw absolute paths stay in-memory only; serialized reports use redacted origin fields or local path hashes, with tests for HTTPS tokens, SSH URLs, and local-path fallback |
| Numerator/denominator display changes scoring | Display logic can drift from score aggregation | Add display fields derived from existing result aggregation, tested separately |
| Detector over-classifies libraries as apps | False denominators lower scores and mislead users | Conservative app heuristics plus false-app fixtures |
| Reproducible build passes from dirty working tree | Not real reproducibility | Require `git archive HEAD`; fallback copy cannot pass |
| Observability passes from import-only evidence | Creates false production confidence | Require config plus wiring/usage for every observability/product criterion |
| Natural-language fix instructions become arbitrary codegen | Engine would stop being deterministic/safe | Instructions only filter/annotate remediation plan; skills do human-authored changes |
| Coverage gate is performative | Total coverage can hide untested new modules | Enforce touched-module 100% branch coverage in addition to total fail-under |
| T2 tests require live GitHub | Flaky and environment-dependent | Use canned gh runners for pass/fail and live GitHub only as smoke when available |

## Recommended `/goal` prompt

Use this when executing the roadmap:

```text
Implement docs/PLAN-roadmap-factory-parity.md phase by phase. Do not start a later phase until the current phase's acceptance commands pass. Preserve deterministic scoring boundaries, keep new criteria advisory unless fixture graduation rules are satisfied, enforce 100% branch coverage for every new or changed Python module, run vendor sync after engine/templates/skill changes, and stop only when the final verification matrix passes.
```
