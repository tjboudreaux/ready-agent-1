"""End-to-end ``python3 -m evals.coverage_gate`` entrypoint coverage.

Runs the module as ``__main__`` in a disposable git repository (the temp-repo pattern
from test_evals_coverage_gate2.py, with the gate module copied in so its REPO constant
resolves to the fixture root). New file by ownership convention.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals import coverage_gate as cg

REPO = Path(__file__).resolve().parents[1]


def _temp_repo(testcase):
    """A disposable git repo with one committed file, a base matrix, and coverage."""
    tmp = Path(tempfile.mkdtemp(prefix="cg-noout-"))
    testcase.addCleanup(shutil.rmtree, tmp, True)
    for args in (["init", "-q"],
                 ["config", "user.email", "t@t"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(tmp), *args], check=True)
    (tmp / "mod.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "base"], check=True)
    base = subprocess.run(["git", "-C", str(tmp), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    (tmp / "mod.py").write_text("x = 2\n", encoding="utf-8")  # unstaged touch
    matrix_dir = tmp / "release"
    matrix_dir.mkdir()
    matrix = matrix_dir / "versions.json"
    matrix.write_text(json.dumps(
        {"publication_source": {"selection_base_commit": base}}), encoding="utf-8")
    cov = tmp / ".coverage.json"
    cov.write_text(json.dumps({"files": {
        "mod.py": {"summary": {"missing_lines": 0, "missing_branches": 0}},
    }}), encoding="utf-8")
    return tmp, cov, matrix


class TestMainOutGuard(unittest.TestCase):
    def test_falsy_out_skips_artifact_write(self):
        # ``out`` is guaranteed non-None by the usage check, and Path objects are always
        # truthy, so the ``if out:`` False arc is defensive; patch Path to hand back a
        # falsy non-None object for the --out argument.
        class FalsyOut:
            def __bool__(self):
                return False

        tmp, cov, matrix = _temp_repo(self)
        real_path = cg.Path
        out_arg = str(tmp / "out.json")

        def fake_path(value):
            return FalsyOut() if value == out_arg else real_path(value)

        with mock.patch.object(cg, "REPO", tmp), \
                mock.patch.object(cg, "Path", side_effect=fake_path):
            rc = cg.main(["--coverage", str(cov), "--release-matrix", str(matrix),
                          "--out", out_arg])
        self.assertEqual(rc, 0)
        self.assertFalse((tmp / "out.json").exists())


class TestModuleMainEntrypoint(unittest.TestCase):
    def test_main_success_end_to_end(self):
        tmp, cov, matrix = _temp_repo(self)
        pkg = tmp / "evals"
        pkg.mkdir()
        shutil.copy(REPO / "evals" / "__init__.py", pkg / "__init__.py")
        shutil.copy(REPO / "evals" / "coverage_gate.py", pkg / "coverage_gate.py")
        fully_covered = {"summary": {"missing_lines": 0, "missing_branches": 0}}
        cov.write_text(json.dumps({"files": {
            "mod.py": fully_covered,
            "evals/__init__.py": fully_covered,
            "evals/coverage_gate.py": fully_covered,
        }}), encoding="utf-8")
        out = tmp / "out.json"
        result = subprocess.run(
            [sys.executable, "-m", "evals.coverage_gate",
             "--coverage", str(cov), "--release-matrix", str(matrix),
             "--out", str(out)],
            cwd=tmp, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(artifact["errors"], [])
        self.assertIn("mod.py",
                      [entry["path"] for entry in artifact["touched_paths"]])


if __name__ == "__main__":
    unittest.main()
