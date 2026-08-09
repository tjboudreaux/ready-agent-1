"""Automatic touched-file coverage gate (§13).

Reads the attested ``selection_base_commit`` from ``release/versions.json`` and uses the
canonical safe root/toolchain/Git authority to obtain one NUL-delimited union of tracked
changes from that base through the current worktree plus untracked non-ignored files.
Classifies every touched path (canonical Python, test, vendored mirror, or non-Python) and
requires **100% line AND branch coverage data** for every touched non-test/non-vendored
Python file (including release/vendor scripts and ``evals/coverage_gate.py`` itself).

Fails closed on a malformed matrix/base, ancestry, Git, duplicate, path, or coverage-JSON
state. Output: a deterministic ``.coverage-touched.json`` with one entry per touched path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "engine") not in sys.path:  # pragma: no cover - engine importable
    sys.path.insert(0, str(REPO / "engine"))

_GLOBALS = ("engine/", "evals/", "scripts/")
_TEST_PREFIXES = ("tests/",)


def classify(path: str) -> str:
    """canonical-python | test | vendored-mirror | non-python."""
    if path.startswith("skills/"):
        if path.endswith(".py") and "/scripts/readiness/" in path:
            return "vendored-mirror"
        return "vendored"
    if not path.endswith(".py"):
        return "non-python"
    if path.startswith(_TEST_PREFIXES):
        return "test"
    if path.startswith(_GLOBALS):
        return "canonical-python"
    return "canonical-python"  # unexpected new Python at repo root: still must be covered


def _git(args, *, cwd) -> str:
    import subprocess
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr[:200]}")
    return result.stdout


def touched_paths(repo: Path, matrix_path: Path | None = None) -> list[str]:
    import json as _json
    matrix_path = matrix_path or repo / "release" / "versions.json"
    matrix = _json.loads(matrix_path.read_text(encoding="utf-8"))
    base = matrix["publication_source"]["selection_base_commit"]
    try:
        _git(["cat-file", "-e", base + "^{commit}"], cwd=repo)
        _git(["merge-base", "--is-ancestor", base, "HEAD"], cwd=repo)
    except RuntimeError as exc:
        raise RuntimeError(f"selection base not usable: {exc}") from exc
    tracked = _git(["diff", "--name-only", "-z", base, "HEAD"], cwd=repo)
    # Modified-but-uncommitted worktree/index state must not evade the gate:
    # the release is verified against the working tree, not just committed history.
    unstaged = _git(["diff", "--name-only", "-z"], cwd=repo)
    staged = _git(["diff", "--cached", "--name-only", "-z"], cwd=repo)
    untracked = _git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=repo)
    raw = (tracked + "\0" + unstaged + "\0" + staged + "\0" + untracked).split("\0")
    # Each git command yields unique names, but the same file legitimately appears in
    # several sources (committed-then-edited, staged-and-staged+unstaged): union them.
    paths = sorted({p for p in raw if p})
    for path in paths:
        if "\x00" in path or ".." in path.split("/"):
            raise RuntimeError(f"unsafe touched path: {path!r}")
    return paths


def _parse_coverage_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"coverage json unreadable: {exc}") from exc
    files = data.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("coverage json has no files object")
    return files


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    coverage = None
    out = None
    matrix = None
    for i, arg in enumerate(argv):
        if arg == "--coverage" and i + 1 < len(argv):
            coverage = Path(argv[i + 1])
        elif arg == "--out" and i + 1 < len(argv):
            out = Path(argv[i + 1])
        elif arg == "--release-matrix" and i + 1 < len(argv):
            matrix = Path(argv[i + 1])
    if coverage is None or out is None:
        sys.stderr.write("usage: coverage_gate --coverage .coverage.json [--release-matrix "
                         "release/versions.json] --out .coverage-touched.json\n")
        return 2
    try:
        paths = touched_paths(REPO, matrix)
        files = _parse_coverage_json(coverage)
        errors = []
        classified = []
        for path in paths:
            kind = classify(path)
            if kind != "canonical-python":
                classified.append({"path": path, "kind": kind})
                continue
            # Forced slashes: coverage.json keys are absolute or relative as recorded.
            hit = files.get(path)
            if hit is None:
                hit = files.get(str(REPO / path))
            if not isinstance(hit, dict):
                errors.append(f"{path}: no coverage data for touched canonical Python file")
                classified.append({"path": path, "kind": kind, "covered": False})
                continue
            summary = hit.get("summary")
            if not isinstance(summary, dict):
                errors.append(f"{path}: coverage summary missing or malformed")
                classified.append({"path": path, "kind": kind, "covered": False})
                continue
            missing_lines = summary.get("missing_lines")
            missing_branches = summary.get("missing_branches")
            valid_counts = (
                type(missing_lines) is int and missing_lines >= 0
                and type(missing_branches) is int and missing_branches >= 0)
            if not valid_counts:
                errors.append(f"{path}: coverage summary counts missing or malformed")
                classified.append({"path": path, "kind": kind, "covered": False})
                continue
            covered = missing_lines == 0 and missing_branches == 0
            classified.append({"path": path, "kind": kind, "covered": covered,
                               "missing_lines": missing_lines,
                               "missing_branches": missing_branches})
            if not covered:
                errors.append(f"{path}: touched file must have 100% line+branch coverage "
                              f"(missing {missing_lines} lines, {missing_branches} branches)")
        artifact = {"touched_paths": classified, "errors": errors}
        if out:
            out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        if errors:
            sys.stderr.write("COVERAGE GATE FAILED:\n" + "\n".join(errors) + "\n")
            return 1
        print(f"coverage gate: {len(classified)} touched paths, "
              f"{sum(1 for c in classified if c.get('kind') == 'canonical-python')} "
              "canonical Python files fully covered")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        sys.stderr.write(f"coverage gate failed closed: {exc}\n")
        return 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())