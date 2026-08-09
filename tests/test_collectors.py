import json
import os
import unittest
from unittest import mock

from readiness import process, safe_io
from readiness.collectors import exec as exec_mod
from readiness.collectors._observation import (
    CollectorObservation,
    _require_immutable,
    absent,
    present,
    unavailable,
    unreadable,
)
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.process import BoundedProcessResult, ProcessState

from tests._util import fake_runner, gh_runner, make_repo, rmtree


class TestStaticCollector(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({
            "package.json": '{"name":"x","dependencies":{"express":"^4"},"devDependencies":'
                            '{"eslint":"^9"}}',
            "pyproject.toml": '[project]\nname="y"\ndependencies=["requests>=2"]\n[tool.ruff]\n'
                              'line-length=100\n',
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
            (
             "log",
             "-3",
             "--format=%cI"
             ): "2026-06-01T00:00:00+00:00\n2026-05-01T00:00:00+00:00\n2026-04-01T00:00:00+00:00\n",
            (
             "log",
             "-100",
             "--format=%an%n%ae%n%B%n==="
             ): "Travis\nt@x\nfix\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n===\n",
            ("tag",): "v1.0.0\nv1.1.0\n",
            ("log", "-1", "--format=%cI", "--", "README.md"): "2026-06-01T00:00:00+00:00\n",
        })
        g = GitCollector("/tmp/whatever", runner=runner)
        self.assertTrue(g.available())
        self.assertEqual(g.head_sha().value, "abc123")
        self.assertEqual(g.branch().value, "main")
        self.assertEqual(g.commit_count().value, 42)
        self.assertEqual(len(g.commit_dates(3).value), 3)
        self.assertTrue(g.has_agent_coauthorship().value)
        self.assertEqual(g.tags().value, ("v1.0.0", "v1.1.0"))
        self.assertEqual(g.file_last_commit_iso("README.md").value,
                         "2026-06-01T00:00:00+00:00")

    def test_unavailable_repo(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({}))
        self.assertFalse(g.available())
        self.assertEqual(g.head_sha().state, "unreadable")
        self.assertEqual(g.commit_count().state, "unreadable")
        self.assertFalse(g.has_agent_coauthorship().present)



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
        self.assertEqual(g.recent_churn(50).value, (35, 40))

    def test_commit_count_for_follow_and_dir(self):
        root = make_repo({"AGENTS.md": "# Agents\n"})
        self.addCleanup(rmtree, root)
        g_file = GitCollector(root, runner=fake_runner({
            ("log", "--follow", "--format=%H", "HEAD", "--", "AGENTS.md"):
                "h1\nh2\nh3\nh4\nh5\nh6\nh7\n",
        }))
        self.assertEqual(g_file.commit_count_for("AGENTS.md").value, 7)
        g_dir = GitCollector(root, runner=fake_runner({
            ("rev-list", "--count", "HEAD", "--", ".claude"): "4\n",
        }))
        self.assertEqual(g_dir.commit_count_for(".claude").value, 4)
        g_fail = GitCollector(root, runner=fake_runner({}))
        self.assertEqual(g_fail.commit_count_for("AGENTS.md").state, "unreadable")


class TestGithubCollector(unittest.TestCase):
    def _gh(self, extra=None):
        responses = {
            "repos/o/r": (
                '{"full_name":"o/r","default_branch":"main","topics":["agent-skills"],'
                '"security_and_analysis":{"secret_scanning":{"status":"enabled"}}}'
            ),
            "repos/o/r/topics": '{"names":["agent-skills","python"]}',
            "repos/o/r/branches/main/protection": '{"required_pull_request_reviews":{}}',
            "repos/o/r/actions/workflows?per_page=100":
                '{"workflows":[{"name":"ci","path":".github/workflows/ci.yml"}]}',
            "repos/o/r/actions/runs?per_page=20":
                '{"workflow_runs":[{"conclusion":"success"}]}',
            "repos/o/r/labels?per_page=100": '[{"name":"bug"},{"name":"enhancement"}]',
            "repos/o/r/issues?state=open&per_page=50":
                '[{"number":1,"labels":[{"name":"bug"}]},{"number":2,"pull_request":{}}]',
        }
        if extra:
            responses.update(extra)
        return GithubCollector("/tmp/x", origin=("github.com", "o", "r"),
                               runner=gh_runner(responses))

    def test_available_and_facts(self):
        gh = self._gh()
        self.assertTrue(gh.available)
        self.assertEqual(gh.slug, "o/r")
        self.assertEqual(gh.default_branch().value, "main")
        self.assertIn("agent-skills", gh.topics().value)
        self.assertTrue(gh.secret_scanning_enabled().value)
        self.assertEqual(gh.workflows().value, 1)
        self.assertEqual(len(gh.recent_runs().value), 1)
        self.assertEqual(gh.labels().value, ("bug", "enhancement"))
        issues = gh.open_issues().value
        self.assertEqual(len(issues), 1)  # the PR is filtered out
        self.assertTrue(issues[0].has_labels)
        self.assertFalse(issues[0].has_milestone)
        self.assertFalse(issues[0].has_body)

    def test_unavailable(self):
        gh = GithubCollector("/tmp/x", runner=gh_runner({}))
        self.assertFalse(gh.available)
        self.assertIsNone(gh.slug)
        self.assertEqual(gh.topics().state, "unavailable")
        self.assertEqual(gh.branch_protected().state, "unavailable")
        self.assertEqual(gh.workflows().state, "unavailable")
        self.assertEqual(gh.open_issues().state, "unavailable")

    def test_branch_protected_true(self):
        gh = self._gh()
        self.assertEqual(gh.branch_protected().state, "present")
        self.assertTrue(gh.branch_protected().value)

    def test_branch_protection_absent_returns_false(self):
        # available repo but no protection object -> present(False), not unreadable
        gh = self._gh({"repos/o/r/branches/main/protection": ("{}", 404)})
        self.assertEqual(gh.branch_protected().state, "present")
        self.assertFalse(gh.branch_protected().value)


    def test_recent_merged_prs_filters_and_paginates(self):
        page1 = [
            {"number": 1, "merged_at": "2026-06-01T00:00:00Z", "title": "m1"},
            {"number": 2, "merged_at": None, "title": "closed unmerged"},
            {"number": 3, "merged_at": "2026-06-02T00:00:00Z", "title": "m2"},
        ]
        page2 = [
            {"number": 4, "merged_at": "2026-06-03T00:00:00Z", "title": "m3"},
        ]
        gh = self._gh({
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1":
                json.dumps(page1),
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2":
                json.dumps(page2),
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=3":
                "[]",
        })
        merged = gh.recent_merged_prs(20).value
        self.assertEqual([p.number for p in merged], [1, 3, 4])

        empty = GithubCollector("/tmp/x", runner=gh_runner({}))
        self.assertEqual(empty.recent_merged_prs().state, "unavailable")
        err = self._gh()  # available but the pulls API answers an error status
        self.assertEqual(err.recent_merged_prs().state, "unreadable")

    def test_pr_first_review_iso(self):
        gh = self._gh({
            "repos/o/r/pulls/1/reviews?per_page=100": json.dumps([
                {"submitted_at": "2026-06-02T00:00:00Z"},
                {"submitted_at": "2026-06-01T12:00:00Z"},
            ]),
        })
        self.assertEqual(gh.pr_first_review_iso(1).value, "2026-06-01T12:00:00Z")

        empty = GithubCollector("/tmp/x", runner=gh_runner({}))
        self.assertEqual(empty.pr_first_review_iso(1).state, "unavailable")
        err = self._gh()
        self.assertEqual(err.pr_first_review_iso(9).state, "unreadable")
        no_reviews = self._gh({
            "repos/o/r/pulls/2/reviews?per_page=100": "[]",
        })
        self.assertEqual(no_reviews.pr_first_review_iso(2).state, "absent")




class TestGitCollectorCoverageGaps(unittest.TestCase):
    def test_commit_count_value_error(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("rev-list", "--count", "HEAD"): "not-an-int\n",
        }))
        self.assertEqual(g.commit_count().state, "unreadable")

    def test_recent_churn_edge_branches(self):
        from readiness.collectors import git as gitmod

        blob = (
            "10\t5\tsrc/before_hash.py\n"  # numstat before hash → current is None path
            "abc123\n"
            "1\t2\n"  # parts < 3
            "3\t4\tsrc/ok.py\n"
        )
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): blob,
        }))
        self.assertEqual(g.recent_churn(50).value, (15, 7))
        self.assertTrue(gitmod._churn_path_excluded("vendor/x.py"))
        self.assertTrue(gitmod._churn_path_excluded("pkg/yarn.lock"))
        self.assertFalse(gitmod._churn_path_excluded("src/a.py"))

        # Non-integer numstat fields are malformed git output → unreadable observation.
        malformed = (
            "abc123\n"
            "foo\tbar\tsrc/bad.py\n"
        )
        g_bad = GitCollector("/tmp/whatever", runner=fake_runner({
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): malformed,
        }))
        self.assertEqual(g_bad.recent_churn(50).state, "unreadable")

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
        self.assertEqual(g2.recent_churn(50).value, ())

    def test_commit_count_for_value_error(self):
        root = make_repo({"AGENTS.md": "# Agents\n"})
        self.addCleanup(rmtree, root)
        g = GitCollector(root, runner=fake_runner({
            ("rev-list", "--count", "HEAD", "--", ".claude"): "nope\n",
        }))
        self.assertEqual(g.commit_count_for(".claude").state, "unreadable")


class TestGithubCollectorCoverageGaps(unittest.TestCase):
    def test_api_and_available_bad_json(self):
        gh = GithubCollector("/tmp/x", runner=gh_runner({}))
        self.assertFalse(gh.available)
        self.assertIsNone(gh.slug)
        self.assertEqual(gh.repo().state, "unavailable")
        self.assertEqual(gh.recent_runs().state, "unavailable")
        self.assertEqual(gh.labels().state, "unavailable")

        bad_api = GithubCollector("/tmp/x", origin=("github.com", "o", "r"),
                                  runner=gh_runner({"repos/o/r": "not-json{"}))
        self.assertTrue(bad_api.available)
        self.assertEqual(bad_api.repo().state, "unreadable")

    def test_topics_fallback_empty(self):
        gh = GithubCollector("/tmp/x", origin=("github.com", "o", "r"),
                             runner=gh_runner({
                                 "repos/o/r/topics": "{}",
                                 "repos/o/r": '{"full_name":"o/r","default_branch":"main"}',
                             }))
        self.assertEqual(gh.topics().value, ())

    def test_recent_merged_prs_early_return(self):
        page1 = [
            {"number": i, "merged_at": "2026-06-01T00:00:00Z"}
            for i in range(1, 6)
        ]
        gh = self._gh_with_pulls({
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1":
                json.dumps(page1),
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2":
                "SHOULD_NOT_BE_READ",
        })
        merged = gh.recent_merged_prs(3).value
        self.assertEqual([p.number for p in merged], [1, 2, 3])

        # Exhaust all three pages without early return or empty-page break.
        page2 = [{"number": 10, "merged_at": "2026-06-02T00:00:00Z"
                                             }, {"number": 11, "merged_at": None}]
        page3 = [{"number": 12, "merged_at": "2026-06-03T00:00:00Z"},
                 {"number": 13, "merged_at": None}]
        gh2 = self._gh_with_pulls({
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1":
                json.dumps(page1),
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2":
                json.dumps(page2),
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=3":
                json.dumps(page3),
        })
        merged2 = gh2.recent_merged_prs(20).value
        self.assertEqual([p.number for p in merged2], [1, 2, 3, 4, 5, 10, 12])

    def _gh_with_pulls(self, responses):
        base = {"repos/o/r": '{"full_name":"o/r","default_branch":"main"}'}
        base.update(responses)
        return GithubCollector("/tmp/x", origin=("github.com", "o", "r"),
                               runner=gh_runner(base))


class TestObservationContract(unittest.TestCase):
    """The immutable CollectorObservation contract: closed states and payload invariants."""

    def test_unknown_state_rejected(self):
        with self.assertRaises(TypeError):
            CollectorObservation("bogus")

    def test_non_present_carries_no_value(self):
        with self.assertRaises(TypeError):
            CollectorObservation("absent", value="x")

    def test_present_absent_carry_no_reason(self):
        with self.assertRaises(TypeError):
            CollectorObservation("present", value="x", reason="why")
        with self.assertRaises(TypeError):
            CollectorObservation("absent", reason="why")

    def test_unreadable_unavailable_require_reason(self):
        with self.assertRaises(TypeError):
            CollectorObservation("unreadable")
        with self.assertRaises(TypeError):
            CollectorObservation("unavailable")

    def test_available_property(self):
        self.assertTrue(present("x").available)
        self.assertTrue(absent().available)
        self.assertFalse(unreadable("r").available)
        self.assertFalse(unavailable("r").available)

    def test_mutable_value_rejected(self):
        with self.assertRaises(TypeError):
            _require_immutable(["list"])
        with self.assertRaises(TypeError):
            _require_immutable({"dict": 1})
        # tuples and frozen dataclasses are valid payloads
        _require_immutable(("a", 1, ("nested",)))
        _require_immutable(None)


class TestStaticCollectorStates(unittest.TestCase):
    """Degraded repository states flip collection_complete and fail closed, never absent."""

    def test_close_without_authority_is_a_noop(self):
        c = StaticCollector(make_repo({}))
        c.close()  # no authority was ever acquired
        self.assertIsNone(c._authority)

    def test_glob_overflow_flips_collection_complete(self):
        root = make_repo({"a.md": "x", "b.md": "y"})
        self.addCleanup(rmtree, root)
        c = StaticCollector(root)
        obs = c.glob_repo_files(["*.md"], max_matches=1)
        self.assertIs(obs.state, safe_io.RepoDiscoveryState.OVERFLOW)
        self.assertFalse(c.collection_complete)
        c.close()

    def test_exists_observation_accepts_a_single_string_pattern(self):
        root = make_repo({"a.md": "x"})
        self.addCleanup(rmtree, root)
        c = StaticCollector(root)
        obs = c.exists_observation("a.md")
        self.assertIs(obs.state, safe_io.PresenceState.PRESENT)
        self.assertEqual(obs.path, "a.md")
        c.close()

    def test_exists_indeterminate_flips_collection_complete(self):
        # Two matches against the internal max_matches=1 is a match overflow, reported as
        # indeterminate rather than collapsed to a boolean.
        root = make_repo({"a.md": "x", "b.md": "y"})
        self.addCleanup(rmtree, root)
        c = StaticCollector(root)
        obs = c.exists_observation(["*.md"])
        self.assertIs(obs.state, safe_io.PresenceState.INDETERMINATE)
        self.assertFalse(c.collection_complete)
        c.close()

    def test_require_ok_raises_on_degraded_observation(self):
        obs = safe_io.RepoFileObservation(safe_io.RepoReadState.UNREADABLE,
                                          reason_code="io_error")
        with self.assertRaises(safe_io.RepositoryInputError):
            StaticCollector._require_ok(obs, "read")

    def test_exists_any_raises_on_indeterminate(self):
        root = make_repo({"a.md": "x", "b.md": "y"})
        self.addCleanup(rmtree, root)
        c = StaticCollector(root)
        with self.assertRaises(safe_io.RepositoryInputError):
            c.exists_any(["*.md"])
        c.close()

    def test_lockfiles_raises_on_indeterminate(self):
        # A multiply-linked lockfile is unsafe discovery state, not an absence.
        root = make_repo({"real.txt": "x"})
        self.addCleanup(rmtree, root)
        os.link(root / "real.txt", root / "package-lock.json")
        c = StaticCollector(root)
        with self.assertRaises(safe_io.RepositoryInputError):
            c.lockfiles()
        c.close()


def _ok_runner(tool_id, argv, cwd_fd, env, timeout):
    return BoundedProcessResult(ProcessState.OK, returncode=0, stdout="")


class TestExecCollectorStates(unittest.TestCase):
    """T3 isolated-copy lifecycle: caching, subroot runs, and fail-closed refusals."""

    def test_copy_is_built_once_and_reused(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        ex = exec_mod.ExecCollector(root, {"exec": True}, runner=_ok_runner)
        first = ex.run_allowed({"pytest": (process.ToolId.PYTEST, ("-q",))}, "pytest")
        second = ex.run_allowed({"npm test": (process.ToolId.NPM, ("test",))}, "npm test")
        self.assertEqual(first["state"], "ok")
        self.assertEqual(second["state"], "ok")
        ex.close()

    def test_copy_refusal_is_sticky_and_unavailable(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        ex = exec_mod.ExecCollector(root, {"exec": True}, runner=_ok_runner)
        with mock.patch.object(exec_mod.safe_io, "acquire_root",
                               side_effect=OSError("boom")):
            first = ex.run_allowed({"pytest": (process.ToolId.PYTEST, ("-q",))}, "pytest")
            # A later call short-circuits on the recorded refusal instead of retrying.
            second = ex.run_allowed({"npm test": (process.ToolId.NPM, ("test",))},
                                    "npm test")
        self.assertEqual(first["state"], "unavailable")
        self.assertEqual(second["state"], "unavailable")
        self.assertFalse(ex.completed)
        self.assertFalse(ex.successful)
        ex.close()

    def test_app_subdir_run_and_missing_subdir(self):
        root = make_repo({"app/pyproject.toml": '[project]\nname="a"\n'})
        self.addCleanup(rmtree, root)
        ex = exec_mod.ExecCollector(root, {"exec": True}, runner=_ok_runner)
        allow = {"pytest": (process.ToolId.PYTEST, ("-q",))}
        sub = ex.run_allowed(allow, "pytest", app_path="app")
        self.assertEqual(sub["state"], "ok")
        missing = ex.run_allowed(allow, "pytest", app_path="missing")
        self.assertEqual(missing["state"], "unavailable")
        self.assertFalse(ex.completed)
        ex.close()

    def test_real_toolchain_path_without_injected_runner(self):
        # No injected runner: the startup toolchain resolves and the bounded launcher
        # answers — ok/nonzero where `make` exists, unsupported where it does not.
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        ex = exec_mod.ExecCollector(root, {"exec": True, "exec_timeout": 10})
        res = ex.run_allowed(exec_mod.ALLOWED_SMOKE_CMDS, "make smoke")
        self.assertTrue(res["allowed"])
        self.assertIn(res["state"], ("ok", "nonzero", "unavailable"))
        self.assertEqual(ex._spawned, 1)
        ex.close()

    def test_supplied_toolchain_skips_resolution(self):
        # A constructor-supplied toolchain is reused as-is; the bounded launcher reports
        # the missing tool as unsupported rather than resolving again.
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        ex = exec_mod.ExecCollector(root, {"exec": True, "exec_timeout": 10},
                                    toolchain={})
        res = ex.run_allowed(exec_mod.ALLOWED_SMOKE_CMDS, "make smoke")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["state"], "unavailable")
        self.assertEqual(ex._toolchain, {})
        ex.close()


if __name__ == "__main__":
    unittest.main()
