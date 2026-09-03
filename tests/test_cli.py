import argparse
import io
import json
import subprocess
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from readiness import cli, history

from tests._util import make_repo, rmtree


def run(argv):
    """Run the CLI, returning (exit_code, stdout, stderr)."""
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = cli.main(argv)
    return code, buf.getvalue(), err.getvalue()


BASE_NON_LOOP = {
    "README.md": "# Project\n\n## Setup\n\n```sh\npython -m unittest\n```\n\n"
                  + ("Maintainer detail. " * 30),
    "pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n[tool.ruff]\nline-length=100\n',
    ".gitignore": ".env\n.env.*\n*.pem\n__pycache__/\nnode_modules/\ndist/\nbuild/\n.venv/\n",
    ".github/workflows/readiness.yml": "name: readiness\non: [push]\njobs:\n  test:\n    runs-on: "
                                       "ubuntu-latest\n    steps: []\n",
    ".github/ISSUE_TEMPLATE/bug_report.md": "---\nname: Bug report\n---\n",
    ".github/pull_request_template.md": "## Summary\n\nDescribe changes and test coverage for "
                                        "reviewers.\n",
    ".github/dependabot.yml": "version: 2\nupdates: []\n",
    ".devcontainer/devcontainer.json": "{}\n",
    ".pre-commit-config.yaml": "repos: []\n",
    "CODEOWNERS": "* @team\n",
    "SECURITY.md": "# Security Policy\n\nReport issues to the maintainers.\n",
    ".env.example": "API_KEY=\n",
    "ruff.toml": "line-length = 100\n",
    "tests/test_example.py": "def test_example():\n    assert True\n",
}

LOOP_ARTIFACTS = {
    "loop-runs/README.md": "# Loop Runs\n\nThis directory records loop attempts and evidence for "
                           "maintainers.\n",
    ".omp/rules/README.md": "# Loop Rules\n\nThis rules index links to the denylist and safety "
                            "policies.\n",
    ".omp/rules/denylist.md": "# Loop Denylist\n\n- Never read or export secrets, "
                              "credentials, or .env files.\n- Never run destructive deletes "
                              "or drop data.\n- Never push, merge, deploy, release, or "
                              "publish without human confirmation.\n- Never disable CI, "
                              "tests, security scanning, or branch protection.\n",
    "signals/README.md": "# Signal Schema\n\n```json\n{\"schema_version\":\"1\",\"signal\":\"loop."
                         "run\",\"source\":\"runner\",\"timestamp\":\"2026-01-01T00:00:00Z\","
                         "\"evidence\":[]}\n```\n",
    ".omp/commands/pr-artifact-template.md": "# PR Evidence\n\nCite artifact evidence, CI logs, "
                                             "screenshots, and loop-runs records.\n",
    ".omp/commands/goal.md": "# Goal Contract\n\nCapture the loop goal, boundaries, evidence "
                             "requirements, and owner.\n",
    ".omp/commands/loop.md": "# Loop Contract\n\nCapture loop iteration rules, stop conditions, "
                             "evidence, and escalation.\n",
    "ARCHITECTURE.md": "# Architecture\n\nDocument the system shape, critical paths, and ownership "
                       "boundaries.\n",
    "domains/billing/README.md": "# Billing Domain\n\nDocument domain vocabulary, invariants, "
                                 "workflows, and maintainer contacts.\n",
    ".omp/skills/a/SKILL.md": "---\nname: a\ndescription: loop skill artifact\n---\n# A\n\nFilled "
                              "loop skill artifact for maintainers.\n",
    ".omp/skills/b/SKILL.md": "---\nname: b\ndescription: loop skill artifact\n---\n# B\n\nFilled "
                              "loop skill artifact for maintainers.\n",
    ".omp/skills/c/SKILL.md": "---\nname: c\ndescription: loop skill artifact\n---\n# C\n\nFilled "
                              "loop skill artifact for maintainers.\n",
}

LOOP_CONFIG = {".ra1/config.json"
               : json.dumps({"schema_version": "1", "loop_ready": True})}


def _loop_results(data):
    return {r["id"]: r for r in data["results"] if r["id"].startswith("loop.")}


class TestCli(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n'
                                                 , "README.md": "# lib"})
        self.addCleanup(rmtree, self.repo)

    def test_report_json(self):
        code, out, _err = run(["report", "--project", str(self.repo), "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["detection"]["project_type"], "library")
        self.assertIn("engine_version", data)
        self.assertFalse(data["github_available"])

    def test_report_writes_out_dir(self):
        out_dir = Path(tempfile.mkdtemp(prefix="ar-out-"))
        self.addCleanup(rmtree, out_dir)
        code, _out2, _err = run(["report", "--project", str(self.repo),
                                 "--out", str(out_dir), "--store-history",
                                 "--format", "json,markdown"])
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "report.json").exists())
        self.assertTrue((out_dir / "report.md").exists())
        self.assertTrue((out_dir / "latest.json").exists())
        json.loads((out_dir / "latest.json").read_text())  # valid JSON

    def test_version(self):
        code, out, _err = run(["version"])
        self.assertEqual(code, 0)
        self.assertIn("engine_version", json.loads(out))

    def test_detect(self):
        code, out, _err = run(["detect", "--project", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["project_type"], "library")

    def test_formats(self):
        code, out, _err = run(["formats"])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(),
                         ["json", "markdown", "html", "github", "junit", "sarif"])

    def test_min_level_gate_fails_when_unreachable(self):
        code, _out2, _err = run(["report", "--project", str(self.repo), "--min-level", "6"])
        self.assertEqual(code, 1)

    def test_no_min_level_passes(self):
        code, _out2, _err = run(["report", "--project", str(self.repo)])
        self.assertEqual(code, 0)

    def test_loop_json_behavior_is_advisory(self):
        opt_out = make_repo({**BASE_NON_LOOP, **LOOP_ARTIFACTS})
        opt_in_full = make_repo({**BASE_NON_LOOP, **LOOP_CONFIG, **LOOP_ARTIFACTS})
        opt_in_missing = make_repo({**BASE_NON_LOOP, **LOOP_CONFIG})
        opt_out_missing = make_repo(BASE_NON_LOOP)
        self.addCleanup(rmtree, opt_out)
        self.addCleanup(rmtree, opt_in_full)
        self.addCleanup(rmtree, opt_in_missing)
        self.addCleanup(rmtree, opt_out_missing)

        reports = {}
        for name, repo in {
            "opt_out": opt_out,
            "opt_in_full": opt_in_full,
            "opt_in_missing": opt_in_missing,
            "opt_out_missing": opt_out_missing,
        }.items():
            code, out, _err = run(["report", "--project", str(repo), "--format", "json"])
            self.assertEqual(code, 0)
            reports[name] = json.loads(out)

        opt_out_loop = _loop_results(reports["opt_out"])
        opt_in_full_loop = _loop_results(reports["opt_in_full"])
        opt_in_missing_loop = _loop_results(reports["opt_in_missing"])
        self.assertEqual(len(opt_out_loop), 9)
        for r in opt_out_loop.values():
            self.assertFalse(r["gating"])
            self.assertEqual(r["status"], "skipped")
            self.assertEqual(r["rationale"], "not opted into loop readiness")
        for r in opt_in_full_loop.values():
            self.assertFalse(r["gating"])
            self.assertEqual(r["status"], "pass")
        for r in opt_in_missing_loop.values():
            self.assertFalse(r["gating"])
            self.assertEqual(r["status"], "fail")
        for field in ("level", "gating_passed", "gating_total"):
            self.assertEqual(
                             reports["opt_in_missing"]["score"][field],
                             reports["opt_out_missing"]["score"][field])


def _init_git(root, origin=None):
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, timeout=30, check=True)
    if origin:
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=root,
                       capture_output=True, timeout=30, check=True)


class TestBannerAndMain(unittest.TestCase):
    def test_render_banner_color_and_plain(self):
        self.assertIn("insert coin", cli.render_banner(color=True))
        self.assertIn("insert coin", cli.render_banner(color=False))

    def test_main_no_command_prints_banner(self):
        code, out, _err = run([])
        self.assertEqual(code, 0)
        self.assertIn("insert coin", out)

    def test_banner_command(self):
        code, out, _err = run(["banner"])
        self.assertEqual(code, 0)
        self.assertIn("insert coin", out)

    def test_banner_degrades_on_non_unicode_console(self):
        # Windows consoles default to cp1252, which cannot encode the block glyphs; the
        # banner is documented as always available, so it must degrade rather than crash.
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with redirect_stdout(stream):
            code = cli.main(["banner"])
        stream.flush()
        self.assertEqual(code, 0)
        out = raw.getvalue().decode("cp1252")
        self.assertIn("R E A D Y   A G E N T   1", out)
        self.assertIn("insert coin", out)
        self.assertIn("?", out)  # replaced glyphs, not a UnicodeEncodeError


class TestReportIdentityAndHistory(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"README.md": "# lib", "pyproject.toml": '[project]\nname="lib"\n'})
        self.addCleanup(rmtree, self.repo)

    def test_require_origin_without_origin_exits_nonzero(self):
        code, _out2, _err = run(["report", "--project", str(self.repo), "--require-origin"])
        self.assertEqual(code, 1)

    def test_origin_identity_is_redacted(self):
        _init_git(self.repo, origin="https://user:secrettoken@github.com/acme/widget.git")
        code, out, _err = run(["report", "--project", str(self.repo),
                         "--require-origin", "--format", "json"])
        self.assertEqual(code, 0)
        repo = json.loads(out)["repository"]
        self.assertEqual(repo["identity_kind"], "origin")
        self.assertEqual(repo["host"], "github.com")
        self.assertNotIn("secrettoken", out)

    def test_in_repo_persistence_requires_ignore_proof(self):
        _init_git(self.repo)
        code, out, err = run(["report", "--project", str(self.repo), "--store-history"])
        self.assertEqual(code, 1)
        self.assertIn("not safely isolated", err)
        self.assertFalse((self.repo / ".ra1" / "reports").exists())

    def test_store_history_local_identity_and_resolve(self):
        _init_git(self.repo)
        (self.repo / ".gitignore").write_text("/.ra1/reports/\n", encoding="utf-8")
        code, _out2, _err = run(["report", "--project", str(self.repo), "--store-history"])
        self.assertEqual(code, 0)
        latest = self.repo / ".ra1" / "reports" / "latest.json"
        self.assertTrue(latest.exists())
        report = json.loads(latest.read_text())
        self.assertEqual(report["repository"]["identity_kind"], "local_path")
        self.assertNotIn(str(self.repo), json.dumps(report["repository"]))
        # the same canonical store resolves the latest report by identity (fix --latest uses this)
        source = history.admit_history_source(
            "current", str(self.repo / ".ra1" / "reports"))
        self.assertIsNotNone(source)
        try:
            resolved, reason = history.resolve_latest(source, str(self.repo))
        finally:
            source.close()
        self.assertEqual(reason, "")
        self.assertEqual(resolved["repository"]["identity_hash"],
                         report["repository"]["identity_hash"])

    def test_store_history_with_out_dir(self):
        out_dir = Path(tempfile.mkdtemp(prefix="ar-out-"))
        self.addCleanup(rmtree, out_dir)
        code, _out2, _err = run(["report", "--project", str(self.repo),
                       "--store-history", "--out", str(out_dir)])
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "latest.json").exists())
        self.assertTrue((out_dir / "history").exists())

    def test_min_level_satisfied_passes(self):
        rich = make_repo({**BASE_NON_LOOP,
                          ".gitignore": BASE_NON_LOOP[".gitignore"] + "/.ra1/reports/\n"})
        self.addCleanup(rmtree, rich)
        _init_git(rich)
        code, out, _err = run(["report", "--project", str(rich), "--format", "json"])
        level = json.loads(out)["score"]["level"]
        self.assertGreaterEqual(level, 1)  # rich fixture must clear at least Level 1
        code, _out2, _err = run(["report", "--project", str(rich), "--min-level", str(level)])
        self.assertEqual(code, 0)


def _schema2_report(ident, ts, *, engine="0.10.0", registry="0.7.0", detector="0.6.0",
                    status="fail", level=2):
    """A minimal valid schema-2 report dict (released tuple, matching score invariants)."""
    results = [{
        "id": "docs.readme", "title": "README", "pillar": "Documentation", "level": 1,
        "scope": "repository", "gating": True, "status": status, "rationale": "r",
        "evidence": [], "app_path": ".", "fixable": False, "fix_kind": "",
        "passed_apps": 0 if status != "pass" else 1, "evaluated_apps": 1,
    }]
    passed = 1 if status == "pass" else 0
    return {
        "schema_version": "2", "engine_version": engine, "registry_version": registry,
        "detector_version": detector, "commit": "", "branch": "main",
        "github_available": False, "generated_at": ts, "repository": ident,
        "detection": None,
        "score": {"level": level, "level_name": "Documented", "pass_rate": passed,
                  "gating_passed": passed, "gating_total": 1,
                  "levels": [{"level": level, "name": n, "passed": 0, "total": 0,
                              "ratio": 0.0, "achieved": False}
                             for level, n in [(1, "Functional"), (2, "Documented"),
                                          (3, "Standardized"), (4, "Optimized"),
                                          (5, "Autonomous")]],
                  "pillars": {}, "recommendations": []},
        "results": results, "advisory": [],
    }


class TestHistoryCommand(unittest.TestCase):
    def _seed(self, root, specs):
        ident = history.repo_identity(str(root))
        for i, (lvl, eng, det) in enumerate(specs):
            rep = _schema2_report(ident, f"2026-06-2{i}T00:00:00+00:00",
                                  engine=eng, detector=det,
                                  status="fail" if i == 0 else "pass", level=lvl)
            source = history.admit_or_create_current_source(
                str(root / ".ra1" / "reports"))
            try:
                history.store_history(rep, source)
            finally:
                source.close()

    def _ids(self, root):
        code, out, _err = run(["history", "list", "--project", str(root)])
        self.assertEqual(code, 0)
        return [e["id"] for e in json.loads(out)["entries"]]

    def test_list_no_history_errors(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        code, out, _err = run(["history", "list", "--project", str(root)])
        self.assertEqual(code, 2)
        self._seed(root, [(2, "0.10.0", "0.6.0"), (3, "0.10.0", "0.6.0")])
        code, out, _err = run(["history", "list", "--project", str(root)])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["entries"]), 2)
        code, md, _err = run(["history", "list", "--project", str(root), "--format", "markdown"])
        self.assertEqual(code, 0)
        self.assertIn("# Readiness History", md)

    def test_diff_json_and_markdown(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed(root, [(2, "0.10.0", "0.6.0"), (3, "0.10.0", "0.6.0")])
        a, b = self._ids(root)
        code, out, _err = run(["history", "diff", "--project", str(root), "--from", a, "--to", b])
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertTrue(d["comparable"])
        self.assertEqual(d["newly_passing"], ["docs.readme"])
        code, md, _err = run(["history", "diff", "--project", str(root), "--from", a, "--to", b,
                        "--format", "markdown"])
        self.assertIn("Level: 2 → 3", md)

    def test_diff_incomparable_versions(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        # engine+detector mismatch is incomparable now (the detector_changed path is gone)
        self._seed(root, [(2, "0.9.1", "0.5.0"), (3, "0.10.0", "0.6.0")])
        ids = self._ids(root)
        code, out, _err = run(["history", "diff", "--project", str(root),
                         "--from", ids[0], "--to", ids[1]])
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertFalse(d["comparable"])
        self.assertIn("version mismatch", d["reason"])
        code, md, _err = run(["history", "diff", "--project", str(root),
                        "--from", ids[0], "--to", ids[1], "--format", "markdown"])
        self.assertIn("Not comparable", md)

    def test_diff_missing_snapshot_errors(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed(root, [(2, "0.10.0", "0.6.0")])
        code, _out2, _err = run([
                       "history",
                       "diff",
                       "--project",
                       str(root),
                       "--from",
                       "nope",
                       "--to",
                       "latest"])
        self.assertEqual(code, 1)


class TestParseExecTimeout(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(cli._parse_exec_timeout("5"), 5)
        self.assertEqual(cli._parse_exec_timeout("3600"), 3600)

    def test_invalid_raises_argument_type_error(self):
        for bad in ("abc", "1.5", "0", "5001", "-3"):
            with self.subTest(bad=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    cli._parse_exec_timeout(bad)


class TestCaptureAuthorities(unittest.TestCase):
    def test_invalid_proxy_env_returns_authored_diagnostic(self):
        from readiness import process
        with mock.patch.object(process, "capture_host_proxy_authority",
                               side_effect=process.HostProxyError("bad")):
            proxy, auth, error = cli._capture_authorities(
                types.SimpleNamespace(host_proxy=True, github=False))
        self.assertIsNone(proxy)
        self.assertIsNone(auth)
        self.assertEqual(error, "ra1: invalid host proxy environment")

    def test_github_auth_captured_when_enabled(self):
        with mock.patch.dict("os.environ", {"GH_TOKEN": "unit-test-token"}):
            proxy, auth, error = cli._capture_authorities(
                types.SimpleNamespace(host_proxy=False, github=True))
        self.assertIsNone(proxy)
        self.assertIsNotNone(auth)
        self.assertEqual(error, "")


class TestReportFlagValidation(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n',
                               "README.md": "# lib"})
        self.addCleanup(rmtree, self.repo)

    def test_report_host_proxy_requires_github(self):
        code, _out, err = run(["report", "--project", str(self.repo), "--host-proxy"])
        self.assertEqual(code, 2)
        self.assertIn("--host-proxy requires --github", err)

    def test_gaps_host_proxy_requires_github(self):
        code, _out, err = run(["gaps", "--project", str(self.repo), "--host-proxy"])
        self.assertEqual(code, 2)
        self.assertIn("--host-proxy requires --github", err)

    def test_report_invalid_proxy_env_exits_1(self):
        from readiness import process
        with mock.patch.object(process, "capture_host_proxy_authority",
                               side_effect=process.HostProxyError("bad")):
            code, _out, err = run(["report", "--project", str(self.repo),
                                   "--github", "--host-proxy"])
        self.assertEqual(code, 1)
        self.assertIn("invalid host proxy environment", err)

    def test_gaps_invalid_proxy_env_exits_1(self):
        from readiness import process
        with mock.patch.object(process, "capture_host_proxy_authority",
                               side_effect=process.HostProxyError("bad")):
            code, _out, err = run(["gaps", "--project", str(self.repo),
                                   "--github", "--host-proxy"])
        self.assertEqual(code, 1)
        self.assertIn("invalid host proxy environment", err)

    def test_cmd_report_invalid_options_exit_2(self):
        args = types.SimpleNamespace(
            host_proxy=False, github=False, format="json", detail="actionable", out=None,
            store_history=False, project=str(self.repo), require_origin=False,
            exec_t3=False, exec_timeout="not-an-int", min_level=None, fail_on=None)
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.cmd_report(args)
        self.assertEqual(code, 2)
        self.assertIn("exec timeout", err.getvalue())

    def test_invalid_canonical_report_exits_1(self):
        from readiness.model import PublicReportValidationError
        with mock.patch.object(cli.report_mod, "render",
                               side_effect=PublicReportValidationError("x")):
            code, _out, err = run(["report", "--project", str(self.repo),
                                   "--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(err, "ra1 report: invalid canonical report\n")

    def test_report_requested_github_incomplete_exits_1(self):
        # no origin remote -> T2 unavailable -> requested evidence is incomplete
        code, _out, err = run(["report", "--project", str(self.repo), "--github"])
        self.assertEqual(code, 1)
        self.assertIn("requested GitHub evidence was incomplete", err)

    def test_gaps_requested_github_incomplete_exits_1(self):
        code, _out, err = run(["gaps", "--project", str(self.repo), "--github"])
        self.assertEqual(code, 1)
        self.assertIn("requested GitHub evidence was incomplete", err)

    def test_report_requested_execution_unsuccessful_exits_1(self):
        code, _out, err = run(["report", "--project", str(self.repo), "--exec"])
        self.assertEqual(code, 1)
        self.assertIn("requested execution evidence was unsuccessful", err)


class TestPersistFailures(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n',
                               "README.md": "# lib"})
        self.addCleanup(rmtree, self.repo)
        self.out_dir = Path(tempfile.mkdtemp(prefix="ar-out-"))
        self.addCleanup(rmtree, self.out_dir)

    def _report(self, *extra):
        return run(["report", "--project", str(self.repo), "--out", str(self.out_dir),
                    *extra])

    def test_output_root_not_admitted(self):
        with mock.patch.object(history, "admit_or_create_root",
                               side_effect=OSError("boom")):
            code, _out, err = self._report()
        self.assertEqual(code, 1)
        self.assertIn("output root could not be admitted", err)

    def test_persistence_busy(self):
        with mock.patch("readiness.safe_io.lock_directory", return_value=False):
            code, _out, err = self._report()
        self.assertEqual(code, 1)
        self.assertIn("persistence busy", err)

    def test_incomplete_generation_refused(self):
        with mock.patch.object(history, "validate_generation",
                               return_value="persistence.incomplete"):
            code, _out, err = self._report()
        self.assertEqual(code, 1)
        self.assertIn("persistence.incomplete", err)

    def test_history_limit_refused(self):
        with mock.patch.object(history, "plan_history_write",
                               side_effect=history.HistoryLimitError("cap")):
            code, _out, err = self._report("--store-history")
        self.assertEqual(code, 1)
        self.assertIn("history limit reached", err)

    def test_history_index_unreadable(self):
        with mock.patch.object(history, "plan_history_write",
                               side_effect=ValueError("bad index")):
            code, _out, err = self._report("--store-history")
        self.assertEqual(code, 1)
        self.assertIn("history index unreadable", err)

    def test_commit_oserror(self):
        with mock.patch("readiness.safe_io.atomic_replace_rooted",
                        side_effect=OSError("disk")):
            code, _out, err = self._report()
        self.assertEqual(code, 1)
        self.assertIn("persistence failed (OSError)", err)


class TestWithinAndIgnoreProof(unittest.TestCase):
    def test_within_realpath_oserror_is_false(self):
        with mock.patch("os.path.realpath", side_effect=OSError("boom")):
            self.assertFalse(cli._within(Path("a"), Path("b")))

    def test_ignore_proof_without_git_is_an_error(self):
        repo = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, repo)
        self.assertIn("not safely isolated", cli._ignore_proof(str(repo)))

    def test_ignore_proof_rejects_ignored_policy_files(self):
        repo = make_repo({"README.md": "# x\n", ".gitignore": ".ra1/\n"})
        self.addCleanup(rmtree, repo)
        _init_git(repo)
        self.assertIn("not safely isolated", cli._ignore_proof(str(repo)))


class TestHistorySourceResolution(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"README.md": "# lib",
                               "pyproject.toml": '[project]\nname="lib"\n'})
        self.addCleanup(rmtree, self.repo)

    def test_list_legacy_mode_requires_explicit_root(self):
        code, _out, err = run(["history", "list", "--project", str(self.repo),
                               "--mode", "legacy"])
        self.assertEqual(code, 2)
        self.assertIn("legacy mode requires an explicit --root", err)

    def test_list_legacy_mode_with_existing_root(self):
        legacy = Path(tempfile.mkdtemp(prefix="ar-legacy-"))
        self.addCleanup(rmtree, legacy)
        code, _out, err = run(["history", "list", "--project", str(self.repo),
                               "--mode", "legacy", "--root", str(legacy)])
        self.assertEqual(code, 1)  # admitted, but no history for this repo there
        self.assertIn("no readiness history", err)

    def test_diff_common_legacy_mode_requires_root(self):
        code, _out, err = run(["history", "diff", "--project", str(self.repo),
                               "--from", "a", "--mode", "legacy"])
        self.assertEqual(code, 2)
        self.assertIn("legacy mode requires an explicit --root", err)

    def test_diff_side_specific_source_is_all_or_none(self):
        code, _out, err = run(["history", "diff", "--project", str(self.repo),
                               "--from", "a", "--from-mode", "current"])
        self.assertEqual(code, 2)
        self.assertIn("all-or-none", err)

    def test_diff_side_specific_conflicts_with_common(self):
        code, _out, err = run(["history", "diff", "--project", str(self.repo),
                               "--from", "a", "--from-mode", "current",
                               "--from-root", "x", "--mode", "current"])
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", err)

    def test_diff_side_specific_unsafe_root(self):
        code, _out, err = run(["history", "diff", "--project", str(self.repo),
                               "--from", "a", "--from-mode", "current",
                               "--from-root", "/nonexistent-ra1-xyz"])
        self.assertEqual(code, 2)
        self.assertIn("does not exist or is unsafe", err)

    def test_diff_to_side_error_closes_from_source(self):
        (self.repo / ".ra1" / "reports").mkdir(parents=True)
        code, _out, err = run(["history", "diff", "--project", str(self.repo),
                               "--from", "a", "--to-mode", "legacy"])
        self.assertEqual(code, 2)
        self.assertIn("all-or-none", err)


class TestFixFlagValidation(unittest.TestCase):
    def test_host_proxy_requires_apply_and_github(self):
        code, _out, err = run(["fix", "--host-proxy"])
        self.assertEqual(code, 2)
        self.assertIn("--host-proxy requires --apply --github", err)
        code, _out, err = run(["fix", "--apply", "--host-proxy"])
        self.assertEqual(code, 2)
        self.assertIn("--host-proxy requires --apply --github", err)

    def test_github_requires_apply(self):
        code, _out, err = run(["fix", "--github"])
        self.assertEqual(code, 2)
        self.assertIn("--github is valid only with --apply", err)


class TestAnswerValidation(unittest.TestCase):
    def test_minutes_and_choice_are_mutually_exclusive(self):
        code, _out, err = run(["answer", "--gap-id", "config.ci_budget_minutes",
                               "--minutes", "5", "--choice", "boolean.yes"])
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", err)

    def test_minutes_range(self):
        for bad in ("0", "1441"):
            code, _out, err = run(["answer", "--gap-id", "config.ci_budget_minutes",
                                   "--minutes", bad])
            self.assertEqual(code, 2)
            self.assertIn("1..1440", err)

    def test_format_must_be_json(self):
        code, _out, err = run(["answer", "--gap-id", "config.loop_ready",
                               "--choice", "boolean.no", "--format", "markdown"])
        self.assertEqual(code, 2)
        self.assertIn("unsupported format", err)

    def test_valid_invocation_dispatches(self):
        with mock.patch("readiness.answers.run_answer", return_value=0) as mocked:
            code, _out, _err = run(["answer", "--gap-id", "config.loop_ready",
                                    "--choice", "boolean.no"])
        self.assertEqual(code, 0)
        self.assertEqual(mocked.call_count, 1)


class TestMainSafeIoGate(unittest.TestCase):
    def test_operational_command_fails_closed_when_unsupported(self):
        with mock.patch("readiness.safe_io.safe_io_supported", return_value=False):
            code, _out, err = run(["detect"])
        self.assertEqual(code, 1)
        self.assertIn("safe_io_unsupported", err)

    def test_non_operational_command_remains_available(self):
        with mock.patch("readiness.safe_io.safe_io_supported", return_value=False):
            code, out, _err = run(["version"])
        self.assertEqual(code, 0)
        self.assertIn("engine_version", json.loads(out))


if __name__ == "__main__":
    unittest.main()
