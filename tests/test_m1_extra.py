import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout

from readiness import cli
from readiness.context import Context
from readiness.collectors.static import StaticCollector
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.detect import detect
from readiness.model import App, Detection
from tests._util import make_repo, rmtree, fake_runner


class TestContext(unittest.TestCase):
    def test_app_static_scoping(self):
        root = make_repo({"sub/app/package.json": "{}"})
        self.addCleanup(rmtree, root)
        ctx = Context(
            root=root, detection=Detection(), static=StaticCollector(root),
            git=GitCollector(root, runner=fake_runner({})),
            github=GithubCollector(root, runner=fake_runner({})),
            app=App(path="sub/app"),
        )
        self.assertTrue(str(ctx.app_static().root).endswith("sub/app"))
        ctx_root = Context(
            root=root, detection=Detection(), static=ctx.static,
            git=ctx.git, github=ctx.github, app=App(path="."),
        )
        self.assertIs(ctx_root.app_static(), ctx_root.static)


class TestDetectBranches(unittest.TestCase):
    def _detect(self, files):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        return detect(root)

    def test_cli_via_pyproject_scripts(self):
        d = self._detect({"pyproject.toml": '[project]\nname="t"\n[project.scripts]\nt = "t:main"\n'})
        self.assertEqual(d.project_type, "cli")

    def test_data_pipeline(self):
        d = self._detect({"pyproject.toml": '[project]\nname="p"\ndependencies=["apache-airflow"]\n'})
        self.assertEqual(d.project_type, "data")

    def test_monorepo_via_turbo_tooling(self):
        d = self._detect({
            "turbo.json": "{}",
            "apps/web/package.json": '{"name":"web","dependencies":{"next":"^14"}}',
        })
        self.assertTrue(d.is_monorepo)
        self.assertEqual([a.path for a in d.apps], ["apps/web"])
        self.assertEqual(d.apps[0].deploy_surface, "frontend")

    def test_test_cmd_variants(self):
        self.assertEqual(self._detect({"go.mod": "module x\n"}).apps[0].test_cmd, "go test ./...")
        self.assertEqual(
            self._detect({"Cargo.toml": '[package]\nname="x"\nversion="0.1.0"\n'}).apps[0].test_cmd,
            "cargo test",
        )
        self.assertEqual(
            self._detect({"pyproject.toml": '[project]\nname="x"\ndependencies=["pytest"]\n'}).apps[0].test_cmd,
            "pytest",
        )
        self.assertEqual(
            self._detect({"package.json": '{"name":"x","scripts":{"test":"jest"}}'}).apps[0].test_cmd,
            "npm test",
        )


class TestGithubBranches(unittest.TestCase):
    def test_repo_none_paths(self):
        gh = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
        }))
        self.assertTrue(gh.available)
        self.assertIsNone(gh.repo())
        self.assertIsNone(gh.default_branch())
        self.assertIsNone(gh.secret_scanning_enabled())
        self.assertFalse(gh.branch_protected())  # available but no protection object

    def test_topics_fallback_to_repo(self):
        gh = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
            ("api", "repos/o/r"): '{"topics":["fallback-topic"]}',
        }))
        self.assertEqual(gh.topics(), ["fallback-topic"])

    def test_malformed_collections_return_empty(self):
        gh = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
            ("api", "repos/o/r/actions/workflows"): "[]",
            ("api", "repos/o/r/actions/runs?per_page=20"): "{}",
            ("api", "repos/o/r/labels?per_page=100"): "{}",
            ("api", "repos/o/r/issues?state=open&per_page=50"): "{}",
        }))
        self.assertEqual(gh.workflows(), [])
        self.assertEqual(gh.recent_runs(), [])
        self.assertEqual(gh.labels(), [])
        self.assertEqual(gh.open_issues(), [])


class TestGitBranches(unittest.TestCase):
    def test_missing_outputs(self):
        g = GitCollector("/tmp/x", runner=fake_runner({
            ("rev-parse", "--is-inside-work-tree"): "true\n",
            ("rev-parse", "HEAD"): "sha\n",
        }))
        self.assertIsNone(g.file_last_commit_iso("README.md"))
        self.assertIsNone(g.most_recent_commit_iso())
        # cache hit on repeated call
        self.assertEqual(g.head_sha(), g.head_sha())


class TestCliBranches(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n'})
        self.addCleanup(rmtree, self.repo)

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_fix_without_module_returns_2(self):
        import sys
        code = cli.main(["fix", "--project", str(self.repo)])  # no fix module in M1
        self.assertEqual(code, 2)
        del sys

    def test_markdown_falls_back_when_no_renderer(self):
        # M1 has no report renderer module; markdown should fall back to JSON, not crash.
        code, out = self._run(["report", "--project", str(self.repo), "--no-github", "--format", "markdown"])
        self.assertEqual(code, 0)
        json.loads(out)

    def test_fail_on_with_no_results_is_clean(self):
        code, _ = self._run(["report", "--project", str(self.repo), "--no-github", "--fail-on", "x.y"])
        self.assertEqual(code, 0)


class TestRealGitIntegration(unittest.TestCase):
    """End-to-end: real git repo, real (non-injected) GitCollector + CLI."""

    def test_real_repo(self):
        repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n', "README.md": "# lib"})
        self.addCleanup(rmtree, repo)
        env_cmds = [
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "Test"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "init\n\nCo-Authored-By: Claude <noreply@anthropic.com>"],
        ]
        for cmd in env_cmds:
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True)

        g = GitCollector(repo)
        self.assertTrue(g.available())
        self.assertEqual(len(g.head_sha()), 40)
        self.assertEqual(g.commit_count(), 1)
        self.assertTrue(g.has_agent_coauthorship())
        self.assertIsNotNone(g.most_recent_commit_iso())

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["report", "--project", str(repo), "--no-github"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(len(data["commit"]), 40)


if __name__ == "__main__":
    unittest.main()
