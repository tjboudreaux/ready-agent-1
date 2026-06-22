<p align="center">
  <img src="assets/banner.png" alt="Ready Agent 1 — is your codebase ready for the agents?" width="100%">
</p>

# Ready Agent 1

**Is your codebase ready for the agents? Score it, clear the gates, level up.**

The agent era booted up and your repo is the world they have to play in. Ready Agent 1 scans your repo,
assigns a readiness **Level (1–5)** — five gates to clear — cites the evidence for every check, and hands
you the **loadout** to reach the next level. Deterministic. Reproducible. No continues required.

> **READY?**  Player One has logged in. Brace for impact.

## What Ready Agent 1 does

Two agent skills over one pure-stdlib Python engine:

- **`ra1-report`** — the Readiness Scan: a reproducible Level score across 7 pillars (Style & Validation,
  Build System, Testing, Documentation, Dev Environment, Security & Governance, Task Discovery). Every check
  cites the file, commit, or GitHub setting that justifies it.
- **`ra1-fix`** — the Loadout: writes the safe config scaffolds that are simply *missing*, proposes
  documentation for your review, and lists the GitHub settings to change — all on a local branch, never pushed.

Ready Agent 1 doesn't play the game for you (it won't write your features). It makes sure the level is beatable.

## Why Ready Agent 1, not the others

| | file-existence tools | Factory (SaaS) | **Ready Agent 1** |
|---|---|---|---|
| Verification | `ls` heuristics | grounded LLM (opaque) | real: semantic config parse + git + **GitHub API** |
| The score | — | server-side | **deterministic & reproducible**, every check cited |
| The LLM's role | optional | authoritative | **advisory only** — it coaches; it can't change the score |
| Remediation | none | PR | **safe scaffolds + drafts**, local branch, never pushes |
| Where it runs | npm | upload your code | **local & open** — the save file is yours |

The split *is* the point: a pure-stdlib engine owns the deterministic score (identical in CI and on your
machine); the agent adds non-gating advisory — and is contractually forbidden from inflating it.

## Insert coin

The skills follow the [agentskills.io](https://agentskills.io) standard and carry the `agent-skills` topic:

```bash
gh skill install tjboudreaux/ready-agent-1          # GitHub CLI
npx skills add tjboudreaux/ready-agent-1            # skills.sh
gemini skills install tjboudreaux/ready-agent-1     # Gemini CLI
# or add the plugin in Claude Code
```

No runtime dependencies — **Python 3.11+** (an authenticated `gh` unlocks the GitHub-side checks).

## Play

```bash
ra1 report --project .                    # readiness scan (Level + cited checks)
ra1 report --project . --format markdown,json --out .agents/readiness --store-history
ra1 history list --project .              # local progression over past runs
ra1 fix --project . --latest              # dry-run: what the loadout would change
ra1 fix --project . --latest --apply      # write safe scaffolds to a local branch
```

(Or, through an agent: *"run a readiness report on this repo."*)

## The Gates

Levels **1 Functional → 2 Documented → 3 Standardized → 4 Optimized → 5 Autonomous**. A gate clears when
≥80% of its checks pass *and* every gate below it is cleared. Checks that don't apply to your project are
`skipped` (visibly, with a reason); when the project type can't be determined they're `unknown` rather than
waved through. *(Ready Agent 1 clears Gate 4 — Optimized — on its own repo.)*

## Clear-to-merge (CI)

```yaml
# .github/workflows/readiness.yml
jobs:
  readiness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: tjboudreaux/ready-agent-1/ci@v1
        with: { min-level: "3", formats: "markdown,junit,sarif,github" }
        env: { GH_TOKEN: "${{ github.token }}" }
```

SARIF → Security tab, JUnit → test UIs, Markdown → the step summary, and a non-zero exit below your minimum
level. Clear the gate to merge.

## Reference

- [Brand guide](BRAND.md) · [Getting started](docs/getting-started.md) · [CLI](docs/cli.md) · [Extending the checks](docs/extending.md) · [Contributing](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE). *Ready Agent 1* is an original product name that winks at a well-known
arcade-quest title; it uses no trademarked title text, characters, story elements, logo, key art, or
typography — only generic synthwave visual language and original copy.
