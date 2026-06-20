"""Build System checks."""
from __future__ import annotations

import re
from datetime import datetime

from ._helpers import adep, ev, failed, passed, skipped, unknown


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
