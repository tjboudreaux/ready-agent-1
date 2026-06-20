import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout

from readiness import cli, history
from tests._util import make_repo, rmtree


def run(argv):
    """Run the CLI, returning (exit_code, stdout)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


BASE_NON_LOOP = {
    "README.md": "# Project\n\n## Setup\n\n```sh\npython -m unittest\n```\n\n" + ("Maintainer detail. " * 30),
    "pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n[tool.ruff]\nline-length=100\n',
    ".gitignore": ".env\n.env.*\n*.pem\n__pycache__/\nnode_modules/\ndist/\nbuild/\n.venv/\n",
    ".github/workflows/readiness.yml": "name: readiness\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n",
    ".github/ISSUE_TEMPLATE/bug_report.md": "---\nname: Bug report\n---\n",
    ".github/pull_request_template.md": "## Summary\n\nDescribe changes and test coverage for reviewers.\n",
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
    "loop-runs/README.md": "# Loop Runs\n\nThis directory records loop attempts and evidence for maintainers.\n",
    ".omp/rules/README.md": "# Loop Rules\n\nThis rules index links to the denylist and safety policies.\n",
    ".omp/rules/denylist.md": "# Loop Denylist\n\n- Never mutate secrets or deploy without confirmation.\n",
    "signals/README.md": "# Signal Schema\n\n```json\n{\"schema_version\":\"1\",\"signal\":\"loop.run\",\"source\":\"runner\",\"timestamp\":\"2026-01-01T00:00:00Z\",\"evidence\":[]}\n```\n",
    ".omp/commands/pr-artifact-template.md": "# PR Evidence\n\nCite artifact evidence, CI logs, screenshots, and loop-runs records.\n",
    ".omp/commands/goal.md": "# Goal Contract\n\nCapture the loop goal, boundaries, evidence requirements, and owner.\n",
    ".omp/commands/loop.md": "# Loop Contract\n\nCapture loop iteration rules, stop conditions, evidence, and escalation.\n",
    "ARCHITECTURE.md": "# Architecture\n\nDocument the system shape, critical paths, and ownership boundaries.\n",
    "domains/billing/README.md": "# Billing Domain\n\nDocument domain vocabulary, invariants, workflows, and maintainer contacts.\n",
    ".omp/skills/a/SKILL.md": "---\nname: a\ndescription: loop skill artifact\n---\n# A\n\nFilled loop skill artifact for maintainers.\n",
    ".omp/skills/b/SKILL.md": "---\nname: b\ndescription: loop skill artifact\n---\n# B\n\nFilled loop skill artifact for maintainers.\n",
    ".omp/skills/c/SKILL.md": "---\nname: c\ndescription: loop skill artifact\n---\n# C\n\nFilled loop skill artifact for maintainers.\n",
}

LOOP_CONFIG = {".agents/readiness/config.json": json.dumps({"schema_version": "1", "loop_ready": True})}


def _loop_results(data):
    return {r["id"]: r for r in data["results"] if r["id"].startswith("loop.")}


class TestCli(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n', "README.md": "# lib"})
        self.addCleanup(rmtree, self.repo)

    def test_report_json(self):
        code, out = run(["report", "--project", str(self.repo), "--no-github", "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["detection"]["project_type"], "library")
        self.assertIn("engine_version", data)
        self.assertFalse(data["github_available"])

    def test_report_writes_out_dir(self):
        out_dir = self.repo / "_out"
        code, _ = run(["report", "--project", str(self.repo), "--no-github", "--out", str(out_dir)])
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "report.json").exists())
        self.assertTrue((out_dir / "latest.json").exists())
        json.loads((out_dir / "latest.json").read_text())  # valid JSON

    def test_version(self):
        code, out = run(["version"])
        self.assertEqual(code, 0)
        self.assertIn("engine_version", json.loads(out))

    def test_detect(self):
        code, out = run(["detect", "--project", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["project_type"], "library")

    def test_formats(self):
        code, out = run(["formats"])
        self.assertEqual(code, 0)
        self.assertIn("json", out)

    def test_min_level_gate_fails_when_unreachable(self):
        code, _ = run(["report", "--project", str(self.repo), "--no-github", "--min-level", "6"])
        self.assertEqual(code, 1)

    def test_no_min_level_passes(self):
        code, _ = run(["report", "--project", str(self.repo), "--no-github"])
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
            code, out = run(["report", "--project", str(repo), "--no-github", "--format", "json"])
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
            self.assertEqual(reports["opt_in_missing"]["score"][field], reports["opt_out_missing"]["score"][field])


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
        code, out = run([])
        self.assertEqual(code, 0)
        self.assertIn("insert coin", out)

    def test_banner_command(self):
        code, out = run(["banner"])
        self.assertEqual(code, 0)
        self.assertIn("insert coin", out)


class TestReportIdentityAndHistory(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"README.md": "# lib", "pyproject.toml": '[project]\nname="lib"\n'})
        self.addCleanup(rmtree, self.repo)

    def test_require_origin_without_origin_exits_nonzero(self):
        code, _ = run(["report", "--project", str(self.repo), "--no-github", "--require-origin"])
        self.assertEqual(code, 1)

    def test_origin_identity_is_redacted(self):
        _init_git(self.repo, origin="https://user:secrettoken@github.com/acme/widget.git")
        code, out = run(["report", "--project", str(self.repo), "--no-github",
                         "--require-origin", "--format", "json"])
        self.assertEqual(code, 0)
        repo = json.loads(out)["repository"]
        self.assertEqual(repo["identity_kind"], "origin")
        self.assertEqual(repo["host"], "github.com")
        self.assertNotIn("secrettoken", out)

    def test_store_history_local_identity_and_resolve(self):
        code, _ = run(["report", "--project", str(self.repo), "--no-github", "--store-history"])
        self.assertEqual(code, 0)
        latest = self.repo / ".agents" / "readiness" / "latest.json"
        self.assertTrue(latest.exists())
        report = json.loads(latest.read_text())
        self.assertEqual(report["repository"]["identity_kind"], "local_path")
        self.assertNotIn(str(self.repo), json.dumps(report["repository"]))
        # the same canonical store resolves the latest report by identity (fix --latest uses this)
        resolved, reason = history.resolve_latest(str(self.repo))
        self.assertEqual(reason, "")
        self.assertEqual(resolved["repository"]["identity_hash"],
                         report["repository"]["identity_hash"])

    def test_store_history_with_out_dir(self):
        out_dir = self.repo / "_out"
        code, _ = run(["report", "--project", str(self.repo), "--no-github",
                       "--store-history", "--out", str(out_dir)])
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "latest.json").exists())
        self.assertTrue((out_dir / "history").exists())

    def test_min_level_satisfied_passes(self):
        rich = make_repo(BASE_NON_LOOP)
        self.addCleanup(rmtree, rich)
        code, out = run(["report", "--project", str(rich), "--no-github", "--format", "json"])
        level = json.loads(out)["score"]["level"]
        self.assertGreaterEqual(level, 1)  # rich fixture must clear at least Level 1
        code, _ = run(["report", "--project", str(rich), "--no-github", "--min-level", str(level)])
        self.assertEqual(code, 0)

class TestHistoryCommand(unittest.TestCase):
    def _seed(self, root, specs):
        for i, (lvl, eng, det) in enumerate(specs):
            ident = history.repo_identity(str(root))
            rep = {"schema_version": "2", "engine_version": eng, "registry_version": "0.4.0",
                   "detector_version": det, "generated_at": f"2026-06-2{i}T00:00:00+00:00",
                   "commit": f"c{i}", "repository": ident,
                   "score": {"level": lvl, "pass_rate": 0.5, "gating_passed": lvl, "gating_total": 10},
                   "results": [{"id": "docs.readme", "status": "fail" if i == 0 else "pass"}]}
            history.store_history(rep, str(root))

    def _ids(self, root):
        code, out = run(["history", "list", "--project", str(root)])
        self.assertEqual(code, 0)
        return [e["id"] for e in json.loads(out)["entries"]]

    def test_list_no_history_errors(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        # local-path identity always resolves; with no history the entries list is simply empty
        code, out = run(["history", "list", "--project", str(root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["entries"], [])
        code, md = run(["history", "list", "--project", str(root), "--format", "markdown"])
        self.assertIn("_(none)_", md)
        self._seed(root, [(2, "0.4.0", "0.4.0"), (3, "0.4.0", "0.4.0")])
        code, out = run(["history", "list", "--project", str(root)])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["entries"]), 2)
        code, md = run(["history", "list", "--project", str(root), "--format", "markdown"])
        self.assertEqual(code, 0)
        self.assertIn("# Readiness History", md)

    def test_diff_json_and_markdown(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed(root, [(2, "0.4.0", "0.4.0"), (3, "0.4.0", "0.4.0")])
        a, b = self._ids(root)
        code, out = run(["history", "diff", "--project", str(root), "--from", a, "--to", b])
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertTrue(d["comparable"])
        self.assertEqual(d["newly_passing"], ["docs.readme"])
        code, md = run(["history", "diff", "--project", str(root), "--from", a, "--to", b,
                        "--format", "markdown"])
        self.assertIn("Level: 2 → 3", md)

    def test_diff_incomparable_and_detector_change(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        # engine mismatch -> incomparable; detector mismatch -> comparable but flagged
        self._seed(root, [(2, "0.3.0", "0.4.0"), (3, "0.4.0", "0.4.0"), (3, "0.4.0", "0.5.0")])
        ids = self._ids(root)
        code, md = run(["history", "diff", "--project", str(root), "--from", ids[0], "--to", ids[1],
                        "--format", "markdown"])
        self.assertIn("Not comparable", md)
        code, out = run(["history", "diff", "--project", str(root), "--from", ids[1], "--to", ids[2]])
        self.assertTrue(json.loads(out)["detector_changed"])
        code, md = run(["history", "diff", "--project", str(root), "--from", ids[1], "--to", ids[2],
                        "--format", "markdown"])
        self.assertIn("detector version changed", md)

    def test_diff_missing_snapshot_errors(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed(root, [(2, "0.4.0", "0.4.0")])
        code, _ = run(["history", "diff", "--project", str(root), "--from", "nope", "--to", "latest"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
