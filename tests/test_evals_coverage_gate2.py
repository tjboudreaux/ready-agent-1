"""Success-path gate tests for evals/coverage_gate (canonical-file True branch)."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from evals import coverage_gate as cg


class TestSuccessWithCanonical(unittest.TestCase):
    def test_success_path_reports_canonical_fully_covered(self):
        # The success print's generator must take its True branch at least once:
        # at least one touched canonical Python file with complete coverage data.
        repo = Path(tempfile.mkdtemp(prefix="cg-can-"))
        self.addCleanup(lambda: subprocess.run(
            ["rm", "-rf", str(repo)], check=True))
        for args in (["git", "-C", str(repo), "init", "-q"],
                     ["git", "-C", str(repo), "config", "user.email", "t@t"],
                     ["git", "-C", str(repo), "config", "user.name", "T"]):
            subprocess.run(args, check=True)
        (repo / "mod.py").write_text("x = 1\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        matrix_dir = repo / "release"
        matrix_dir.mkdir()
        matrix = matrix_dir / "versions.json"
        matrix.write_text(json.dumps(
            {"publication_source": {"selection_base_commit": base}}))
        (repo / "mod.py").write_text("x = 2\n")  # touched: unstaged edit

        cov = repo / ".coverage.json"
        cov.write_text(json.dumps({"files": {
            "mod.py": {"summary": {"missing_lines": 0, "missing_branches": 0}},
        }}))
        out = repo / "out.json"
        import unittest.mock as mock
        with mock.patch.object(cg, "REPO", repo):
            rc = cg.main(["--coverage", str(cov), "--release-matrix", str(matrix),
                          "--out", str(out)])
        self.assertEqual(rc, 0)
        artifact = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(artifact["errors"], [])
        canon = [c for c in artifact["touched_paths"]
                 if c.get("kind") == "canonical-python"]
        self.assertEqual([c["path"] for c in canon], ["mod.py"])
        self.assertTrue(all(c.get("covered") for c in canon))


if __name__ == "__main__":
    unittest.main()