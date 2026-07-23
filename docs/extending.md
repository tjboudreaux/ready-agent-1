# Extending the criteria

Criteria are **data + typed Python** — there is no expression DSL and nothing is `eval`'d.

## Add a criterion

1. **Write the check** in the right pillar module under `engine/readiness/checks/`. A check is a pure
   function of the context that returns a `Verdict`:

   ```python
   # engine/readiness/checks/security.py
   from ._helpers import passed, failed, ev

   def security_txt(ctx):
       if ctx.static.glob(["security.txt", ".well-known/security.txt"]):
           return passed("security.txt present", [ev("security.txt")])
       return failed("No security.txt")
   ```

   Read evidence from `ctx.static` (T0), `ctx.git` (T1), `ctx.github` (T2). For application-scoped
   checks use `aglob`/`adep` so shared monorepo config at the repo root still counts.

2. **Register it** in `engine/readiness/criteria/registry.json` (metadata + routing only):

   ```json
   {"id": "security.security_txt", "pillar": "Security & Governance", "title": "security.txt",
    "level": 4, "scope": "repository", "decide": "deterministic", "gating": false,
    "check": "security.security_txt",
    "applies_when": {"project_types": ["service", "api"], "languages": ["*"], "requires": []},
    "engine_min_version": "0.3.0"}
   ```

3. **Bump `REGISTRY_VERSION`** in `engine/readiness/version.py` (so stale cached state re-evaluates),
   then re-vendor: `python3 scripts/vendor.py`.

## advisory → gating

New criteria start `"gating": false` (advisory — they appear in the report but don't move the Level).
A criterion graduates to `"gating": true` only after the evals show it's reliable: low false-positive
and false-negative rates on the labeled fixtures in `tests/`. This keeps the gating score trustworthy.

## Evidence discipline (observability / product)

The Observability and Product criteria are **advisory** and require **two-part evidence**: a
configuration/dependency signal AND a wiring/usage signal (use `agrep` to confirm a usage site in
source). A dependency, a config file, or a README mention on its own never passes — an OpenTelemetry
import does not make a system observable, and a Segment/LaunchDarkly package does not make a product
instrumented. RA1 verifies *configuration evidence is present and wired*, not the runtime quality of
the telemetry, experiments, or flags.

## Applicability

- `project_types` — `["*"]` for all; otherwise matched against the app's detected type. If the type is
  `unknown`, a type-restricted criterion reports `unknown` (never silently skipped).
- `languages` — `["*"]` or an intersection with detected languages.
- `requires` — criterion ids that must `pass` first (e.g. `agents_md_validation` requires `agents_md`).
- `opt_in` — optional intent gate. The only supported value is `loop_ready`; when absent from
  top-level `.agents/readiness/config.json` as the literal JSON boolean `true`, matching criteria
  report `skipped` with rationale `not opted into loop readiness`.

## Project type pinning

If detection is wrong or low-confidence, pin it in `.agents/readiness/config.json`:

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

Vendor-neutral verification-loop declarations live in the same readiness config:

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

- `acdc.verify_command` names one verify entrypoint. RA1 resolves supported Make/Just/Task targets,
  package scripts, repository scripts, or recognized direct check commands before granting credit.
- `acdc.instruction_files` adds string globs to the agent-instruction files inspected for a local
  verification instruction plus runnable command.
- `acdc.hook_files` adds string globs for maintainer-declared executed-hook files; a matching file
  must contain a recognized check command, `sonar`, or `ra1`.

Every config-driven verdict cites `.agents/readiness/config.json`. Invalid shapes (a non-string
command, non-list file fields, or non-string list entries) are ignored and built-in detection still
runs; a non-empty `verify_command` that does not resolve fails rather than silently falling through.

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
non-static targets in `engine/readiness/fix/recipes.py`. Scaffolds must be safe to write blindly into a
repo that lacks them.
