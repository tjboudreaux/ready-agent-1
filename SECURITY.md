# Security Policy

## Reporting a Vulnerability

Please report security issues privately — open a GitHub Security Advisory or email the maintainers.
Do **not** open a public issue for a vulnerability. We aim to acknowledge reports within 7 days.

## Supported Versions

The latest released version is supported with security fixes.

## Threat model: `ra1 report --exec`

By default `ra1` only **reads** the repository it scans (T0 static files, T1 `git`, T2 `gh`). No code
from the scanned repository is ever executed.

`--exec` opts in to T3 collection, which **executes commands from the scanned repository** —
`pytest`, `npm test`, `go test`, `cargo test`, and `devcontainer build`. Treat this as running
untrusted code.

The command *name* is allowlisted, but the code it runs is not:

- `pytest` imports the target repo's `conftest.py`.
- `npm test` runs whatever the target repo's `package.json` declares.
- `devcontainer build` invokes Docker and executes the target's Dockerfile.

### What the collector does and does not guarantee

Implemented (see `engine/readiness/collectors/exec.py`):

- **Opt-in.** Disabled unless `--exec` is passed; otherwise no subprocess is ever spawned.
- **Command allowlist.** Only exact detected commands map to fixed argv lists. Nothing goes through
  a shell, so there is no argument-injection path from repository contents.
- **Scrubbed environment.** A minimal env (`PATH`, neutral `HOME`, `LANG`, `CI`, `NO_COLOR`). No
  tokens, no `GH_TOKEN`, no inherited secrets.
- **Copied worktree.** `shutil.copytree` into a temp dir excluding `.git` and `.agents`; the command
  runs there, never in your tree.
- **Hard timeout.** Default 120s, `--exec-timeout` to change.

**Not** guaranteed:

- **No filesystem jail.** The copied worktree protects *your checkout* from modification. It is not a
  security boundary — executed code runs as your user and can read anything your user can read,
  including SSH keys and cloud credentials outside the copy.
- **No network isolation.** Executed code can make arbitrary outbound connections. The collector does
  not claim otherwise; isolation is the runner's responsibility.
- **No resource limits** beyond the wall-clock timeout.

### Guidance

- Only pass `--exec` for repositories you already trust enough to `git clone && npm install`.
- For untrusted or unattended scans (the agent-driven case), run `ra1 --exec` inside a container or
  VM with no network egress and no mounted credentials.
- T3-derived criteria are `gating: false` (advisory only), so omitting `--exec` never lowers a score
  below the level a trusted read-only scan would produce.

## Report contents

Reports may quote configuration paths and filenames from the scanned repository, so they are written
under `.agents/`, which is gitignored. If you export SARIF into GitHub code scanning, remember those
findings are visible to everyone with repository read access.
