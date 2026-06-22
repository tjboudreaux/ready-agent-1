"""Tests for the touched-file coverage gate (engine/readiness/coverage_gate.py)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import readiness
from readiness import coverage_gate as cg

REPO = Path(readiness.__file__).resolve().parents[2]


def _cov(total=95.0, files=None):
    return {"totals": {"percent_covered": total}, "files": files or {}}


def _full(pct=100.0):
    return {"summary": {"missing_lines": 0, "missing_branches": 0, "percent_covered": pct}}


def _partial(ml=2, mb=1, pct=90.0):
    return {"summary": {"missing_lines": ml, "missing_branches": mb, "percent_covered": pct}}


class TestNormAndMatch(unittest.TestCase):
    def test_norm_collapses_separators(self):
        self.assertEqual(cg._norm("./a/../a/b.py"), "a/b.py")

    def test_match_exact(self):
        files = {"evals/fixtures.py": _full()}
        self.assertEqual(cg.match_file(files, "evals/fixtures.py"), "evals/fixtures.py")

    def test_match_suffix(self):
        files = {"/abs/root/engine/readiness/cli.py": _full()}
        self.assertEqual(
            cg.match_file(files, "engine/readiness/cli.py"),
            "/abs/root/engine/readiness/cli.py",
        )

    def test_match_none(self):
        self.assertIsNone(cg.match_file({"evals/fixtures.py": _full()}, "engine/x.py"))


class TestFileCoverage(unittest.TestCase):
    def test_ok_when_no_missing(self):
        self.assertTrue(cg.file_coverage_ok(_full()["summary"]))

    def test_not_ok_with_missing_lines(self):
        self.assertFalse(cg.file_coverage_ok(_partial(ml=1, mb=0)["summary"]))

    def test_not_ok_with_missing_branches(self):
        self.assertFalse(cg.file_coverage_ok(_partial(ml=0, mb=1)["summary"]))


class TestCheckTotal(unittest.TestCase):
    def test_under_fails(self):
        self.assertEqual(len(cg.check_total(_cov(total=89.99), 90.0)), 1)

    def test_at_boundary_passes(self):
        self.assertEqual(cg.check_total(_cov(total=90.0), 90.0), [])

    def test_above_passes(self):
        self.assertEqual(cg.check_total(_cov(total=99.0), 90.0), [])


class TestCheckChangedFiles(unittest.TestCase):
    def test_non_py_is_skipped(self):
        self.assertEqual(cg.check_changed_files(_cov(), ["pyproject.toml"]), [])

    def test_missing_file_hard_fails_naming_file(self):
        violations = cg.check_changed_files(_cov(files={}), ["engine/readiness/new.py"])
        self.assertEqual(len(violations), 1)
        self.assertIn("engine/readiness/new.py", violations[0])
        self.assertIn("not measured", violations[0])

    def test_missing_file_allowed_as_thin_wrapper(self):
        violations = cg.check_changed_files(
            _cov(files={}), ["scripts/coverage_gate.py"],
            thin_wrappers=["scripts/coverage_gate.py"],
        )
        self.assertEqual(violations, [])

    def test_partial_file_fails(self):
        files = {"evals/fixtures.py": _partial(ml=2, mb=1)}
        violations = cg.check_changed_files(_cov(files=files), ["evals/fixtures.py"])
        self.assertEqual(len(violations), 1)
        self.assertIn("missing_lines=2", violations[0])
        self.assertIn("missing_branches=1", violations[0])

    def test_full_file_passes(self):
        files = {"evals/fixtures.py": _full()}
        self.assertEqual(cg.check_changed_files(_cov(files=files), ["evals/fixtures.py"]), [])


class TestGate(unittest.TestCase):
    def test_gate_combines_total_and_files(self):
        files = {"evals/fixtures.py": _partial()}
        violations = cg.gate(_cov(total=80.0, files=files), ["evals/fixtures.py"], fail_under=90.0)
        self.assertEqual(len(violations), 2)


class TestMain(unittest.TestCase):
    def _write(self, payload):
        tmp = Path(tempfile.mkdtemp(prefix="ar-cov-")) / "cov.json"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        return tmp

    def test_main_passes(self):
        path = self._write(_cov(total=99.0, files={"evals/fixtures.py": _full()}))
        rc = cg.main(["--coverage", str(path), "--changed-files", "evals/fixtures.py"])
        self.assertEqual(rc, 0)

    def test_main_fails_on_violation(self):
        path = self._write(_cov(total=50.0, files={}))
        rc = cg.main(["--coverage", str(path), "--changed-files", "engine/x.py",
                      "--fail-under", "90"])
        self.assertEqual(rc, 1)

    def test_main_thin_wrapper_flag(self):
        path = self._write(_cov(total=99.0, files={}))
        rc = cg.main(["--coverage", str(path), "--changed-files", "scripts/coverage_gate.py",
                      "--thin-wrapper", "scripts/coverage_gate.py"])
        self.assertEqual(rc, 0)


class TestThinWrapperDelegates(unittest.TestCase):
    """The scripts entrypoint must be a pure passthrough to the covered engine module."""

    def test_script_delegates_to_engine_main(self):
        path = REPO / "scripts" / "coverage_gate.py"
        spec = importlib.util.spec_from_file_location("ra1_scripts_coverage_gate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIs(mod.main, cg.main)


if __name__ == "__main__":
    unittest.main()
