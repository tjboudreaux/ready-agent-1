import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout

from readiness import cli
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.context import Context
from readiness.detect import detect
from readiness.model import App, Detection

from tests._util import fake_runner, gh_runner, make_repo, rmtree


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
        d = self._detect({"pyproject.toml": '[project]\nname="t"\n[project.scripts]\nt = "t:main"\n'
                                            })
        self.assertEqual(d.project_type, "cli")

    def test_data_pipeline(self):
        d = self._detect({"pyproject.toml": '[project]\nname="p"\ndependencies=["apache-airflow"]\n'
                                            })
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
            self._detect({"pyproject.toml": '[project]\nname="x"\ndependencies=["pytest"]\n'
                                            }).apps[0].test_cmd,
            "pytest",
        )
        self.assertEqual(
            self._detect({"package.json": '{"name":"x","scripts":{"test":"jest"}}'
                                          }).apps[0].test_cmd,
            "npm test",
        )


class TestGithubBranches(unittest.TestCase):
    def test_unavailable_without_origin_identity(self):
        """No safe github.com origin identity: every T2 fact is unavailable, never a guess."""
        gh = GithubCollector("/tmp/x", runner=gh_runner({}))
        self.assertFalse(gh.available)
        self.assertEqual(gh.repo().state, "unavailable")
        self.assertEqual(gh.default_branch().state, "unavailable")
        self.assertEqual(gh.secret_scanning_enabled().state, "unavailable")
        self.assertEqual(gh.branch_protected().state, "unavailable")
        self.assertFalse(gh.collection_complete)

    def test_unprotected_branch_is_a_confirmed_absent(self):
        """An exact 404 on the protection endpoint is present(False), not an error."""
        gh = GithubCollector("/tmp/x", origin=("github.com", "o", "r"), runner=gh_runner({
            "repos/o/r": '{"full_name":"o/r","default_branch":"main"}',
        }))
        self.assertTrue(gh.available)
        protected = gh.branch_protected()
        self.assertEqual(protected.state, "present")
        self.assertFalse(protected.value)

    def test_topics_fallback_to_repo(self):
        gh = GithubCollector("/tmp/x", origin=("github.com", "o", "r"), runner=gh_runner({
            "repos/o/r/topics": "{}",
            "repos/o/r": '{"full_name":"o/r","topics":["fallback-topic"]}',
        }))
        topics = gh.topics()
        self.assertEqual(topics.state, "present")
        self.assertEqual(topics.value, ("fallback-topic",))

    def test_malformed_collections_are_unreadable_not_empty(self):
        """A wrong-shape body can no longer masquerade as a confirmed empty list."""
        gh = GithubCollector("/tmp/x", origin=("github.com", "o", "r"), runner=gh_runner({
            "repos/o/r": '{"full_name":"o/r"}',
            "repos/o/r/actions/workflows?per_page=100": "[]",
            "repos/o/r/actions/runs?per_page=20": "{}",
            "repos/o/r/labels?per_page=100": "{}",
            "repos/o/r/issues?state=open&per_page=50": "{}",
        }))
        self.assertEqual(gh.workflows().state, "unreadable")
        self.assertEqual(gh.recent_runs().state, "unreadable")
        self.assertEqual(gh.labels().state, "unreadable")
        self.assertEqual(gh.open_issues().state, "unreadable")
        self.assertFalse(gh.collection_complete)


class TestGitBranches(unittest.TestCase):
    def test_missing_outputs(self):
        g = GitCollector("/tmp/x", runner=fake_runner({
            ("rev-parse", "--is-inside-work-tree"): "true\n",
            ("rev-parse", "HEAD"): "sha\n",
        }))
        # Unmapped argv exits 128: a per-file log is a confirmed absence, while a bare
        # commit-date query is unreadable — the two states are never collapsed into None.
        self.assertEqual(g.file_last_commit_iso("README.md").state, "absent")
        self.assertEqual(g.most_recent_commit_iso().state, "unreadable")
        # cache hit on repeated call
        self.assertIs(g.head_sha(), g.head_sha())
        self.assertEqual(g.head_sha().value, "sha")


class TestCliBranches(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n'})
        self.addCleanup(rmtree, self.repo)

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_fix_plan_mode_is_source_less(self):
        # plan mode runs a fresh in-memory scan — no stored report required
        code = cli.main(["fix", "--project", str(self.repo)])
        self.assertEqual(code, 0)

    def test_markdown_renders(self):
        code, out = self._run(["report", "--project", str(self.repo),
                               "--format", "markdown"])
        self.assertEqual(code, 0)
        self.assertIn("# Agent Readiness Report", out)

    def test_fail_on_with_no_results_is_clean(self):
        code, _ = self._run(["report", "--project", str(self.repo),
                             "--fail-on", "x.y"])
        self.assertEqual(code, 0)


class TestRealGitIntegration(unittest.TestCase):
    """End-to-end: real git repo, real (non-injected) GitCollector + CLI."""

    def test_real_repo(self):
        repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n'
                                            , "README.md": "# lib"})
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
        try:
            self.assertTrue(g.available())
            self.assertEqual(len(g.head_sha().value), 40)
            self.assertEqual(g.commit_count().value, 1)
            self.assertTrue(g.has_agent_coauthorship().value)
            self.assertEqual(g.most_recent_commit_iso().state, "present")
        finally:
            g.close()

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["report", "--project", str(repo), "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(len(data["commit"]), 40)
        self.assertEqual(data["schema_version"], "3")


if __name__ == "__main__":
    unittest.main()
