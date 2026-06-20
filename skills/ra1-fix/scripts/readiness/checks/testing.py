"""Testing checks."""
from __future__ import annotations

from ._helpers import adep, aglob, ev, failed, passed, skipped

_UNIT_PATTERNS = [
    "**/*_test.go", "**/test_*.py", "**/*_test.py", "**/*.test.ts", "**/*.test.js",
    "**/*.test.tsx", "**/*.spec.ts", "**/*.spec.js", "**/*Test.java", "**/*Tests.cs",
    "**/*_test.rb", "**/*_spec.rb",
]


def unit_tests_exist(ctx):
    hits = aglob(ctx, _UNIT_PATTERNS + ["tests/**/*.py", "test/**/*.py", "spec/**"])
    if hits:
        return passed(f"{len(hits)} test file(s) found.", [ev(f"e.g. {hits[0]}", source=hits[0])])
    return failed("No unit test files found.")


def integration_tests_exist(ctx):
    hits = aglob(ctx, ["**/integration/**", "**/e2e/**", "**/*e2e*.*", "**/*integration*.*",
                       "cypress.config.*", "playwright.config.*", "tests/integration/**"])
    if hits:
        return passed("Integration/e2e tests present.", [ev(f"{hits[0]}", source=hits[0])])
    dep = adep(ctx, ["playwright", "@playwright/test", "cypress", "supertest", "testcontainers"])
    if dep:
        return passed(f"Integration test framework: {dep}")
    return failed("No integration/e2e tests found.")


def test_naming(ctx):
    standard = aglob(ctx, _UNIT_PATTERNS)
    if standard:
        return passed(f"Tests follow standard naming ({len(standard)} files).", [ev(f"{standard[0]}", source=standard[0])])
    any_tests = aglob(ctx, ["tests/**", "test/**", "spec/**"])
    if any_tests:
        return failed("Test directories exist but files don't match a standard naming convention.")
    return skipped("No tests present to assess naming.")


def tests_pass(ctx):
    """T3 (advisory): the detected test command succeeds on an isolated copy of the repo.

    Skips unless the user opted in (``--exec``); CI status from T2 substitutes by default.
    """
    ex = ctx.exec
    if ex is None or not ex.enabled:
        return skipped("T3 execution disabled (opt in with --exec); CI status (T2) substitutes.")
    cmd = ctx.app.test_cmd
    if not cmd:
        return skipped("No detected test command to execute.")
    res = ex.run_test_cmd(cmd, ctx.app.path)
    if res is None:  # pragma: no cover - unreachable: enabled is checked above before this call
        return skipped("T3 execution unavailable.")
    if not res["allowed"]:
        return skipped(f"'{cmd}' is not on the T3 allowlist; not executed.")
    if res["timed_out"]:
        return failed(f"'{cmd}' timed out after {ex.timeout}s on an isolated copy.")
    if res["returncode"] == 0:
        return passed(f"'{cmd}' succeeded on an isolated copy.",
                      [ev(f"T3 sandboxed run: {' '.join(res['argv'])}", tier="T3")])
    return failed(f"'{cmd}' exited {res['returncode']} on an isolated copy.")


def _coverage_config(ctx):
    for f in (".coveragerc", "codecov.yml", ".codecov.yml"):
        if ctx.static.glob([f]):
            return f
    if ctx.static.has_tool_config("coverage"):
        return "pyproject.toml [tool.coverage]"
    pkg = ctx.static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and isinstance(pkg.get("jest"), dict) and pkg["jest"].get("coverageThreshold"):
        return "package.json jest.coverageThreshold"
    return ""


_ENFORCE_TOKENS = ("fail-under", "cov-fail-under", "coveragethreshold", "codecov")


def _coverage_enforced_in_ci(ctx):
    for wf in ctx.static.glob([".github/workflows/*.yml", ".github/workflows/*.yaml"]):
        low = (ctx.static.read(wf) or "").lower()
        if any(tok in low for tok in _ENFORCE_TOKENS):
            return wf
    return ""


def coverage_threshold(ctx):
    """Pass only when a coverage threshold is BOTH configured AND enforced in CI; config without
    CI enforcement is a fail (it cannot block a regression)."""
    cfg = _coverage_config(ctx)
    if not cfg:
        return failed("No coverage threshold configured (coverage config / jest coverageThreshold / codecov).")
    enforced = _coverage_enforced_in_ci(ctx)
    if not enforced:
        return failed(f"Coverage configured ({cfg}) but not enforced in CI.")
    return passed(f"Coverage configured ({cfg}) and enforced in CI ({enforced}).",
                  [ev("coverage config", source=cfg), ev("CI enforcement", source=enforced)])


def flake_quarantine(ctx):
    """Pass on a documented flaky-test quarantine policy. A bare retry dependency is not enough —
    blind reruns hide flakiness rather than tracking it."""
    for doc in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/testing.md", "docs/flaky.md",
                "docs/flaky-tests.md"):
        low = (ctx.static.read(doc) or "").lower()
        if "quarantine" in low and ("flaky" in low or "flake" in low):
            return passed(f"Documented flaky-test quarantine policy in {doc}.",
                          [ev("flake quarantine policy", source=doc)])
    return failed("No documented flaky-test quarantine policy (blind retries do not count).")
