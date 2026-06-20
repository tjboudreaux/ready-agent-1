"""Coverage gate: total fail-under PLUS 100% branch coverage for changed files.

The roadmap (``docs/PLAN-roadmap-factory-parity.md``, invariant 6) requires every new or
changed Python module to reach 100% branch coverage or carry reviewed ``# pragma: no cover``
exclusions. This gate enforces that contract against a ``coverage json`` report:

- total branch coverage must meet a fail-under threshold;
- each ``--changed-files`` Python file must have zero missing lines and zero missing
  branches in the coverage report (reviewed ``# pragma: no cover`` lines are already
  excluded by coverage and therefore never count against a file);
- a changed Python file absent from the coverage report is a hard failure naming the
  file, unless it is declared a logic-free entrypoint via ``--thin-wrapper``.

All logic lives here so it is unit-tested; ``scripts/coverage_gate.py`` is only a thin
entrypoint that imports :func:`main`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def load_coverage(path):
    """Load a ``coverage json`` report from ``path``."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _norm(path):
    return os.path.normpath(path).replace(os.sep, "/")


def match_file(files, changed):
    """Return the coverage-report key for ``changed``, or ``None`` if it is absent.

    Matches on the normalized path first, then on a path-suffix so a report keyed by an
    absolute or rooted path still matches a repo-relative changed path.
    """
    target = _norm(changed)
    normalized = {_norm(k): k for k in files}
    if target in normalized:
        return normalized[target]
    for nk, original in normalized.items():
        if nk.endswith("/" + target):
            return original
    return None


def file_coverage_ok(summary):
    """A file is fully covered when it has no missing lines and no missing branches."""
    return summary.get("missing_lines", 0) == 0 and summary.get("missing_branches", 0) == 0


def check_total(coverage, fail_under):
    """Return violations if total coverage is under ``fail_under`` percent."""
    pct = coverage.get("totals", {}).get("percent_covered", 0.0)
    if pct + 1e-9 < fail_under:
        return [f"coverage gate: total coverage {pct:.2f}% < fail-under {fail_under:.2f}%"]
    return []


def check_changed_files(coverage, changed_files, thin_wrappers=()):
    """Return violations for any changed ``*.py`` file that is not fully covered.

    A changed file missing from the report is a hard failure unless it is listed in
    ``thin_wrappers`` (a logic-free entrypoint with all behavior delegated elsewhere).
    """
    files = coverage.get("files", {})
    thin = {_norm(t) for t in thin_wrappers}
    violations = []
    for cf in changed_files:
        if not cf.endswith(".py"):
            continue
        key = match_file(files, cf)
        if key is None:
            if _norm(cf) in thin:
                continue
            violations.append(f"coverage gate: changed file not measured by coverage: {cf}")
            continue
        summary = files[key].get("summary", {})
        if not file_coverage_ok(summary):
            violations.append(
                f"coverage gate: {cf} is not fully covered "
                f"(missing_lines={summary.get('missing_lines', 0)}, "
                f"missing_branches={summary.get('missing_branches', 0)})"
            )
    return violations


def gate(coverage, changed_files, fail_under=90.0, thin_wrappers=()):
    """Run both the total fail-under and the per-file 100% checks; return all violations."""
    return check_total(coverage, fail_under) + check_changed_files(
        coverage, changed_files, thin_wrappers
    )


def _build_parser():
    p = argparse.ArgumentParser(
        prog="coverage_gate",
        description="Enforce total fail-under and 100% branch coverage for changed files.",
    )
    p.add_argument("--coverage", required=True,
                   help="Path to a coverage json report (coverage json -o ...)")
    p.add_argument("--changed-files", nargs="*", default=[],
                   help="Python files that must have 100% branch coverage")
    p.add_argument("--fail-under", type=float, default=90.0,
                   help="Minimum total branch coverage percent (default 90)")
    p.add_argument("--thin-wrapper", nargs="*", default=[],
                   help="Changed files allowed to be absent from the report (logic-free entrypoints)")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    coverage = load_coverage(args.coverage)
    violations = gate(coverage, args.changed_files, args.fail_under, args.thin_wrapper)
    for v in violations:
        sys.stderr.write(v + "\n")
    if violations:
        return 1
    sys.stdout.write("coverage gate: OK\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin entrypoint
    raise SystemExit(main())
