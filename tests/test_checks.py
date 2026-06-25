import unittest

from readiness.checks import build, devenv, docs, loop, observability, product, security, style, taskdisc, testing
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.context import Context
from readiness.detect import detect
from readiness.model import Status
from tests._util import make_repo, rmtree, fake_runner


def _gh_available(extra=None):
    base = {("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner":"o/r"}'}
    base.update(extra or {})
    return base


class CheckCase(unittest.TestCase):
    def ctx(self, files, gh=None, git=None, app_path=".", options=None):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        app = next((a for a in det.apps if a.path == app_path), det.apps[0])
        return Context(
            root=root, detection=det, static=static,
            git=GitCollector(root, runner=fake_runner(git or {})),
            github=GithubCollector(root, runner=fake_runner(gh or {})),
            app=app,
            options=options or {},
        )

    def s(self, verdict):
        return verdict.status


class TestStyleChecks(CheckCase):
    def test_linter_paths(self):
        self.assertEqual(self.s(style.linter_config(self.ctx({".eslintrc.json": "{}"}))), Status.PASS)
        self.assertEqual(self.s(style.linter_config(self.ctx({"pyproject.toml": '[tool.ruff]\nx=1\n'}))), Status.PASS)
        self.assertEqual(self.s(style.linter_config(self.ctx({"package.json": '{"devDependencies":{"eslint":"^9"}}'}))), Status.PASS)
        self.assertEqual(self.s(style.linter_config(self.ctx({"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_formatter_paths(self):
        self.assertEqual(self.s(style.formatter(self.ctx({".prettierrc": "{}"}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx({"pyproject.toml": "[tool.black]\nx=1\n"}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx({"package.json": '{"devDependencies":{"prettier":"^3"}}'}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx({"go.mod": "module x\n"}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx({"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_type_check_paths(self):
        self.assertEqual(self.s(style.type_check(self.ctx({"go.mod": "module x\n"}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx({"tsconfig.json": "{}"}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx({"package.json": '{"devDependencies":{"typescript":"^5"}}'}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx({"pyproject.toml": "[tool.mypy]\nx=1\n"}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx({"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_strict_typing_paths(self):
        self.assertEqual(self.s(style.strict_typing(self.ctx({"go.mod": "module x\n"}))), Status.PASS)
        self.assertEqual(self.s(style.strict_typing(self.ctx({"tsconfig.json": '{"compilerOptions":{"strict":true}}'}))), Status.PASS)
        self.assertEqual(self.s(style.strict_typing(self.ctx({"tsconfig.json": '{"compilerOptions":{}}'}))), Status.FAIL)
        self.assertEqual(self.s(style.strict_typing(self.ctx({"pyproject.toml": "[tool.mypy]\nstrict=true\n"}))), Status.PASS)
        self.assertEqual(self.s(style.strict_typing(self.ctx({"pyproject.toml": "[tool.mypy]\nx=1\n"}))), Status.FAIL)
        self.assertEqual(self.s(style.strict_typing(self.ctx({"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_precommit_paths(self):
        self.assertEqual(self.s(style.precommit_hooks(self.ctx({".pre-commit-config.yaml": "repos: []\n"}))), Status.PASS)
        self.assertEqual(self.s(style.precommit_hooks(self.ctx({"package.json": '{"lint-staged":{}}'}))), Status.PASS)
        self.assertEqual(self.s(style.precommit_hooks(self.ctx({"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_atool_root_fallback_in_monorepo(self):
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            "pyproject.toml": "[tool.ruff]\nx=1\n",
            "packages/a/package.json": '{"name":"a"}',
            "packages/b/package.json": '{"name":"b"}',
        }
        ctx = self.ctx(files, app_path="packages/a")
        self.assertEqual(self.s(style.linter_config(ctx)), Status.PASS)  # via root [tool.ruff] fallback

    def test_strict_typing_uses_root_mypy_config_in_monorepo(self):
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            "pyproject.toml": "[tool.mypy]\nstrict=true\n",
            "packages/a/package.json": '{"name":"a"}',
            "packages/b/package.json": '{"name":"b"}',
        }
        ctx = self.ctx(files, app_path="packages/a")
        self.assertEqual(self.s(style.strict_typing(ctx)), Status.PASS)


class TestBuildChecks(CheckCase):
    def test_deps_pinned_fail(self):
        ctx = self.ctx({"package.json": '{"dependencies":{"express":"^4"}}'})
        self.assertEqual(self.s(build.deps_pinned(ctx)), Status.FAIL)

    def test_vcs_cli(self):
        git_repo = {("rev-parse", "--is-inside-work-tree"): "true\n"}
        self.assertEqual(self.s(build.vcs_cli(self.ctx({}, git=git_repo))), Status.PASS)
        self.assertEqual(self.s(build.vcs_cli(self.ctx({}))), Status.FAIL)

    def test_agentic_development(self):
        self.assertEqual(self.s(build.agentic_development(self.ctx({}))), Status.UNKNOWN)
        git_repo = {("rev-parse", "--is-inside-work-tree"): "true\n",
                    ("log", "-100", "--format=%an%n%ae%n%B%n==="): "T\nt@x\nfix\n===\n"}
        self.assertEqual(self.s(build.agentic_development(self.ctx({}, git=git_repo))), Status.FAIL)

    def test_ci_present_via_gh(self):
        ctx = self.ctx({}, gh=_gh_available({("api", "repos/o/r/actions/workflows"): '{"workflows":[{"name":"ci"}]}'}))
        self.assertEqual(self.s(build.ci_present(ctx)), Status.PASS)
        self.assertEqual(self.s(build.ci_present(self.ctx({}))), Status.FAIL)

    def test_ci_runs_tests_variants(self):
        self.assertEqual(self.s(build.ci_runs_tests(self.ctx({}))), Status.SKIPPED)
        no_wf = self.ctx({}, gh=_gh_available({("api", "repos/o/r/actions/workflows"): '{"workflows":[]}'}))
        self.assertEqual(self.s(build.ci_runs_tests(no_wf)), Status.FAIL)
        wf_no_tests = self.ctx({}, gh=_gh_available({
            ("api", "repos/o/r/actions/workflows"): '{"workflows":[{"name":"ci"}]}',
            ("api", "repos/o/r/actions/runs?per_page=20"): '{"workflow_runs":[{}]}',
        }))
        self.assertEqual(self.s(build.ci_runs_tests(wf_no_tests)), Status.FAIL)
        wf_tests_no_runs = self.ctx({"src/x.test.ts": "t"}, gh=_gh_available({
            ("api", "repos/o/r/actions/workflows"): '{"workflows":[{"name":"ci"}]}',
            ("api", "repos/o/r/actions/runs?per_page=20"): '{"workflow_runs":[]}',
        }))
        self.assertEqual(self.s(build.ci_runs_tests(wf_tests_no_runs)), Status.FAIL)

    def test_release_automation(self):
        self.assertEqual(self.s(build.release_automation(self.ctx({"package.json": '{"devDependencies":{"semantic-release":"^x"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.release_automation(self.ctx({}))), Status.FAIL)


class TestDocsChecks(CheckCase):
    def test_readme_variants(self):
        self.assertEqual(self.s(docs.readme(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(docs.readme(self.ctx({"README.md": "# tiny"}))), Status.FAIL)
        self.assertEqual(self.s(docs.readme(self.ctx({"README.md": "plain text " * 40}))), Status.FAIL)

    def test_agents_md_validation_variants(self):
        self.assertEqual(self.s(docs.agents_md_validation(self.ctx({"AGENTS.md": "# only one heading\n\ntext"}))), Status.FAIL)
        long_doc = "# A\n## B\n" + ("\n" * 420)
        self.assertEqual(self.s(docs.agents_md_validation(self.ctx({"AGENTS.md": long_doc}))), Status.FAIL)

    def test_doc_freshness_variants(self):
        self.assertEqual(self.s(docs.doc_freshness(self.ctx({"README.md": "# x"}))), Status.UNKNOWN)  # no git
        no_docs_git = {("log", "-1", "--format=%cI"): "2026-06-01T00:00:00+00:00\n"}
        self.assertEqual(self.s(docs.doc_freshness(self.ctx({}, git=no_docs_git))), Status.UNKNOWN)
        stale_git = {
            ("log", "-1", "--format=%cI"): "2026-06-01T00:00:00+00:00\n",
            ("log", "-1", "--format=%cI", "--", "README.md"): "2024-01-01T00:00:00+00:00\n",
        }
        self.assertEqual(self.s(docs.doc_freshness(self.ctx({"README.md": "# x"}, git=stale_git))), Status.FAIL)

    def test_doc_freshness_edge_branches(self):
        # agents_md_validation: AGENTS.md absent/unreadable
        self.assertEqual(self.s(docs.agents_md_validation(self.ctx({"README.md": "# x"}))), Status.FAIL)
        # doc exists but no per-file commit date -> skipped in loop -> nothing tracked
        git_no_file_date = {("log", "-1", "--format=%cI"): "2026-06-01T00:00:00+00:00\n"}
        self.assertEqual(self.s(docs.doc_freshness(
            self.ctx({"README.md": "# x"}, git=git_no_file_date))), Status.UNKNOWN)
        # unparseable per-file commit date -> ValueError path
        git_bad_date = {
            ("log", "-1", "--format=%cI"): "2026-06-01T00:00:00+00:00\n",
            ("log", "-1", "--format=%cI", "--", "README.md"): "not-a-date\n",
        }
        self.assertEqual(self.s(docs.doc_freshness(
            self.ctx({"README.md": "# x"}, git=git_bad_date))), Status.UNKNOWN)

    def test_api_schema_via_dep(self):
        self.assertEqual(self.s(docs.api_schema_docs(self.ctx({"pyproject.toml": '[project]\nname="x"\ndependencies=["fastapi"]\n'}))), Status.PASS)
        self.assertEqual(self.s(docs.api_schema_docs(self.ctx({"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_skills_fail(self):
        self.assertEqual(self.s(docs.skills(self.ctx({"README.md": "# x"}))), Status.FAIL)


class TestDevenvChecks(CheckCase):
    def test_fails(self):
        self.assertEqual(self.s(devenv.env_template(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(devenv.devcontainer(self.ctx({}))), Status.FAIL)


class TestSecurityChecks(CheckCase):
    def test_branch_protection_fail(self):
        ctx = self.ctx({}, gh=_gh_available({("api", "repos/o/r"): '{"default_branch":"main"}'}))
        self.assertEqual(self.s(security.branch_protection(ctx)), Status.FAIL)

    def test_secret_scanning_fail(self):
        ctx = self.ctx({}, gh=_gh_available({("api", "repos/o/r"): '{"default_branch":"main","security_and_analysis":{"secret_scanning":{"status":"disabled"}}}'}))
        self.assertEqual(self.s(security.secret_scanning(ctx)), Status.FAIL)

    def test_simple_fails(self):
        self.assertEqual(self.s(security.codeowners(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(security.dependency_update_automation(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(security.security_md(self.ctx({}))), Status.FAIL)

    def test_automated_security_review(self):
        self.assertEqual(self.s(security.automated_security_review(self.ctx({"pyproject.toml": '[project]\nname="x"\ndependencies=["bandit"]\n'}))), Status.PASS)
        self.assertEqual(self.s(security.automated_security_review(self.ctx({}))), Status.FAIL)

    def test_gitignore_partial(self):
        self.assertEqual(self.s(security.gitignore_comprehensive(self.ctx({".gitignore": ".env\n"}))), Status.FAIL)  # secret only
        self.assertEqual(self.s(security.gitignore_comprehensive(self.ctx({".gitignore": "dist/\n"}))), Status.FAIL)  # artifact only
        self.assertEqual(self.s(security.gitignore_comprehensive(self.ctx({}))), Status.FAIL)  # none


class TestTestingChecks(CheckCase):
    def test_unit_fail(self):
        self.assertEqual(self.s(testing.unit_tests_exist(self.ctx({"src/app.py": "x=1"}))), Status.FAIL)

    def test_integration_via_dep(self):
        self.assertEqual(self.s(testing.integration_tests_exist(self.ctx({"package.json": '{"devDependencies":{"cypress":"^13"}}'}))), Status.PASS)
        self.assertEqual(self.s(testing.integration_tests_exist(self.ctx({"src/app.py": "x"}))), Status.FAIL)

    def test_naming_variants(self):
        self.assertEqual(self.s(testing.test_naming(self.ctx({"tests/test_x.py": "x"}))), Status.PASS)
        self.assertEqual(self.s(testing.test_naming(self.ctx({"tests/helper.py": "x"}))), Status.FAIL)  # dir but nonstandard
        self.assertEqual(self.s(testing.test_naming(self.ctx({"src/app.py": "x"}))), Status.SKIPPED)  # no tests


class TestTaskDiscChecks(CheckCase):
    def test_templates_fail(self):
        self.assertEqual(self.s(taskdisc.issue_templates(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(taskdisc.pr_templates(self.ctx({}))), Status.FAIL)

    def test_issue_labeling(self):
        only_default = self.ctx({}, gh=_gh_available({("api", "repos/o/r/labels?per_page=100"): '[{"name":"bug"},{"name":"enhancement"}]'}))
        self.assertEqual(self.s(taskdisc.issue_labeling(only_default)), Status.FAIL)
        via_file = self.ctx({".github/labels.yml": "x"}, gh=_gh_available({("api", "repos/o/r/labels?per_page=100"): '[{"name":"bug"}]'}))
        self.assertEqual(self.s(taskdisc.issue_labeling(via_file)), Status.PASS)

    def test_backlog_health_low(self):
        ctx = self.ctx({}, gh=_gh_available({
            ("api", "repos/o/r/issues?state=open&per_page=50"): '[{"number":1,"labels":[{"name":"bug"}]},{"number":2,"labels":[]},{"number":3,"labels":[]}]',
        }))
        self.assertEqual(self.s(taskdisc.backlog_health(ctx)), Status.FAIL)



class TestLoopChecks(CheckCase):
    FILLED = "# Artifact\n\nThis filled loop readiness artifact documents a stable maintainer-owned convention with enough detail.\n"
    RULES = "# Loop Rules\n\nThis rules index points maintainers to the denylist and related loop policies.\n"
    DENY = "# Loop Denylist\n\nNever mutate secrets or deploy without confirmation. Block unsafe paths.\n"
    SIGNAL = (
        "# Signal Schema\n\n```json\n"
        "{\"schema_version\":\"1\",\"signal\":\"loop.run\",\"source\":\"runner\","
        "\"timestamp\":\"2026-01-01T00:00:00Z\",\"evidence\":[]}\n"
        "```\n"
    )
    PR_ARTIFACT = "# PR Evidence\n\nCite the loop-runs log, CI output, screenshot, video, and artifact evidence.\n"
    SKILL = "---\nname: loop-skill\ndescription: Filled OMP loop skill artifact\n---\n# Skill\n\nUse this loop skill artifact for safe loop operations.\n"

    def test_loop_runs_dir_pass_and_fail(self):
        self.assertEqual(self.s(loop.loop_runs_dir(self.ctx({"loop-runs/README.md": self.FILLED}))), Status.PASS)
        self.assertEqual(self.s(loop.loop_runs_dir(self.ctx({}))), Status.FAIL)
        v = loop.loop_runs_dir(self.ctx({"loop-runs/README.md": "# Loop\n\nTODO write the loop run convention in detail.\n"}))
        self.assertEqual(v.status, Status.FAIL)
        self.assertIn("placeholder", v.rationale)

    def test_rules_index_pass_and_fail(self):
        self.assertEqual(self.s(loop.rules_index(self.ctx({".omp/rules/README.md": self.RULES}))), Status.PASS)
        self.assertEqual(self.s(loop.rules_index(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(loop.rules_index(self.ctx({".omp/rules/README.md": "# Policy\n\nThis describes safe execution without the required index terms.\n"}))), Status.FAIL)

    def test_denylist_pass_and_fail(self):
        self.assertEqual(self.s(loop.denylist(self.ctx({".omp/rules/denylist.md": self.DENY}))), Status.PASS)
        self.assertEqual(self.s(loop.denylist(self.ctx({}))), Status.FAIL)
        no_policy = "# Policy\n\nThis document has prose about safe execution but no required policy vocabulary.\n"
        self.assertEqual(self.s(loop.denylist(self.ctx({".omp/rules/denylist.md": no_policy}))), Status.FAIL)

    def test_signal_schema_pass_and_fail(self):
        self.assertEqual(self.s(loop.signal_schema(self.ctx({"signals/README.md": self.SIGNAL}))), Status.PASS)
        self.assertEqual(self.s(loop.signal_schema(self.ctx({}))), Status.FAIL)
        no_fence = "# Signal\n\nschema_version signal source timestamp evidence are documented without code.\n"
        self.assertEqual(self.s(loop.signal_schema(self.ctx({"signals/README.md": no_fence}))), Status.FAIL)

    def test_pr_artifact_template_variants(self):
        generic = "# Pull Request\n\nSummarize the change and testing for reviewers in a normal template.\n"
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({".github/pull_request_template.md": generic}))), Status.FAIL)
        evidence_heading_with_incidental_ci = (
            "# Pull Request\n\n"
            "## Evidence\n\n"
            "Reviewer decisions need sufficient context and logical explanations, but no artifacts.\n"
        )
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({".github/pull_request_template.md": evidence_heading_with_incidental_ci}))), Status.FAIL)
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({".github/pull_request_template.md": self.PR_ARTIFACT}))), Status.PASS)
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({".omp/commands/pr-artifact-template.md": self.FILLED}))), Status.PASS)

    def test_skills_present_minimum(self):
        files = {f".omp/skills/s{i}/SKILL.md": self.SKILL for i in range(loop.LOOP_SKILL_MIN)}
        self.assertEqual(self.s(loop.skills_present(self.ctx(files))), Status.PASS)
        too_few = {f".omp/skills/s{i}/SKILL.md": self.SKILL for i in range(loop.LOOP_SKILL_MIN - 1)}
        v = loop.skills_present(self.ctx(too_few))
        self.assertEqual(v.status, Status.FAIL)
        self.assertEqual(v.rationale, "Only 2 OMP loop skill artifact(s) found (<3).")

    def test_prompt_contracts_pass_and_missing_paths(self):
        files = {".omp/commands/goal.md": self.FILLED, ".omp/commands/loop.md": self.FILLED}
        self.assertEqual(self.s(loop.prompt_contracts(self.ctx(files))), Status.PASS)
        v = loop.prompt_contracts(self.ctx({".omp/commands/goal.md": self.FILLED}))
        self.assertEqual(v.status, Status.FAIL)
        self.assertIn(".omp/commands/loop.md", v.rationale)

    def test_architecture_doc_pass_and_fail(self):
        self.assertEqual(self.s(loop.architecture_doc(self.ctx({"docs/architecture.md": self.FILLED}))), Status.PASS)
        self.assertEqual(self.s(loop.architecture_doc(self.ctx({}))), Status.FAIL)

    def test_domain_docs_pass_and_fail(self):
        ordinary_markdown = (
            "# Billing Domain\n\n"
            "- [ ] Keep the billing workflow documented for maintainers.\n"
            "See [reference](https://example.com) for external context and examples.\n"
        )
        self.assertEqual(self.s(loop.domain_docs(self.ctx({"domains/billing/README.md": ordinary_markdown}))), Status.PASS)
        self.assertEqual(self.s(loop.domain_docs(self.ctx({}))), Status.FAIL)
        placeholder = "# Domain\n\n[owner] should replace this placeholder with domain documentation.\n"
        self.assertEqual(self.s(loop.domain_docs(self.ctx({"domains/core/README.md": placeholder}))), Status.FAIL)
class TestPhase5BuildChecks(CheckCase):
    def test_build_command_documented(self):
        self.assertEqual(self.s(build.build_command_documented(
            self.ctx({"package.json": '{"scripts":{"build":"tsc"}}'}))), Status.PASS)
        readme = "# Project\n\n## Build\n\n```\nmake release\n```\n"
        self.assertEqual(self.s(build.build_command_documented(
            self.ctx({"README.md": readme}))), Status.PASS)
        no_block = "# Project\n\n## Build\n\nRun it somehow.\n"  # heading, no code block
        self.assertEqual(self.s(build.build_command_documented(
            self.ctx({"README.md": no_block}))), Status.FAIL)
        self.assertEqual(self.s(build.build_command_documented(
            self.ctx({"README.md": "# Project\n\n## Usage\n"}))), Status.FAIL)

    def test_ci_duration_budget(self):
        runs_fast = ('{"workflow_runs":[{"run_started_at":"2026-06-01T00:00:00Z",'
                     '"updated_at":"2026-06-01T00:05:00Z"}]}')
        runs_slow = ('{"workflow_runs":[{"run_started_at":"2026-06-01T00:00:00Z",'
                     '"updated_at":"2026-06-01T00:30:00Z"}]}')
        runs_untimed = '{"workflow_runs":[{"conclusion":"success"}]}'
        cfg = {".agents/readiness/config.json": '{"ci_budget_minutes": 15}'}
        runs_key = ("api", "repos/o/r/actions/runs?per_page=20")
        # no github -> skipped
        self.assertEqual(self.s(build.ci_duration_budget(self.ctx(cfg))), Status.SKIPPED)
        # github but no budget -> unknown
        self.assertEqual(self.s(build.ci_duration_budget(
            self.ctx({}, gh=_gh_available({runs_key: runs_fast})))), Status.UNKNOWN)
        # injected budget option -> pass
        self.assertEqual(self.s(build.ci_duration_budget(
            self.ctx({}, gh=_gh_available({runs_key: runs_fast}),
                     options={"readiness_config": {"ci_budget_minutes": 15}}))), Status.PASS)
        # budget but no timed runs -> unknown
        self.assertEqual(self.s(build.ci_duration_budget(
            self.ctx(cfg, gh=_gh_available({runs_key: runs_untimed})))), Status.UNKNOWN)
        # within budget -> pass
        self.assertEqual(self.s(build.ci_duration_budget(
            self.ctx(cfg, gh=_gh_available({runs_key: runs_fast})))), Status.PASS)
        # exceeds budget -> fail
        self.assertEqual(self.s(build.ci_duration_budget(
            self.ctx(cfg, gh=_gh_available({runs_key: runs_slow})))), Status.FAIL)

    def test_run_minutes_malformed(self):
        self.assertIsNone(build._run_minutes({"run_started_at": "bad", "updated_at": "also-bad"}))
        self.assertIsNone(build._run_minutes({}))


class TestPhase5TestingChecks(CheckCase):
    def test_coverage_threshold(self):
        wf = {".github/workflows/aaa.yml": "name: lint\nrun: echo no coverage here\n",
              ".github/workflows/ci.yml": "name: ci\nrun: coverage report --fail-under=90\n"}
        # config + CI enforcement -> pass (first workflow has no enforce token, second does)
        self.assertEqual(self.s(testing.coverage_threshold(
            self.ctx({".coveragerc": "[run]\n", **wf}))), Status.PASS)
        # config only -> fail
        self.assertEqual(self.s(testing.coverage_threshold(
            self.ctx({"pyproject.toml": "[tool.coverage.run]\nbranch=true\n"}))), Status.FAIL)
        # no config -> fail
        self.assertEqual(self.s(testing.coverage_threshold(self.ctx(wf))), Status.FAIL)
        # jest coverageThreshold config path + codecov enforcement
        jest = {"package.json": '{"jest":{"coverageThreshold":{"global":{"lines":80}}}}',
                ".github/workflows/ci.yml": "name: ci\nuses: codecov/codecov-action@v4\n"}
        self.assertEqual(self.s(testing.coverage_threshold(self.ctx(jest))), Status.PASS)

    def test_flake_quarantine(self):
        doc = "# Testing\n\n## Flaky tests\n\nWe quarantine flaky tests in a separate job.\n"
        self.assertEqual(self.s(testing.flake_quarantine(
            self.ctx({"CONTRIBUTING.md": doc}))), Status.PASS)
        self.assertEqual(self.s(testing.flake_quarantine(
            self.ctx({"README.md": "# x\n\nWe retry tests automatically.\n"}))), Status.FAIL)


class TestPhase5TaskdiscChecks(CheckCase):
    def test_actionable_backlog_items(self):
        issues_good = ('[{"number":1,"labels":[{"name":"bug"}],"body":"steps to reproduce"},'
                       '{"number":2,"milestone":{"title":"v1"},"body":"do the thing"}]')
        issues_bad = '[{"number":1,"labels":[],"body":""},{"number":2,"body":"   "}]'
        key = ("api", "repos/o/r/issues?state=open&per_page=50")
        self.assertEqual(self.s(taskdisc.actionable_backlog_items(self.ctx({}))), Status.SKIPPED)
        self.assertEqual(self.s(taskdisc.actionable_backlog_items(
            self.ctx({}, gh=_gh_available({key: "[]"})))), Status.PASS)
        self.assertEqual(self.s(taskdisc.actionable_backlog_items(
            self.ctx({}, gh=_gh_available({key: issues_good})))), Status.PASS)
        self.assertEqual(self.s(taskdisc.actionable_backlog_items(
            self.ctx({}, gh=_gh_available({key: issues_bad})))), Status.FAIL)


class TestG1CodeHealth(CheckCase):
    def test_naming_convention_rule(self):
        self.assertEqual(self.s(style.naming_convention_rule(self.ctx(
            {".eslintrc.json": '{"rules":{"@typescript-eslint/naming-convention":"error"}}'}))), Status.PASS)
        self.assertEqual(self.s(style.naming_convention_rule(self.ctx(
            {"ruff.toml": 'select = ["N", "E"]\n'}))), Status.PASS)
        self.assertEqual(self.s(style.naming_convention_rule(self.ctx(
            {"pyproject.toml": '[tool.ruff.lint]\nselect = ["N"]\n'}))), Status.PASS)
        self.assertEqual(self.s(style.naming_convention_rule(self.ctx(
            {"ruff.toml": 'select = "N"\n'}))), Status.FAIL)  # non-list select -> no codes
        self.assertEqual(self.s(style.naming_convention_rule(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_complexity_budget(self):
        self.assertEqual(self.s(style.complexity_budget(self.ctx(
            {".eslintrc.json": '{"rules":{"complexity":["error",10]}}'}))), Status.PASS)
        self.assertEqual(self.s(style.complexity_budget(self.ctx(
            {"ruff.toml": 'extend-select = ["C901"]\n'}))), Status.PASS)
        self.assertEqual(self.s(style.complexity_budget(self.ctx(
            {"pyproject.toml": "[tool.ruff.lint.mccabe]\nmax-complexity = 10\n"}))), Status.PASS)
        self.assertEqual(self.s(style.complexity_budget(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_dead_code_detection(self):
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(
            {"package.json": '{"devDependencies":{"knip":"^5"},"scripts":{"deadcode":"knip"}}'}))), Status.PASS)
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            "packages/a/package.json": '{"devDependencies":{"knip":"^5"},"scripts":{"deadcode":"knip"}}',
            "packages/b/package.json": '{"name":"b"}',
        }
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(files, app_path="packages/a"))), Status.PASS)
        files = {
            "package.json": '{"workspaces":["packages/*"],"scripts":{"deadcode":"knip"}}',
            "packages/a/package.json": '{"devDependencies":{"knip":"^5"}}',
            "packages/b/package.json": '{"name":"b"}',
        }
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(files, app_path="packages/a"))), Status.PASS)
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(
            {"knip.json": "{}", "package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_duplicate_code_detection(self):
        self.assertEqual(self.s(style.duplicate_code_detection(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)
        self.assertEqual(self.s(style.duplicate_code_detection(self.ctx(
            {".jscpd.json": "{}", ".github/workflows/ci.yml": "name: ci\nrun: npx jscpd src\n"}))), Status.PASS)
        self.assertEqual(self.s(style.duplicate_code_detection(self.ctx(
            {"package.json": '{"devDependencies":{"jscpd":"^4"}}'}))), Status.FAIL)

    def test_large_file_guard(self):
        self.assertEqual(self.s(style.large_file_guard(self.ctx(
            {".pre-commit-config.yaml": "repos:\n  - hooks:\n      - id: check-added-large-files\n"}))), Status.PASS)
        self.assertEqual(self.s(style.large_file_guard(self.ctx(
            {".gitattributes": "*.psd filter=lfs diff=lfs merge=lfs -text\n"}))), Status.PASS)
        self.assertEqual(self.s(style.large_file_guard(self.ctx(
            {".eslintrc.json": '{"rules":{"max-lines":["error",300]}}'}))), Status.PASS)
        self.assertEqual(self.s(style.large_file_guard(self.ctx(
            {"Makefile": "lint:\n\tgit-sizer\n"}))), Status.PASS)
        self.assertEqual(self.s(style.large_file_guard(self.ctx(
            {".pre-commit-config.yaml": "repos: []\n"}))), Status.FAIL)

    def test_tech_debt_tracking(self):
        self.assertEqual(self.s(style.tech_debt_tracking(self.ctx(
            {"TECH_DEBT.md": "# Debt\n"}))), Status.PASS)
        self.assertEqual(self.s(style.tech_debt_tracking(self.ctx(
            {".eslintrc.json": '{"rules":{"no-warning-comments":["error"]}}'}))), Status.PASS)
        self.assertEqual(self.s(style.tech_debt_tracking(self.ctx(
            {".github/workflows/debt.yml": "name: debt\nrun: npx leasot src\n"}))), Status.PASS)
        self.assertEqual(self.s(style.tech_debt_tracking(self.ctx(
            {"README.md": "# x"}))), Status.FAIL)

    def test_cfg_texts_monorepo_root_fallback(self):
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            ".eslintrc.json": '{"rules":{"@typescript-eslint/naming-convention":"error"}}',
            "packages/a/package.json": '{"name":"a"}',
            "packages/b/package.json": '{"name":"b"}',
        }
        ctx = self.ctx(files, app_path="packages/a")
        self.assertEqual(self.s(style.naming_convention_rule(ctx)), Status.PASS)  # root config via fallback


class TestG2Depth(CheckCase):
    def test_error_tracking(self):
        self.assertEqual(self.s(observability.error_tracking(self.ctx(
            {"package.json": '{"dependencies":{"@sentry/node":"^7"}}', "src/i.js": "Sentry.init({dsn:'x'})\n"}))), Status.PASS)
        self.assertEqual(self.s(observability.error_tracking(self.ctx(
            {"package.json": '{"dependencies":{"@sentry/node":"^7"}}'}))), Status.FAIL)  # import-only

    def test_runbooks(self):
        rb = "# Runbook\n\n## Restart procedure\n\n" + "Follow the operational steps carefully. " * 8
        self.assertEqual(self.s(observability.runbooks(self.ctx({"RUNBOOK.md": rb}))), Status.PASS)
        self.assertEqual(self.s(observability.runbooks(self.ctx({"docs/RUNBOOK.md": "# tiny\n"}))), Status.FAIL)
        prose = "Runbook " * 40  # >=200 chars but no sections/steps
        self.assertEqual(self.s(observability.runbooks(self.ctx({"RUNBOOK.md": prose}))), Status.FAIL)
        self.assertEqual(self.s(observability.runbooks(self.ctx({}))), Status.FAIL)

    def test_profiling(self):
        self.assertEqual(self.s(observability.profiling(self.ctx(
            {"package.json": '{"dependencies":{"@pyroscope/nodejs":"^0.3"}}', "src/p.js": "pyroscope.start()\n"}))), Status.PASS)
        self.assertEqual(self.s(observability.profiling(self.ctx({}))), Status.FAIL)

    def test_circuit_breakers(self):
        self.assertEqual(self.s(observability.circuit_breakers(self.ctx(
            {"package.json": '{"dependencies":{"opossum":"^8"}}', "src/cb.js": "const b = new Opossum(fn)\n"}))), Status.PASS)
        self.assertEqual(self.s(observability.circuit_breakers(self.ctx({}))), Status.FAIL)

    def test_deployment_markers(self):
        self.assertEqual(self.s(observability.deployment_markers(self.ctx(
            {".github/workflows/deploy.yml": "name: deploy\nsteps:\n  - uses: sentry/action-release@v1\n"}))), Status.PASS)
        self.assertEqual(self.s(observability.deployment_markers(self.ctx(
            {".github/workflows/ci.yml": "name: ci\nrun: echo hi\n"}))), Status.FAIL)  # entered, no marker
        self.assertEqual(self.s(observability.deployment_markers(self.ctx({}))), Status.FAIL)  # no workflows

    def test_dependency_min_age(self):
        self.assertEqual(self.s(security.dependency_min_age(self.ctx(
            {"renovate.json": '{"minimumReleaseAge":"3 days"}'}))), Status.PASS)
        self.assertEqual(self.s(security.dependency_min_age(self.ctx(
            {"package.json": '{"renovate":{"stabilityDays":3}}'}))), Status.PASS)
        self.assertEqual(self.s(security.dependency_min_age(self.ctx(
            {"renovate.json": '{"extends":["config:base"]}'}))), Status.FAIL)  # renovate w/o age policy
        self.assertEqual(self.s(security.dependency_min_age(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_log_scrubbing(self):
        self.assertEqual(self.s(security.log_scrubbing(self.ctx(
            {"src/log.js": "logger.redact(['password'])\n"}))), Status.PASS)
        self.assertEqual(self.s(security.log_scrubbing(self.ctx(
            {"src/log.js": "console.log('hi')\n"}))), Status.FAIL)

    def test_secrets_management(self):
        self.assertEqual(self.s(security.secrets_management(self.ctx(
            {".github/workflows/ci.yml": "env:\n  T: ${{ secrets.TOKEN }}\n"}))), Status.PASS)
        self.assertEqual(self.s(security.secrets_management(self.ctx(
            {"package.json": '{"dependencies":{"@google-cloud/secret-manager":"^5"}}'}))), Status.PASS)
        self.assertEqual(self.s(security.secrets_management(self.ctx(
            {".github/workflows/ci.yml": "name: ci\nrun: echo hi\n"}))), Status.FAIL)  # entered, no secret ref
        self.assertEqual(self.s(security.secrets_management(self.ctx({}))), Status.FAIL)

    def test_dast(self):
        self.assertEqual(self.s(security.dast(self.ctx(
            {".github/workflows/sec.yml": "steps:\n  - uses: zaproxy/action-baseline@v0\n"}))), Status.PASS)
        self.assertEqual(self.s(security.dast(self.ctx(
            {".github/workflows/ci.yml": "name: ci\nrun: echo\n"}))), Status.FAIL)  # entered, no dast
        self.assertEqual(self.s(security.dast(self.ctx({}))), Status.FAIL)


class TestG3Hygiene(CheckCase):
    def test_unused_dependencies(self):
        self.assertEqual(self.s(build.unused_dependencies(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)
        self.assertEqual(self.s(build.unused_dependencies(self.ctx(
            {"package.json": '{"devDependencies":{"depcheck":"^1"},"scripts":{"deps":"depcheck"}}'}))), Status.PASS)
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            "packages/a/package.json": '{"devDependencies":{"depcheck":"^1"},"scripts":{"deps":"depcheck"}}',
            "packages/b/package.json": '{"name":"b"}',
        }
        self.assertEqual(self.s(build.unused_dependencies(self.ctx(files, app_path="packages/a"))), Status.PASS)
        self.assertEqual(self.s(build.unused_dependencies(self.ctx(
            {"knip.json": "{}", "package.json": '{"name":"x"}'}))), Status.FAIL)  # tool, no wiring

    def test_version_drift(self):
        self.assertEqual(self.s(build.version_drift(self.ctx(
            {"package.json": '{"devDependencies":{"syncpack":"^12"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.version_drift(self.ctx(
            {"pnpm-workspace.yaml": "catalog:\n  react: ^18\n"}))), Status.PASS)
        self.assertEqual(self.s(build.version_drift(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_monorepo_tooling(self):
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx({"turbo.json": "{}"}))), Status.PASS)
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx(
            {"package.json": '{"devDependencies":{"nx":"^18"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx(
            {"package.json": '{"workspaces":["packages/*"]}'}))), Status.PASS)
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx({"README.md": "# x"}))), Status.FAIL)  # no manifest

    def test_single_command_setup(self):
        self.assertEqual(self.s(build.single_command_setup(self.ctx({"bin/setup": "#!/bin/sh\n"}))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx(
            {"Makefile": "setup:\n\tpip install -e .\n"}))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx(
            {".devcontainer/devcontainer.json": '{"postCreateCommand":"make setup"}'}))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx(
            {"package.json": '{"scripts":{"setup":"npm i"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx({"README.md": "# x"}))), Status.FAIL)

    def test_release_notes_automation(self):
        self.assertEqual(self.s(build.release_notes_automation(self.ctx({".releaserc": "{}"}))), Status.PASS)
        self.assertEqual(self.s(build.release_notes_automation(self.ctx(
            {"package.json": '{"devDependencies":{"@changesets/cli":"^2"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.release_notes_automation(self.ctx(
            {"pyproject.toml": "[tool.towncrier]\npackage = \"x\"\n"}))), Status.PASS)
        self.assertEqual(self.s(build.release_notes_automation(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_dependency_weight_budget(self):
        self.assertEqual(self.s(build.dependency_weight_budget(self.ctx(
            {"package.json": '{"size-limit":[{"limit":"10 kb"}]}'}))), Status.PASS)
        self.assertEqual(self.s(build.dependency_weight_budget(self.ctx(
            {".size-limit.json": "[]", "package.json": '{"name":"x"}'}))), Status.PASS)
        self.assertEqual(self.s(build.dependency_weight_budget(self.ctx(
            {"package.json": '{"devDependencies":{"size-limit":"^11"},"scripts":{"size":"size-limit"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.dependency_weight_budget(self.ctx(
            {"package.json": '{"devDependencies":{"webpack-bundle-analyzer":"^4"}}'}))), Status.FAIL)  # dep, no wiring
        self.assertEqual(self.s(build.dependency_weight_budget(self.ctx({"README.md": "# x"}))), Status.FAIL)

    def test_local_services(self):
        self.assertEqual(self.s(devenv.local_services(self.ctx(
            {"docker-compose.yml": "services:\n  db:\n    image: postgres\n"}))), Status.PASS)
        self.assertEqual(self.s(devenv.local_services(self.ctx({"README.md": "# x"}))), Status.FAIL)

    def test_database_schema(self):
        self.assertEqual(self.s(devenv.database_schema(self.ctx(
            {"migrations/001_init.sql": "CREATE TABLE x(id int);"}))), Status.PASS)
        self.assertEqual(self.s(devenv.database_schema(self.ctx({"src/app.py": "x = 1\n"}))), Status.FAIL)


class TestG4DocsProduct(CheckCase):
    def test_auto_generation(self):
        self.assertEqual(self.s(docs.auto_generation(self.ctx({"README.md": "# x"}))), Status.FAIL)
        self.assertEqual(self.s(docs.auto_generation(self.ctx(
            {"mkdocs.yml": "site_name: X\n", ".github/workflows/docs.yml": "name: docs\nrun: mkdocs build\n"}))), Status.PASS)
        self.assertEqual(self.s(docs.auto_generation(self.ctx(
            {"typedoc.json": "{}", "package.json": '{"name":"x"}'}))), Status.FAIL)  # tool, no wiring

    def test_agents_md_ci_validation(self):
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx({"README.md": "# x"}))), Status.FAIL)
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx(
            {"AGENTS.md": "# A\n", ".github/workflows/ci.yml": "name: ci\nrun: validate AGENTS.md commands\n"}))), Status.PASS)
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx({"AGENTS.md": "# A\n"}))), Status.FAIL)  # no CI
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx(
            {"AGENTS.md": "# A\n", ".github/workflows/ci.yml": "name: ci\nrun: echo\n"}))), Status.FAIL)  # CI, no check

    def test_architecture_doc(self):
        self.assertEqual(self.s(docs.architecture_doc(self.ctx(
            {"ARCHITECTURE.md": "# Architecture\n\n" + "Layered design described in depth. " * 8}))), Status.PASS)
        self.assertEqual(self.s(docs.architecture_doc(self.ctx({"ARCHITECTURE.md": "# tiny\n"}))), Status.FAIL)
        self.assertEqual(self.s(docs.architecture_doc(self.ctx({"README.md": "# x"}))), Status.FAIL)

    def test_error_to_insight(self):
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            {"package.json": '{"dependencies":{"@sentry/node":"^7"}}',
             ".github/workflows/sentry.yml": "name: s\nsteps:\n  - uses: getsentry/action-release@v1\n"}))), Status.PASS)
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            {".github/workflows/ci.yml": "name: ci\nrun: echo\n"}))), Status.FAIL)  # neither
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            {"package.json": '{"dependencies":{"@sentry/node":"^7"}}'}))), Status.FAIL)  # tracker only
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            {".github/workflows/s.yml": "name: s\nsteps:\n  - uses: getsentry/action-release@v1\n"}))), Status.FAIL)  # integ only


if __name__ == "__main__":
    unittest.main()
