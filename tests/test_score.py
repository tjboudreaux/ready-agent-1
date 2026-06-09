import unittest

from readiness import score
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.detect import detect
from readiness.model import CriterionResult, Status
from tests._util import make_repo, rmtree, fake_runner

RICH_GH = {
    ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}',
    ("api", "repos/o/r"): '{"default_branch":"main","topics":["agent-skills"],"security_and_analysis":{"secret_scanning":{"status":"enabled"}}}',
    ("api", "repos/o/r/topics"): '{"names":["agent-skills"]}',
    ("api", "repos/o/r/branches/main/protection"): '{"required_pull_request_reviews":{}}',
    ("api", "repos/o/r/actions/workflows"): '{"workflows":[{"name":"ci"}]}',
    ("api", "repos/o/r/actions/runs?per_page=20"): '{"workflow_runs":[{"conclusion":"success"}]}',
    ("api", "repos/o/r/labels?per_page=100"): '[{"name":"priority:high"},{"name":"area:api"}]',
    ("api", "repos/o/r/issues?state=open&per_page=50"): '[{"number":1,"labels":[{"name":"bug"}]}]',
}
RICH_GIT = {
    ("rev-parse", "--is-inside-work-tree"): "true\n",
    ("rev-parse", "HEAD"): "abc\n",
    ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
    ("log", "-1", "--format=%cI"): "2026-06-01T00:00:00+00:00\n",
    ("log", "-100", "--format=%an%n%ae%n%B%n==="): "T\nt@x\nfix\n\nCo-Authored-By: Claude\n===\n",
    ("log", "-1", "--format=%cI", "--", "README.md"): "2026-06-01T00:00:00+00:00\n",
    ("log", "-1", "--format=%cI", "--", "AGENTS.md"): "2026-06-01T00:00:00+00:00\n",
}

RICH_FILES = {
    "README.md": "# Project\n\n## Setup\n\n```\nnpm install\n```\n\n## Usage\n\n" + ("Detailed docs. " * 30),
    "AGENTS.md": "# Agents\n\n## Build\n\nnpm test\n\n## Conventions\n\nUse TS.\n",
    ".gitignore": ".env\nnode_modules/\n__pycache__/\ndist/\n",
    "package-lock.json": "{}",
    "package.json": '{"name":"app","dependencies":{"express":"^4"},"devDependencies":{"eslint":"^9","prettier":"^3","typescript":"^5"},"scripts":{"test":"jest"},"lint-staged":{}}',
    ".eslintrc.json": "{}",
    ".prettierrc": "{}",
    "tsconfig.json": '{"compilerOptions":{"strict":true}}',
    ".pre-commit-config.yaml": "repos: []\n",
    "src/app.test.ts": "test('x', () => {});\n",
    "tests/integration/e2e.test.ts": "test('e2e', () => {});\n",
    "CODEOWNERS": "* @team\n",
    "SECURITY.md": "# Security Policy\n",
    ".github/ISSUE_TEMPLATE/bug.md": "---\nname: Bug\n---\n",
    ".github/pull_request_template.md": "## Summary\n",
    ".github/dependabot.yml": "version: 2\n",
    ".github/workflows/ci.yml": "name: ci\n",
    ".github/workflows/codeql.yml": "name: codeql\n",
    ".devcontainer/devcontainer.json": "{}",
    ".env.example": "API_KEY=\n",
    ".releaserc": "{}",
    "openapi.yaml": "openapi: 3.0.0\n",
    "skills/foo/SKILL.md": "---\nname: foo\ndescription: x\n---\n# foo\n",
}


def _evaluate(files, gh_responses=None, git_responses=None, options=None):
    root = make_repo(files)
    static = StaticCollector(root)
    det = detect(root, static)
    git = GitCollector(root, runner=fake_runner(git_responses or {}))
    gh = GithubCollector(root, runner=fake_runner(gh_responses or {}))
    results, summary = score.evaluate(root, det, static, git, gh, options or {})
    return root, results, summary


class TestEvaluateIntegration(unittest.TestCase):
    def test_rich_repo_reaches_level_4(self):
        root, results, summary = _evaluate(RICH_FILES, RICH_GH, RICH_GIT)
        self.addCleanup(rmtree, root)
        by = {r.id: r.status for r in results}
        # spot-check a representative criterion per pillar
        for cid in ["docs.readme", "style.linter_config", "security.branch_protection",
                    "testing.integration_tests_exist", "docs.skills", "build.ci_runs_tests",
                    "style.strict_typing", "docs.api_schema_docs", "taskdisc.backlog_health"]:
            self.assertEqual(by[cid], Status.PASS, f"{cid} expected PASS, got {by[cid]}")
        self.assertEqual(summary.level, 4)  # L5 has no gating criteria in v1, so 4 is the ceiling
        self.assertGreater(summary.pass_rate, 0.95)

    def test_bare_repo_is_level_zero(self):
        root, results, summary = _evaluate({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        by = {r.id: r.status for r in results}
        self.assertEqual(by["docs.readme"], Status.FAIL)
        self.assertEqual(by["build.deps_pinned"], Status.PASS)  # no deps to pin
        # T2 criteria skip cleanly with no GitHub
        self.assertEqual(by["security.branch_protection"], Status.SKIPPED)
        self.assertEqual(summary.level, 0)

    def test_t2_skipped_without_github(self):
        root, results, summary = _evaluate(RICH_FILES, gh_responses={}, git_responses=RICH_GIT)
        self.addCleanup(rmtree, root)
        by = {r.id: r.status for r in results}
        self.assertEqual(by["security.branch_protection"], Status.SKIPPED)
        self.assertEqual(by["security.secret_scanning"], Status.SKIPPED)
        self.assertEqual(by["taskdisc.backlog_health"], Status.SKIPPED)


class TestApplicability(unittest.TestCase):
    def test_library_skips_api_schema(self):
        root, results, _ = _evaluate({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n'})
        self.addCleanup(rmtree, root)
        by = {r.id: r.status for r in results}
        self.assertEqual(by["docs.api_schema_docs"], Status.SKIPPED)

    def test_unknown_type_marks_api_schema_unknown(self):
        root, results, _ = _evaluate({"README.md": "# ambiguous", "Makefile": "all:\n\techo hi\n"})
        self.addCleanup(rmtree, root)
        by = {r.id: r.status for r in results}
        self.assertEqual(by["docs.api_schema_docs"], Status.UNKNOWN)

    def test_prerequisite_skips_validation(self):
        root, results, _ = _evaluate({"README.md": "# x"})  # no AGENTS.md
        self.addCleanup(rmtree, root)
        by = {r.id: r.status for r in results}
        self.assertEqual(by["docs.agents_md"], Status.FAIL)
        self.assertEqual(by["docs.agents_md_validation"], Status.SKIPPED)


class TestMonorepoAggregation(unittest.TestCase):
    def test_partial_application_pass_is_fail_with_ratio(self):
        files = {
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a"}',
            "packages/a/.eslintrc.json": "{}",
            "packages/b/package.json": '{"name":"b"}',
        }
        root, results, _ = _evaluate(files)
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "style.linter_config")
        self.assertEqual(r.status, Status.FAIL)
        self.assertIn("1/2", r.rationale)
        self.assertEqual(r.app_path, "*")


class TestWaivers(unittest.TestCase):
    def test_waiver_excludes_from_gate(self):
        waivers = [{"id": "docs.readme", "reason": "docs live elsewhere", "owner": "t"}]
        root, results, _ = _evaluate({"README.md": "# x"}, options={"waivers": waivers})
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "docs.readme")
        self.assertEqual(r.status, Status.WAIVED)
        self.assertIn("docs live elsewhere", r.rationale)

    def test_expired_waiver_reactivates(self):
        waivers = [{"id": "docs.readme", "reason": "x", "owner": "t", "expires": "2020-01-01"}]
        root, results, _ = _evaluate({"README.md": "# x"}, options={"waivers": waivers, "now": "2026-06-01"})
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "docs.readme")
        self.assertEqual(r.status, Status.FAIL)


class TestSummarize(unittest.TestCase):
    def _result(self, cid, level, status):
        return CriterionResult(id=cid, title=cid, pillar="P", level=level, scope="repository",
                               gating=True, status=status)

    def test_80_percent_boundary(self):
        # 4/5 pass at L1 = 80% -> achieved; L2 empty/defined-less -> capped
        results = [self._result(f"l1_{i}", 1, Status.PASS) for i in range(4)]
        results.append(self._result("l1_4", 1, Status.FAIL))
        summary = score.summarize(results)
        self.assertTrue(summary.levels[0].achieved)
        self.assertEqual(summary.level, 1)

    def test_below_boundary_not_achieved(self):
        results = [self._result(f"l1_{i}", 1, Status.PASS) for i in range(3)]
        results += [self._result(f"l1_{i}", 1, Status.FAIL) for i in range(3, 5)]  # 3/5 = 60%
        summary = score.summarize(results)
        self.assertFalse(summary.levels[0].achieved)
        self.assertEqual(summary.level, 0)

    def test_all_skipped_level_is_vacuously_achieved(self):
        results = [self._result("l1_0", 1, Status.PASS),
                   self._result("l2_skip", 2, Status.SKIPPED)]
        summary = score.summarize(results)
        self.assertTrue(summary.levels[0].achieved)
        self.assertTrue(summary.levels[1].achieved)  # all-skipped -> vacuous pass
        self.assertEqual(summary.level, 2)

    def test_empty_level_blocks_progression(self):
        # L1 passes, L2 has no defined criteria -> not assessable -> caps at 1
        results = [self._result("l1_0", 1, Status.PASS)]
        summary = score.summarize(results)
        self.assertEqual(summary.level, 1)
        self.assertFalse(summary.levels[1].achieved)


class TestRegistryIntegrity(unittest.TestCase):
    def test_registry_well_formed(self):
        registry = score.load_registry()
        ids = [c["id"] for c in registry]
        self.assertEqual(len(ids), len(set(ids)), "duplicate criterion ids")
        for crit in registry:
            self.assertIn(crit["level"], (1, 2, 3, 4, 5))
            self.assertIn(crit["scope"], ("repository", "application"))
            score._resolve_check(crit["check"])  # must import without error
            for req in crit.get("applies_when", {}).get("requires", []):
                self.assertIn(req, ids, f"{crit['id']} requires unknown {req}")


if __name__ == "__main__":
    unittest.main()
