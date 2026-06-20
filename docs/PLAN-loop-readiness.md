# Plan — Loop-readiness criteria (advisory, opt-in)

Status: superseded by engine `0.3.0` implementation
Date: 2026-06-20
Owner: RA1

## Implemented decision

RA1 `0.3.0` implements a narrow, advisory loop-readiness cluster. The implementation intentionally differs from the original proposal where it would have made unsafe or over-broad claims.

Loop readiness is an explicit repo intent, enabled only by the top-level readiness config flag:

```json
{ "schema_version": "1", "loop_ready": true }
```

Without the literal JSON boolean `true`, every `loop.*` criterion is visible as `skipped` with rationale `not opted into loop readiness`.

## Implemented criteria

All implemented rows are repository-scoped, deterministic T0, advisory (`gating: false`), and require `applies_when.opt_in: "loop_ready"`.

| id | Pillar | Check |
|---|---|---|
| `loop.loop_runs_dir` | Documentation | Filled `loop-runs/README.md` or `loop-runs/readme.md` |
| `loop.rules_index` | Security & Governance | Filled `.omp/rules/README.md` mentioning rules or denylist |
| `loop.denylist` | Security & Governance | Filled `.omp/rules/denylist.md` with a starter deny/block/never policy term or bullet |
| `loop.signal_schema` | Documentation | Filled `signals/README.md` with a fenced schema block and required schema terms |
| `loop.pr_artifact_template` | Task Discovery | Filled `.omp/commands/pr-artifact-template.md`; artifact-specific GitHub PR template is a fallback check only |
| `loop.skills_present` | Documentation | At least 3 filled `.omp/skills/*/SKILL.md` files |
| `loop.prompt_contracts` | Documentation | Filled `.omp/commands/goal.md` and `.omp/commands/loop.md` |
| `loop.architecture_doc` | Documentation | Filled `ARCHITECTURE.md`, `docs/ARCHITECTURE.md`, or `docs/architecture.md` |
| `loop.domain_docs` | Documentation | At least one filled `domains/*/README.md` |

## Implemented safe scaffolds

`ra1 fix` may scaffold only the small neutral loop artifacts below, preserving existing safety semantics: dry-run by default, no overwrites of non-empty files, and dirty worktree refusal unless `--force`.

| criterion | target | template |
|---|---|---|
| `loop.loop_runs_dir` | `loop-runs/README.md` | `templates/loop/loop-runs-README.md` |
| `loop.denylist` | `.omp/rules/denylist.md` | `templates/loop/denylist.md` |
| `loop.signal_schema` | `signals/README.md` | `templates/loop/signals-README.md` |
| `loop.pr_artifact_template` | `.omp/commands/pr-artifact-template.md` | `templates/loop/pr-artifact-template.md` |

The scaffolds are filled starter contracts, not placeholder TODO files, so they satisfy the weak filledness checks they create.

## Explicitly rejected from this implementation

- Mobile-doc presence checks: mobile intent needs a separate detector or opt-in. A generic loop-ready repo must not fail for lacking mobile docs.
- Artifact-presence smoke checks: artifact manifests and behavioral proof need a separate T1/T2 design. RA1 does not claim smoke behavior passed.
- Scaffolding `.github/pull_request_template.md` for loop artifacts: this collides with the existing generic PR-template fix. Loop remediation writes `.omp/commands/pr-artifact-template.md` instead.
- Scaffolding `.agents/readiness/config.json`, `.omp/skills/*`, `.omp/commands/goal.md`, `.omp/commands/loop.md`, `.omp/rules/README.md`, `domains/*/README.md`, architecture docs, mobile docs, kill-switch/autonomy files, GitHub settings, or a full loop harness.
- Treating `detect` config as the opt-in source. `loop_ready` is a top-level readiness config key; `detect` remains only project-type pinning.

## Boundaries that remain true

- RA1 Level 1-5 is repository readiness, not L0-L3 loop autonomy.
- Loop criteria check structural presence/filledness only. They do not verify runtime enforcement, denylist correctness, schema semantic correctness, behavioral smoke flows, kill switches, or unattended-loop clearance.
- Advisory loop failures are separated from gating action items in Markdown and omitted from GitHub warnings, SARIF results, JUnit gates, and deterministic score totals. A user may still opt into hard enforcement with `--fail-on loop.some_id`.
- Graduation to gating still requires the normal eval discipline: deterministic tier, labeled fixtures, and zero false positives/false negatives.

## Verification contract

The implementation is covered by unit and CLI integration tests for config opt-in, scoring skip behavior, all nine loop checks, non-gating score invariance, safe fix scaffolds, advisory reporting, vendor sync, and CLI JSON behavior. Fixture evals intentionally do not add `loop.*` expectations in this release because these criteria are advisory and opt-in.
