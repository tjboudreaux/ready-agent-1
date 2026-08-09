# Contributing

## Setup

No dependencies to install for the engine — it's pure Python standard library (3.11+).
For development you'll want `coverage`:

```bash
python3 -m pip install coverage   # dev-only; not a runtime dependency
```

## Build & test

There is no build step (pure stdlib). Run the test suite:

```bash
python3 -m unittest discover -s tests -t .
```

With coverage (the project gates on >90% total **and** 100% line+branch coverage for every
touched non-test Python file):

```bash
python3 -m coverage run --branch --source=engine/readiness,evals,scripts,ci -m unittest discover -s tests -t .
python3 -m coverage json -o .coverage.json
python3 -m coverage report -m
# Enforce 100% line+branch coverage on every touched canonical file (CI runs this against
# the release selection base recorded in release/versions.json):
python3 -m evals.coverage_gate --coverage .coverage.json --release-matrix release/versions.json --out .coverage-touched.json
```

## After changing the engine

The skills carry a **vendored** copy of the engine + templates. Re-sync and verify before committing:

```bash
python3 scripts/vendor.py            # sync engine + templates into skills/*/
python3 scripts/vendor.py --check    # CI runs this; must report "in sync"
```

## Layout

- `engine/readiness/` — the canonical pure-stdlib engine (detect, collectors, checks, score, report, fix).
- `engine/readiness/criteria/registry.json` — criteria metadata (logic lives in `checks/`, never in JSON).
- `skills/` — the three agentskills.io skills (self-contained; engine vendored in).
- `evals/` — agent-contract evals (deterministic contracts + LLM-as-judge).
- `templates/` — scaffolds written by `ra1-fix`.
- `ci/action.yml` — the composite GitHub Action.

## Adding or changing a criterion

See [docs/extending.md](docs/extending.md). New criteria start **advisory** and only graduate to
**gating** after they pass the evals (false-positive/negative thresholds on labeled fixtures).

Commits should be scoped and end with a `Co-Authored-By` trailer when authored with an agent.
