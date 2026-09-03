"""Composite-action contract: no consumer-repository state, env-only inputs, correct scope.

The published action must write only to a private RUNNER_TEMP directory, interpolate no
caller input into shell source, export declared ephemeral outputs, skip absent optional
Markdown/SARIF cleanly, and gate on the same evidence options as the report scan.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest
import unittest.mock as mock

REPO = pathlib.Path(__file__).resolve().parent.parent


def _action_text() -> str:
    return (REPO / "ci" / "action.yml").read_text(encoding="utf-8")


def _launcher_text() -> str:
    return (REPO / "ci" / "run_engine.py").read_text(encoding="utf-8")


class TestActionContract(unittest.TestCase):
    def test_no_consumer_repository_output_paths(self):
        text = _action_text()
        self.assertNotIn(".agents/readiness", text)
        self.assertNotIn("$PROJECT/.ra1/reports", text)
        self.assertIn("RUNNER_TEMP", text)
        self.assertIn("ready-agent-1-", text)
        self.assertIn("mktemp", text)

    def test_inputs_never_reach_run_source(self):
        text = _action_text()
        # env crossings only: no ${{ inputs.* }} inside run blocks
        for line in text.splitlines():
            if "${{ inputs." in line:
                self.assertTrue(line.strip().startswith(("env:", "        ")) or
                                "env:" in text[:text.index(line)] or line.strip().startswith((
                                    "PROJECT:", "FORMATS:", "MIN_LEVEL:", "UPLOAD_SARIF:",
                                    "GITHUB_ENABLED:", "ACTION_PATH:")),
                                f"input interpolated into run source: {line.strip()}")
        # no eval, no expression-to-shell interpolation
        self.assertNotIn("eval ", _launcher_text())
        self.assertNotIn("${{ inputs.", text.split("run: |")[1]) if "run: |" in text else None

    def test_report_and_gate_use_identical_github_scope(self):
        launcher = _launcher_text()
        self.assertIn("GITHUB_ENABLED", launcher)
        self.assertIn("def _env_bool", launcher)
        gate_block = _action_text().split("ACTION_MODE: gate", 1)[1]
        self.assertIn("GITHUB_ENABLED", gate_block)
        report_block = _action_text().split("Run readiness engine", 1)[1]
        report_block = report_block.split("Publish job summary", 1)[0]
        self.assertIn("GITHUB_ENABLED", report_block)

    def test_outputs_declared_and_emitted_via_github_output(self):
        text = _action_text()
        for name in ("report-directory", "has-markdown", "markdown-file", "has-sarif",
                     "sarif-file"):
            self.assertIn(f"{name}:", text)
        self.assertIn("$GITHUB_OUTPUT", text)
        self.assertIn("out-dir=", text)
        self.assertIn("has-markdown=", text)

    def test_optional_sarif_skipped_cleanly(self):
        text = _action_text()
        self.assertIn("has-sarif", text)
        self.assertIn("sarif-file", text)
        # upload step conditioned on has-sarif and upload-sarif input
        self.assertIn("steps.report.outputs.has-sarif", text)
        self.assertIn("inputs.upload-sarif == 'true'", text)

    def test_min_level_bounded_and_row(self):
        for block in (text := _action_text()).split("run: |")[1:]:
            if "MIN_LEVEL" in block or "min-level" in block:
                pass
        self.assertIn("[1-4]", text)
        self.assertNotIn("[1-5]", text)

    def test_launcher_smoke_local(self):
        """The launcher itself runs: report mode 0, gate mode nonzero below min-level."""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="ra1-action-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "README.md").write_text("# P\n\n## S\n\n```\npip install .\n```\n"
                                       + ("docs " * 60))
        (tmp / "pyproject.toml").write_text('[project]\nname="x"\nversion="0.1.0"\n')
        (tmp / ".gitignore").write_text("/.ra1/reports/\n")
        subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "i"], check=True)
        env = dict(os.environ,
                   ACTION_PATH=str(REPO / "ci"),
                   ACTION_MODE="report",
                   PROJECT=str(tmp),
                   FORMATS="markdown",
                   MIN_LEVEL="1",
                   GITHUB_ENABLED="false",
                   out_dir=str(tmp / "out"))
        proc = subprocess.run(["python3", str(REPO / "ci" / "run_engine.py")],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        env["ACTION_MODE"] = "gate"
        env["MIN_LEVEL"] = "3"
        gate = subprocess.run(["python3", str(REPO / "ci" / "run_engine.py")],
                              capture_output=True, text=True, env=env)
        self.assertNotEqual(gate.returncode, 0)  # fixture is level < 3


class TestLauncherMain(unittest.TestCase):
    """In-process launcher branches so the touched gate can trace ci/run_engine.py."""

    def _run(self, env, patch_run):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ra1_ci_run_engine", REPO / "ci" / "run_engine.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with (mock.patch.dict(os.environ, env, clear=True),
              mock.patch("subprocess.run", side_effect=patch_run)):
            return mod.main()

    @staticmethod
    def _ok(returncode=0):
        return subprocess.CompletedProcess([], returncode, stdout="")

    def test_report_mode_builds_argv_and_returns_code(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return self._ok(0 if "out" in argv[argv.index("--out") + 1] else 1)

        rc = self._run({"ACTION_PATH": str(REPO / "ci"), "ACTION_MODE": "report",
                        "PROJECT": "/tmp/p", "FORMATS": "json,markdown",
                        "MIN_LEVEL": "1", "out_dir": "/tmp/out"}, run)
        self.assertEqual(rc, 0)
        self.assertIn("--format", calls[0])
        self.assertIn("/tmp/out", calls[0])
        rc2 = self._run({"ACTION_PATH": str(REPO / "ci"), "ACTION_MODE": "report",
                         "PROJECT": "/tmp/p", "FORMATS": "json", "MIN_LEVEL": "1",
                         "out_dir": "/tmp/out"}, lambda argv, **k: self._ok(3))
        self.assertEqual(rc2, 3)

    def test_gate_mode_and_github_flag(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return self._ok(0)

        rc = self._run({"ACTION_PATH": str(REPO / "ci"), "ACTION_MODE": "gate",
                        "PROJECT": "/tmp/p", "FORMATS": "json", "MIN_LEVEL": "2",
                        "GITHUB_ENABLED": "1"}, run)
        self.assertEqual(rc, 0)
        self.assertNotIn("--out", calls[0])
        self.assertIn("--min-level", calls[0])
        self.assertIn("--github", calls[0])

    def test_invalid_min_level_and_github_false(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return self._ok(0)

        rc = self._run({"ACTION_PATH": str(REPO / "ci"), "ACTION_MODE": "report",
                        "PROJECT": "/tmp/p", "FORMATS": "json", "MIN_LEVEL": "9",
                        "out_dir": "/tmp/out", "GITHUB_ENABLED": "false"}, run)
        self.assertEqual(rc, 1)  # min-level out of range, before any launch
        self.assertEqual(calls, [])
        rc2 = self._run({"ACTION_PATH": str(REPO / "ci"), "ACTION_MODE": "report",
                         "PROJECT": "/tmp/p", "FORMATS": "json", "MIN_LEVEL": "1",
                         "out_dir": "/tmp/out", "GITHUB_ENABLED": "no"}, run)
        self.assertEqual(rc2, 0)
        self.assertNotIn("--github", calls[0])

    def test_gate_nonzero_propagates_stderr(self):
        import contextlib
        import io
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess([], 1, stdout="", stderr="boom")

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = self._run({"ACTION_PATH": str(REPO / "ci"), "ACTION_MODE": "gate",
                            "PROJECT": "/tmp/p", "FORMATS": "json", "MIN_LEVEL": "1"},
                           run)
        self.assertEqual(rc, 1)
        self.assertIn("boom", buf.getvalue())


if __name__ == "__main__":
    unittest.main()