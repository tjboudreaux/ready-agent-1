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
