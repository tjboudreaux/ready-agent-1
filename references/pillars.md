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

## Current gating set (v0.3.0 — 32 deterministic criteria)

Generated from `registry.json` v0.3.0; if this table and the registry disagree, the registry wins.

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

### Build System (deterministic candidates)
- **Agentic Development** (T1) — agent co-authorship trailers in recent history. The check
  exists (`checks/build.py:agentic_development`) but is intentionally unregistered until the
  fixture corpus covers git-history shapes.
- Build reproducibility (T3) — clean-checkout build succeeds under the sandbox contract.
- CI duration budget (T2) — feedback loop fast enough for agent iteration.

### Testing (deterministic + T3 candidates)
- Coverage threshold configured/enforced (T0/T2).
- **Tests Pass** (`testing.tests_pass`, T3) — shipped advisory in engine 0.2.0: the detected
  test command runs on an isolated copy under the sandbox contract, opt-in via `--exec`.
  Graduates only with deterministic T3 fixtures.
- Flake quarantine / retry policy present (T0).

### Observability (advisory-only in v1, the largest deferred pillar)
- Structured logging configured · distributed tracing · metrics export · alerting rules ·
  health/readiness endpoints · dashboards-as-code · on-call/runbook linkage.
- Deliberately not gating: the presence of an OTel import does not make a system observable;
  these graduate only with checks that read real configuration, not vibes.

### Product & Analytics (advisory-only in v1)
- Analytics instrumentation · feature-flag tooling · experiment configuration.

### Agent-graded soft criteria (T4, advisory forever unless evals prove otherwise)
- Naming Consistency · Code Modularization · README quality · AGENTS.md quality ·
  Service-Flow documentation quality · Runbooks.
- T4 judgments are rendered by the skills as advisory commentary and never change the
  deterministic score.

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

Notes on current coverage: T2 criteria are exercised in the corpus for applicability only
(fixtures run with `no_github`, so they must skip cleanly); their pass/fail logic is covered
by unit tests with canned `gh` responses. Canned-runner fixtures that exercise T2 pass/fail
in the corpus are the next pipeline improvement.

## Parity

`python3 -m evals.parity` compares engine verdicts against hand-curated snapshots of
Factory.ai's public FastAPI/Express readiness-report shapes (fixtures carry a `parity` block).
Offline by design — Factory's published criterion counts conflict with each other, so this is
a reviewed equivalence map, not a scrape; the gate fails below `parity_min_agree`.
