"""Build System checks."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from statistics import median

from ._helpers import (
    acdc_config, adep, aglob, check_needles, ev, failed, parse_iso, passed, skipped,
    tool_invoked, unknown,
)


def deps_pinned(ctx):
    locks = ctx.app_static().lockfiles()
    if not locks and ctx.app.path != ".":
        locks = ctx.static.lockfiles()
    if locks:
        return passed(f"Lockfile present: {locks[0]}", [ev("lockfile", source=locks[0])])
    declared = ctx.app_static().declared_deps()
    thirdparty = {d for d in declared if not d.startswith("tool:")}
    if not thirdparty:
        return passed("No third-party dependencies to pin.")
    return failed("Dependencies declared but no lockfile present.")


def vcs_cli(ctx):
    if ctx.github.available:
        return passed("gh CLI authenticated for this repo.", [ev("gh repo view succeeds", tier="T2")])
    if ctx.git.available():
        return passed("git available (repository initialized).", [ev("git work tree", tier="T1")])
    return failed("Neither git nor authenticated gh available.")


def agentic_development(ctx):
    if not ctx.git.available():
        return unknown("No git history available.")
    if ctx.git.has_agent_coauthorship():
        return passed("Agent co-authorship present in git history.", [ev("co-author trailer", tier="T1")])
    return failed("No agent co-authorship in recent commits.")


def ci_present(ctx):
    files = ctx.static.glob([".github/workflows/*.yml", ".github/workflows/*.yaml",
                             ".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile",
                             ".travis.yml", "azure-pipelines.yml", ".drone.yml"])
    if files:
        return passed(f"CI configuration: {files[0]}", [ev("CI config", source=files[0])])
    if ctx.github.available and ctx.github.workflows():
        return passed("GitHub Actions workflows present.", [ev("workflows via API", tier="T2")])
    return failed("No CI configuration found.")


def release_automation(ctx):
    files = ctx.static.glob([".releaserc*", "release.config.*", ".goreleaser.yml", ".goreleaser.yaml",
                             ".github/workflows/release*.yml", ".github/workflows/release*.yaml",
                             ".changeset/config.json"])
    if files:
        return passed(f"Release automation: {files[0]}", [ev("release config", source=files[0])])
    dep = adep(ctx, ["semantic-release", "release-please", "@changesets/cli", "standard-version"])
    if dep:
        return passed(f"Release automation dependency: {dep}")
    return failed("No release automation (semantic-release/changesets/goreleaser).")


def ci_runs_tests(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot confirm CI runs tests.")
    if not ctx.github.workflows():
        return failed("No CI workflows.")
    has_tests = bool(ctx.static.glob(["**/*test*.*", "**/*_test.*", "**/*.spec.*",
                                      "test/**", "tests/**", "spec/**"]))
    runs = ctx.github.recent_runs()
    if has_tests and runs:
        return passed(
            f"CI active ({len(runs)} recent runs) with a test suite present.",
            [ev("CI runs + tests (inferred from CI activity, not step parsing)", tier="T2")],
        )
    if not has_tests:
        return failed("CI present but no test suite detected.")
    return failed("CI workflows present but no recent runs.")


def _has_build_command_section(text):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^#+\s+.*build", ln, re.I):
            if "```" in "\n".join(lines[i + 1:i + 9]):
                return True
    return False


def build_command_documented(ctx):
    """Pass on an explicit build command in package config or docs. Makefile targets are not
    inferred in v1 (they are too ambiguous to credit deterministically)."""
    pkg = ctx.app_static().manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and isinstance(pkg.get("scripts"), dict) and pkg["scripts"].get("build"):
        return passed("Build command declared in package.json scripts.build.",
                      [ev("scripts.build", source="package.json")])
    for doc in ("README.md", "AGENTS.md", "docs/README.md", "CONTRIBUTING.md"):
        text = ctx.static.read(doc)
        if text and _has_build_command_section(text):
            return passed(f"Build command documented under a Build section in {doc}.",
                          [ev("documented build command", source=doc)])
    return failed("No explicit build command in package config or docs (Makefile targets not inferred).")


def _target_recipe(text, name):
    match = re.search(r"(?m)^" + re.escape(name) + r"\s*:(.*)$", text)
    if not match:
        return None, ""
    lines = text[match.end():].splitlines()
    recipe = []
    for line in lines:
        if line and not line[0].isspace():
            break
        recipe.append(line)
    return match, "\n".join(recipe)


def _configured_verify_command(ctx, command):
    tokens = command.split()
    if len(tokens) >= 2 and tokens[0] in {"make", "just"}:
        patterns = ["Makefile"] if tokens[0] == "make" else ["Justfile", "justfile"]
        for path in aglob(ctx, patterns):
            if re.search(r"(?m)^" + re.escape(tokens[1]) + r"\s*:", ctx.app_static().read(path) or ctx.static.read(path) or ""):
                return path
        return ""
    if len(tokens) >= 2 and tokens[0] == "task":
        for path in aglob(ctx, ["Taskfile.yml", "Taskfile.yaml"]):
            if re.search(r"(?m)^\s*" + re.escape(tokens[1]) + r"\s*:", ctx.app_static().read(path) or ctx.static.read(path) or ""):
                return path
        return ""
    script = ""
    if len(tokens) >= 3 and tokens[:2] in (["npm", "run"], ["pnpm", "run"]):
        script = tokens[2]
    elif len(tokens) >= 2 and tokens[0] in {"yarn", "pnpm"}:
        script = tokens[1]
    if script:
        pkg = ctx.app_static().manifests().get("package.json", (None, None))[1]
        scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
        return "package.json" if isinstance(scripts, dict) and script in scripts else ""
    first = tokens[0]
    if first.startswith("scripts/") or first.startswith("./scripts/"):
        rel = first[2:] if first.startswith("./") else first
        return rel if aglob(ctx, [rel]) else ""
    if check_needles(command) or command.startswith("python -m") or command.startswith("python3 -m"):
        return "."
    return ""


def _script_check_text(value, scripts):
    text = str(value)
    siblings = []
    for match in re.finditer(r"\b(?:npm run|yarn|pnpm run)\s+([\w:.-]+)", text):
        siblings.append(match.group(1))
    for match in re.finditer(r"\b(?:run-s|run-p)\s+([^&|;\n]+)", text):
        siblings.extend(token for token in match.group(1).split() if not token.startswith("-"))
    return "\n".join([text] + [str(scripts[name]) for name in siblings if name in scripts])


def check_command(ctx):
    """Detect one named inner-loop entrypoint that chains deterministic checks."""
    configured = acdc_config(ctx).get("verify_command")
    if isinstance(configured, str) and configured.strip():
        command = configured.strip()
        source = _configured_verify_command(ctx, command)
        if source:
            evidence = [ev("acdc.verify_command", source=".agents/readiness/config.json")]
            if source != ".":
                evidence.append(ev("verify command", source=source))
            return passed(f"Verify entrypoint designated in readiness config: '{command}'.", evidence)
        return failed(
            f"Config declares verify_command '{command}' but it does not resolve to an existing target/script.",
            [ev("acdc.verify_command", source=".agents/readiness/config.json")],
        )

    pkg = ctx.app_static().manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and isinstance(pkg.get("scripts"), dict):
        scripts = pkg["scripts"]
        for name in ("check", "verify", "validate"):
            if name in scripts:
                count = len(check_needles(_script_check_text(scripts[name], scripts)))
                if count >= 2:
                    return passed(
                        f"Single verify entrypoint '{name}' chains {count} check tools.",
                        [ev("verify command", source="package.json")],
                    )
                return failed(
                    f"'{name}' exists but chains only {count} recognized check tool(s); a verify entrypoint should run lint/type/test together."
                )

    for path in aglob(ctx, ["Makefile", "Justfile", "justfile"]):
        text = ctx.app_static().read(path) or ctx.static.read(path) or ""
        for name in ("check", "verify", "validate"):
            match, recipe = _target_recipe(text, name)
            if not match:
                continue
            prerequisites = []
            for token in match.group(1).split():
                if token != name and "=" not in token and ":=" not in token:
                    prerequisites.append(token)
            resolved = [recipe]
            for prerequisite in prerequisites:
                _, body = _target_recipe(text, prerequisite)
                if body:
                    resolved.append(body)
            count = len(check_needles("\n".join(resolved)))
            if count >= 2:
                return passed(
                    f"Single verify entrypoint '{name}' chains {count} check tools.",
                    [ev("verify command", source=path)],
                )
            return failed(
                f"'{name}' exists but chains only {count} recognized check tool(s); a verify entrypoint should run lint/type/test together."
            )

    for path in aglob(ctx, ["Taskfile.yml", "Taskfile.yaml"]):
        text = ctx.app_static().read(path) or ctx.static.read(path) or ""
        match = re.search(r"(?m)^(\s*)(check|verify|validate)\s*:", text)
        if match:
            indent = len(match.group(1))
            body = []
            for line in text[match.end():].splitlines():
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                body.append(line)
            name = match.group(2)
            count = len(check_needles("\n".join(body)))
            if count >= 2:
                return passed(
                    f"Single verify entrypoint '{name}' chains {count} check tools.",
                    [ev("verify command", source=path)],
                )
            return failed(
                f"'{name}' exists but chains only {count} recognized check tool(s); a verify entrypoint should run lint/type/test together."
            )

    for path in aglob(ctx, ["scripts/check*", "scripts/verify*"]):
        text = ctx.app_static().read(path) or ctx.static.read(path) or ""
        name = path.rsplit("/", 1)[-1]
        count = len(check_needles(text))
        if count >= 2:
            return passed(
                f"Single verify entrypoint '{name}' chains {count} check tools.",
                [ev("verify command", source=path)],
            )
        return failed(
            f"'{name}' exists but chains only {count} recognized check tool(s); a verify entrypoint should run lint/type/test together."
        )

    return failed(
        "No single check/verify command; agents need one fast inner-loop verification entrypoint (e.g. 'make check' running lint + typecheck + tests). Designate one via acdc.verify_command in .agents/readiness/config.json if yours is unconventional."
    )


def _ci_budget_minutes(ctx):
    from ..detect import load_readiness_config
    v = load_readiness_config(ctx.root, ctx.options).get("ci_budget_minutes")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def _run_minutes(run):
    start = run.get("run_started_at") or run.get("created_at")
    end = run.get("updated_at")
    if not (start and end):
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return max((e - s).total_seconds() / 60.0, 0.0)


def ci_duration_budget(ctx):
    """Pass when recent CI runs stay within a configured per-run minute budget.

    Needs both GitHub run timing (T2) and an explicit ``ci_budget_minutes`` in the readiness
    config; without a budget the criterion is ``unknown`` rather than guessed."""
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read CI run durations.")
    budget = _ci_budget_minutes(ctx)
    if budget is None:
        return unknown("No ci_budget_minutes in .agents/readiness/config.json; cannot judge duration.")
    durations = [d for d in (_run_minutes(r) for r in ctx.github.recent_runs()) if d is not None]
    if not durations:
        return unknown("No timed CI runs available to assess duration.")
    worst = max(durations)
    if worst <= budget:
        return passed(f"Recent CI runs within the {budget}m budget (worst {worst:.1f}m).",
                      [ev("CI run durations", tier="T2")])
    return failed(f"Recent CI runs exceed the {budget}m budget (worst {worst:.1f}m).")


# --- Factory-parity build hygiene (advisory; T0) ------------------------------------

_UNUSED_DEP_TOOLS = ["depcheck", "knip", "deptry", "cargo-udeps", "npm-check", "ts-prune"]
_UNUSED_DEP_CFG = [".depcheckrc*", "depcheck.json", "knip.json", ".knip.json", "deptry.toml"]


def unused_dependencies(ctx):
    cfg = aglob(ctx, _UNUSED_DEP_CFG)
    tool = adep(ctx, _UNUSED_DEP_TOOLS) or (cfg[0] if cfg else None)
    if not tool:
        return failed("No unused-dependency scanner (depcheck/knip/deptry).")
    wiring = tool_invoked(ctx, _UNUSED_DEP_TOOLS)
    if wiring:
        return passed(f"Unused-dependency scan wired: {tool}.",
                      [ev("unused-dep tool", source=str(tool)), ev("invocation", source=wiring)])
    return failed(f"Unused-dependency scanner present ({tool}) but not wired into CI/scripts.")


_DRIFT_TOOLS = ["syncpack", "@manypkg/cli", "manypkg"]
_DRIFT_CFG = [".syncpackrc*", "syncpack.config.*", ".manypkgrc"]


def version_drift(ctx):
    if adep(ctx, _DRIFT_TOOLS) or aglob(ctx, _DRIFT_CFG):
        return passed("Dependency version-drift control configured (syncpack/manypkg).", [ev("version-drift tool")])
    if "catalog:" in (ctx.static.read("pnpm-workspace.yaml") or ""):
        return passed("Versions centralized via pnpm catalog.", [ev("pnpm catalog", source="pnpm-workspace.yaml")])
    return failed("No version-drift control (syncpack/manypkg/pnpm catalog).")


_MONO_CFG = ["turbo.json", "nx.json", "lerna.json", "rush.json", "pnpm-workspace.yaml",
             "WORKSPACE", "WORKSPACE.bazel", "pants.toml"]
_MONO_DEPS = ["turbo", "nx", "lerna", "@nrwl/workspace", "@microsoft/rush"]


def monorepo_tooling(ctx):
    cfg = ctx.static.glob(_MONO_CFG)
    if cfg:
        return passed(f"Monorepo tooling configured: {cfg[0]}", [ev("monorepo tool", source=cfg[0])])
    if adep(ctx, _MONO_DEPS):
        return passed("Monorepo task runner declared (turbo/nx/lerna).", [ev("monorepo dep")])
    pkg = ctx.static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and pkg.get("workspaces"):
        return passed("npm/yarn workspaces configured.", [ev("workspaces field", source="package.json")])
    return failed("No monorepo tooling (turbo/nx/lerna/rush/bazel/workspaces).")


_SETUP_FILES = ["bin/setup", "script/bootstrap", "scripts/setup*", "scripts/bootstrap*", "setup.sh"]
_SETUP_TARGET_FILES = ("Makefile", "Taskfile.yml", "Taskfile.yaml", "justfile", "Justfile")


def single_command_setup(ctx):
    if aglob(ctx, _SETUP_FILES):
        return passed("One-command setup script present.", [ev("setup script")])
    for f in _SETUP_TARGET_FILES:
        low = (ctx.app_static().read(f) or ctx.static.read(f) or "").lower()
        if "setup:" in low or "bootstrap:" in low:
            return passed(f"Setup target in {f}.", [ev("setup target", source=f)])
    for f in ctx.static.glob([".devcontainer/devcontainer.json", ".devcontainer.json"]):
        if "postcreatecommand" in (ctx.static.read(f) or "").lower():
            return passed("Devcontainer postCreateCommand bootstraps the env.", [ev("postCreateCommand", source=f)])
    pkg = ctx.app_static().manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and any(k in (pkg.get("scripts") or {}) for k in ("setup", "bootstrap")):
        return passed("package.json setup/bootstrap script present.", [ev("setup script", source="package.json")])
    return failed("No single-command setup (bin/setup, make setup, devcontainer postCreateCommand, npm setup).")


_RELNOTES_CFG = [".releaserc*", "release.config.*", ".changeset/config.json", "cliff.toml",
                 ".github/release.yml", "release-please-config.json", ".release-please-manifest.json",
                 "towncrier.toml"]
_RELNOTES_DEPS = ["semantic-release", "@changesets/cli", "changesets", "release-please",
                  "standard-version", "git-cliff", "towncrier"]


def release_notes_automation(ctx):
    cfg = ctx.static.glob(_RELNOTES_CFG)
    if cfg:
        return passed(f"Release-notes automation configured: {cfg[0]}", [ev("release notes", source=cfg[0])])
    if adep(ctx, _RELNOTES_DEPS):
        return passed("Release-notes tool declared (semantic-release/changesets/git-cliff).", [ev("release-notes dep")])
    if "towncrier" in (ctx.static.read("pyproject.toml") or "").lower():
        return passed("Towncrier changelog configured.", [ev("towncrier", source="pyproject.toml")])
    return failed("No release-notes automation (semantic-release/changesets/release-please/git-cliff/towncrier).")


_WEIGHT_DEPS = ["size-limit", "@size-limit/preset-app", "bundlesize", "bundlewatch",
                "@next/bundle-analyzer", "webpack-bundle-analyzer", "rollup-plugin-visualizer"]
_WEIGHT_CFG = [".size-limit.json", ".size-limit.js", ".bundlewatch.config.json"]


def dependency_weight_budget(ctx):
    pkg = ctx.app_static().manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and (pkg.get("size-limit") or pkg.get("bundlesize") or pkg.get("bundlewatch")):
        return passed("Bundle-size budget configured in package.json.", [ev("size budget", source="package.json")])
    if aglob(ctx, _WEIGHT_CFG):
        return passed("Bundle-size budget config present.", [ev("size budget config")])
    if adep(ctx, _WEIGHT_DEPS) and tool_invoked(ctx, _WEIGHT_DEPS):
        return passed("Bundle analyzer/size tool wired.", [ev("bundle tool wired")])
    return failed("No dependency-weight budget (size-limit/bundlesize/bundle-analyzer with a budget).")


# --- DORA / AI-capability build proxies (advisory) ------------------------------------


def small_batches(ctx):
    """Pass when median LOC churn across recent non-merge commits is ≤ 400.

    RA1 LOC heuristic — squash-merge workflows may inflate per-commit churn.
    """
    if not ctx.git.available():
        return unknown("No git history available.")
    churn = ctx.git.recent_churn(50)
    if not churn:
        return unknown("No git history available.")
    if len(churn) < 10:
        return skipped("insufficient history (<10 non-merge commits)")
    med = median(churn)
    evidence = [ev(f"median churn {med} LOC (n={len(churn)})", tier="T1")]
    if med <= 400:
        return passed(f"Median commit churn {med} ≤ 400 LOC (n={len(churn)}).", evidence)
    return failed(
        f"Median commit churn {med} > 400 LOC (n={len(churn)}); "
        "RA1 LOC heuristic — squash-merge may inflate churn.",
        evidence,
    )


def integration_frequency(ctx):
    """Pass when commits land in ≥4 distinct ISO weeks of the trailing 8 weeks
    anchored at the most recent commit (activity-anchored)."""
    if not ctx.git.available():
        return unknown("No git history available.")
    dates = ctx.git.commit_dates(200)
    if not dates:
        return unknown("No git history available.")
    anchor = parse_iso(dates[0])
    if not anchor:
        return unknown("Could not parse recent commit dates.")
    now = datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if (now - anchor).days > 90:
        return skipped("inactive repository")
    window_start = anchor - timedelta(weeks=8)
    weeks = set()
    for d in dates:
        dt = parse_iso(d)
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if window_start <= dt <= anchor:
            weeks.add(dt.isocalendar()[:2])
    evidence = [ev(f"{len(weeks)} active ISO weeks in trailing 8 (n={len(dates)} dates)", tier="T1")]
    if len(weeks) >= 4:
        return passed(f"Commits in {len(weeks)} distinct ISO weeks of the trailing 8.", evidence)
    return failed(
        f"Only {len(weeks)} distinct ISO week(s) with commits in the trailing 8 weeks (need ≥4).",
        evidence,
    )


def agent_config_versioned(ctx):
    """Pass when at least one agent-config path has ≥2 commits of history."""
    if not ctx.git.available():
        return unknown("No git history available.")
    candidates = ["AGENTS.md", "CLAUDE.md", ".claude", ".cursor", "skills", ".agents"]
    existing = [p for p in candidates if (ctx.root / p).exists()]
    if not existing:
        return skipped("no agent configuration present")
    for path in existing:
        count = ctx.git.commit_count_for(path)
        if count >= 2:
            return passed(
                f"Agent configuration versioned: {path} has {count} commits.",
                [ev("agent config history", source=path, tier="T1")],
            )
    return failed(
        "Agent configuration present but none have ≥2 commits of history "
        f"(checked: {', '.join(existing)})."
    )
