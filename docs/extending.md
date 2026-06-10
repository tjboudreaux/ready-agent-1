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
    "engine_min_version": "0.1.0"}
   ```

3. **Bump `REGISTRY_VERSION`** in `engine/readiness/version.py` (so stale cached state re-evaluates),
   then re-vendor: `python3 scripts/vendor.py`.

## advisory → gating

New criteria start `"gating": false` (advisory — they appear in the report but don't move the Level).
A criterion graduates to `"gating": true` only after the evals show it's reliable: low false-positive
and false-negative rates on the labeled fixtures in `tests/`. This keeps the gating score trustworthy.

## Applicability

- `project_types` — `["*"]` for all; otherwise matched against the app's detected type. If the type is
  `unknown`, a type-restricted criterion reports `unknown` (never silently skipped).
- `languages` — `["*"]` or an intersection with detected languages.
- `requires` — criterion ids that must `pass` first (e.g. `agents_md_validation` requires `agents_md`).

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

## Fix recipes

To make a criterion auto-remediable, add a `fix` block in the registry (`"kind": "scaffold"` +
`"template"`, or `"propose"` for prose, or `"github_setting"`) and a template under `templates/`. Wire
non-static targets in `engine/readiness/fix/recipes.py`. Scaffolds must be safe to write blindly into a
repo that lacks them.
