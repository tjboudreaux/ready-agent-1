import json
import tempfile
import unittest
from pathlib import Path

from readiness import judgments, score
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.detect import detect
from readiness.model import (
    App,
    CriterionResult,
    DecisionStep,
    DecisionTrace,
    Detection,
    Evidence,
    Status,
    Verdict,
)

from tests._util import fake_runner, gh_runner, make_repo, rmtree

# T2 fixtures: fixed ``gh api --include`` endpoints. There is no ``gh repo view`` call
# anymore — identity comes from the injected sanitized origin tuple, and every endpoint
# body travels in one bounded HTTP envelope (built by gh_runner).
RICH_GH = {
    "repos/o/r": '{"full_name":"o/r","default_branch":"main","topics":["agent-skills"],'
                 '"security_and_analysis":{"secret_scanning":{"status":"enabled"}}}',
    "repos/o/r/topics": '{"names":["agent-skills"]}',
    "repos/o/r/branches/main/protection": '{"required_pull_request_reviews":{}}',
    "repos/o/r/actions/workflows?per_page=100": '{"workflows":[{"name":"ci"}]}',
    "repos/o/r/actions/runs?per_page=20":
        '{"workflow_runs":[{"run_started_at":"2026-05-31T00:00:00Z",'
        '"updated_at":"2026-05-31T01:00:00Z"}]}',
    "repos/o/r/labels?per_page=100": '[{"name":"priority:high"},{"name":"area:api"}]',
    "repos/o/r/issues?state=open&per_page=50":
        '[{"number":1,"labels":[{"name":"bug"}],"body":"repro steps"}]',
}
# T1 fixtures: git argv has no ``-C`` prefix; the injected runner answers argv tuples.
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
    "README.md": "# Project\n\n## Setup\n\n```\nnpm install\n```\n\n## Usage\n\n"
                  + ("Detailed docs. " * 30),
    "AGENTS.md": "# Agents\n\n## Build\n\nnpm test\n\n## Conventions\n\nUse TS.\n",
    ".gitignore": ".env\nnode_modules/\n__pycache__/\ndist/\n",
    "package-lock.json": "{}",
    "package.json": '{"name":"app","dependencies":{"express":"^4"},"devDependencies":{"eslint":'
                    '"^9","prettier":"^3","typescript":"^5"},"scripts":{"test":"jest"},"lint-'
                    'staged":{}}',
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


def _evaluate(files, gh_responses=None, git_responses=None, deps=None):
    """Score a fixture repo with injected collectors.

    GitHub is opt-in: passing ``gh_responses`` (even empty) builds a collector with a
    sanitized origin identity and the bounded-envelope runner; omitting it leaves T2
    criteria skipped, exactly like an offline scan. ``deps`` is the internal dependency
    channel (waivers/now/registry_path/readiness_config), mirroring what run.analyze
    builds from AnalyzeDependencies.
    """
    root = make_repo(files)
    static = StaticCollector(root)
    det = detect(root, static)
    git = GitCollector(root, runner=fake_runner(git_responses or {}), static=static)
    if gh_responses is None:
        gh = GithubCollector(root)
    else:
        gh = GithubCollector(root, origin=("github.com", "o", "r"),
                             runner=gh_runner(gh_responses))
    results, summary = score.evaluate(root, det, static, git, gh, {"_deps": deps or {}})
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
        root, results, summary = _evaluate(RICH_FILES, git_responses=RICH_GIT)
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
        root, results, _ = _evaluate({"README.md": "# x"}, deps={"waivers": waivers})
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "docs.readme")
        self.assertEqual(r.status, Status.WAIVED)
        # The free-form reason stays in the source policy; it is never copied.
        self.assertEqual(r.rationale, score.WAIVER_RATIONALE)
        self.assertNotIn("docs live elsewhere", r.rationale)
        self.assertEqual(r.decision_trace.reason_code, "waiver.active")

    def test_expired_waiver_reactivates(self):
        waivers = [{"id": "docs.readme", "reason": "x", "owner": "t", "expires": "2020-01-01"}]
        root, results, _ = _evaluate(
                                     {"README.md": "# x"},
                                     deps={"waivers": waivers, "now": "2026-06-01"})
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

    def test_defined_level_all_skipped_is_not_achieved(self):
        # A defined Level whose every criterion is skipped/waived is never achieved:
        # zero evaluated evidence cannot clear a schema-v3 gate.
        results = [self._result("l1_0", 1, Status.PASS),
                   self._result("l2_skip", 2, Status.SKIPPED)]
        summary = score.summarize(results)
        self.assertTrue(summary.levels[0].achieved)
        l2 = summary.levels[1]
        self.assertTrue(l2.defined)
        self.assertEqual(l2.defined_total, 1)
        self.assertEqual(l2.total, 0)
        self.assertEqual(l2.ratio, 0.0)  # zero denominator -> 0.0, never 1.0
        self.assertFalse(l2.achieved)
        self.assertEqual(summary.level, 1)

    def test_empty_level_blocks_progression(self):
        # L1 passes, L2 has no defined criteria -> not assessable -> caps at 1
        results = [self._result("l1_0", 1, Status.PASS)]
        summary = score.summarize(results)
        self.assertEqual(summary.level, 1)
        self.assertFalse(summary.levels[1].defined)
        self.assertEqual(summary.levels[1].defined_total, 0)
        self.assertFalse(summary.levels[1].achieved)

    def test_summary_carries_schema3_fields(self):
        results = [self._result(f"l1_{i}", 1, Status.PASS) for i in range(3)]
        results += [self._result(f"l1_{i}", 1, Status.FAIL) for i in range(3, 5)]  # 60%
        summary = score.summarize(results)
        self.assertEqual(summary.max_available_level, 1)
        # Every gating fail/unknown at the first unachieved defined Level, sorted.
        self.assertEqual([a["id"] for a in summary.next_gate_actions], ["l1_3", "l1_4"])
        coverage = summary.evidence_coverage
        self.assertEqual(coverage["status_counts"]["pass"], 3)
        self.assertEqual(coverage["status_counts"]["fail"], 2)
        self.assertEqual(sum(coverage["status_counts"].values()), 5)
        self.assertEqual(coverage["evidence_items_by_tier"],
                         {"T0": 0, "T1": 0, "T2": 0, "T3": 0, "T4": 0})
        d = summary.to_dict()
        for key in ("max_available_level", "next_gate_actions", "evidence_coverage"):
            self.assertIn(key, d)
        self.assertIn("defined", d["levels"][0])
        self.assertIn("defined_total", d["levels"][0])


class TestLoopOptIn(unittest.TestCase):
    def _registry_file(self, registry):
        path = Path(tempfile.mkdtemp(prefix="ar-registry-"))
        self.addCleanup(rmtree, path)
        reg = path / "registry.json"
        reg.write_text(json.dumps(registry), encoding="utf-8")
        return reg

    def test_opt_in_applies_before_check_dispatch(self):
        registry = [{
            "id": "loop.test_opt_in",
            "title": "Loop Opt In",
            "pillar": "Documentation",
            "level": 2,
            "scope": "repository",
            "decide": "deterministic",
            "gating": False,
            "check": "build.deps_pinned",
            "applies_when": {
                             "project_types": ["*"],
                             "languages": ["*"],
                             "requires": [],
                             "opt_in": "loop_ready"},
            "engine_min_version": "0.3.0",
        }]
        reg = self._registry_file(registry)

        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        results, _summary = score.evaluate(
                                           root,
                                           det,
                                           static,
                                           GitCollector(root, runner=fake_runner({}),
                                                        static=static),
                                           GithubCollector(root),
                                           {"_deps": {"registry_path": str(reg)}})
        self.assertEqual(results[0].status, Status.SKIPPED)
        self.assertEqual(results[0].rationale, "not opted into loop readiness")
        self.assertEqual(results[0].decision_trace.reason_code,
                         "applicability.not_opted_in")

        opted = make_repo({
            "README.md": "# x\n",
            ".ra1/config.json": json.dumps({
                "schema_version": "1",
                "loop_ready": True}),
        })
        self.addCleanup(rmtree, opted)
        static = StaticCollector(opted)
        det = detect(opted, static)
        results, _summary = score.evaluate(
                                           opted,
                                           det,
                                           static,
                                           GitCollector(opted, runner=fake_runner({}),
                                                        static=static),
                                           GithubCollector(opted),
                                           {"_deps": {"registry_path": str(reg)}})
        self.assertEqual(results[0].status, Status.PASS)

    def test_real_loop_criteria_skip_when_not_opted_in(self):
        root, results, _summary = _evaluate({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        loop_results = [r for r in results if r.id.startswith("loop.")]
        self.assertEqual(len(loop_results), 9)
        for r in loop_results:
            self.assertEqual(r.status, Status.SKIPPED)
            self.assertFalse(r.gating)
            self.assertEqual(r.rationale, "not opted into loop readiness")
            self.assertEqual(r.decision_trace.reason_code, "applicability.not_opted_in")

    def test_loop_failures_do_not_move_deterministic_score(self):
        root_out, out_results, out_summary = _evaluate(RICH_FILES, RICH_GH, RICH_GIT)
        self.addCleanup(rmtree, root_out)
        root_in, in_results, in_summary = _evaluate(
            {**RICH_FILES, ".ra1/config.json": json.dumps({
                "schema_version": "1",
                "loop_ready": True})},
            RICH_GH,
            RICH_GIT,
        )
        self.addCleanup(rmtree, root_in)
        self.assertTrue(any(
            r.id.startswith("loop.") and r.status == Status.FAIL and not r.gating
            for r in in_results))
        self.assertTrue(all(
            r.status == Status.SKIPPED for r in out_results if r.id.startswith("loop.")))
        self.assertEqual(in_summary.level, out_summary.level)
        self.assertEqual(in_summary.gating_passed, out_summary.gating_passed)
        self.assertEqual(in_summary.gating_total, out_summary.gating_total)


class TestRegistryIntegrity(unittest.TestCase):
    def test_registry_well_formed(self):
        registry = score.load_registry()
        ids = [c["id"] for c in registry]
        self.assertEqual(len(ids), len(set(ids)), "duplicate criterion ids")
        allowed_aw_keys = {"project_types", "languages", "requires", "opt_in"}
        for crit in registry:
            self.assertIn(crit["level"], (1, 2, 3, 4, 5))
            self.assertIn(crit["scope"], ("repository", "application"))
            score._resolve_check(crit["check"])  # must import without error
            aw = crit.get("applies_when", {})
            self.assertLessEqual(
                                 set(aw),
                                 allowed_aw_keys,
                                 f"{crit['id']} has unsupported applies_when keys")
            if "opt_in" in aw:
                self.assertEqual(aw["opt_in"], "loop_ready")
                for key in ("project_types", "languages", "requires"):
                    self.assertIn(key, aw)
            for req in aw.get("requires", []):
                self.assertIn(req, ids, f"{crit['id']} requires unknown {req}")


class TestAcdcMetadata(unittest.TestCase):
    def test_registry_acdc_blocks_well_formed(self):
        for crit in score.load_registry():
            acdc = crit.get("acdc")
            if acdc is None:
                continue
            self.assertLessEqual(set(acdc), {"stage", "loop"},
                                 f"{crit['id']} has unsupported acdc keys")
            self.assertIn(acdc.get("stage"), ("guide", "verify", "solve"),
                          f"{crit['id']} has invalid acdc stage")
            # loop is required: 'both' is the explicit both-loops classification, so an
            # omitted loop always means 'not classified' and fails here.
            self.assertIn(acdc.get("loop"), ("inner", "outer", "both"),
                          f"{crit['id']} has invalid or missing acdc loop")

    def test_evaluate_threads_acdc_fields(self):
        root, results, _ = _evaluate(RICH_FILES, RICH_GH, RICH_GIT)
        self.addCleanup(rmtree, root)
        by = {r.id: r for r in results}
        expectations = {
            "docs.agents_md": ("guide", "both"),
            "docs.agents_md_ci_validation": ("guide", "outer"),
            "docs.architecture_doc": ("guide", "both"),
            "docs.agent_verify_contract": ("guide", "both"),
            "build.check_command": ("verify", "inner"),
            "devenv.agent_hooks": ("verify", "inner"),
            "build.ci_runs_tests": ("verify", "outer"),
            "testing.coverage_threshold": ("verify", "outer"),
            "testing.new_code_quality_gate": ("verify", "outer"),
            "security.branch_protection": ("verify", "outer"),
            "style.precommit_hooks": ("solve", "inner"),
        }
        for cid, pair in expectations.items():
            self.assertEqual((by[cid].acdc_stage, by[cid].acdc_loop), pair,
                             f"{cid} acdc classification mismatch")
        self.assertEqual((by["docs.readme"].acdc_stage, by["docs.readme"].acdc_loop), ("", ""),
                         "unmapped criterion must carry empty acdc fields")

    def test_mapped_criteria_documented_in_pillars(self):
        from pathlib import Path
        doc = (Path(__file__).resolve().parent.parent / "references" / "pillars.md").read_text(
            encoding="utf-8")
        for crit in score.load_registry():
            if crit.get("acdc"):
                self.assertIn(f"`{crit['id']}`", doc,
                              f"acdc-mapped {crit['id']} missing from references/pillars.md")


class TestAppCounts(unittest.TestCase):
    def test_repository_scope_counts_by_status(self):
        root, results, _ = _evaluate(RICH_FILES, RICH_GH, RICH_GIT)
        self.addCleanup(rmtree, root)
        by = {r.id: r for r in results}
        # README passes -> 1/1
        self.assertEqual((by["docs.readme"].passed_apps, by["docs.readme"].evaluated_apps), (1, 1))

    def test_repository_scope_fail_and_skip_counts(self):
        root, results, _ = _evaluate({"README.md": "# x"})  # bare: readme fails, T2 skipped
        self.addCleanup(rmtree, root)
        by = {r.id: r for r in results}
        self.assertEqual((by["docs.readme"].passed_apps, by["docs.readme"].evaluated_apps), (0, 1))
        self.assertEqual((by["security.branch_protection"].passed_apps,
                          by["security.branch_protection"].evaluated_apps), (0, 0))  # skipped

    def test_app_scope_counts_reflect_apps_not_rationale(self):
        files = {
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a"}',
            "packages/a/.eslintrc.json": "{}",
            "packages/b/package.json": '{"name":"b"}',
        }
        root, results, _ = _evaluate(files)
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "style.linter_config")
        self.assertEqual(r.evaluated_apps, 2)
        self.assertEqual(r.passed_apps, 1)


class TestEvalCriterionBranches(unittest.TestCase):
    def _eval(self, crit, files):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        git = GitCollector(root, runner=fake_runner({}), static=static)
        gh = GithubCollector(root)
        return score._eval_criterion(crit, root, det, static, git, gh, {}, {}, {})

    def _crit(self, scope="repository", types=None, langs=None, opt_in=None):
        aw = {"project_types": types or ["*"], "languages": langs or ["*"], "requires": []}
        if opt_in is not None:
            aw["opt_in"] = opt_in
        return {"id": "docs.readme", "title": "R", "pillar": "Docs", "level": 1,
                "scope": scope, "gating": True, "check": "docs.readme", "applies_when": aw}

    def test_unsupported_opt_in_is_unknown(self):
        r = self._eval(self._crit(opt_in="bogus"), {"README.md": "# x"})
        self.assertEqual(r.status, Status.UNKNOWN)

    def test_repository_type_skip(self):
        r = self._eval(self._crit(types=["service"]), {"pyproject.toml": '[project]\nname="lib"\n'})
        self.assertEqual(r.status, Status.SKIPPED)
        self.assertIn("project type", r.rationale)

    def test_repository_language_skip(self):
        r = self._eval(self._crit(langs=["rust"]), {"pyproject.toml": '[project]\nname="lib"\n'})
        self.assertEqual(r.status, Status.SKIPPED)
        self.assertIn("language", r.rationale)

    def test_repository_unknown_type(self):
        r = self._eval(
                       self._crit(types=["service"]),
                       {"README.md": "# x", "Makefile": "all:\n\techo\n"})
        self.assertEqual(r.status, Status.UNKNOWN)

    def test_app_scope_language_skip_yields_not_applicable(self):
        files = {
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a"}',
            "packages/b/package.json": '{"name":"b"}',
        }
        r = self._eval(self._crit(scope="application", langs=["rust"]), files)
        self.assertEqual(r.status, Status.SKIPPED)
        self.assertEqual(r.evaluated_apps, 0)


class TestAggregateProdFacing(unittest.TestCase):
    def test_prod_facing_failing_note(self):
        files = {
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/api/package.json": '{"name":"api","dependencies":{"express":"^4"}}',
            "packages/api/Dockerfile": "FROM node\n",  # prod-facing service, no eslint -> fails
            "packages/web/package.json": '{"name":"web","dependencies":{"express":"^4"}}',
            "packages/web/.eslintrc.json": "{}",
        }
        root, results, _ = _evaluate(files)
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "style.linter_config")
        self.assertEqual(r.status, Status.FAIL)
        self.assertIn("Production-facing failing", r.rationale)


class TestWaiverEdgeCases(unittest.TestCase):
    def test_waiver_without_id_ignored(self):
        root, results, _ = _evaluate({"README.md": "# x"},
                                     deps={"waivers": [{"reason": "no id here"}]})
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "docs.readme")
        self.assertEqual(r.status, Status.FAIL)  # not waived

    def test_waiver_malformed_expires_still_waives(self):
        waivers = [{"id": "docs.readme", "reason": "x", "expires": "not-a-date"}]
        root, results, _ = _evaluate({"README.md": "# x"},
                                     deps={"waivers": waivers, "now": "2026-06-01"})
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "docs.readme")
        self.assertEqual(r.status, Status.WAIVED)



class TestAggregateUnknownAndWaiverFuture(unittest.TestCase):
    _CRIT = {"id": "x.y", "title": "t", "pillar": "P", "level": 3, "scope": "application",
             "gating": False, "check": "style.linter_config"}

    def test_aggregate_unknown_app(self):
        from readiness.model import App, Verdict
        crit = dict(self._CRIT)
        per = [(App(path="a"), Verdict(Status.UNKNOWN, "undetermined", []))]
        r = score._aggregate(crit, score._base(crit), per)
        self.assertEqual(r.status, Status.UNKNOWN)
        self.assertEqual(r.evaluated_apps, 1)
        self.assertEqual(r.passed_apps, 0)
        self.assertEqual(r.decision_trace.reason_code, "aggregate.unknown")

    def test_aggregate_pass_and_unknown_is_unknown(self):
        from readiness.model import App, Verdict
        crit = dict(self._CRIT)
        per = [(App(path="known"), Verdict(Status.PASS, "ok", [])),
               (App(path="unknown"), None)]
        r = score._aggregate(crit, score._base(crit), per)
        self.assertEqual(r.status, Status.UNKNOWN)
        self.assertEqual(r.passed_apps, 1)
        self.assertEqual(r.evaluated_apps, 2)
        self.assertEqual(r.app_path, "*")
        self.assertIn("1/2", r.rationale)
        self.assertIn("undetermined for unknown", r.rationale)

    def test_future_waiver_still_waives(self):
        waivers = [{"id": "docs.readme", "reason": "x", "expires": "2099-01-01"}]
        root, results, _ = _evaluate({"README.md": "# x"},
                                     deps={"waivers": waivers, "now": "2026-06-01"})
        self.addCleanup(rmtree, root)
        r = next(r for r in results if r.id == "docs.readme")
        self.assertEqual(r.status, Status.WAIVED)

class TestRecommendationSelector(unittest.TestCase):
    def _r(self, cid, level, status, gating=True, fix_kind=""):
        return CriterionResult(id=cid, title=cid.upper(), pillar="P", level=level,
                               scope="repository", gating=gating, status=status,
                               fix_kind=fix_kind)

    def test_next_level_first_lowest_effort_capped(self):
        results = [
            self._r("a", 2, Status.FAIL, fix_kind="scaffold"),
            self._r("b", 1, Status.FAIL, fix_kind=""),
            self._r("c", 1, Status.FAIL, fix_kind="scaffold"),
            self._r("d", 3, Status.UNKNOWN),
            self._r("e", 1, Status.FAIL, gating=False),  # advisory -> excluded
        ]
        recs = score._recommendations(results, level=0)  # next locked level is 1
        ids = [r["id"] for r in recs]
        self.assertEqual(len(ids), 3)            # capped at 3
        self.assertNotIn("e", ids)               # advisory excluded
        self.assertEqual(ids[0], "c")            # L1 scaffold (next level, lowest effort)
        self.assertEqual(ids[1], "b")            # L1 manual
        self.assertEqual(ids[2], "a")            # L2 before L3



class TestJudgmentsDecide(unittest.TestCase):
    def test_no_judgments_config(self):
        self.assertEqual(judgments.decide({}, "judgment.naming_consistency"), ("advisory", ""))

    def test_judgments_not_dict(self):
        self.assertEqual(judgments.decide({"judgments": "nope"}, "judgment.x"), ("advisory", ""))

    def test_off_and_advisory(self):
        cfg = {"judgments": {"naming_consistency": "off", "code_modularization": "advisory"}}
        self.assertEqual(judgments.decide(cfg, "judgment.naming_consistency"), ("off", ""))
        self.assertEqual(judgments.decide(cfg, "judgment.code_modularization"), ("advisory", ""))

    def test_star_default(self):
        self.assertEqual(judgments.decide(
                                          {"judgments": {"*": "off"}},
                                          "judgment.readme_quality")[0], "off")

    def test_dict_entry_with_reason(self):
        cfg = {"judgments": {"pii_handling": {"severity": "off", "reason": "no PII"}}}
        self.assertEqual(judgments.decide(cfg, "judgment.pii_handling"), ("off", "no PII"))

    def test_error_severity_downgraded(self):
        self.assertEqual(judgments.decide({"judgments": {"naming_consistency": "error"}},
                                          "judgment.naming_consistency"), ("advisory", ""))

    def test_short_id_without_prefix(self):
        self.assertEqual(judgments.decide({"judgments": {"naming_consistency": "off"}},
                                          "naming_consistency")[0], "off")

    def test_path_override(self):
        cfg = {"judgments": {"naming_consistency": "advisory"},
               "judgment_overrides": [{
                                       "paths": ["legacy/**"],
                                       "judgments": {"naming_consistency": "off"}}]}
        self.assertEqual(judgments.decide(
                                          cfg,
                                          "judgment.naming_consistency",
                                          path="legacy/x.py")[0], "off")
        self.assertEqual(judgments.decide(
                                          cfg,
                                          "judgment.naming_consistency",
                                          path="src/x.py")[0], "advisory")

    def test_path_override_malformed_entries(self):
        cfg = {"judgments": {}, "judgment_overrides": ["bad", {"paths": ["x/**"]},
                                                        {"judgments": {"a": "off"}}]}
        self.assertEqual(judgments.decide(
                                          cfg,
                                          "judgment.naming_consistency",
                                          path="x/y")[0], "advisory")


class TestAgentJudgments(unittest.TestCase):
    def test_advisory_judgment_is_unknown_nongating(self):
        root, results, _ = _evaluate({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        by = {r.id: r for r in results}
        self.assertEqual(by["judgment.naming_consistency"].status, Status.UNKNOWN)
        self.assertFalse(by["judgment.naming_consistency"].gating)

    def test_off_judgment_is_waived_with_reason(self):
        cfg = {"judgments": {"naming_consistency": {"severity": "off", "reason": "n/a"}}}
        root, results, _ = _evaluate({"README.md": "# x"}, deps={"readiness_config": cfg})
        self.addCleanup(rmtree, root)
        by = {r.id: r for r in results}
        r = by["judgment.naming_consistency"]
        self.assertEqual(r.status, Status.WAIVED)
        # The free-form suppression reason stays in the source policy, never the report.
        self.assertEqual(r.rationale,
                         "Suppressed by policy; the free-form reason remains in the "
                         "source policy.")
        self.assertNotIn("n/a", r.rationale)
        self.assertEqual(r.decision_trace.reason_code, "judgment.suppressed")
        self.assertEqual(by["judgment.code_modularization"].status, Status.UNKNOWN)

    def test_off_judgment_without_reason(self):
        cfg = {"judgments": {"naming_consistency": "off"}}
        root, results, _ = _evaluate({"README.md": "# x"}, deps={"readiness_config": cfg})
        self.addCleanup(rmtree, root)
        by = {r.id: r for r in results}
        self.assertEqual(by["judgment.naming_consistency"].rationale,
                         "Suppressed by policy; the free-form reason remains in the "
                         "source policy.")

    def test_agent_row_never_gates_even_if_flagged(self):
        b = score._base({"id": "judgment.x", "title": "X", "pillar": "P", "level": 2,
                         "decide": "agent", "gating": True})
        self.assertFalse(b["gating"])
        b2 = score._base({"id": "docs.readme", "title": "R", "pillar": "D", "level": 1,
                          "decide": "deterministic", "gating": True})
        self.assertTrue(b2["gating"])


class TestScoreInternals(unittest.TestCase):
    """Defensive scorer internals: registry loading, reason-code rules, trace merging."""

    def test_load_registry_unreadable_raises(self):
        with self.assertRaises(ValueError):
            score.load_registry("/nonexistent/registry.json")

    def test_load_waivers_skips_non_dict_entries(self):
        out, invalid = score.load_waivers(None, {"_deps": {"waivers": ["junk", 42,
                                                                       {"id": "x.y"}]}})
        self.assertEqual(list(out), ["x.y"])
        self.assertFalse(invalid)

    def test_decision_trace_merges_limitations_deduped(self):
        crit = {"id": "x.y", "title": "t", "check": "docs.readme"}
        trace = score._decision_trace(crit, Status.PASS, "ok", [],
                                      reason_code="check.pass",
                                      limitations=["a", "a", "", None, 5, "b"])
        self.assertEqual(trace.limitations, ["a", "b"])

    def test_reason_code_for_unrequired_criterion(self):
        crit = {"id": "x.y"}  # not in REQUIRED_REASON_CODES
        good = Verdict(Status.PASS, "r", [], reason_code="x.y.custom_code")
        self.assertEqual(score._reason_code_for(crit, good, Status.PASS), "x.y.custom_code")
        bad = Verdict(Status.PASS, "r", [], reason_code="BAD CODE!")
        self.assertEqual(score._reason_code_for(crit, bad, Status.PASS), "check.pass")

    def test_read_refusal_code_typed_and_generic(self):
        typed = score._read_refusal_code({"id": "style.linter_config"})
        self.assertEqual(typed, "style.linter_config.observation_indeterminate")
        generic = score._read_refusal_code({"id": "x.y"})
        self.assertEqual(generic, "input.repository_unreadable")

    def test_read_refusal_code_scans_suffix_order(self):
        # branch_protection's only indeterminate suffix sits later in the precedence
        # list, so earlier suffixes are checked and skipped first.
        code = score._read_refusal_code({"id": "security.branch_protection"})
        self.assertEqual(code, "security.branch_protection.observation_unreadable")

    def test_read_refusal_code_required_without_indeterminate_suffix(self):
        # Defensive fallback: a required criterion with no indeterminate suffix at all
        # still gets the generic input-refusal code.
        from unittest import mock
        with mock.patch.dict(score.REQUIRED_REASON_CODES,
                             {"fake.required": ("complete", "missing")}):
            code = score._read_refusal_code({"id": "fake.required"})
        self.assertEqual(code, "input.repository_unreadable")

    def test_run_check_maps_input_refusal_to_unknown(self):
        def boom(ctx):
            raise score.safe_io.RepositoryInputError("refused")
        verdict = score._run_check(boom, {"id": "style.linter_config"}, ".",
                                   None, None, None, None, None, {})
        self.assertIs(verdict.status, Status.UNKNOWN)
        self.assertEqual(verdict.reason_code,
                         "style.linter_config.observation_indeterminate")

    def test_aggregate_merges_per_app_limitations_in_order(self):
        crit = {"id": "x.y", "title": "t", "pillar": "P", "level": 3,
                "scope": "application", "gating": False, "check": "style.linter_config"}
        per = [(App(path="a"), Verdict(Status.PASS, "ok", [], limitations=["L1"])),
               (App(path="b"), Verdict(Status.PASS, "ok", [], limitations=["L1", "L2"]))]
        r = score._aggregate(crit, score._base(crit), per)
        self.assertIs(r.status, Status.PASS)
        self.assertEqual(r.decision_trace.limitations, ["L1", "L2"])

    def test_evidence_coverage_keeps_unexpected_tier_out_of_subcounts(self):
        # An unexpected tier stays in evidence_items but absent from tier subcounts: a
        # visible coverage contract defect, never coerced.
        result = CriterionResult(id="x.y", title="t", pillar="P", level=1,
                                 scope="repository", gating=True, status=Status.PASS,
                                 evidence=[Evidence(summary="s", tier="T9", source="",
                                                    detail="")])
        coverage = score._evidence_coverage([result])
        self.assertEqual(coverage["evidence_items"], 1)
        self.assertEqual(sum(coverage["evidence_items_by_tier"].values()), 0)

    def test_evidence_coverage_rule_step_guard(self):
        # Defensive guard: a trace the validator accepts (patched here) that lacks a rule
        # step counts as traced but not as rule-stepped.
        result = CriterionResult(id="x.y", title="t", pillar="P", level=1,
                                 scope="repository", gating=True, status=Status.PASS)
        result.decision_trace = DecisionTrace(
            reason_code="check.pass", rule_ref="x.y",
            steps=[DecisionStep(kind="evaluation", code="check.pass", message="m"),
                   DecisionStep(kind="conclusion", code="conclusion.pass",
                                message="Result: pass.")])
        from unittest import mock
        with mock.patch.object(score, "validate_decision_trace", lambda probe: []):
            coverage = score._evidence_coverage([result])
        self.assertEqual(coverage["results_with_decision_trace"], 1)
        self.assertEqual(coverage["results_with_rule_step"], 0)


class TestRepositoryIndeterminateScoring(unittest.TestCase):
    _CRIT = {"id": "docs.readme", "title": "R", "pillar": "Docs", "level": 1,
             "scope": "repository", "gating": True, "check": "docs.readme",
             "applies_when": {"project_types": ["*"], "languages": ["*"], "requires": []}}

    def _eval_with(self, detection):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        git = GitCollector(root, runner=fake_runner({}), static=static)
        gh = GithubCollector(root)
        return score._eval_criterion(dict(self._CRIT), root, detection, static, git, gh,
                                     {}, {}, {})

    def test_legacy_policy_path_reason(self):
        det = Detection(repository_indeterminate=True,
                        indeterminate_reason="input.legacy_policy_path")
        r = self._eval_with(det)
        self.assertIs(r.status, Status.UNKNOWN)
        self.assertIn("Legacy .agents/readiness policy files", r.rationale)
        self.assertEqual(r.decision_trace.reason_code, "input.legacy_policy_path")

    def test_generic_repository_indeterminate_reason(self):
        det = Detection(repository_indeterminate=True,
                        indeterminate_reason="input.repository_indeterminate")
        r = self._eval_with(det)
        self.assertIs(r.status, Status.UNKNOWN)
        self.assertIn("could not be read safely", r.rationale)
        self.assertEqual(r.decision_trace.reason_code, "input.repository_indeterminate")

    def test_invalid_waivers_file_inderminates_every_criterion(self):
        # A malformed policy file blocks the whole scan rather than scoring partial input.
        root = make_repo({"README.md": "# x", ".ra1/waivers.json": "{not json"})
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        git = GitCollector(root, runner=fake_runner({}), static=static)
        gh = GithubCollector(root)
        results, _summary = score.evaluate(root, det, static, git, gh, {"_deps": {}})
        self.assertTrue(det.repository_indeterminate)
        self.assertEqual(det.indeterminate_reason, "input.repository_indeterminate")
        self.assertTrue(all(r.status is Status.UNKNOWN for r in results))
        static.close()

    def test_invalid_waivers_preserves_an_existing_indeterminate_reason(self):
        # Legacy policy state already owns the indeterminate reason; the waiver failure
        # must not overwrite it.
        root = make_repo({"README.md": "# x", ".ra1/waivers.json": "{not json"})
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = Detection(repository_indeterminate=True,
                        indeterminate_reason="input.legacy_policy_path")
        git = GitCollector(root, runner=fake_runner({}), static=static)
        gh = GithubCollector(root)
        results, _summary = score.evaluate(root, det, static, git, gh, {"_deps": {}})
        self.assertEqual(det.indeterminate_reason, "input.legacy_policy_path")
        self.assertTrue(all(r.status is Status.UNKNOWN for r in results))
        static.close()


if __name__ == "__main__":
    unittest.main()
