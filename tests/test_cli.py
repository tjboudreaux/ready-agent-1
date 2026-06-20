import io
import json
import unittest
from contextlib import redirect_stdout

from readiness import cli
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


if __name__ == "__main__":
    unittest.main()
