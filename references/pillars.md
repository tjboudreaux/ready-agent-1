# Pillars — the criteria taxonomy and public roadmap

This is the published roadmap for Ready Agent 1's criteria. The deterministic gating set is
deliberately small and trustworthy; everything else ships **advisory-only** and graduates to
gating one criterion at a time, only after passing the labeled-fixture evals. The registry
(`engine/readiness/criteria/registry.json`) is the source of truth for what gates today; this
document is the source of truth for what comes next and the rules for getting there.

## Tier model

| Tier | What it reads | Default |
|---|---|---|
| **T0 static** | file globs + semantic parses (JSON/TOML/JSONC via stdlib) | always on |
| **T1 git** | commit history, co-authorship trailers, doc dates vs commits | always on |
| **T2 gh API** | branch protection, secret scanning, labels, backlog, CI runs | when remote is GitHub and `gh` is authed; otherwise `skipped: no GitHub API` (never failed) |
| **T3 execution** | running the repo's own lint/test/build | **OFF** — opt-in only, behind a sandbox contract (no network, scrubbed env, isolated copy, timeout, command allowlist); CI status from T2 substitutes |
| **T4 agent** | qualitative judgment (naming, doc quality, modularization) | skills only, **advisory** — never changes the score |

## Current gating set (v0.6.0 — 32 deterministic gating criteria)

Generated from `registry.json` v0.6.0; if this table and the registry disagree, the registry wins.
The gating set is **unchanged at 32**. 0.5.0 added a large advisory tier (Factory-parity gap closure:
Style code-health, observability/security depth, build/dev-env hygiene, docs/product), nine
agent-graded `judgment.*` criteria with an ESLint-style ignore (`.agents/readiness/config.json`
`judgments`), and two T3 execution criteria — all non-gating. See the CHANGELOG for the full list.

| id | Pillar | Title | Level | Applies to |
|---|---|---|---|---|
| `build.deps_pinned` | Build System | Dependencies Pinned | 1 | all |
| `build.vcs_cli` | Build System | VCS CLI Tools | 1 | all |
| `docs.readme` | Documentation | README | 1 | all |
| `security.gitignore_comprehensive` | Security & Governance | Gitignore Comprehensive | 1 | all |
| `testing.unit_tests_exist` | Testing | Unit Tests Exist | 1 | all |
| `build.ci_present` | Build System | CI Present | 2 | all |
| `devenv.env_template` | Dev Environment | Env Template | 2 | service, api, frontend, data, unknown, monorepo-root |
| `docs.agents_md` | Documentation | AGENTS.md | 2 | all |
| `security.security_md` | Security & Governance | SECURITY.md | 2 | all |
| `style.formatter` | Style & Validation | Formatter | 2 | all |
| `style.linter_config` | Style & Validation | Linter Config | 2 | all |
| `taskdisc.issue_templates` | Task Discovery | Issue Templates | 2 | all |
| `taskdisc.pr_templates` | Task Discovery | PR Templates | 2 | all |
| `testing.test_naming` | Testing | Test Naming Conventions | 2 | all |
| `build.ci_runs_tests` | Build System | CI Runs Tests | 3 | all |
| `devenv.devcontainer` | Dev Environment | Devcontainer | 3 | all |
| `docs.agents_md_validation` | Documentation | AGENTS.md Validation | 3 | all |
| `docs.doc_freshness` | Documentation | Documentation Freshness | 3 | all |
| `docs.skills` | Documentation | Skills | 3 | all |
| `security.branch_protection` | Security & Governance | Branch Protection | 3 | all |
| `security.codeowners` | Security & Governance | CODEOWNERS | 3 | all |
| `security.dependency_update_automation` | Security & Governance | Dependency Update Automation | 3 | all |
| `security.secret_scanning` | Security & Governance | Secret Scanning | 3 | all |
| `style.precommit_hooks` | Style & Validation | Pre-commit Hooks | 3 | all |
| `style.type_check` | Style & Validation | Type Check | 3 | all |
| `testing.integration_tests_exist` | Testing | Integration Tests Exist | 3 | service, api, frontend, monorepo-root, unknown |
| `build.release_automation` | Build System | Release Automation | 4 | all |
| `docs.api_schema_docs` | Documentation | API Schema Docs | 4 | service, api |
| `security.automated_security_review` | Security & Governance | Automated Security Review | 4 | all |
| `style.strict_typing` | Style & Validation | Strict Typing | 4 | all |
| `taskdisc.backlog_health` | Task Discovery | Backlog Health | 4 | all |
| `taskdisc.issue_labeling` | Task Discovery | Issue Labeling System | 4 | all |

## The roadmap — advisory criteria, by pillar

The full taxonomy targets roughly 90 criteria across the pillars below. Everything in this
section is **non-gating** until it graduates (rules in the next section). Criteria are added
as advisory first, in whatever release specs them; none of this list affects today's score.

### Loop readiness advisory cluster

Engine 0.3.0 adds nine `loop.*` criteria as an opt-in advisory cluster. A repo opts in with the
top-level readiness config flag:

```json
{ "schema_version": "1", "loop_ready": true }
```

The checks are T0 structural presence/filledness only: loop run log README, rules index, denylist,
signal schema README, PR artifact evidence template, OMP loop skills, prompt contracts,
architecture doc, and at least one domain README. They are all `gating: false`; failures appear as
Advisory Improvements and do not change the deterministic RA1 level or gating totals.

These criteria are **not** L0-L3 loop-autonomy clearance, behavioral smoke/gate verification, or
proof that a denylist/schema is semantically correct or enforced. They only say that a
maintainer-owned contract exists and is not obviously empty or placeholder text.


### Verification loop advisory cluster (AC/DC-derived)

Engine/registry 0.6.0 adds four always-on T0 criteria that operationalize Sonar's
Guide → Verify → Solve model across the fast inner loop and the task/PR outer loop:

- `build.check_command` (L2) — a single `check`/`verify` entrypoint chains lint, type, and test tools,
  or resolves from a maintainer-designated `acdc.verify_command`.
- `docs.agent_verify_contract` (L3) — one agent instruction file locally pairs an instruction to
  verify with a runnable command (Guide).
- `devenv.agent_hooks` (L4) — a machine-enforced post-edit hook executes a recognized check command
  (inner-loop Verify), rather than merely telling the agent what to do.
- `testing.new_code_quality_gate` (L4) — codecov patch, diff-cover, Sonar, or Qodana is configured and
  wired to bound changed-code quality (outer-loop Verify).

All four are `gating: false`; they appear as advisory improvements and graduate only under the
standard fixture and evidence rules. `testing.coverage_threshold` measures an absolute repository
coverage floor, while `testing.new_code_quality_gate` measures only new or changed code. Likewise,
`docs.agent_verify_contract` credits persistent guidance, while `devenv.agent_hooks` requires an
executed hook.

The optional `acdc` block in `.agents/readiness/config.json` supports `verify_command`,
`instruction_files`, and `hook_files`. Declarations are still resolved against repository files or
recognized commands, and every config-driven pass cites `.agents/readiness/config.json`. A declared
hook file is presence- and command-checked but not executed by T0; that residual maintainer trust is
visible in its config-cited evidence. The vendor-agnostic adoption pack under `templates/acdc/`
contains a workflow directive plus Guide, Verify, and Solve skill templates.

Known limit: `testing.new_code_quality_gate` detects only codecov patch status, diff-cover, Sonar,
and Qodana. Custom gates such as this repo's `scripts/coverage_gate.py` remain an intentional false-
negative class until a follow-up adds a verified schema.

Sources: [Sonar AC/DC overview](https://www.sonarsource.com/blog/the-future-is-ac-dc-the-agent-centric-development-cycle/),
[AC/DC documentation](https://docs.sonarsource.com/agent-centric-development-cycle), and the
[downloadable workflow pack](https://www.sonarsource.com/agent-centric-development/).

### Build System (deterministic candidates)
- **Agentic Development** (`build.agentic_development`, T1) — shipped advisory in 0.4.0: agent
  co-authorship trailers in recent history. Graduates only after the fixture corpus covers
  git-history shapes.
- **Build Command Documented** (`build.build_command_documented`, T0) — shipped advisory in 0.4.0:
  an explicit build command in package config or a Build doc section (Makefile targets not inferred).
- **CI Duration Budget** (`build.ci_duration_budget`, T2) — shipped advisory in 0.4.0: recent CI
  runs within a configured `ci_budget_minutes`; needs canned-`gh` run-timing fixtures before graduation.
- Build reproducibility (T3) — **deferred** (not registered): a clean-checkout build under the
  sandbox contract; see the reproducible-build rules below.

### Testing (deterministic + T3 candidates)
- **Coverage Threshold Enforced** (`testing.coverage_threshold`, T0) — shipped advisory in 0.4.0:
  coverage config **and** CI enforcement; config without enforcement fails.
- **Tests Pass** (`testing.tests_pass`, T3) — shipped advisory in engine 0.2.0: the detected
  test command runs on an isolated copy under the sandbox contract, opt-in via `--exec`.
  Graduates only with deterministic T3 fixtures.
- **Flaky Test Quarantine Policy** (`testing.flake_quarantine`, T0) — shipped advisory in 0.4.0:
  a documented quarantine policy; blind retries do not count.

### Task Discovery (deterministic candidates)
- **Actionable Backlog Items** (`taskdisc.actionable_backlog_items`, T2) — shipped advisory in
  0.4.0: most open issues are labeled/milestoned **and** carry a body; needs canned-`gh` fixtures
  before graduation.

### Observability (shipped advisory in 0.4.0, application/repository scoped)
- `observability.structured_logging` · `observability.tracing` · `observability.metrics` ·
  `observability.health_endpoints` (application, L3); `observability.alerting_rules` ·
  `observability.dashboards_as_code` (repository, L4).
- Every criterion requires **two-part evidence** — configuration/dependency AND wiring/usage — so
  an OTel/Prometheus import, a config file, or a README mention never passes on its own. RA1 checks
  configuration evidence, not the runtime quality of the telemetry. Applies only to
  service/api/frontend/monorepo-root; libraries/CLIs are skipped. Graduation needs the pass/fail
  fixture corpus to stay clean across stacks.

### Product & Experimentation (shipped advisory in 0.4.0)
- `product.analytics_instrumentation` (application, L3) · `product.feature_flags` (application, L4) ·
  `product.experiment_config` (repository, L4).
- Two-part evidence as above: an analytics/flag SDK in the dependency list is not enough without a
  named event, flag evaluation, or an owned experiment registry (variants + success metric + owner).

### Agent-graded soft criteria (T4, advisory forever unless evals prove otherwise)
- Naming Consistency · Code Modularization · README quality · AGENTS.md quality ·
  Service-Flow documentation quality · Runbooks.
- T4 judgments are rendered by the skills as advisory commentary and never change the
  deterministic score.


### DORA-derived advisory criteria (0.6.0)

Engine/registry 0.6.0 adds ten always-on advisory criteria grounded in the DORA decade + AI
Capabilities Model crosswalk (`references/dora-crosswalk.md`). All are `gating: false`.
Repo-level proxies stay honest (`partial` in the crosswalk); they do not claim full DORA coverage.

| id | One-line DORA rationale |
|---|---|
| `build.small_batches` | LOC-churn proxy for DORA small batches / AI capability #5 (heuristic, not releasability). |
| `build.integration_frequency` | Activity-anchored integration cadence proxy for CI/trunk short-cycle delivery. |
| `taskdisc.review_latency` | Median first-review latency ≤48h — fast peer review lever (2023). |
| `observability.slo_definitions` | Reliability-contract artifact + CI/deploy wiring (SRE/SLO line). |
| `observability.incident_learning` | Postmortem/incident-review docs proxy for learning-from-failure. |
| `docs.ai_stance` | Discoverable AI usage policy — AI capability #1 (repo proxy). |
| `security.agent_permissions` | Shared least-privilege agent deny/restrictive-allow config (2026 guidance). |
| `docs.machine_context` | MCP/`llms.txt` machine-readable context beyond AGENTS.md — AI capability #3. |
| `build.agent_config_versioned` | Prompts/agent configs with multi-commit history — AI #4 + 2026 guidance. |
| `judgment.user_feedback_loop` | T4 forever: feedback reaches prioritization — AI capability #6. |

`build.agentic_development` remains adoption evidence only and is not a readiness claim.

## Graduation rules (advisory → gating)

A criterion graduates only when **all** of the following hold, enforced by
`python3 -m evals.fixtures` in CI (`evals/thresholds.json`):

1. **Fixture coverage** — at least one labeled fixture exercises the criterion, including
   both a `pass`-shaped and a `fail`-shaped repo for newly graduating criteria.
2. **Zero false positives / false negatives** on the whole corpus (`max_fp_rate` /
   `max_fn_rate` = 0.0). A false positive — undeserved credit — is the disqualifying
   direction; a wrong skip counts too (`max_applicability_rate` = 0.0).
3. **Deterministic tier** — only T0/T1/T2 criteria can gate. T3-derived criteria stay
   advisory until the sandbox contract is implemented and their fixtures are deterministic;
   T4 criteria never gate.
4. **Applicability honesty** — unsupported environments must `skip` with a reason (e.g.
   `skipped: no GitHub API`), never silently pass or fail.
5. **Coverage discipline (roadmap work)** — any roadmap criterion phase is blocked until its
   pass/fail fixture pair exists *and* every new or changed Python module reaches 100% branch
   coverage (or carries a reviewed `# pragma: no cover`). This is enforced by
   `scripts/coverage_gate.py` against the PR diff, in addition to the >90% total gate. T2
   criteria additionally require canned-`gh` fixtures in the corpus — not only unit tests —
   before they may graduate.

Notes on current coverage: T2 criteria are exercised in the corpus for applicability only
(fixtures run with `no_github`, so they must skip cleanly); their pass/fail logic is covered
by unit tests with canned `gh` responses. Canned-runner fixtures that exercise T2 pass/fail
in the corpus are the next pipeline improvement.

## Parity

`python3 -m evals.parity` compares engine verdicts against hand-curated snapshots of
Factory.ai's public FastAPI/Express readiness-report shapes (fixtures carry a `parity` block).
Offline by design — Factory's published criterion counts conflict with each other, so this is
a reviewed equivalence map, not a scrape; the gate fails below `parity_min_agree`.
