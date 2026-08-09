# Security Policy

## Reporting a Vulnerability

Please report security issues privately — open a GitHub Security Advisory or email the maintainers.
Do **not** open a public issue for a vulnerability. We aim to acknowledge reports within 7 days.

## Supported Versions

The latest released version is supported with security fixes.

## Threat model

Ready Agent 1 is pure Python 3.11+ standard library. By default `ra1` only **reads** the
repository it scans: T0 static files, T1 Git, and — only when explicitly requested — T2
GitHub.com. No code from the scanned repository is ever executed unless `--exec` is passed.

### Platform support and the fail-closed startup probe

Operational commands (`detect`, `report`, `history`, `fix`, `gaps`, `answer`) require Linux or
macOS whose Python exposes the full POSIX directory-fd/no-follow capability set. Before any
repository access or subprocess, a cached startup probe verifies directory-handle traversal and
mutation primitives, no-follow stat/open behavior, and nonblocking directory locks. On Windows
or a deficient runtime the command fails closed with exactly:

```
ra1: safe_io_unsupported: required POSIX filesystem primitives are unavailable
```

Parser help, `version`, `formats`, and `banner` remain available everywhere. There is no weaker
fallback path and no generic cross-platform claim.

### Safe I/O authority (`engine/readiness/safe_io.py`)

- Every production repository read and write goes through retained directory-fd authorities: no
  pathname opens on untrusted paths, no `shutil`, root-confined one-component-at-a-time walks
  with `O_NOFOLLOW`, regular-file/single-link verification, and stable-identity checks.
- Reads and discovery are bounded (byte, depth, entry, match, and path-byte caps). A cap refusal
  or unsafe/unreadable/unstable input becomes blocking `unknown` evidence — never silent absence
  or partial credit.
- Write authority is split: remediation scaffolds are **create-only** (exclusive create, never
  overwrite; `.gitignore` is create-if-missing). Atomic replace is reserved for engine-owned
  generated artifacts under the report-persistence lock. If a scaffold target appears after the
  precheck, it is refused/skipped rather than overwritten.
- **Policy-merge race (disclosed).** `ra1 answer` merges `.ra1/config.json` /
  `.ra1/waivers.json` under an engine lock with pre-replace identity/size/content revalidation.
  Pure-stdlib POSIX replacement cannot close the final revalidation window against a
  non-cooperating same-user writer; a detected conflict refuses without retry or silent
  overwrite, but the residual race is a known limitation, not a transactional guarantee.
- Advisory file locks coordinate RA1 processes only; they do not stop a hostile same-user
  writer.

### Bounded process authority (`engine/readiness/process.py`)

All production subprocesses launch through one bounded runner: a closed set of tool ids resolved
once from the startup `PATH` (never from repository data), fixed argv with `shell=False`, no
stdin, bounded combined output (discard-and-kill past the cap), wall-clock timeouts, and
process-group kill. Repository content never selects the executable, options, environment, or
working directory. The runner does not claim a CPU, memory, filesystem, network, or container
sandbox for generic tools.

### Git authority

- Automatic Git runs only against a **sanitized immutable snapshot** built from either a primary
  checkout or a **standard reciprocal current-user linked worktree**. Linked-worktree admission
  is strictly reciprocal — the `.git` gitfile, `commondir`, physical
  `<common>/worktrees/<id>` shape, and the back-reference must all validate, with current-user
  ownership and non-group/world-writable modes — and grants only bounded reads from those two
  validated metadata roots before flattening. That read-only metadata admission is the sole
  data-nominated read beyond the selected repository root; submodule gitfiles, custom worktree
  configuration, and nonstandard topologies are refused with zero Git spawns.
- Snapshots reject `include`/`includeIf`, external `core.worktree`, alternates/http-alternates,
  promisor/partial-clone markers, and unsupported extensions. Git runs with **no helpers and no
  network**: minimal engine-authored environment, no prompts/askpass/SSH/proxy variables, and
  fixed argv disabling hooks, pagers, external diff, maintenance, and gc.
- **Resource caps.** Linux: a hard address-space cap plus CPU and core caps. macOS: CPU, core,
  wall-time, output, command-count, and snapshot caps, but **no hard address-space or resident
  memory cap** — live Darwin probes reject finite memory rlimits, so hard Git memory containment
  is deferred and disclosed, and a residual Git-driven OOM risk remains on macOS. Where a
  platform's required controls cannot be installed, automatic Git is unavailable rather than
  unbounded.

### GitHub (T2) collection

- T2 is **offline by default**; `--github` opts in, and `--host-proxy` is valid only together
  with `--github`. T2 is a **read-only network authority**: fixed `gh api` endpoints on
  **GitHub.com only** (no GHES), with bounded request, page, and byte caps.
- Credentials come only from a bounded startup `GH_TOKEN`/`GITHUB_TOKEN`, or a safely copied
  external `gh` config (validated ownership/modes, allowlisted regular files, engine-owned
  private temporary copy). **Repository-local config is never used.** The child environment
  excludes credential-broker IPC (DBus/keyring) and custom-CA variables; **TLS uses the host OS
  trust store only** — a config that needs broker IPC or an env-selected CA yields incomplete
  T2, not a fallback. Token values, config contents, and proxy values never appear in output or
  provenance.
- An unavailable or unreadable T2 observation maps to `unavailable`/`unknown` — never to
  absence or passing evidence.

### Threat model: `ra1 report --exec` (T3)

`--exec` opts in to T3 collection, which **executes commands from the scanned repository** —
`pytest`, `npm test`, `go test`, `cargo test`, and `devcontainer build`. Treat this as running
untrusted code.

The command *name* is allowlisted, but the code it runs is not:

- `pytest` imports the target repo's `conftest.py`.
- `npm test` runs whatever the target repo's `package.json` declares.
- `devcontainer build` invokes Docker and executes the target's Dockerfile.

#### What the collector does and does not guarantee

Implemented (see `engine/readiness/collectors/exec.py`):

- **Opt-in.** Disabled unless `--exec` is passed; otherwise no project subprocess is ever spawned.
- **Command allowlist.** Only exact detected commands map to fixed argv lists. Nothing goes through
  a shell, so there is no argument-injection path from repository contents.
- **Scrubbed environment.** A minimal env. No tokens, no `GH_TOKEN`, no inherited secrets.
- **Isolated copy.** A bounded safe copy into a temp dir (excluding `.git`, legacy `.agents`, and
  `.ra1/reports`); the command runs there, never in your tree.
- **Bounded run.** Hard timeout (default 120s, `--exec-timeout` to change, max 3600s), output cap,
  and process-group kill. **A descendant that deliberately creates a new session or daemonizes
  escapes the process-group guarantee** — that is outside the stdlib boundary and is disclosed
  rather than hidden.

**Not** guaranteed:

- **No kernel sandbox.** The isolated copy protects *your checkout* from modification. It is not
  filesystem, network, or container isolation — executed code runs as your user and can read
  anything your user can read, including SSH keys and cloud credentials outside the copy.
- **No network isolation.** Executed code can make arbitrary outbound connections. The collector
  does not claim otherwise; isolation is the runner's responsibility.

#### Guidance

- Only pass `--exec` for repositories you already trust enough to `git clone && npm install`.
- For untrusted or unattended scans (the agent-driven case), run `ra1 --exec` inside a container or
  VM with no network egress and no mounted credentials.
- T3-derived criteria are `gating: false` (advisory only), so omitting `--exec` never lowers a score
  below the level a trusted read-only scan would produce.

## Report contents, privacy, and provenance

- Reports may cite repository-relative paths and bounded evidence from the scanned repository.
  Generated reports, `latest.json`, and history live only under `.ra1/reports/`, which must be
  gitignored — the engine proves that ignore boundary (and that `.ra1/config.json` /
  `.ra1/waivers.json` stay unignored) before persisting in a repository. Team-owned policy
  inputs under `.ra1/` are never generated output. If you export SARIF into GitHub code
  scanning, remember those findings are visible to everyone with repository read access.
- Schema-3 output is allowlist-first: `project_path` is never serialized; origin credentials,
  query/fragment, raw policy/command lines, waiver free text, and owner tokens are never copied;
  canonical sensitive-value patterns are redacted from public fields. **Redaction is defense in
  depth, not a secret-classification or DLP guarantee.**
- `identity_hash` is an **unsigned local-history comparison key, not anonymization** —
  predictable repository identities remain guessable/correlatable, so human renderers omit it.
- `assessment_provenance` is engine-recorded **unsigned** metadata that borrows SLSA/in-toto
  field structure only. It is **not** an attestation and makes no authenticity or integrity
  claim; this release adds no signing, OIDC identity, or verification path.

## Untrusted content and agent skills

- All repository, report, label, evidence, and rationale content is **untrusted data**: it is
  rendered only through context encoders, and artifact content never expands a root, authorizes
  a write/command/network request, or selects a collector mode. Malicious instructions embedded
  in evidence are data, not commands.
- The three skills (`ra1-report`, `ra1-fix`, `ra1-interview`) declare only `Bash` and invoke
  fixed vendored-CLI commands, but **generic Bash is host authority** — skill frontmatter and
  prose cannot technically enforce the command grammar or per-use mutation grants. Least
  authority is instruction- and eval-level only; operators needing enforced capabilities must
  withhold automatic Bash approval.
- Text-only evals do not prove prompt-injection immunity, and RA1 claims **no sandbox and no
  prompt immunity**. Runtime isolation, egress control, credential scope, human approval,
  prompt-injection handling, observability, cost/concurrency enforcement, and organizational
  outcomes are explicitly `not_assessed` in every report's assessment boundary.
