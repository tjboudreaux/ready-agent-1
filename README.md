# agent-readiness

**Score a repository's readiness for AI agents — deterministically, with cited evidence — and remediate the gaps.**

An open, local alternative to Factory.ai's Agent Readiness Report. Two agent skills over one
pure-stdlib Python engine:

- **`readiness-report`** — produces a reproducible **Level 1–5** score across 7 pillars (Style &
  Validation, Build System, Testing, Documentation, Dev Environment, Security & Governance, Task
  Discovery), with every verdict backed by cited evidence.
- **`readiness-fix`** — remediates the gaps with safe config scaffolds, proposes documentation drafts
  for review, and lists the GitHub settings to change — applied to a local branch, never pushed.

It runs anywhere: Claude Code, Droid, Codex, Gemini, or CI.

## Why it's different

| | file-existence tools (e.g. kodus) | Factory (SaaS) | **agent-readiness** |
|---|---|---|---|
| Verification | `ls` heuristics | grounded LLM (opaque) | real: semantic config parse + git + **GitHub API** |
| Score | — | server-side | **deterministic & reproducible**, every verdict cited |
| LLM role | optional | authoritative | **advisory only** — never moves the gating score |
| Project-type awareness | none | yes | yes, with explicit `unknown` (no silent skips) |
| Remediation | none | PR | **safe scaffolds + drafts**, local-branch, never pushes |
| Hosting | npm | upload your code | **local & open** — nothing leaves your machine |
| Extensible | fork | — | typed Python checks + data registry |

The split is the point: a **pure-stdlib engine owns the deterministic gating score** (same repo +
same engine version → identical score, in CI or locally); the **agent adds non-gating advisory**.

## Install

The skills follow the [agentskills.io](https://agentskills.io) `SKILL.md` standard and carry the
`agent-skills` topic, so any of these work:

```bash
gh skill install tjboudreaux/agent-readiness          # GitHub CLI
npx skills add tjboudreaux/agent-readiness            # skills.sh
gemini skills install tjboudreaux/agent-readiness     # Gemini CLI
# or add the plugin in Claude Code
```

No runtime dependencies — just **Python 3.11+** (and an authenticated `gh` for the GitHub checks).

## Use

In your agent, ask for a readiness report (the `readiness-report` skill runs the engine and adds
advisory). Or run the engine directly:

```bash
# Score a repo (writes report.md / report.json / latest.json under .agents/readiness/)
python3 skills/readiness-report/scripts/readiness/cli.py report --project . \
  --format markdown,json --out .agents/readiness

# See what remediation would do (dry run), then apply safe scaffolds to a branch
python3 skills/readiness-fix/scripts/readiness/cli.py fix --project .
python3 skills/readiness-fix/scripts/readiness/cli.py fix --project . --apply
```

## CI

Gate merges on a minimum level and publish findings (SARIF → Security tab, JUnit, step summary):

```yaml
# .github/workflows/readiness.yml
jobs:
  readiness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: tjboudreaux/agent-readiness/ci@v1
        with: { min-level: "3", formats: "markdown,junit,sarif,github" }
        env: { GH_TOKEN: "${{ github.token }}" }
```

## The level model

Levels **1 Functional → 2 Documented → 3 Standardized → 4 Optimized → 5 Autonomous**. A level is
achieved when ≥80% of its gating criteria pass *and* all lower levels are achieved. Criteria that
don't apply to the project type are `skipped` (visibly, with a reason); when the type can't be
determined they're `unknown`, never silently skipped. v1 gates ~32 high-confidence deterministic
criteria; observability and product-analytics ship advisory-only (see [docs/extending.md](docs/extending.md)).

## Docs

- [Getting started](docs/getting-started.md)
- [CLI reference](docs/cli.md)
- [Extending the criteria](docs/extending.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE).
