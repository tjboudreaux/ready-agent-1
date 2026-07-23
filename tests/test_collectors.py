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



    def test_recent_churn_filters_binary_and_lockfiles(self):
        blob = (
            "abc123\n"
            "10\t5\tsrc/a.py\n"
            "-\t-\timg.png\n"
            "100\t50\tpackage-lock.json\n"
            "20\t10\tnode_modules/pkg/index.js\n"
            "15\t5\tsrc/b.py\n"
            "def456\n"
            "30\t10\tsrc/c.py\n"
        )
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): blob,
        }))
        self.assertEqual(g.recent_churn(50), [35, 40])

    def test_commit_count_for_follow_and_dir(self):
        root = make_repo({"AGENTS.md": "# Agents\n"})
        self.addCleanup(rmtree, root)
        g_file = GitCollector(root, runner=fake_runner({
            ("rev-list", "--count", "--follow", "HEAD", "--", "AGENTS.md"): "7\n",
        }))
        self.assertEqual(g_file.commit_count_for("AGENTS.md"), 7)
        g_dir = GitCollector(root, runner=fake_runner({
            ("rev-list", "--count", "HEAD", "--", ".claude"): "4\n",
        }))
        self.assertEqual(g_dir.commit_count_for(".claude"), 4)
        g_fail = GitCollector(root, runner=fake_runner({}))
        self.assertEqual(g_fail.commit_count_for("AGENTS.md"), 0)


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


    def test_recent_merged_prs_filters_and_paginates(self):
        import json

        page1 = [
            {"number": 1, "merged_at": "2026-06-01T00:00:00Z", "title": "m1"},
            {"number": 2, "merged_at": None, "title": "closed unmerged"},
            {"number": 3, "merged_at": "2026-06-02T00:00:00Z", "title": "m2"},
        ]
        page2 = [
            {"number": 4, "merged_at": "2026-06-03T00:00:00Z", "title": "m3"},
        ]
        gh = self._gh({
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1"):
                json.dumps(page1),
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2"):
                json.dumps(page2),
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=3"):
                "[]",
        })
        merged = gh.recent_merged_prs(20)
        self.assertEqual([p["number"] for p in merged], [1, 3, 4])

        empty = GithubCollector("/tmp/x", runner=fake_runner({}))
        self.assertEqual(empty.recent_merged_prs(), [])
        err = self._gh()  # available but no pulls API payload
        self.assertEqual(err.recent_merged_prs(), [])

    def test_pr_first_review_iso(self):
        import json

        gh = self._gh({
            ("api", "repos/o/r/pulls/1/reviews"): json.dumps([
                {"submitted_at": "2026-06-02T00:00:00Z"},
                {"submitted_at": "2026-06-01T12:00:00Z"},
            ]),
        })
        self.assertEqual(gh.pr_first_review_iso(1), "2026-06-01T12:00:00Z")

        empty = GithubCollector("/tmp/x", runner=fake_runner({}))
        self.assertIsNone(empty.pr_first_review_iso(1))
        err = self._gh()
        self.assertIsNone(err.pr_first_review_iso(9))
        no_reviews = self._gh({
            ("api", "repos/o/r/pulls/2/reviews"): "[]",
        })
        self.assertIsNone(no_reviews.pr_first_review_iso(2))




class TestGitCollectorCoverageGaps(unittest.TestCase):
    def test_commit_count_value_error(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("rev-list", "--count", "HEAD"): "not-an-int\n",
        }))
        self.assertEqual(g.commit_count(), 0)

    def test_recent_churn_edge_branches(self):
        from readiness.collectors import git as gitmod

        blob = (
            "10\t5\tsrc/before_hash.py\n"  # numstat before hash → current is None path
            "abc123\n"
            "1\t2\n"  # parts < 3
            "foo\tbar\tsrc/bad.py\n"  # non-int added/deleted
            "3\t4\tsrc/ok.py\n"
        )
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): blob,
        }))
        self.assertEqual(g.recent_churn(50), [15, 7])
        self.assertTrue(gitmod._churn_path_excluded("vendor/x.py"))
        self.assertTrue(gitmod._churn_path_excluded("pkg/yarn.lock"))
        self.assertFalse(gitmod._churn_path_excluded("src/a.py"))

        # No hash and only skipped rows → current stays None (false branch before return).
        skipped_only = (
            "\n"
            "1\t2\n"
            "-\t-\timg.png\n"
            "100\t50\tpackage-lock.json\n"
        )
        g2 = GitCollector("/tmp/whatever", runner=fake_runner({
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): skipped_only,
        }))
        self.assertEqual(g2.recent_churn(50), [])

    def test_commit_count_for_value_error(self):
        root = make_repo({"AGENTS.md": "# Agents\n"})
        self.addCleanup(rmtree, root)
        g = GitCollector(root, runner=fake_runner({
            ("rev-list", "--count", "--follow", "HEAD", "--", "AGENTS.md"): "nope\n",
        }))
        self.assertEqual(g.commit_count_for("AGENTS.md"), 0)


class TestGithubCollectorCoverageGaps(unittest.TestCase):
    def test_api_and_available_bad_json(self):
        gh = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): "not-json",
        }))
        self.assertFalse(gh.available)
        self.assertIsNone(gh.slug)
        self.assertIsNone(gh.repo())
        self.assertEqual(gh.recent_runs(), [])
        self.assertEqual(gh.labels(), [])

        bad_api = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
            ("api", "repos/o/r"): "not-json{",
        }))
        self.assertTrue(bad_api.available)
        self.assertIsNone(bad_api.repo())

    def test_topics_fallback_empty(self):
        gh = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
            ("api", "repos/o/r/topics"): "{}",
            ("api", "repos/o/r"): "{}",
        }))
        self.assertEqual(gh.topics(), [])

    def test_recent_merged_prs_early_return(self):
        import json

        page1 = [
            {"number": i, "merged_at": "2026-06-01T00:00:00Z"}
            for i in range(1, 6)
        ]
        gh = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1"):
                json.dumps(page1),
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2"):
                "SHOULD_NOT_BE_READ",
        }))
        merged = gh.recent_merged_prs(3)
        self.assertEqual([p["number"] for p in merged], [1, 2, 3])

        # Exhaust all three pages without early return or empty-page break.
        page2 = [{"number": 10, "merged_at": "2026-06-02T00:00:00Z"}, {"number": 11, "merged_at": None}]
        page3 = [{"number": 12, "merged_at": "2026-06-03T00:00:00Z"}, "skip"]
        gh2 = GithubCollector("/tmp/x", runner=fake_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1"):
                json.dumps(page1),
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2"):
                json.dumps(page2),
            ("api", "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=3"):
                json.dumps(page3),
        }))
        merged2 = gh2.recent_merged_prs(20)
        self.assertEqual([p["number"] for p in merged2], [1, 2, 3, 4, 5, 10, 12])


if __name__ == "__main__":
    unittest.main()
