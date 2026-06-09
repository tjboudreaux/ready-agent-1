import unittest

from readiness.collectors.static import StaticCollector
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from tests._util import make_repo, rmtree, fake_runner


class TestStaticCollector(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({
            "package.json": '{"name":"x","dependencies":{"express":"^4"},"devDependencies":{"eslint":"^9"}}',
            "pyproject.toml": '[project]\nname="y"\ndependencies=["requests>=2"]\n[tool.ruff]\nline-length=100\n',
            ".eslintrc.json": "{}",
            ".gitignore": "node_modules/\n# comment\n.env\n",
            "package-lock.json": "{}",
            "src/app.test.ts": "test",
        })
        self.addCleanup(rmtree, self.root)
        self.c = StaticCollector(self.root)

    def test_glob_and_exists(self):
        self.assertIn(".eslintrc.json", self.c.glob([".eslintrc*"]))
        self.assertEqual(self.c.exists_any(["nope*", ".eslintrc*"]), ".eslintrc.json")
        self.assertIsNone(self.c.exists_any(["does-not-exist*"]))
        self.assertIn("src/app.test.ts", self.c.glob(["**/*.test.ts"]))

    def test_glob_ignores_vendor_dirs(self):
        (self.root / "node_modules" / "p").mkdir(parents=True)
        (self.root / "node_modules" / "p" / "package.json").write_text("{}")
        self.assertNotIn("node_modules/p/package.json", self.c.glob(["**/package.json"]))

    def test_manifests_and_languages(self):
        self.assertIn("package.json", self.c.manifests())
        self.assertIn("npm", self.c.languages())
        self.assertIn("python", self.c.languages())

    def test_declared_deps_and_has_dep(self):
        deps = self.c.declared_deps()
        self.assertIn("express", deps)
        self.assertIn("eslint", deps)
        self.assertIn("requests", deps)
        self.assertEqual(self.c.has_dep(["eslint", "ruff"]), "eslint")
        self.assertIsNone(self.c.has_dep("nonexistent-pkg"))

    def test_has_tool_config(self):
        self.assertTrue(self.c.has_tool_config("ruff"))
        self.assertFalse(self.c.has_tool_config("black"))

    def test_lockfiles_and_gitignore(self):
        self.assertIn("package-lock.json", self.c.lockfiles())
        patterns = self.c.gitignore_patterns()
        self.assertIn(".env", patterns)
        self.assertNotIn("# comment", patterns)

    def test_within(self):
        self.assertIs(self.c.within("."), self.c)
        sub = self.c.within("src")
        self.assertTrue(str(sub.root).endswith("src"))


class TestGitCollector(unittest.TestCase):
    def test_history_facts(self):
        runner = fake_runner({
            ("rev-parse", "--is-inside-work-tree"): "true\n",
            ("rev-parse", "HEAD"): "abc123\n",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
            ("rev-list", "--count", "HEAD"): "42\n",
            ("log", "-3", "--format=%cI"): "2026-06-01T00:00:00+00:00\n2026-05-01T00:00:00+00:00\n2026-04-01T00:00:00+00:00\n",
            ("log", "-100", "--format=%an%n%ae%n%B%n==="): "Travis\nt@x\nfix\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n===\n",
            ("tag",): "v1.0.0\nv1.1.0\n",
            ("log", "-1", "--format=%cI", "--", "README.md"): "2026-06-01T00:00:00+00:00\n",
        })
        g = GitCollector("/tmp/whatever", runner=runner)
        self.assertTrue(g.available())
        self.assertEqual(g.head_sha(), "abc123")
        self.assertEqual(g.branch(), "main")
        self.assertEqual(g.commit_count(), 42)
        self.assertEqual(len(g.commit_dates(3)), 3)
        self.assertTrue(g.has_agent_coauthorship())
        self.assertEqual(g.tags(), ["v1.0.0", "v1.1.0"])
        self.assertEqual(g.file_last_commit_iso("README.md"), "2026-06-01T00:00:00+00:00")

    def test_unavailable_repo(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({}))
        self.assertFalse(g.available())
        self.assertEqual(g.head_sha(), "")
        self.assertEqual(g.commit_count(), 0)
        self.assertFalse(g.has_agent_coauthorship())


class TestGithubCollector(unittest.TestCase):
    def _gh(self, extra=None):
        responses = {
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
            ("api", "repos/o/r"): (
                '{"default_branch":"main","topics":["agent-skills"],'
                '"security_and_analysis":{"secret_scanning":{"status":"enabled"}}}'
            ),
            ("api", "repos/o/r/topics"): '{"names":["agent-skills","python"]}',
            ("api", "repos/o/r/branches/main/protection"): '{"required_pull_request_reviews":{}}',
            ("api", "repos/o/r/actions/workflows"): '{"workflows":[{"name":"ci","path":".github/workflows/ci.yml"}]}',
            ("api", "repos/o/r/actions/runs?per_page=20"): '{"workflow_runs":[{"conclusion":"success"}]}',
            ("api", "repos/o/r/labels?per_page=100"): '[{"name":"bug"},{"name":"enhancement"}]',
            ("api", "repos/o/r/issues?state=open&per_page=50"): '[{"number":1,"labels":[{"name":"bug"}]},{"number":2,"pull_request":{}}]',
        }
        if extra:
            responses.update(extra)
        return GithubCollector("/tmp/x", runner=fake_runner(responses))

    def test_available_and_facts(self):
        gh = self._gh()
        self.assertTrue(gh.available)
        self.assertEqual(gh.slug, "o/r")
        self.assertEqual(gh.default_branch(), "main")
        self.assertIn("agent-skills", gh.topics())
        self.assertTrue(gh.branch_protected())
        self.assertTrue(gh.secret_scanning_enabled())
        self.assertEqual(len(gh.workflows()), 1)
        self.assertEqual(len(gh.recent_runs()), 1)
        self.assertEqual(gh.labels(), ["bug", "enhancement"])
        issues = gh.open_issues()
        self.assertEqual(len(issues), 1)  # the PR is filtered out

    def test_unavailable(self):
        gh = GithubCollector("/tmp/x", runner=fake_runner({}))
        self.assertFalse(gh.available)
        self.assertIsNone(gh.slug)
        self.assertEqual(gh.topics(), [])
        self.assertIsNone(gh.branch_protected())
        self.assertEqual(gh.workflows(), [])
        self.assertEqual(gh.open_issues(), [])

    def test_branch_protection_absent_returns_false(self):
        # available repo but no protection object -> False (not None/unknown)
        gh = self._gh()
        gh._cache[("api", "repos/o/r/branches/main/protection")] = None
        self.assertFalse(gh.branch_protected())


if __name__ == "__main__":
    unittest.main()
