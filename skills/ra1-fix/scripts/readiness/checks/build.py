"""Build System checks."""
from __future__ import annotations

import re
from datetime import datetime

from ._helpers import adep, aglob, ev, failed, passed, skipped, tool_invoked, unknown


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


def _ci_budget_minutes(ctx):
    from ..detect import load_readiness_config
    v = load_readiness_config(ctx.root).get("ci_budget_minutes")
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
