"""Tests for the touched-file coverage gate (evals/coverage_gate.py).

Builds real temporary git repositories (an attested selection base plus tracked and
untracked changes), feeds synthetic coverage JSON through ``main()``, and asserts exit
codes 0/1/2, the deterministic artifact shape, and fail-closed behavior on malformed
matrix/base/coverage state.
"""
from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import tests._util as U
from evals import coverage_gate as cg


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout


def _base_repo() -> Path:
    """A git repo with one seed commit recorded as the selection base, then the matrix."""
    repo = U.make_repo({"seed.txt": "seed\n"})
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    matrix = {"publication_source": {"selection_base_commit": base}}
    (repo / "release").mkdir()
    (repo / "release" / "versions.json").write_text(json.dumps(matrix), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "matrix")
    return repo


def _touched_repo() -> Path:
    """A base repo plus one committed canonical Python file and two untracked files."""
    repo = _base_repo()
    (repo / "engine").mkdir()
    (repo / "engine" / "touched.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "touched")
    (repo / "evals").mkdir()
    (repo / "evals" / "extra.py").write_text("y = 2\n", encoding="utf-8")
    (repo / "notes.md").write_text("untracked\n", encoding="utf-8")
    return repo


class TestClassify(unittest.TestCase):
    def test_vendored_mirror(self):
        self.assertEqual(cg.classify("skills/ra1-report/scripts/readiness/cli.py"),
                         "vendored-mirror")

    def test_vendored_non_mirror(self):
        self.assertEqual(cg.classify("skills/ra1-report/SKILL.md"), "vendored")
        self.assertEqual(cg.classify("skills/ra1-report/scripts/helper.py"), "vendored")

    def test_non_python(self):
        self.assertEqual(cg.classify("README.md"), "non-python")
        self.assertEqual(cg.classify("release/versions.json"), "non-python")

    def test_test_files(self):
        self.assertEqual(cg.classify("tests/test_x.py"), "test")

    def test_canonical_python(self):
        for path in ("engine/readiness/x.py", "evals/x.py", "scripts/x.py",
                     "root_level.py"):
            with self.subTest(path=path):
                self.assertEqual(cg.classify(path), "canonical-python")


class TestTouchedPaths(unittest.TestCase):
    def test_union_of_tracked_and_untracked(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        self.assertEqual(cg.touched_paths(repo),
                         ["engine/touched.py", "evals/extra.py", "notes.md",
                          "release/versions.json"])

    def test_worktree_states_are_unioned_once(self):
        """Committed-then-edited and staged files appear exactly once."""
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        with (repo / "engine" / "touched.py").open("a", encoding="utf-8") as fh:
            fh.write("z = 3\n")                       # unstaged modification
        (repo / "staged.py").write_text("s = 4\n", encoding="utf-8")
        _git(repo, "add", "staged.py")                # staged, uncommitted
        self.assertEqual(cg.touched_paths(repo),
                         ["engine/touched.py", "evals/extra.py", "notes.md",
                          "release/versions.json", "staged.py"])

    def test_explicit_matrix_path_is_honored(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        matrix = repo / "release" / "versions.json"
        moved = repo / "elsewhere.json"
        moved.write_text(matrix.read_text(encoding="utf-8"), encoding="utf-8")
        matrix.unlink()
        self.assertIn("engine/touched.py", cg.touched_paths(repo, moved))

    def test_unusable_base_fails_closed(self):
        repo = _base_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        (repo / "release" / "versions.json").write_text(
            json.dumps({"publication_source": {"selection_base_commit": "0" * 40}}),
            encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "selection base not usable"):
            cg.touched_paths(repo)

    def test_missing_matrix_raises(self):
        repo = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(repo))
        with self.assertRaises(OSError):
            cg.touched_paths(repo)

    def test_unsafe_paths_rejected(self):
        repo = _base_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        def fake(args, *, cwd):
            if args[0] == "diff" and len(args) > 3:
                return "../evil.py"  # base..HEAD tracked diff
            return ""
        with mock.patch.object(cg, "_git", fake):
            with self.assertRaisesRegex(RuntimeError, "unsafe touched path"):
                cg.touched_paths(repo)


class TestParseCoverageJson(unittest.TestCase):
    def _write(self, text: str) -> Path:
        repo = U.make_repo({"cov.json": text})
        self.addCleanup(lambda: U.rmtree(repo))
        return repo / "cov.json"

    def test_valid(self):
        files = cg._parse_coverage_json(self._write('{"files": {"a.py": {}}}'))
        self.assertEqual(files, {"a.py": {}})

    def test_unreadable(self):
        with self.assertRaisesRegex(RuntimeError, "coverage json unreadable"):
            cg._parse_coverage_json(Path("/nonexistent-ra1/cov.json"))

    def test_malformed_json(self):
        with self.assertRaisesRegex(RuntimeError, "coverage json unreadable"):
            cg._parse_coverage_json(self._write("{not json"))

    def test_missing_files_object(self):
        with self.assertRaisesRegex(RuntimeError, "no files object"):
            cg._parse_coverage_json(self._write('{"totals": {}}'))

    def test_files_not_a_dict(self):
        with self.assertRaisesRegex(RuntimeError, "no files object"):
            cg._parse_coverage_json(self._write('{"files": []}'))


class TestMain(unittest.TestCase):
    def _run(self, argv, repo):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cg, "REPO", repo), \
                redirect_stdout(out), redirect_stderr(err):
            rc = cg.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _coverage(self, repo, files) -> Path:
        cov = repo / "coverage.json"
        cov.write_text(json.dumps({"files": files}), encoding="utf-8")
        return cov

    def test_usage_errors_exit_2(self):
        repo = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(repo))
        for argv in ([], ["--coverage", "c.json"], ["--out", "o.json"],
                     ["--coverage", "c.json", "--out"], ["--coverage"]):
            with self.subTest(argv=argv):
                rc, _out, err = self._run(argv, repo)
                self.assertEqual(rc, 2)
                self.assertIn("usage:", err)

    def test_success_exit_0(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        full = {"summary": {"missing_lines": 0, "missing_branches": 0}}
        cov = self._coverage(repo, {
            "engine/touched.py": full,                  # relative key form
            str(repo / "evals" / "extra.py"): full,     # absolute key form
        })
        out_path = repo / "touched.json"
        rc, out, err = self._run(
            ["--coverage", str(cov), "--release-matrix",
             str(repo / "release" / "versions.json"), "--out", str(out_path),
             "--unknown-flag"], repo)
        self.assertEqual(rc, 0, err)
        self.assertIn("2 canonical Python files fully covered", out)
        artifact = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(artifact["errors"], [])
        by_path = {e["path"]: e for e in artifact["touched_paths"]}
        self.assertEqual(by_path["engine/touched.py"]["kind"], "canonical-python")
        self.assertTrue(by_path["engine/touched.py"]["covered"])
        self.assertEqual(by_path["evals/extra.py"]["kind"], "canonical-python")
        self.assertEqual(by_path["notes.md"]["kind"], "non-python")
        self.assertNotIn("covered", by_path["notes.md"])
        self.assertEqual(by_path["release/versions.json"]["kind"], "non-python")

    def test_missing_coverage_data_exit_1(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        cov = self._coverage(repo, {})
        out_path = repo / "touched.json"
        rc, _out, err = self._run(["--coverage", str(cov), "--out", str(out_path)], repo)
        self.assertEqual(rc, 1)
        self.assertIn("COVERAGE GATE FAILED", err)
        self.assertIn("no coverage data", err)
        artifact = json.loads(out_path.read_text(encoding="utf-8"))
        by_path = {e["path"]: e for e in artifact["touched_paths"]}
        self.assertFalse(by_path["engine/touched.py"]["covered"])
        self.assertEqual(len(artifact["errors"]), 2)

    def test_non_dict_entry_is_no_data(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        cov = self._coverage(repo, {"engine/touched.py": None, "evals/extra.py": 5})
        rc, _out, err = self._run(["--coverage", str(cov), "--out", str(repo / "t.json")],
                                  repo)
        self.assertEqual(rc, 1)
        self.assertEqual(err.count("no coverage data"), 2)

    def test_partial_coverage_exit_1(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        full = {"summary": {"missing_lines": 0, "missing_branches": 0}}
        partial = {"summary": {"missing_lines": 3, "missing_branches": 1}}
        cov = self._coverage(repo, {"engine/touched.py": partial,
                                    "evals/extra.py": full})
        out_path = repo / "touched.json"
        rc, _out, err = self._run(["--coverage", str(cov), "--out", str(out_path)], repo)
        self.assertEqual(rc, 1)
        self.assertIn("missing 3 lines, 1 branches", err)
        artifact = json.loads(out_path.read_text(encoding="utf-8"))
        by_path = {e["path"]: e for e in artifact["touched_paths"]}
        self.assertFalse(by_path["engine/touched.py"]["covered"])
        self.assertTrue(by_path["evals/extra.py"]["covered"])

    def test_missing_branch_coverage_exit_1(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        full = {"summary": {"missing_lines": 0, "missing_branches": 0}}
        branchy = {"summary": {"missing_lines": 0, "missing_branches": 2}}
        cov = self._coverage(repo, {"engine/touched.py": branchy,
                                    "evals/extra.py": full})
        rc, _out, err = self._run(["--coverage", str(cov), "--out", str(repo / "t.json")],
                                  repo)
        self.assertEqual(rc, 1)
        self.assertIn("missing 0 lines, 2 branches", err)

    def test_missing_summary_fails_closed(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        cov = self._coverage(repo, {"engine/touched.py": {},
                                    "evals/extra.py": {"summary": {}}})
        rc, _out, err = self._run(["--coverage", str(cov), "--out", str(repo / "t.json")],
                                  repo)
        self.assertEqual(rc, 1)
        self.assertIn("summary missing or malformed", err)
        self.assertIn("counts missing or malformed", err)

    def test_negative_counts_fail_closed(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        bad = {"summary": {"missing_lines": -1, "missing_branches": 0}}
        cov = self._coverage(repo, {"engine/touched.py": bad})
        rc, _out, err = self._run(["--coverage", str(cov), "--out", str(repo / "t.json")],
                                  repo)
        self.assertEqual(rc, 1)
        self.assertIn("counts missing or malformed", err)

    def test_unreadable_coverage_fails_closed(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        out_path = repo / "touched.json"
        rc, _out, err = self._run(
            ["--coverage", str(repo / "nope.json"), "--out", str(out_path)], repo)
        self.assertEqual(rc, 1)
        self.assertIn("failed closed", err)
        self.assertFalse(out_path.exists())

    def test_malformed_coverage_fails_closed(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        cov = self._coverage(repo, {})
        cov.write_text("{broken", encoding="utf-8")
        rc, _out, err = self._run(
            ["--coverage", str(cov), "--out", str(repo / "t.json")], repo)
        self.assertEqual(rc, 1)
        self.assertIn("failed closed", err)

    def test_coverage_without_files_fails_closed(self):
        repo = _touched_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        cov = repo / "coverage.json"
        cov.write_text('{"files": null}', encoding="utf-8")
        rc, _out, err = self._run(
            ["--coverage", str(cov), "--out", str(repo / "t.json")], repo)
        self.assertEqual(rc, 1)
        self.assertIn("no files object", err)

    def test_unusable_base_fails_closed(self):
        repo = _base_repo()
        self.addCleanup(lambda: U.rmtree(repo))
        (repo / "release" / "versions.json").write_text(
            json.dumps({"publication_source": {"selection_base_commit": "z" * 40}}),
            encoding="utf-8")
        cov = self._coverage(repo, {})
        rc, _out, err = self._run(
            ["--coverage", str(cov), "--out", str(repo / "t.json")], repo)
        self.assertEqual(rc, 1)
        self.assertIn("selection base not usable", err)


if __name__ == "__main__":
    unittest.main()
