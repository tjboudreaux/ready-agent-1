import unittest

from readiness.checks import build, devenv, docs, security, style, taskdisc, testing
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
    def ctx(self, files, gh=None, git=None, app_path="."):
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


if __name__ == "__main__":
    unittest.main()
