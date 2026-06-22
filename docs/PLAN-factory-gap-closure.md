# PLAN — Factory gap closure (T0–T3 criteria) + T4 ignore mechanism

Status: **proposed** (design only; no engine rows mutated yet).
Source of truth for tiers/graduation: `references/pillars.md`. Registry: `engine/readiness/criteria/registry.json`.
Companion to the completed `docs/PLAN-roadmap-factory-parity.md`.

## 0. Scope and ground rules

Factory's published model is **82 criteria / 9 pillars**. Our registry is **57 rows** (32 gating
deterministic + 25 advisory, of which 9 are the opt-in `loop.*` cluster). The deterministic gating
*core* is already at parity; the gap is a long tail of **advisory** criteria plus two methodology
divergences. This plan closes the **T0–T3** tail and designs an **ESLint-style ignore mechanism**
for the **T4** judgment criteria that we deliberately keep out of the score.

Non-negotiable constraints (from `pillars.md` §"Tier model" and §"Graduation rules"):

1. **Only T0/T1/T2 may gate.** T3 (`decide:"execution"`) stays advisory until the sandbox contract
   exists *and* its fixtures are deterministic; **T4 never gates, ever.**
2. **The disqualifying direction is undeserved credit** (`pillars.md:138`, `max_fp_rate=0.0`). Every
   new criterion is designed so that *presence-only cannot grant a pass*.
3. **Two-part evidence** is mandatory for any "tooling present" criterion: a configuration/dependency
   signal **AND** a wiring/enforcement signal. Reuse `observability._two_part` and the `adep`/`aglob`/
   `agrep`/`atool` helpers (`engine/readiness/checks/_helpers.py`).
4. **Every row ships `gating:false` first** and graduates one at a time only after a pass-shaped *and*
   a fail-shaped fixture exist, the whole corpus is FP/FN/applicability-clean, and the new/changed
   Python module hits 100% branch coverage (`scripts/coverage_gate.py`) on top of the >90% total gate.
   T2 rows additionally require canned-`gh` fixtures.
5. **Applicability honesty.** Unsupported stacks `skip` with a reason; they never silently pass/fail.

This plan does **not** adopt Factory's weighted 1/2-point scoring or its skip→fail behavior on
unavailable GitHub APIs. Our binary + level-gating model and our `skip` (neutral) on a 403/404 are
deliberate and more defensible (their model penalizes a billing artifact — e.g. private repo without
GitHub Pro — as a readiness deficit). That divergence is documented, not closed.

## 1. Adversarial gap inventory (T0–T3)

Verdict legend:
- **GATE-CANDIDATE** — clean two-part T0/T2 signal; can graduate to gating after fixtures.
- **ADVISORY-ONLY** — useful signal but FP-prone, niche, or stack-narrow; surfaces as advisory,
  unlikely to ever graduate.
- **REJECT** — measures org behavior or duplicates an existing row; do not add as gating.
- **T3** — execution; advisory until the sandbox contract lands (§2); never gates per rule 1.

No proposed id collides with an existing registry id (checked against all 57 rows).

### 1.1 Style & Validation — code-health detectors (T0)

| Proposed id | Scope | Applies to | Two-part detection | Undeserved-credit guard | Verdict |
|---|---|---|---|---|---|
| `style.naming_convention_rule` | application | all | `@typescript-eslint/naming-convention` enabled / pylint naming / ruff `N` rules **AND** rule severity = error (not `off`/`warn`) | Dep present ≠ rule enabled — must parse the rule entry, not the package | GATE-CANDIDATE |
| `style.complexity_budget` | application | all | eslint `complexity`/sonarjs / ruff `[tool.ruff.lint.mccabe]`+`C901` / radon CI **AND** a numeric limit set | A complexity-capable linter installed ≠ a budget configured | GATE-CANDIDATE |
| `style.dead_code_detection` | application | all | `knip`/`ts-prune`/`vulture`/`deptry` config **AND** wired into CI or pre-commit | Tool in devDeps but never invoked = theater | GATE-CANDIDATE |
| `style.duplicate_code_detection` | application | all | `jscpd`/`pmd-cpd`/sonar dup config **AND** CI invocation | config file without a CI step | GATE-CANDIDATE |
| `style.large_file_guard` | repository | all | pre-commit `check-added-large-files` / max-lines lint rule / `.gitattributes` LFS **AND** enforcement | `.gitignore` of large dirs is the opposite of a guard | GATE-CANDIDATE |
| `style.tech_debt_tracking` | repository | all | TODO/FIXME scanner as CI error OR a tracked debt register (labeled issues / `TECH_DEBT.md`) | TODOs *in code* are the negative signal — must detect a *policy*, not the comments | ADVISORY-ONLY |

`naming_convention_rule` is the **deterministic proxy** for Factory's "Naming Consistency": it checks
that an *enforced rule exists* (T0), not whether names are good (that stays the T4 judgment — §3).
Both can coexist; the T0 row gates, the T4 judgment is advisory-and-ignorable.

### 1.2 Build System — dependency & release hygiene (T0; one T2)

| Proposed id | Scope | Applies to | Detection | Guard / note | Verdict |
|---|---|---|---|---|---|
| `build.unused_dependencies` | application | all | `depcheck`/`knip`/`deptry`/`cargo-udeps` **AND** CI wiring | dep present ≠ scan run | GATE-CANDIDATE |
| `build.version_drift` | repository | monorepo-root | `syncpack`/`manypkg`/pnpm `catalog`/workspace `engines` pinning | monorepo-only; skip elsewhere | GATE-CANDIDATE |
| `build.monorepo_tooling` | repository | monorepo-root | turbo/nx/lerna/rush/bazel/pants/pnpm-workspaces config | monorepo-only | GATE-CANDIDATE |
| `build.single_command_setup` | application | all | `make setup`/`bin/setup`/`script/bootstrap`/`task setup`/devcontainer `postCreateCommand` | narrower than `build_command_documented` (setup ≠ build) | GATE-CANDIDATE |
| `build.release_notes_automation` | repository | all | `semantic-release`/`changesets`/`release-please`/`git-cliff`/`towncrier` config | distinct facet from existing `build.release_automation` (publish) — frame as changelog/notes only to avoid double-counting | GATE-CANDIDATE |
| `build.dependency_weight_budget` | application | frontend | `size-limit`/`bundlesize`/bundle-analyzer **AND** a budget value | frontend-only; skip backend/libs | GATE-CANDIDATE |
| `build.pr_review_automation` | repository | all | a PR-review GH Action workflow | overlaps `automated_security_review`; low marginal value | ADVISORY-ONLY |
| `build.deployment_frequency` | repository | all | GH releases/deployments cadence (T2) | **org cadence ≠ repo readiness** | REJECT (gating) |
| `build.build_performance_tracking` | repository | all | turbo remote-cache / CI timing | duplicates `ci_duration_budget` | REJECT (fold in) |

### 1.3 Testing (T0 + T3)

| Proposed id | Scope | Detection | Verdict |
|---|---|---|---|
| `testing.test_isolation` | repository | `pytest-randomly`/`pytest-xdist`/go `-race`/jest sequencer config | ADVISORY-ONLY (ambiguous signal) |
| `testing.test_performance_tracking` | repository | `--durations`/jest timing / CI slow-test report | ADVISORY-ONLY (weak) |
| `testing.tests_pass` *(exists, T3)* | application | runs detected test cmd under sandbox; opt-in `--exec` | T3 — advisory until §2 |
| `testing.behavioral_smoke` *(new, T3)* | application | run a declared smoke/health command post-build under sandbox | T3 — advisory until §2 |

### 1.4 Documentation (T0)

| Proposed id | Scope | Applies to | Detection | Note | Verdict |
|---|---|---|---|---|---|
| `docs.auto_generation` | repository | all | `typedoc`/`sphinx`/`mkdocs`/`docusaurus`/`redoc` **AND** CI/build wiring | config without a build step fails | GATE-CANDIDATE |
| `docs.agents_md_ci_validation` | repository | all | a CI job that validates/executes AGENTS.md commands | extends `docs.agents_md_validation` (well-formedness → command-freshness) | ADVISORY-ONLY |
| `docs.architecture_doc` | repository | service, api, monorepo-root | a non-opt-in architecture doc (ADR dir / `docs/architecture*` / `CONTEXT.md`) | overlaps opt-in `loop.architecture_doc`; gate behind disjoint applicability to avoid double-fire | ADVISORY-ONLY |

### 1.5 Dev Environment (T0)

| Proposed id | Scope | Applies to | Detection | Verdict |
|---|---|---|---|---|
| `devenv.local_services` | repository | service, api, monorepo-root | `docker-compose.y*ml`/`compose.y*ml`/`Procfile`/`Tiltfile`/`skaffold.yaml` | GATE-CANDIDATE |
| `devenv.database_schema` | application | data, service, api | `migrations/`/`prisma/schema.prisma`/`alembic/`/`db/migrate`/schema `*.sql` | GATE-CANDIDATE |
| `devenv.devcontainer_runnable` *(T3)* | repository | all | build the devcontainer under sandbox | T3 — advisory until §2 |

### 1.6 Debugging & Observability — depth (T0, two-part, mirror existing 6)

| Proposed id | Scope | Applies to | Two-part detection | Verdict |
|---|---|---|---|---|
| `observability.error_tracking` | application | service, api, frontend, monorepo-root | Sentry/Bugsnag/Rollbar/Airbrake dep **AND** `init`/DSN wiring | GATE-CANDIDATE |
| `observability.runbooks` | repository | service, api, monorepo-root | `runbooks/`/`RUNBOOK.md` **AND** non-placeholder content (filledness, like `loop.*`) | GATE-CANDIDATE |
| `observability.profiling` | application | service, api | APM/profiler dep (pyroscope/py-spy/datadog-profiler) **AND** wiring | ADVISORY-ONLY (niche) |
| `observability.circuit_breakers` | application | service, api | resilience4j/opossum/pybreaker/polly **AND** wiring | ADVISORY-ONLY (niche) |
| `observability.deployment_markers` | repository | service, api | deploy annotations (Sentry releases / Datadog events / GH deployments) wired in CI | ADVISORY-ONLY (niche) |

### 1.7 Security — supply-chain & data-handling depth (T0)

| Proposed id | Scope | Applies to | Detection | Note | Verdict |
|---|---|---|---|---|---|
| `security.dependency_min_age` | repository | all | Renovate `minimumReleaseAge`/`stabilityDays` (or equivalent) | clean T0 supply-chain config | GATE-CANDIDATE |
| `security.log_scrubbing` | application | service, api | redaction utility wired (pino `redact`, structlog processors, log filters) | two-part | GATE-CANDIDATE |
| `security.secrets_management` | repository | all | secret-manager refs (vault/doppler/AWS SM/GH secrets in workflows) | overlaps `secret_scanning`; frame as *managed-usage* | ADVISORY-ONLY |
| `security.dast` | repository | service, api | DAST workflow (OWASP ZAP/Burp) present | config is T0 but *verification* is T3 | ADVISORY-ONLY |

`PII handling` and `privacy compliance` are **T4** (judgment) — routed to §3, not added here.

### 1.8 Product & Experimentation (T0)

| Proposed id | Scope | Applies to | Two-part detection | Verdict |
|---|---|---|---|---|
| `product.error_to_insight` | repository | service, api, frontend, monorepo-root | error-tracker **AND** issue-tracker integration (Sentry↔GitHub/Linear/Jira) so errors create issues | GATE-CANDIDATE (niche) |

### 1.9 Inventory summary

- **GATE-CANDIDATEs (T0/T2):** 19 — Style 5, Build 6, Docs 1, DevEnv 2, Observability 2 (+ shipped 6),
  Security 2, Product 1. Each graduates only through the fixture/coverage gate.
- **ADVISORY-ONLY:** 11 — surface as advisory, expected to stay non-gating.
- **REJECT:** 2 — `build.deployment_frequency` (org behavior), `build.build_performance_tracking` (dup).
- **T3:** `testing.tests_pass` (exists) + `testing.behavioral_smoke`, `devenv.devcontainer_runnable`,
  `build.reproducible_build` (deferred) — all advisory until §2.

## 2. T3 sandbox contract (the keystone)

Three T3 criteria (`tests_pass`, `behavioral_smoke`, `devcontainer_runnable`) and the deferred
`build.reproducible_build` all block on one thing: a trustworthy execution sandbox. `pillars.md:16`
already fixes the contract shape — implement it as a first-class module so any `decide:"execution"`
check runs identically.

**Contract (`engine/readiness/exec/sandbox.py`, advisory-gated behind `--exec`):**
- **Isolated copy** of the worktree (no in-place mutation).
- **No network** (deny by default).
- **Scrubbed env** (allowlist only; secrets stripped).
- **Wall-clock timeout** + output cap.
- **Command allowlist** — only the repo's *declared* test/build/smoke command (from manifest scripts
  or a config-declared command), never arbitrary input.

**Gating stance:** even with the sandbox, T3 stays advisory per rule 1. The *gating* signal for
"tests actually pass" remains the **T2** pair `build.ci_runs_tests` + a green CI run; T3 execution is
corroborating advisory. T3 graduates to gating only if/when deterministic T3 fixtures exist — an
explicit future decision, not assumed here.

Building the contract is the highest-leverage engineering item: it unblocks the testing keystone,
behavioral smoke, devcontainer-runnable, and reproducible-build in one stroke.

## 3. T4 ignore mechanism — "disable a judgment like an ESLint rule"

### 3.1 Problem framing

T4 criteria (naming consistency, code modularization, README/AGENTS.md quality, service-flow doc
quality, runbook quality, PII handling, privacy compliance, N+1 queries) are **agent-graded judgments**.
Per `pillars.md:124-128` they live in the **skills layer** as advisory commentary and **never touch the
deterministic score**. Today there is no way for a repo to say "this judgment doesn't apply to us" —
the agent just re-raises it every run. We want the ESLint experience: a declarative, repo-owned way to
turn a judgment **off**, scoped globally or by path, with the suppression **disclosed** (never silent).

### 3.2 Two muting mechanisms, kept distinct

| | Waivers (`waivers.json`) — **exists** | Judgment config (`config.json#judgments`) — **new** |
|---|---|---|
| Targets | deterministic gating criteria (T0–T2) | T4 judgment criteria only |
| Score effect | sets `Status.WAIVED`, **removed from gate denominator** | **none** — T4 is not in any denominator |
| Requires | `reason`, optional `expires` | optional `reason`; severity flag |
| Analogy | a signed exception to a hard rule | `eslint-disable` / `rules: { x: "off" }` |

Keeping them separate is the anti-gaming property: "ignoring a judgment" must never look like, or be
implemented as, a *waiver that inflates the score*. A judgment toggled `off` adds **zero** to any
passed count.

### 3.3 Config schema (lives in the existing `.agents/readiness/config.json`)

```jsonc
{
  "schema_version": "2",
  "loop_ready": false,
  "judgments": {
    "*": "advisory",                 // default for all judgments
    "naming_consistency": "off",     // ≡ eslint rules: { naming: "off" }
    "code_modularization": "advisory",
    "pii_handling": { "severity": "off", "reason": "no PII; static marketing site" }
  },
  "judgment_overrides": [
    { "paths": ["legacy/**", "vendor/**"], "judgments": { "naming_consistency": "off" } }
  ]
}
```

**Severity vocabulary — `off | advisory` only.** `error` is **rejected by the parser** (downgraded to
`advisory` with a `signals` note), because a T4 judgment can never gate. This is the structural mirror
of the engine refusing undeserved credit: there is no config path that turns a judgment into a
score-affecting pass/fail.

Mapping to ESLint:
- `judgments["*"]: "off"` ≡ disabling the whole judgment plugin.
- `judgments.{id}: "off"` ≡ `rules: { id: "off" }`.
- `judgment_overrides[].paths` ≡ ESLint `overrides[].files` / `.eslintignore` (path-scoped disable —
  the right analog for holistic judgments, since a line-level `disable-next-line` is meaningless for a
  repo-wide naming judgment).
- optional inline `# ra1-disable: naming_consistency` in a file/dir `AGENTS.md` ≡ inline disable —
  **stretch goal**, lower value than path overrides.

### 3.4 Engine + skills wiring

1. **Promote T4 to first-class advisory rows** with `decide:"agent"`, `gating:false`. This makes them
   appear in reports (Factory surfaces them too) and gives the ignore config a concrete target.
   Example row:
   ```json
   {"id":"judgment.naming_consistency","pillar":"Style & Validation","title":"Naming Consistency",
    "level":2,"scope":"repository","decide":"agent","gating":false,
    "check":"judgment.naming_consistency","applies_when":{"project_types":["*"],"languages":["*"]},
    "engine_min_version":"0.5.0"}
   ```
2. **Scorer guardrail (`score._eval_criterion`):** add an early branch — if `decide == "agent"`, the
   criterion is resolved to an advisory result and is **structurally barred from `PASSING`/gating** even
   if someone flips `gating:true`. The agent layer supplies the qualitative verdict; the engine only
   carries it as advisory. (Analogous to the existing `opt_in`/`waivers` early branches at
   `score.py:96-104`.)
3. **Ignore resolution:** extend `load_readiness_config` consumption — a helper
   `judgment_severity(config, criterion_id, path=None)` returns `off|advisory`, applying
   `judgment_overrides` by longest-path match. `off` ⇒ the result carries `Status.WAIVED` with
   `rationale="ignored by judgments config"` **but is tagged `decide:"agent"`**, so it is excluded from
   the gate denominator (already true for `WAIVED`) *and* never countable as a pass.
4. **Report surface (`report._advisory_items`):** `off` judgments are filtered out of the advisory
   nag list and instead summarized in a single disclosure line — `Ignored judgments (N): naming_consistency, pii_handling`
   — mirroring ESLint's `--report-unused-disable-directives`. Suppression is always visible; it is
   never silent. JSON keeps the per-criterion `WAIVED`+`reason` so the disclosure is machine-readable.

### 3.5 Why not just use waivers for T4?

Because waivers are score-bearing exceptions to *gating* rules and require `reason`/`expires`
governance. Overloading them for "I don't want this naming nag" would (a) imply a score effect that
doesn't exist, and (b) make the report read as if a gating criterion were excused. The judgments block
is intentionally lighter and explicitly non-scoring — the correct ESLint analogy.

## 4. Phased rollout

Each phase = registry rows + check module + pass/fail fixtures + 100% branch coverage on the new
module; then `scripts/vendor.py` + `--check`; commit with the `Co-Authored-By` trailer. Rows land
`gating:false`; graduation to gating is a *separate* PR per criterion after the corpus is clean.

| Phase | Content | Tier | Risk |
|---|---|---|---|
| **G1 — Style code-health** | the 5 Style GATE-CANDIDATEs + `tech_debt_tracking` advisory | T0 | low; highest value |
| **G2 — Observability/Security depth** | `error_tracking`, `runbooks`, `log_scrubbing`, `dependency_min_age` (+ niche advisory) | T0 | low (reuses two-part) |
| **G3 — Build/DevEnv hygiene** | `unused_dependencies`, `version_drift`, `monorepo_tooling`, `single_command_setup`, `release_notes_automation`, `dependency_weight_budget`, `local_services`, `database_schema` | T0 | medium (stack-scoped applicability) |
| **G4 — Docs/Product** | `docs.auto_generation`, `product.error_to_insight` (+ advisory docs rows) | T0 | low |
| **G5 — T4 ignore mechanism** | `decide:"agent"` rows, scorer guardrail, `judgments` config, report disclosure | engine | medium (scorer + schema 2 surface) |
| **G6 — T3 sandbox contract** | `exec/sandbox.py`; wire `tests_pass`, add `behavioral_smoke`, `devcontainer_runnable` | T3 | high (isolation correctness) |

G1–G4 are independent and parallelizable. G5 is independent of the detector phases. G6 is the keystone
and can proceed in parallel but is the largest. Graduation of any G1–G4 row to gating is gated on
`python3 -m evals.fixtures` staying FP/FN/applicability-clean.

## 5. Verification seams

- **Per check:** unit tests in `tests/test_checks.py` (pass/fail/skip per stack) + a labeled fixture in
  `evals/fixtures/` exercising the new id (pass-shaped and fail-shaped repo).
- **Applicability:** fixtures run `no_github`; stack-scoped rows must `skip` cleanly off-stack
  (`tests/test_score.py`).
- **T4 ignore:** `tests/test_score.py` — `decide:"agent"` never enters `PASSING` even with `gating:true`;
  `off` judgment is `WAIVED`, excluded from denominator, contributes 0 passes; `tests/test_report.py` —
  `off` judgments leave the advisory list and appear in the disclosure line; `error` severity is
  downgraded with a signal.
- **Config:** `tests/test_detect.py` — `judgments`/`judgment_overrides` parsed; malformed → `{}` (mirror
  `load_readiness_config`); `options["readiness_config"]` override beats disk.
- **Sandbox (G6):** `tests/test_exec.py` — no-network enforced, env scrubbed, timeout honored, only the
  allowlisted command runs; isolated copy not mutated.
- **Whole-engine gates:** `python3 -m unittest discover`, `coverage run --branch` ≥90% total +
  `scripts/coverage_gate.py` 100% branch on changed modules, `python3 -m evals.fixtures`,
  `python3 -m evals.parity`, `scripts/vendor.py --check`.

## 6. Explicitly out of scope / rejected

- **Weighted scoring** (Factory's 1/2-point model + overall %). Our binary level-gating is retained;
  revisit only if external "match the %" parity is required.
- **skip→fail on unavailable GitHub APIs.** We keep `skip` (neutral); Factory's fail penalizes billing
  artifacts. Documented divergence.
- `build.deployment_frequency` — org cadence, not repo readiness.
- `build.build_performance_tracking` — folded into existing `build.ci_duration_budget`.
- T4 judgments as gating rows — barred by rule 1; they exist only as `decide:"agent"` advisory + the
  ignore mechanism.
- L5 / autonomy criteria — out of this plan (see prior L5 analysis); `pillars.md` stops at L4.
