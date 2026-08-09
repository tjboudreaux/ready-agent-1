# Extending the criteria

Criteria are **data + typed Python** — there is no expression DSL and nothing is `eval`'d.

## Add a criterion

1. **Write the check** in the right pillar module under `engine/readiness/checks/`. A check is a pure
   function of the context that returns a `Verdict`:

   ```python
   # engine/readiness/checks/security.py
   from ..safe_io import RepoReadState
   from ._helpers import passed, failed, unknown, ev

   def security_txt(ctx):
       obs = ctx.static.read_repo_file("security.txt")
       if obs.state is RepoReadState.OK:
           return passed("security.txt present", [ev("security.txt")],
                         reason_code="security.security_txt.configured")
       if obs.state is RepoReadState.MISSING:
           return failed("No security.txt",
                         reason_code="security.security_txt.missing")
       return unknown("security.txt could not be read safely",
                      reason_code="security.security_txt.observation_indeterminate")
   ```

   Read evidence through the bounded observation APIs only. `ctx.static` (T0) exposes
   `read_repo_file` / `glob_repo_files` over `safe_io` — typed `RepoFileObservation` /
   `RepoDiscoveryObservation` values with closed states (`ok`, `missing`, `unreadable`,
   `unsafe_path`, `oversize`, `overflow`, `unsupported`) and never a partial payload; checks
   never call `Path.read_text` / `Path.glob` directly. `ctx.git` (T1) and `ctx.github` (T2)
   return the same lossless `CollectorObservation` shape (`present` / `absent` / `unreadable` /
   `unavailable`). For application-scoped checks use `aglob`/`adep` so shared monorepo config at
   the repo root still counts.

2. **Register it** in `engine/readiness/criteria/registry.json` (metadata + routing only):

   ```json
   {"id": "security.security_txt", "pillar": "Security & Governance", "title": "security.txt",
    "level": 4, "scope": "repository", "decide": "deterministic", "gating": false,
    "check": "security.security_txt",
    "applies_when": {"project_types": ["service", "frontend"], "languages": ["*"], "requires": []},
    "engine_min_version": "0.11.0"}
   ```

   Valid `project_types` are `"*"`, `"unknown"`, `"monorepo-root"`, and the detector's pinned
   types (`library`, `service`, `frontend`, `cli`, `data`, `infra`). The dead `"api"` type was
   removed from the registry in 0.11.0 — `service` is the canonical type for deployable
   backends; do not reintroduce `api`.

3. **Bump `REGISTRY_VERSION`** in `engine/readiness/version.py` (so stale cached state re-evaluates),
   then re-vendor: `python3 scripts/vendor.py`.

Optional metadata: a criterion that belongs to the AC/DC verification loop carries an `acdc` block
— `"acdc": {"stage": "guide|verify|solve", "loop": "inner|outer|both"}`. Both keys are required
when the block is present (`"loop": "both"` is the explicit both-loops classification; never omit
`loop` to mean it). The engine copies the pair onto each result as `acdc_stage`/`acdc_loop` and
the reports render it as a label; it never affects the score. See `references/pillars.md` for the
current mapping.

## Decision traces, reason codes, and limitations

Every runtime result carries a deterministic **decision trace** built by the scorer — rule →
observation → evaluation → conclusion — that references evidence by index rather than copying it.
Checks never build traces; the verdict helpers accept keyword-only `reason_code=` and
`limitations=`.

- A direct verdict's `reason_code` is a literal dotted code
  (`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, ≤128 bytes) prefixed with the criterion id —
  `security.security_txt.missing`, not prose. Structural paths keep the scorer's own stable
  codes (`waiver.active`, `prerequisite.unmet`, `applicability.*`, `aggregate.<status>`).
- **Typed-code compatibility.** Consumers may depend on schema/trace versions, object keys, step
  order, enum literals, criterion ids, `reason_code`, `rule_ref`, and evidence-reference
  semantics. Human prose (`message`, `rationale`, `limitations`, evidence summaries) may improve
  without a schema bump and must never be used as a policy key.
- `limitations` are deterministic disclosure strings. Attach one whenever a pass proves less
  than its title suggests — repository permission-policy shape does not prove runtime
  enforcement; recognized CODEOWNERS syntax does not prove identity, access, or required review.
- Never interpolate repository-derived text (policy lines, command values, owner handles) into
  rationales or evidence; cite safe categories, counts, and repository-relative sources only.

## Strict unknown vs fail

A definite repository condition decides `pass`/`fail`. Evidence that is unsafe, unreadable,
oversize, overflow, or unsupported — anything that could hide the deciding signal — is a
blocking `unknown`, never a silent absence, and one safe file can never mask an unsafe or
malformed sibling. Discovery has no "missing" state: an empty successful search is `ok` with no
paths. Malformed or unreadable T2 observations likewise map to `unknown` rather than collapsing
to fail or pass.

## advisory → gating

New criteria start `"gating": false` (advisory — they appear in the report but don't move the
Level). Graduation is **manual** and follows `docs/criterion-graduation.md`: the labeled corpus
(`evals/criterion_labels.json`) is scored by `evals/criterion_benchmark.py` — at least 100
human-reviewed cases with minimum pass/blocking/ecosystem representation, ≥0.99 pass precision,
≥0.95 exact four-status accuracy, and zero adversarial or high-severity false passes — and then
only a maintainer-authored ADR plus a reviewed release change flips `gating` to `true` with
before/after score fixtures. Benchmark eligibility never edits the registry by itself.

## Evidence discipline (observability / product)

The Observability and Product criteria are **advisory** and require **two-part evidence**: a
configuration/dependency signal AND a wiring/usage signal (use `agrep` to confirm a usage site in
source). A dependency, a config file, or a README mention on its own never passes — an OpenTelemetry
import does not make a system observable, and a Segment/LaunchDarkly package does not make a product
instrumented. RA1 verifies *configuration evidence is present and wired*, not the runtime quality of
the telemetry, experiments, or flags.

## Applicability

- `project_types` — `["*"]` for all; otherwise matched against the app's detected type. If the type
  is `unknown`, a type-restricted criterion reports `unknown` (never silently skipped).
- `languages` — `["*"]` or an intersection with detected languages.
- `requires` — criterion ids that must `pass` first (e.g. `agents_md_validation` requires `agents_md`).
- `opt_in` — optional intent gate. The only supported value is `loop_ready`; when absent from
  top-level `.ra1/config.json` as the literal JSON boolean `true`, matching criteria report
  `skipped` with rationale `not opted into loop readiness`.

Config, waiver, or manifest input that is malformed, unsafe, or unreadable **and** could change
detection or applicability marks the whole scan repository-indeterminate: every criterion reports
`unknown` rather than a partial positive score.

## Project type pinning

If detection is wrong or low-confidence, pin it in `.ra1/config.json`:

```json
{
  "schema_version": "1",
  "detect": {
    "project_type": "service",
    "apps": { "packages/api": "service", "packages/web": "frontend" }
  }
}
```

- `detect.project_type` pins a single-app repo (one of `library`, `service`, `frontend`, `cli`,
  `data`, `infra`); `detect.apps` pins per-app types in a monorepo, keyed by app path.
- A pin sets the type to high confidence and always emits a signal naming the config file, so the
  override stays auditable in the report. Invalid values are ignored (with a signal), and pins can
  only set a type — they cannot skip criteria or lower confidence.

Also consider opening an issue with the repo shape — misclassification is treated as a bug, since a
wrong skip inflates the score.


## AC/DC verification-loop configuration

Vendor-neutral verification-loop declarations live in the same readiness config (`.ra1/config.json`):

```json
{
  "schema_version": "1",
  "acdc": {
    "verify_command": "make check",
    "instruction_files": ["docs/agent-guide.md"],
    "hook_files": [".cursor/hooks.json", ".agents/hooks/*.sh"]
  }
}
```

- `acdc.verify_command` names one verify entrypoint. RA1 resolves a bounded allowlist — Make/Just/
  Task targets, npm/pnpm/yarn scripts, `python -m pytest|unittest|mypy|ruff`, or one root-confined
  `scripts/` path — before granting credit; a non-empty command that does not resolve fails rather
  than silently falling through, and unsafe/unreadable candidates report `unknown`.
- `acdc.instruction_files` adds string globs to the agent-instruction files inspected for a local
  verification instruction plus runnable command.
- `acdc.hook_files` adds string globs for maintainer-declared executed-hook files; a matching file
  must contain a recognized check command, `sonar`, or `ra1`.

Config-supplied globs use a restricted pattern grammar (relative POSIX literals plus `*|?|**`, at
most 128 patterns of at most 512 bytes each) and resolve through the bounded discovery API — they
nominate in-root reads only. Every config-driven verdict cites `.ra1/config.json`. Invalid shapes
(a non-string command, non-list file fields, or non-string list entries) are ignored and built-in
detection still runs.

## Application discovery

Detection inventories independently deployable applications so app-scoped criteria report
`passed_apps/evaluated_apps` (an N/M numerator/denominator). Discovered sources:

- **npm/yarn/pnpm workspaces** (`workspaces` in `package.json`, or `pnpm-workspace.yaml` /
  `turbo.json` / `nx.json` / `lerna.json` globbing `packages|apps|services/*`).
- **Cargo workspaces** (`[workspace].members`).
- **Go binaries** — each `cmd/<name>/` with a `.go` file (classified `service` when the module
  declares a web framework, else `cli`).
- **Maven modules** (`<modules>` in `pom.xml`) and **Gradle** `include` entries in
  `settings.gradle[.kts]`.

Library-only and non-deployable directories are never inflated into apps: a workspace glob match is
only an app when it carries a manifest, and paths under `examples/`, `vendor/`, `third_party/`,
`node_modules/`, `testdata/`, `fixtures/`, `samples/`, `docs/`, and `test(s)/` are excluded even when
they do. Honesty over score: when signals are weak the type stays `unknown` rather than guessed.

## Fix recipes

To make a criterion auto-remediable, add a `fix` block in the registry (`"kind": "scaffold"` +
`"template"`, or `"propose"` for prose, or `"github_setting"`) and a template under `templates/`. Wire
non-static targets in `engine/readiness/fix/recipes.py`. Scaffolds must be safe to create blindly
into a repo that lacks them: apply is always verified — clean/known Git, fresh baseline,
create-only writes (existing targets are skipped; `.gitignore` is create-if-missing only), a
same-option rescan, and a comparable delta decide exit 0. There are no verification or force
escape hatches.
