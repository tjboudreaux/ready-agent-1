import unittest
from datetime import UTC
from unittest import mock

from readiness.checks import (
    build,
    devenv,
    docs,
    loop,
    observability,
    product,
    security,
    style,
    taskdisc,
    testing,
)
from readiness.checks._helpers import acdc_config, check_needles
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.context import Context
from readiness.detect import detect
from readiness.model import Status
from readiness.process import BoundedProcessResult, ProcessState

from tests._util import fake_runner, gh_runner, make_repo, rmtree


def _gh_available(extra=None):
    """Endpoint map for an available GitHub collector (identity comes from ``origin``).

    Keys are bare ``gh api`` endpoints; ``gh_runner`` wraps them in the fixed
    ``("api", "--hostname", "github.com", "--include", endpoint)`` argv/envelope shape.
    """
    return dict(extra or {})


class CheckCase(unittest.TestCase):
    def ctx(self, files, gh=None, git=None, app_path=".", options=None):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        app = next((a for a in det.apps if a.path == app_path), det.apps[0])
        git_runner = git if callable(git) else fake_runner(git or {})
        return Context(
            root=root, detection=det, static=static,
            git=GitCollector(root, runner=git_runner, static=static),
            github=GithubCollector(
                root,
                origin=("github.com", "o", "r") if gh is not None else (),
                runner=gh_runner(gh or {})),
            app=app,
            options=options or {},
        )

    def s(self, verdict):
        return verdict.status


class TestStyleChecks(CheckCase):
    def test_linter_paths(self):
        self.assertEqual(self.s(style.linter_config(self.ctx(
            {".eslintrc.json": "{}"}))), Status.PASS)
        self.assertEqual(self.s(style.linter_config(self.ctx(
            {"pyproject.toml": '[tool.ruff]\nx=1\n'}))), Status.PASS)
        self.assertEqual(self.s(style.linter_config(self.ctx(
            {"package.json": '{"devDependencies":{"eslint":"^9"}}'}))), Status.PASS)
        self.assertEqual(self.s(style.linter_config(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_formatter_paths(self):
        self.assertEqual(self.s(style.formatter(self.ctx({".prettierrc": "{}"}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx(
            {"pyproject.toml": "[tool.black]\nx=1\n"}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx(
            {"package.json": '{"devDependencies":{"prettier":"^3"}}'}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx({"go.mod": "module x\n"}))), Status.PASS)
        self.assertEqual(self.s(style.formatter(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_type_check_paths(self):
        self.assertEqual(self.s(style.type_check(self.ctx({"go.mod": "module x\n"}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx({"tsconfig.json": "{}"}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx(
            {"package.json": '{"devDependencies":{"typescript":"^5"}}'}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx(
            {"pyproject.toml": "[tool.mypy]\nx=1\n"}))), Status.PASS)
        self.assertEqual(self.s(style.type_check(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_strict_typing_paths(self):
        self.assertEqual(self.s(style.strict_typing(self.ctx(
            {"go.mod": "module x\n"}))), Status.PASS)
        self.assertEqual(self.s(style.strict_typing(self.ctx(
            {"tsconfig.json": '{"compilerOptions":{"strict":true}}'}))), Status.PASS)
        self.assertEqual(self.s(style.strict_typing(self.ctx(
            {"tsconfig.json": '{"compilerOptions":{}}'}))), Status.FAIL)
        self.assertEqual(self.s(style.strict_typing(self.ctx(
            {"pyproject.toml": "[tool.mypy]\nstrict=true\n"}))), Status.PASS)
        self.assertEqual(self.s(style.strict_typing(self.ctx(
            {"pyproject.toml": "[tool.mypy]\nx=1\n"}))), Status.FAIL)
        self.assertEqual(self.s(style.strict_typing(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_precommit_paths(self):
        self.assertEqual(self.s(style.precommit_hooks(self.ctx(
            {".pre-commit-config.yaml": "repos: []\n"}))), Status.PASS)
        self.assertEqual(self.s(style.precommit_hooks(self.ctx(
            {"package.json": '{"lint-staged":{}}'}))), Status.PASS)
        self.assertEqual(self.s(style.precommit_hooks(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_atool_root_fallback_in_monorepo(self):
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            "pyproject.toml": "[tool.ruff]\nx=1\n",
            "packages/a/package.json": '{"name":"a"}',
            "packages/b/package.json": '{"name":"b"}',
        }
        ctx = self.ctx(files, app_path="packages/a")
        # via root [tool.ruff] fallback
        self.assertEqual(self.s(style.linter_config(ctx)), Status.PASS)

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

    def test_vcs_cli_unreadable_git_is_unknown(self):
        # An unsafe/unreadable repository is never called "not version controlled".
        def timeout_runner(args):
            return BoundedProcessResult(ProcessState.TIMEOUT, returncode=None)
        self.assertEqual(self.s(build.vcs_cli(self.ctx({}, git=timeout_runner))),
                         Status.UNKNOWN)

    def test_agentic_development(self):
        self.assertEqual(self.s(build.agentic_development(self.ctx({}))), Status.UNKNOWN)
        git_repo = {("rev-parse", "--is-inside-work-tree"): "true\n",
                    ("log", "-100", "--format=%an%n%ae%n%B%n==="): "T\nt@x\nfix\n===\n"}
        self.assertEqual(self.s(build.agentic_development(self.ctx({}, git=git_repo))), Status.FAIL)

    def test_ci_present_via_gh(self):
        ctx = self.ctx({}, gh=_gh_available(
            {"repos/o/r/actions/workflows?per_page=100": '{"workflows":[{"name":"ci"}]}'}))
        self.assertEqual(self.s(build.ci_present(ctx)), Status.PASS)
        self.assertEqual(self.s(build.ci_present(self.ctx({}))), Status.FAIL)

    def test_ci_runs_tests_variants(self):
        self.assertEqual(self.s(build.ci_runs_tests(self.ctx({}))), Status.SKIPPED)
        no_wf = self.ctx({}, gh=_gh_available(
            {"repos/o/r/actions/workflows?per_page=100": '{"workflows":[]}'}))
        self.assertEqual(self.s(build.ci_runs_tests(no_wf)), Status.FAIL)
        wf_no_tests = self.ctx({}, gh=_gh_available({
            "repos/o/r/actions/workflows?per_page=100": '{"workflows":[{"name":"ci"}]}',
            "repos/o/r/actions/runs?per_page=20": '{"workflow_runs":[{}]}',
        }))
        self.assertEqual(self.s(build.ci_runs_tests(wf_no_tests)), Status.FAIL)
        wf_tests_no_runs = self.ctx({"src/x.test.ts": "t"}, gh=_gh_available({
            "repos/o/r/actions/workflows?per_page=100": '{"workflows":[{"name":"ci"}]}',
            "repos/o/r/actions/runs?per_page=20": '{"workflow_runs":[]}',
        }))
        self.assertEqual(self.s(build.ci_runs_tests(wf_tests_no_runs)), Status.FAIL)

    def test_release_automation(self):
        self.assertEqual(self.s(build.release_automation(self.ctx(
            {"package.json": '{"devDependencies":{"semantic-release":"^x"}}'}))), Status.PASS)
        # No explicit artifact-publication path: skipped, not failed (0.11.0 applicability).
        v = build.release_automation(self.ctx({}))
        self.assertEqual(v.status, Status.SKIPPED)
        self.assertEqual(v.reason_code, "build.release_automation.not_applicable")


class TestDocsChecks(CheckCase):
    def test_readme_variants(self):
        self.assertEqual(self.s(docs.readme(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(docs.readme(self.ctx({"README.md": "# tiny"}))), Status.FAIL)
        self.assertEqual(self.s(docs.readme(self.ctx(
            {"README.md": "plain text " * 40}))), Status.FAIL)

    def test_agents_md_validation_variants(self):
        self.assertEqual(self.s(docs.agents_md_validation(self.ctx(
            {"AGENTS.md": "# only one heading\n\ntext"}))), Status.FAIL)
        long_doc = "# A\n## B\n" + ("\n" * 420)
        self.assertEqual(self.s(docs.agents_md_validation(self.ctx(
            {"AGENTS.md": long_doc}))), Status.FAIL)

    def test_doc_freshness_variants(self):
        # no git
        self.assertEqual(self.s(docs.doc_freshness(self.ctx({"README.md": "# x"}))), Status.UNKNOWN)
        no_docs_git = {("log", "-1", "--format=%cI"): "2026-06-01T00:00:00+00:00\n"}
        self.assertEqual(self.s(docs.doc_freshness(self.ctx({}, git=no_docs_git))), Status.UNKNOWN)
        stale_git = {
            ("log", "-1", "--format=%cI"): "2026-06-01T00:00:00+00:00\n",
            ("log", "-1", "--format=%cI", "--", "README.md"): "2024-01-01T00:00:00+00:00\n",
        }
        self.assertEqual(self.s(docs.doc_freshness(self.ctx(
            {"README.md": "# x"}, git=stale_git))), Status.FAIL)

    def test_doc_freshness_edge_branches(self):
        # agents_md_validation: AGENTS.md absent/unreadable
        self.assertEqual(self.s(docs.agents_md_validation(self.ctx(
            {"README.md": "# x"}))), Status.FAIL)
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
        self.assertEqual(self.s(docs.api_schema_docs(self.ctx(
            {"pyproject.toml": '[project]\nname="x"\ndependencies=["fastapi"]\n'}))), Status.PASS)
        self.assertEqual(self.s(docs.api_schema_docs(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_skills_fail(self):
        self.assertEqual(self.s(docs.skills(self.ctx({"README.md": "# x"}))), Status.FAIL)


class TestDevenvChecks(CheckCase):
    def test_fails(self):
        self.assertEqual(self.s(devenv.env_template(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(devenv.devcontainer(self.ctx({}))), Status.FAIL)


class TestSecurityChecks(CheckCase):
    def test_branch_protection_fail(self):
        # The protection endpoint 404s: confirmed "not protected", never a crash or skip.
        ctx = self.ctx({}, gh=_gh_available(
            {"repos/o/r": '{"full_name":"o/r","default_branch":"main"}'}))
        v = security.branch_protection(ctx)
        self.assertEqual(v.status, Status.FAIL)
        self.assertEqual(v.reason_code, "security.branch_protection.not_protected")

    def test_branch_protection_pass(self):
        ctx = self.ctx({}, gh=_gh_available({
            "repos/o/r": '{"full_name":"o/r","default_branch":"main"}',
            "repos/o/r/branches/main/protection":
                '{"required_pull_request_reviews":{"required_approving_review_count":1}}',
        }))
        v = security.branch_protection(ctx)
        self.assertEqual(v.status, Status.PASS)
        self.assertEqual(v.reason_code, "security.branch_protection.protected")

    def test_secret_scanning_fail(self):
        ctx = self.ctx({}, gh=_gh_available(
            {"repos/o/r": '{"full_name":"o/r","default_branch":"main","security_and_analysis":'
                '{"secret_scanning":{"status":"disabled"}}}'}))
        v = security.secret_scanning(ctx)
        self.assertEqual(v.status, Status.FAIL)
        self.assertEqual(v.reason_code, "security.secret_scanning.disabled")

    def test_simple_fails(self):
        self.assertEqual(self.s(security.codeowners(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(security.dependency_update_automation(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(security.security_md(self.ctx({}))), Status.FAIL)

    def test_automated_security_review(self):
        self.assertEqual(self.s(security.automated_security_review(self.ctx(
            {"pyproject.toml": '[project]\nname="x"\ndependencies=["bandit"]\n'}))), Status.PASS)
        self.assertEqual(self.s(security.automated_security_review(self.ctx({}))), Status.FAIL)

    def test_gitignore_partial(self):
        # secret only
        self.assertEqual(self.s(security.gitignore_comprehensive(self.ctx(
            {".gitignore": ".env\n"}))), Status.FAIL)
        # artifact only
        self.assertEqual(self.s(security.gitignore_comprehensive(self.ctx(
            {".gitignore": "dist/\n"}))), Status.FAIL)
        # none
        self.assertEqual(self.s(security.gitignore_comprehensive(self.ctx({}))), Status.FAIL)


class TestTestingChecks(CheckCase):
    def test_unit_fail(self):
        self.assertEqual(self.s(testing.unit_tests_exist(self.ctx(
            {"src/app.py": "x=1"}))), Status.FAIL)

    def test_integration_via_dep(self):
        self.assertEqual(self.s(testing.integration_tests_exist(self.ctx(
            {"package.json": '{"devDependencies":{"cypress":"^13"}}'}))), Status.PASS)
        self.assertEqual(self.s(testing.integration_tests_exist(self.ctx(
            {"src/app.py": "x"}))), Status.FAIL)

    def test_naming_variants(self):
        self.assertEqual(self.s(testing.test_naming(self.ctx(
            {"tests/test_x.py": "x"}))), Status.PASS)
        # dir but nonstandard
        self.assertEqual(self.s(testing.test_naming(self.ctx(
            {"tests/helper.py": "x"}))), Status.FAIL)
        # no tests
        self.assertEqual(self.s(testing.test_naming(self.ctx({"src/app.py": "x"}))), Status.SKIPPED)


class TestTaskDiscChecks(CheckCase):
    def test_templates_fail(self):
        self.assertEqual(self.s(taskdisc.issue_templates(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(taskdisc.pr_templates(self.ctx({}))), Status.FAIL)

    def test_issue_labeling(self):
        only_default = self.ctx({}, gh=_gh_available(
            {"repos/o/r/labels?per_page=100": '[{"name":"bug"},{"name":"enhancement"}]'}))
        self.assertEqual(self.s(taskdisc.issue_labeling(only_default)), Status.FAIL)
        via_file = self.ctx({".github/labels.yml": "x"}, gh=_gh_available(
            {"repos/o/r/labels?per_page=100": '[{"name":"bug"}]'}))
        self.assertEqual(self.s(taskdisc.issue_labeling(via_file)), Status.PASS)

    def test_backlog_health_low(self):
        ctx = self.ctx({}, gh=_gh_available({
            "repos/o/r/issues?state=open&per_page=50": (
                '[{"number":1,"labels":[{"name":"bug"}]},{"number":2,"labels":[]},{"number":3,'
                '"labels":[]}]'
            ),
        }))
        self.assertEqual(self.s(taskdisc.backlog_health(ctx)), Status.FAIL)



class TestLoopChecks(CheckCase):
    FILLED = (
        "# Artifact\n\nThis filled loop readiness artifact documents a stable maintainer-owned "
        "convention with enough detail.\n"
    )
    RULES = (
        "# Loop Rules\n\nThis rules index points maintainers to the denylist and related loop "
        "policies.\n"
    )
    DENY = (
        "# Loop Denylist\n\n- Never read or export secrets, credentials, or .env files.\n"
        "- Never run destructive deletes or drop data.\n"
        "- Never push, merge, deploy, release, or publish without human confirmation.\n"
        "- Never disable CI, tests, security scanning, or branch protection.\n"
    )
    SIGNAL = (
        "# Signal Schema\n\n```json\n"
        "{\"schema_version\":\"1\",\"signal\":\"loop.run\",\"source\":\"runner\","
        "\"timestamp\":\"2026-01-01T00:00:00Z\",\"evidence\":[]}\n"
        "```\n"
    )
    PR_ARTIFACT = (
        "# PR Evidence\n\nCite the loop-runs log, CI output, screenshot, video, and artifact "
        "evidence.\n"
    )
    SKILL = (
        "---\nname: loop-skill\ndescription: Filled OMP loop skill artifact\n---\n# Skill\n\nUse "
        "this loop skill artifact for safe loop operations.\n"
    )

    def test_loop_runs_dir_pass_and_fail(self):
        self.assertEqual(self.s(loop.loop_runs_dir(self.ctx(
            {"loop-runs/README.md": self.FILLED}))), Status.PASS)
        self.assertEqual(self.s(loop.loop_runs_dir(self.ctx({}))), Status.FAIL)
        v = loop.loop_runs_dir(self.ctx({"loop-runs/README.md": "# Loop\n\nTODO write the loop run "
            "convention in detail.\n"}))
        self.assertEqual(v.status, Status.FAIL)
        self.assertIn("placeholder", v.rationale)

    def test_rules_index_pass_and_fail(self):
        self.assertEqual(self.s(loop.rules_index(self.ctx(
            {".omp/rules/README.md": self.RULES}))), Status.PASS)
        self.assertEqual(self.s(loop.rules_index(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(loop.rules_index(self.ctx(
            {".omp/rules/README.md": "# Policy\n\nThis describes safe execution without the "
                "required index terms.\n"}))), Status.FAIL)

    def test_denylist_pass_and_fail(self):
        self.assertEqual(self.s(loop.denylist(self.ctx(
            {".omp/rules/denylist.md": self.DENY}))), Status.PASS)
        self.assertEqual(self.s(loop.denylist(self.ctx({}))), Status.FAIL)
        no_policy = (
            "# Policy\n\nThis document has prose about safe execution but no required policy "
            "vocabulary.\n"
        )
        self.assertEqual(self.s(loop.denylist(self.ctx(
            {".omp/rules/denylist.md": no_policy}))), Status.FAIL)

    def test_signal_schema_pass_and_fail(self):
        self.assertEqual(self.s(loop.signal_schema(self.ctx(
            {"signals/README.md": self.SIGNAL}))), Status.PASS)
        self.assertEqual(self.s(loop.signal_schema(self.ctx({}))), Status.FAIL)
        no_fence = (
            "# Signal\n\nschema_version signal source timestamp evidence are documented without "
            "code.\n"
        )
        self.assertEqual(self.s(loop.signal_schema(self.ctx(
            {"signals/README.md": no_fence}))), Status.FAIL)

    def test_pr_artifact_template_variants(self):
        generic = (
            "# Pull Request\n\nSummarize the change and testing for reviewers in a normal template."
            "\n"
        )
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx(
            {".github/pull_request_template.md": generic}))), Status.FAIL)
        evidence_heading_with_incidental_ci = (
            "# Pull Request\n\n"
            "## Evidence\n\n"
            "Reviewer decisions need sufficient context and logical explanations, but no artifacts."
                "\n"
        )
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({
            ".github/pull_request_template.md": evidence_heading_with_incidental_ci,
            }))), Status.FAIL)
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({
            ".github/pull_request_template.md": self.PR_ARTIFACT,
            }))), Status.PASS)
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({
            ".omp/commands/pr-artifact-template.md": self.FILLED,
            }))), Status.PASS)

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
        self.assertEqual(self.s(loop.architecture_doc(self.ctx({
            "docs/architecture.md": self.FILLED,
            }))), Status.PASS)
        self.assertEqual(self.s(loop.architecture_doc(self.ctx({}))), Status.FAIL)

    def test_domain_docs_pass_and_fail(self):
        ordinary_markdown = (
            "# Billing Domain\n\n"
            "- [ ] Keep the billing workflow documented for maintainers.\n"
            "See [reference](https://example.com) for external context and examples.\n"
        )
        self.assertEqual(self.s(loop.domain_docs(self.ctx({
            "domains/billing/README.md": ordinary_markdown,
            }))), Status.PASS)
        self.assertEqual(self.s(loop.domain_docs(self.ctx({}))), Status.FAIL)
        placeholder = (
            "# Domain\n\n[owner] should replace this placeholder with domain documentation.\n"
        )
        self.assertEqual(self.s(loop.domain_docs(self.ctx({
            "domains/core/README.md": placeholder,
            }))), Status.FAIL)
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
        cfg = {".ra1/config.json": '{"ci_budget_minutes": 15}'}
        runs_key = "repos/o/r/actions/runs?per_page=20"
        # no github -> skipped
        self.assertEqual(self.s(build.ci_duration_budget(self.ctx(cfg))), Status.SKIPPED)
        # github but no budget -> unknown
        self.assertEqual(self.s(build.ci_duration_budget(
            self.ctx({}, gh=_gh_available({runs_key: runs_fast})))), Status.UNKNOWN)
        # injected budget dependency -> pass
        self.assertEqual(self.s(build.ci_duration_budget(
            self.ctx({}, gh=_gh_available({runs_key: runs_fast}),
                     options={"_deps": {"readiness_config": {"ci_budget_minutes": 15}}}),
        )), Status.PASS)
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
        key = "repos/o/r/issues?state=open&per_page=50"
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
            {
                ".eslintrc.json": '{"rules":{"@typescript-eslint/naming-convention":"error"}}',
            }))), Status.PASS)
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
            {
                "package.json": '{"devDependencies":{"knip":"^5"},"scripts":{"deadcode":"knip"}}',
            }))), Status.PASS)
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            "packages/a/package.json": (
                '{"devDependencies":{"knip":"^5"},"scripts":{"deadcode":"knip"}}'
            ),
            "packages/b/package.json": '{"name":"b"}',
        }
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(
            files, app_path="packages/a"))), Status.PASS)
        files = {
            "package.json": '{"workspaces":["packages/*"],"scripts":{"deadcode":"knip"}}',
            "packages/a/package.json": '{"devDependencies":{"knip":"^5"}}',
            "packages/b/package.json": '{"name":"b"}',
        }
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(
            files, app_path="packages/a"))), Status.PASS)
        self.assertEqual(self.s(style.dead_code_detection(self.ctx(
            {"knip.json": "{}", "package.json": '{"name":"x"}'}))), Status.FAIL)

    def test_duplicate_code_detection(self):
        self.assertEqual(self.s(style.duplicate_code_detection(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)
        self.assertEqual(self.s(style.duplicate_code_detection(self.ctx(
            {
                ".jscpd.json": "{}",
                ".github/workflows/ci.yml": "name: ci\nrun: npx jscpd src\n",
            }))), Status.PASS)
        self.assertEqual(self.s(style.duplicate_code_detection(self.ctx(
            {"package.json": '{"devDependencies":{"jscpd":"^4"}}'}))), Status.FAIL)

    def test_large_file_guard(self):
        self.assertEqual(self.s(style.large_file_guard(self.ctx(
            {
                ".pre-commit-config.yaml": (
                    "repos:\n  - hooks:\n      - id: check-added-large-files\n"
                ),
            }))), Status.PASS)
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
        # root config via fallback
        self.assertEqual(self.s(style.naming_convention_rule(ctx)), Status.PASS)


class TestG2Depth(CheckCase):
    def test_error_tracking(self):
        self.assertEqual(self.s(observability.error_tracking(self.ctx(
            {
                "package.json": '{"dependencies":{"@sentry/node":"^7"}}',
                "src/i.js": "Sentry.init({dsn:'x'})\n",
            }))), Status.PASS)
        self.assertEqual(self.s(observability.error_tracking(self.ctx(
            # import-only
            {
                "package.json": '{"dependencies":{"@sentry/node":"^7"}}',
            }))), Status.FAIL)

    def test_runbooks(self):
        rb = (
            "# Runbook\n\n## Restart procedure\n\n" + "Follow the operational steps carefully. " * 8
        )
        self.assertEqual(self.s(observability.runbooks(self.ctx({"RUNBOOK.md": rb}))), Status.PASS)
        self.assertEqual(self.s(observability.runbooks(self.ctx({
            "docs/RUNBOOK.md": "# tiny\n",
            }))), Status.FAIL)
        prose = "Runbook " * 40  # >=200 chars but no sections/steps
        self.assertEqual(self.s(observability.runbooks(self.ctx({
            "RUNBOOK.md": prose,
            }))), Status.FAIL)
        self.assertEqual(self.s(observability.runbooks(self.ctx({}))), Status.FAIL)

    def test_profiling(self):
        self.assertEqual(self.s(observability.profiling(self.ctx(
            {
                "package.json": '{"dependencies":{"@pyroscope/nodejs":"^0.3"}}',
                "src/p.js": "pyroscope.start()\n",
            }))), Status.PASS)
        self.assertEqual(self.s(observability.profiling(self.ctx({}))), Status.FAIL)

    def test_circuit_breakers(self):
        self.assertEqual(self.s(observability.circuit_breakers(self.ctx(
            {
                "package.json": '{"dependencies":{"opossum":"^8"}}',
                "src/cb.js": "const b = new Opossum(fn)\n",
            }))), Status.PASS)
        self.assertEqual(self.s(observability.circuit_breakers(self.ctx({}))), Status.FAIL)

    def test_deployment_markers(self):
        self.assertEqual(self.s(observability.deployment_markers(self.ctx(
            {
                ".github/workflows/deploy.yml": (
                    "name: deploy\nsteps:\n  - uses: sentry/action-release@v1\n"
                ),
            }))), Status.PASS)
        self.assertEqual(self.s(observability.deployment_markers(self.ctx(
            # entered, no marker
            {
                ".github/workflows/ci.yml": "name: ci\nrun: echo hi\n",
            }))), Status.FAIL)
        # no workflows
        self.assertEqual(self.s(observability.deployment_markers(self.ctx({}))), Status.FAIL)

    def test_dependency_min_age(self):
        self.assertEqual(self.s(security.dependency_min_age(self.ctx(
            {"renovate.json": '{"minimumReleaseAge":"3 days"}'}))), Status.PASS)
        self.assertEqual(self.s(security.dependency_min_age(self.ctx(
            {"package.json": '{"renovate":{"stabilityDays":3}}'}))), Status.PASS)
        self.assertEqual(self.s(security.dependency_min_age(self.ctx(
            # renovate w/o age policy
            {
                "renovate.json": '{"extends":["config:base"]}',
            }))), Status.FAIL)
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
            {
                "package.json": '{"dependencies":{"@google-cloud/secret-manager":"^5"}}',
            }))), Status.PASS)
        self.assertEqual(self.s(security.secrets_management(self.ctx(
            # entered, no secret ref
            {
                ".github/workflows/ci.yml": "name: ci\nrun: echo hi\n",
            }))), Status.FAIL)
        self.assertEqual(self.s(security.secrets_management(self.ctx({}))), Status.FAIL)

    def test_dast(self):
        self.assertEqual(self.s(security.dast(self.ctx(
            {
                ".github/workflows/sec.yml": "steps:\n  - uses: zaproxy/action-baseline@v0\n",
            }))), Status.PASS)
        self.assertEqual(self.s(security.dast(self.ctx(
            # entered, no dast
            {
                ".github/workflows/ci.yml": "name: ci\nrun: echo\n",
            }))), Status.FAIL)
        self.assertEqual(self.s(security.dast(self.ctx({}))), Status.FAIL)


class TestG3Hygiene(CheckCase):
    def test_unused_dependencies(self):
        self.assertEqual(self.s(build.unused_dependencies(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)
        self.assertEqual(self.s(build.unused_dependencies(self.ctx(
            {
                "package.json": (
                    '{"devDependencies":{"depcheck":"^1"},"scripts":{"deps":"depcheck"}}'
                ),
            }))), Status.PASS)
        files = {
            "package.json": '{"workspaces":["packages/*"]}',
            "packages/a/package.json": (
                '{"devDependencies":{"depcheck":"^1"},"scripts":{"deps":"depcheck"}}'
            ),
            "packages/b/package.json": '{"name":"b"}',
        }
        self.assertEqual(self.s(build.unused_dependencies(self.ctx(
            files, app_path="packages/a"))), Status.PASS)
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
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx({
            "turbo.json": "{}",
            }))), Status.PASS)
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx(
            {"package.json": '{"devDependencies":{"nx":"^18"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx(
            {"package.json": '{"workspaces":["packages/*"]}'}))), Status.PASS)
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx(
            {"package.json": '{"name":"x"}'}))), Status.FAIL)
        # no manifest
        self.assertEqual(self.s(build.monorepo_tooling(self.ctx({
            "README.md": "# x",
            }))), Status.FAIL)

    def test_single_command_setup(self):
        self.assertEqual(self.s(build.single_command_setup(self.ctx({
            "bin/setup": "#!/bin/sh\n",
            }))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx(
            {"Makefile": "setup:\n\tpip install -e .\n"}))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx(
            {
                ".devcontainer/devcontainer.json": '{"postCreateCommand":"make setup"}',
            }))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx(
            {"package.json": '{"scripts":{"setup":"npm i"}}'}))), Status.PASS)
        self.assertEqual(self.s(build.single_command_setup(self.ctx({
            "README.md": "# x",
            }))), Status.FAIL)

    def test_release_notes_automation(self):
        self.assertEqual(self.s(build.release_notes_automation(self.ctx({
            ".releaserc": "{}",
            }))), Status.PASS)
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
            {
                "package.json": (
                    '{"devDependencies":{"size-limit":"^11"},"scripts":{"size":"size-limit"}}'
                ),
            }))), Status.PASS)
        self.assertEqual(self.s(build.dependency_weight_budget(self.ctx(
            # dep, no wiring
            {
                "package.json": '{"devDependencies":{"webpack-bundle-analyzer":"^4"}}',
            }))), Status.FAIL)
        self.assertEqual(self.s(build.dependency_weight_budget(self.ctx({
            "README.md": "# x",
            }))), Status.FAIL)

    def test_local_services(self):
        self.assertEqual(self.s(devenv.local_services(self.ctx(
            {"docker-compose.yml": "services:\n  db:\n    image: postgres\n"}))), Status.PASS)
        self.assertEqual(self.s(devenv.local_services(self.ctx({"README.md": "# x"}))), Status.FAIL)

    def test_database_schema(self):
        self.assertEqual(self.s(devenv.database_schema(self.ctx(
            {"migrations/001_init.sql": "CREATE TABLE x(id int);"}))), Status.PASS)
        self.assertEqual(self.s(devenv.database_schema(self.ctx({
            "src/app.py": "x = 1\n",
            }))), Status.FAIL)


class TestG4DocsProduct(CheckCase):
    def test_auto_generation(self):
        self.assertEqual(self.s(docs.auto_generation(self.ctx({"README.md": "# x"}))), Status.FAIL)
        self.assertEqual(self.s(docs.auto_generation(self.ctx(
            {
                "mkdocs.yml": "site_name: X\n",
                ".github/workflows/docs.yml": "name: docs\nrun: mkdocs build\n",
            }))), Status.PASS)
        self.assertEqual(self.s(docs.auto_generation(self.ctx(
            # tool, no wiring
            {
                "typedoc.json": "{}",
                "package.json": '{"name":"x"}',
            }))), Status.FAIL)

    def test_agents_md_ci_validation(self):
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx({
            "README.md": "# x",
            }))), Status.FAIL)
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx(
            {
                "AGENTS.md": "# A\n",
                ".github/workflows/ci.yml": "name: ci\nrun: validate AGENTS.md commands\n",
            }))), Status.PASS)
        # no CI
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx({
            "AGENTS.md": "# A\n",
            }))), Status.FAIL)
        self.assertEqual(self.s(docs.agents_md_ci_validation(self.ctx(
            # CI, no check
            {
                "AGENTS.md": "# A\n",
                ".github/workflows/ci.yml": "name: ci\nrun: echo\n",
            }))), Status.FAIL)

    def test_architecture_doc(self):
        self.assertEqual(self.s(docs.architecture_doc(self.ctx(
            {
                "ARCHITECTURE.md": "# Architecture\n\n" + "Layered design described in depth. " * 8,
            }))), Status.PASS)
        self.assertEqual(self.s(docs.architecture_doc(self.ctx({
            "ARCHITECTURE.md": "# tiny\n",
            }))), Status.FAIL)
        self.assertEqual(self.s(docs.architecture_doc(self.ctx({"README.md": "# x"}))), Status.FAIL)

    def test_error_to_insight(self):
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            {"package.json": '{"dependencies":{"@sentry/node":"^7"}}',
             ".github/workflows/sentry.yml": (
                 "name: s\nsteps:\n  - uses: getsentry/action-release@v1\n"
             ),
            }))), Status.PASS)
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            {".github/workflows/ci.yml": "name: ci\nrun: echo\n"}))), Status.FAIL)  # neither
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            # tracker only
            {
                "package.json": '{"dependencies":{"@sentry/node":"^7"}}',
            }))), Status.FAIL)
        self.assertEqual(self.s(product.error_to_insight(self.ctx(
            # integ only
            {
                ".github/workflows/s.yml": (
                    "name: s\nsteps:\n  - uses: getsentry/action-release@v1\n"
                ),
            }))), Status.FAIL)


class TestAcdcVerificationLoop(CheckCase):
    def test_shared_helpers(self):
        self.assertEqual(check_needles("ESLint . && TSC"), {"eslint", "tsc"})
        self.assertEqual(check_needles("node scripts/gen-tsconfig.js"), set())
        self.assertEqual(check_needles("jest --coverage"), {"jest"})
        self.assertEqual(check_needles("cargo test"), {"cargo test"})
        valid = self.ctx({
            ".ra1/config.json": (
                '{"acdc":{"verify_command":"make check"}}'
            ),
        })
        self.assertEqual(acdc_config(valid), {"verify_command": "make check"})
        invalid = self.ctx({".ra1/config.json": '{"acdc":[]}'})
        self.assertEqual(acdc_config(invalid), {})

    def test_check_command_make_and_prerequisites(self):
        self.assertEqual(self.s(build.check_command(self.ctx({
            "Makefile": "check:\n\truff check . && pytest\n",
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            "Makefile": "check: lint test MODE=fast\n\nlint:\n\truff check .\n\ntest:\n\tpytest\n",
        }))), Status.PASS)

    def test_check_command_package_scripts(self):
        self.assertEqual(self.s(build.check_command(self.ctx({
            "package.json": '{"scripts":{"check":"eslint . && tsc"}}',
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            "package.json": (
                '{"scripts":{"check":"run-s lint test","lint":"eslint .","test":"vitest run"}}'
            ),
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            "package.json": (
                '{"scripts":{"verify":"npm run lint && yarn test","lint":"eslint .","test":"jest"}}'
            ),
        }))), Status.PASS)

    def test_check_command_file_entrypoints(self):
        self.assertEqual(self.s(build.check_command(self.ctx({
            "scripts/check.sh": "#!/bin/sh\nruff check .\npytest\n",
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            "justfile": "check:\n    ruff check .\n    pytest\n",
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            "Taskfile.yml": (
                "version: '3'\ntasks:\n  check:\n    cmds:\n      - ruff check .\n      - pytest\n "
                " other:\n    cmds: [echo]\n"
            ),
        }))), Status.PASS)

    def test_check_command_config_designations(self):
        config = '{"schema_version":"1","acdc":{"verify_command":"make check"}}'
        verdict = build.check_command(self.ctx({
            ".ra1/config.json": config,
            "Makefile": "check:\n\tpytest\n",
        }))
        self.assertEqual(self.s(verdict), Status.PASS)
        self.assertTrue(any(e.source == ".ra1/config.json" for e in verdict.evidence))
        self.assertEqual(self.s(build.check_command(self.ctx({
            ".ra1/config.json": '{"acdc":{"verify_command":"npm run check"}}',
            "package.json": '{"scripts":{"check":"jest --coverage"}}',
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            ".ra1/config.json": '{"acdc":{"verify_command":"scripts/check.sh"}}',
            "scripts/check.sh": "pytest\n",
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            ".ra1/config.json": '{"acdc":{"verify_command":"ruff check ."}}',
        }))), Status.FAIL)  # outside the §5.1.2 accepted grammar
        self.assertEqual(self.s(build.check_command(self.ctx({
            ".ra1/config.json": '{"acdc":{"verify_command":"python3 -m unittest"}}',
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            ".ra1/config.json": '{"acdc":{"verify_command":"task check"}}',
            "Taskfile.yaml": "tasks:\n  check:\n    cmds: [pytest]\n",
        }))), Status.PASS)
        self.assertEqual(self.s(build.check_command(self.ctx({
            ".ra1/config.json": config,
        }))), Status.FAIL)
        self.assertEqual(self.s(build.check_command(self.ctx({
            ".ra1/config.json": '{"acdc":{"verify_command":42}}',
            "Makefile": "check:\n\truff check . && pytest\n",
        }))), Status.PASS)

    def test_check_command_failures(self):
        self.assertEqual(self.s(build.check_command(self.ctx({
            "Makefile": "check:\n\tpytest\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(build.check_command(self.ctx({
            "package.json": '{"scripts":{"check":"jest --coverage"}}',
        }))), Status.FAIL)
        self.assertEqual(self.s(build.check_command(self.ctx({
            "package.json": '{"scripts":{"check":"node scripts/gen-tsconfig.js"}}',
        }))), Status.FAIL)
        self.assertEqual(self.s(build.check_command(self.ctx({}))), Status.FAIL)

    def test_agent_verify_contract_passes(self):
        cases = [
            {"AGENTS.md": "## Build & Test\n\n```sh\npython3 -m unittest\n```\n"},
            {"CLAUDE.md": "Always run `npm test` after every change.\n"},
            {".cursor/rules/verify.mdc": "## Verification\n`pnpm lint`\n"},
            {"AGENTS.md": "## VERIFY phase\nRun `sonar analyze --file src/a.py` after edits.\n"},
        ]
        for files in cases:
            with self.subTest(files=files):
                self.assertEqual(self.s(docs.agent_verify_contract(self.ctx(files))), Status.PASS)
        configured = docs.agent_verify_contract(self.ctx({
            ".ra1/config.json": (
                '{"acdc":{"instruction_files":["docs/agent-guide.md",42]}}'
            ),
            "docs/agent-guide.md": "Execute verification with `ra1 report --project .`.\n",
        }))
        self.assertEqual(self.s(configured), Status.PASS)
        self.assertTrue(any(e.summary == "acdc.instruction_files" for e in configured.evidence))

    def test_agent_verify_contract_failures(self):
        cases = [
            {"AGENTS.md": "## Testing\nTBD\n\n" + "filler\n" * 11 + "## Setup\n`pnpm install`\n"},
            {"AGENTS.md": "## Setup\n```sh\npython3 -m pytest\n```\n"},
            {"AGENTS.md": "## Verification\nNo command is documented.\n"},
            {
                ".ra1/config.json": (
                    '{"acdc":{"instruction_files":["docs/agent-guide.md"]}}'
                ),
                "docs/agent-guide.md": "## Verification\nDescribe checks without a command.\n",
            },
            {},
        ]
        for files in cases:
            with self.subTest(files=files):
                self.assertEqual(self.s(docs.agent_verify_contract(self.ctx(files))), Status.FAIL)

    def test_agent_hooks_passes(self):
        self.assertEqual(self.s(devenv.agent_hooks(self.ctx({
            ".claude/settings.json": (
                '{"hooks":{"PostToolUse":[{"hooks":[{"type":"command","command":"ruff check ."}]}]}'
                '}'
            ),
        }))), Status.PASS)
        self.assertEqual(self.s(devenv.agent_hooks(self.ctx({
            ".claude/settings.json": '{"hooks":{"Stop":{"command":"ra1 report --project ."}}}',
        }))), Status.PASS)
        configured = devenv.agent_hooks(self.ctx({
            ".ra1/config.json": (
                '{"acdc":{"hook_files":[".agents/hooks/post-edit.sh",7]}}'
            ),
            ".agents/hooks/post-edit.sh": "#!/bin/sh\nruff check .\n",
        }))
        self.assertEqual(self.s(configured), Status.PASS)
        self.assertTrue(any(e.summary == "acdc.hook_files" for e in configured.evidence))

    def test_agent_hooks_fallthrough_and_failures(self):
        cases = [
            {".ra1/config.json": '{"acdc":{"hook_files":["missing/*.sh"]}}'},
            {
                ".ra1/config.json": (
                    '{"acdc":{"hook_files":[".agents/hooks/post-edit.sh"]}}'
                ),
                ".agents/hooks/post-edit.sh": "#!/bin/sh\necho done\n",
            },
            {".claude/settings.json": '{"permissions":{}}'},
            {".claude/settings.json": "{invalid"},
            {".claude/settings.json": '{"hooks":{"PostToolUse":[{"command":"echo done"}]}}'},
            {".cursor/rules/always.mdc": "---\nalwaysApply: true\n---\nRun eslint after edits.\n"},
            {},
        ]
        for files in cases:
            with self.subTest(files=files):
                self.assertEqual(self.s(devenv.agent_hooks(self.ctx(files))), Status.FAIL)

    def test_new_code_quality_gate_passes(self):
        cases = [
            {
                "codecov.yml": (
                    "coverage:\n  status:\n    patch:\n      default:\n        target: 80%\n"
                ),
                ".github/workflows/ci.yml": "uses: codecov/codecov-action@v5\n",
            },
            {
                "sonar-project.properties": "sonar.projectKey=x\n",
                ".github/workflows/ci.yaml": "run: sonar analyze\n",
            },
            {"Makefile": "quality:\n\tdiff-cover coverage.xml\n"},
            {".github/workflows/ci.yml": "run: diff_cover coverage.xml\n"},
            {
                "qodana.yaml": "version: 1.0\n",
                ".github/workflows/ci.yml": "uses: JetBrains/qodana-action@v2026\n",
            },
        ]
        for files in cases:
            with self.subTest(files=files):
                self.assertEqual(
                    self.s(testing.new_code_quality_gate(self.ctx(files))), Status.PASS)

    def test_new_code_quality_gate_failures(self):
        cases = [
            {"codecov.yml": "coverage:\n  status:\n    patch:\n"},
            {"sonar-project.properties": "sonar.projectKey=x\n"},
            {},
        ]
        for files in cases:
            with self.subTest(files=files):
                self.assertEqual(
                    self.s(testing.new_code_quality_gate(self.ctx(files))), Status.FAIL)


    def test_check_command_edge_branches(self):
        passes = [
            {
                ".ra1/config.json": '{"acdc":{"verify_command":"just check"}}',
                "Justfile": "check:\n    pytest\n",
            },
            {
                ".ra1/config.json": '{"acdc":{"verify_command":"just check"}}',
                "Justfile": "lint:\n    ruff check .\n",
                "justfile": "check:\n    pytest\n",
            },
            {
                ".ra1/config.json": '{"acdc":{"verify_command":"yarn check"}}',
                "package.json": '{"scripts":{"check":"pytest"}}',
            },
            {
                ".ra1/config.json": '{"acdc":{"verify_command":"pnpm run check"}}',
                "package.json": '{"scripts":{"check":"pytest"}}',
            },
            {
                ".ra1/config.json": '{"acdc":{"verify_command":"scripts/check.sh"}}',
                "scripts/check.sh": "pytest\n",
            },
            {"Makefile": "check: absent\n\truff check . && pytest\n"},
            {
                "package.json": (
                    '{"scripts":{"validate":"run-p --print-label lint missing test","lint":"eslint '
                    '.","test":"jest"}}'
                ),
            },
        ]
        for files in passes:
            with self.subTest(pass_files=files):
                self.assertEqual(self.s(build.check_command(self.ctx(files))), Status.PASS)
        failures = [
            {
                ".ra1/config.json": '{"acdc":{"verify_command":"task check"}}',
                "Taskfile.yml": "tasks:\n  lint:\n    cmds: [ruff]\n",
            },
            {
                ".ra1/config.json": '{"acdc":{"verify_command":"npm run missing"}}',
                "package.json": '{"scripts":{"check":"pytest"}}',
            },
            {".ra1/config.json": '{"acdc":{"verify_command":"custom verify"}}'},
            {"Makefile": "lint:\n\truff check .\n"},
            {"Taskfile.yml": "tasks:\n  lint:\n    cmds: [ruff]\n"},
            {"Taskfile.yml": "tasks:\n  check:\n    cmds:\n      - pytest\n"},
            {"scripts/verify.sh": "pytest\n"},
            {"package.json": '{"scripts":{"build":"tsc"}}'},
        ]
        for files in failures:
            with self.subTest(fail_files=files):
                self.assertEqual(self.s(build.check_command(self.ctx(files))), Status.FAIL)

    def test_contract_locality_edges(self):
        self.assertEqual(self.s(docs.agent_verify_contract(self.ctx({
            "AGENTS.md": "Run verification now.\n\n`npm test`\n",
        }))), Status.PASS)
        self.assertEqual(self.s(docs.agent_verify_contract(self.ctx({
            "AGENTS.md": "## Verification\n" + "filler\n" * 11 + "`npm test`\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(docs.agent_verify_contract(self.ctx({
            "AGENTS.md": "Run verification now.\nnot the command\n\n`npm test`\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(docs.agent_verify_contract(self.ctx({
            ".ra1/config.json": '{"acdc":{"instruction_files":{}}}',
            "AGENTS.md": "## Verification\n`make check`\n",
        }))), Status.PASS)

    def test_agent_hook_vendor_needles(self):
        self.assertEqual(self.s(devenv.agent_hooks(self.ctx({
            ".ra1/config.json": (
                '{"acdc":{"hook_files":[".agents/hooks/post-edit.sh"]}}'
            ),
            ".agents/hooks/post-edit.sh": "sonar analyze --file src/a.py\n",
        }))), Status.PASS)
        self.assertEqual(self.s(devenv.agent_hooks(self.ctx({
            ".claude/settings.json": '{"hooks":{"PostToolUse":{"command":"sonar analyze"}}}',
        }))), Status.PASS)

    def test_new_code_quality_gate_edges(self):
        self.assertEqual(self.s(testing.new_code_quality_gate(self.ctx({
            "qodana.yml": "version: 1.0\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(testing.new_code_quality_gate(self.ctx({
            ".codecov.yml": "coverage:\n  status:\n    patch:\n",
            ".github/workflows/a.yml": "run: echo no-match\n",
            ".github/workflows/z.yml": "uses: codecov/codecov-action@v5\n",
        }))), Status.PASS)
        self.assertEqual(self.s(testing.new_code_quality_gate(self.ctx({
            "codecov.yml": "coverage:\n  status:\n    project:\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(testing.new_code_quality_gate(self.ctx({
            "sonar-project.properties": "sonar.projectKey=x\n",
            "qodana.yml": "version: 1.0\n",
            ".github/workflows/ci.yml": "uses: JetBrains/qodana-action@v2026\n",
        }))), Status.PASS)


    def test_configured_entrypoint_checks_all_candidate_files(self):
        ctx = self.ctx({
            ".ra1/config.json": '{"acdc":{"verify_command":"just check"}}',
            "first": "lint:\n    ruff check .\n",
            "second": "check:\n    pytest\n",
        })
        with mock.patch.object(build, "aglob", return_value=["first", "second"]):
            self.assertEqual(self.s(build.check_command(ctx)), Status.PASS)


class TestDoraAdvisoryChecks(CheckCase):
    _GIT_AVAIL = {("rev-parse", "--is-inside-work-tree"): "true\n"}
    _AI_POLICY = (
        "# AI Policy\n\nEngineers may use Claude and Copilot. "
        "Secrets are prohibited from prompts.\n"
    )
    _OPENSLO = (
        "apiVersion: openslo/v1\nkind: SLO\nmetadata:\n  name: availability\n"
        "spec:\n  budgetingMethod: Occurrences\n"
    )

    def test_build_small_batches(self):
        self.assertEqual(self.s(build.small_batches(self.ctx({}))), Status.UNKNOWN)
        thin = "a\n1\t1\tsrc/a.py\nb\n2\t2\tsrc/b.py\nc\n3\t3\tsrc/c.py\n"
        self.assertEqual(self.s(build.small_batches(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): thin,
        }))), Status.SKIPPED)
        pass_lines = []
        for i in range(10):
            pass_lines.append(f"c{i}")
            pass_lines.append(f"10\t5\tsrc/a{i}.py")
        self.assertEqual(self.s(build.small_batches(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): "\n".join(pass_lines) + "\n",
        }))), Status.PASS)
        fail_lines = []
        for i in range(10):
            fail_lines.append(f"c{i}")
            fail_lines.append(f"300\t200\tsrc/a{i}.py")
        self.assertEqual(self.s(build.small_batches(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): "\n".join(fail_lines) + "\n",
        }))), Status.FAIL)

    def test_build_integration_frequency(self):
        from datetime import datetime, timedelta

        self.assertEqual(self.s(build.integration_frequency(self.ctx({}))), Status.UNKNOWN)
        old = "2020-01-01T00:00:00+00:00\n" * 5
        self.assertEqual(self.s(build.integration_frequency(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-200", "--format=%cI"): old,
        }))), Status.SKIPPED)
        now = datetime.now(UTC)
        pass_dates = "\n".join((now - timedelta(weeks=w)).isoformat() for w in range(5)) + "\n"
        self.assertEqual(self.s(build.integration_frequency(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-200", "--format=%cI"): pass_dates,
        }))), Status.PASS)
        fail_dates = "\n".join([
            now.isoformat(),
            (now - timedelta(weeks=1)).isoformat(),
        ]) + "\n"
        self.assertEqual(self.s(build.integration_frequency(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-200", "--format=%cI"): fail_dates,
        }))), Status.FAIL)

    def test_build_agent_config_versioned(self):
        self.assertEqual(self.s(build.agent_config_versioned(self.ctx({}))), Status.UNKNOWN)
        self.assertEqual(self.s(build.agent_config_versioned(self.ctx(
            {}, git=self._GIT_AVAIL))), Status.SKIPPED)
        self.assertEqual(self.s(build.agent_config_versioned(self.ctx(
            {"AGENTS.md": "# Agents\n"},
            git={
                **self._GIT_AVAIL,
                ("log", "--follow", "--format=%H", "HEAD", "--", "AGENTS.md"): "c1\nc2\nc3\n",
            },
        ))), Status.PASS)
        self.assertEqual(self.s(build.agent_config_versioned(self.ctx(
            {"AGENTS.md": "# Agents\n"},
            git={
                **self._GIT_AVAIL,
                ("log", "--follow", "--format=%H", "HEAD", "--", "AGENTS.md"): "c1\n",
            },
        ))), Status.FAIL)

    def test_taskdisc_review_latency(self):
        import json

        self.assertEqual(self.s(taskdisc.review_latency(self.ctx({}))), Status.SKIPPED)
        pulls_p1 = "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1"
        pulls_p2 = "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2"

        def pr_fixture(n, created, review):
            prs = [{"number": i + 1, "merged_at": "2026-06-02T00:00:00Z",
                    "created_at": created} for i in range(n)]
            endpoints = {pulls_p1: json.dumps(prs), pulls_p2: "[]"}
            for i in range(n):
                reviews = [] if review is None else [{"submitted_at": review}]
                endpoints[f"repos/o/r/pulls/{i + 1}/reviews?per_page=100"] = json.dumps(reviews)
            return endpoints

        # Fewer than 5 reviewed PRs -> skipped.
        self.assertEqual(self.s(taskdisc.review_latency(self.ctx(
            {}, gh=_gh_available(pr_fixture(3, "2026-06-01T00:00:00Z",
                                            "2026-06-01T02:00:00Z"))))), Status.SKIPPED)
        # Median first-review latency 12h <= 48h -> pass.
        self.assertEqual(self.s(taskdisc.review_latency(self.ctx(
            {}, gh=_gh_available(pr_fixture(5, "2026-06-01T00:00:00Z",
                                            "2026-06-01T12:00:00Z"))))), Status.PASS)
        # Median first-review latency 96h > 48h -> fail.
        self.assertEqual(self.s(taskdisc.review_latency(self.ctx(
            {}, gh=_gh_available(pr_fixture(5, "2026-06-01T00:00:00Z",
                                            "2026-06-05T00:00:00Z"))))), Status.FAIL)

    def test_docs_ai_stance(self):
        self.assertEqual(self.s(docs.ai_stance(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(docs.ai_stance(self.ctx(
            {"AI_POLICY.md": "# AI\nshort"}))), Status.FAIL)
        self.assertEqual(self.s(docs.ai_stance(self.ctx(
            {"AI_POLICY.md": self._AI_POLICY}))), Status.PASS)
        agents = (
            "# Agents\n\n## AI Policy\n\nEngineers may use Claude for coding. "
            "Secrets are prohibited from prompts.\n"
        )
        self.assertEqual(self.s(docs.ai_stance(self.ctx(
            {"AGENTS.md": agents}))), Status.PASS)

    def test_docs_machine_context(self):
        import json

        self.assertEqual(self.s(docs.machine_context(self.ctx(
            {"AGENTS.md": "# Agents\n"}))), Status.FAIL)
        mcp_ok = json.dumps({
            "mcpServers": {"fs": {"command": "npx", "args": ["-y", "srv"]}},
        })
        self.assertEqual(self.s(docs.machine_context(self.ctx(
            {".mcp.json": mcp_ok}))), Status.PASS)
        self.assertEqual(self.s(docs.machine_context(self.ctx(
            {".mcp.json": json.dumps({"mcpServers": {}})}))), Status.FAIL)
        self.assertEqual(self.s(docs.machine_context(self.ctx({
            "llms.txt": "# Project\n\nhttps://example.com/docs\nSee docs/api.md for details.\n",
        }))), Status.PASS)

    _SAFE_PERMS = (
        '{"permissions":{"deny":["Read(.env*)","Read(**/*.pem)","Read(**/*.key)",'
        '"Read(~/.ssh/**)","Read(~/.aws/**)","Read(~/.kube/**)"],"allow":["Bash(npm test)"]}}'
    )

    def test_security_agent_permissions(self):
        self.assertEqual(self.s(security.agent_permissions(self.ctx({
            ".claude/settings.local.json": '{"permissions":{"deny":["Bash"]}}',
        }))), Status.FAIL)
        self.assertEqual(self.s(security.agent_permissions(self.ctx({
            ".claude/settings.json": self._SAFE_PERMS,
        }))), Status.PASS)
        self.assertEqual(self.s(security.agent_permissions(self.ctx({
            ".claude/settings.json": '{"permissions":{"allow":["*"]}}',
        }))), Status.FAIL)
        self.assertEqual(self.s(security.agent_permissions(self.ctx({
            ".claude/settings.json": '{"permissions":{"deny":["Read(.env*)"],'
            '"allow":["Bash(npm test)"]}}',
        }))), Status.FAIL)  # missing mandatory secret classes

    def test_observability_slo_definitions(self):
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({
            "openslo/availability.yaml": self._OPENSLO,
        }))), Status.FAIL)
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({
            "openslo/availability.yaml": self._OPENSLO,
            ".github/workflows/ci.yml": (
                "name: ci\non: push\njobs:\n  slo:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: sloth generate -i openslo/availability.yaml\n"
            ),
        }))), Status.PASS)

    def test_observability_incident_learning(self):
        self.assertEqual(self.s(observability.incident_learning(self.ctx({}))), Status.FAIL)
        self.assertEqual(self.s(observability.incident_learning(self.ctx({
            "docs/postmortems/outage.md": "# Outage\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(observability.incident_learning(self.ctx({
            "docs/postmortems/2024-outage.md": (
                "# 2024 Outage Postmortem\n\n## Summary\n\n"
                "The API was down for 40 minutes due to a bad deploy. "
                "We rolled back and added a canary gate.\n"
            ),
        }))), Status.PASS)




    def test_helpers_filled_and_parse_iso(self):
        from readiness.checks import _helpers

        class S:
            def read(self, p):
                return None

        class C:
            static = S()

        ok, rationale = _helpers.filled(C(), "x", "lab")
        self.assertFalse(ok)
        self.assertIn("unreadable", rationale)

        class SEmpty:
            def read(self, p):
                return "   \n"

        class CEmpty:
            static = SEmpty()

        ok, rationale = _helpers.filled(CEmpty(), "x", "lab")
        self.assertFalse(ok)
        self.assertIn("empty", rationale)

        self.assertIsNone(_helpers.parse_iso(None))
        self.assertIsNone(_helpers.parse_iso(1))
        self.assertIsNone(_helpers.parse_iso("not-a-date"))

    def test_build_integration_frequency_edge_branches(self):
        from datetime import datetime, timedelta

        self.assertEqual(self.s(build.integration_frequency(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-200", "--format=%cI"): "not-a-date\n",
        }))), Status.UNKNOWN)

        now = datetime.now(UTC)
        naive_anchor = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        dates = "\n".join([
            naive_anchor,
            "bad-date",
            (now - timedelta(weeks=1)).strftime("%Y-%m-%dT%H:%M:%S"),
            (now - timedelta(weeks=2)).strftime("%Y-%m-%dT%H:%M:%S"),
            (now - timedelta(weeks=3)).strftime("%Y-%m-%dT%H:%M:%S"),
            (now - timedelta(weeks=4)).strftime("%Y-%m-%dT%H:%M:%S"),
            (now - timedelta(weeks=12)).strftime("%Y-%m-%dT%H:%M:%S"),  # outside trailing-8 window
        ]) + "\n"
        self.assertEqual(self.s(build.integration_frequency(self.ctx({}, git={
            **self._GIT_AVAIL,
            ("log", "-200", "--format=%cI"): dates,
        }))), Status.PASS)

    def test_docs_dora_helpers_and_edge_branches(self):
        import json

        self.assertFalse(docs._text_filled("short"))
        self.assertFalse(docs._text_filled(
            "TODO replace this placeholder with a real AI policy document body."
        ))
        self.assertFalse(docs._text_filled(""))

        # Heading match with no trailing newline after the heading line.
        bodies = list(docs._heading_sections("## AI Policy", docs._AI_HEADING_RE))
        self.assertEqual(bodies, [""])

        # AGENTS heading present but body lacks tool/permission signal → invalid path.
        thin_agents = (
            "# Agents\n\n## AI Policy\n\n"
            "This section is long enough to count as filled content for the "
            "length check but intentionally omits any stance keywords.\n"
        )
        self.assertEqual(self.s(docs.ai_stance(self.ctx({"AGENTS.md": thin_agents}))), Status.FAIL)

        self.assertFalse(docs._mcp_servers_ok(None))
        self.assertFalse(docs._mcp_servers_ok("x"))
        self.assertFalse(docs._mcp_servers_ok({"mcpServers": {"x": "not-a-dict"}}))
        self.assertFalse(docs._mcp_servers_ok({"mcpServers": {"x": {"args": ["a"]}}}))
        self.assertFalse(docs._llms_has_ref("just words\nno links here"))
        self.assertFalse(docs._llms_has_ref(""))

        # Filled llms.txt without URL/path refs.
        self.assertEqual(self.s(docs.machine_context(self.ctx({
            "llms.txt": (
                "# Project overview for language models reading this file carefully.\n"
                "There are no URLs or path references in this document body.\n"
            ),
        }))), Status.FAIL)
        self.assertEqual(self.s(docs.machine_context(self.ctx({
            ".mcp.json": json.dumps({"mcpServers": {"x": "bad"}}),
        }))), Status.FAIL)

    def test_security_agent_permissions_edge_branches(self):
        from readiness.checks import _agent_policy as ap
        from readiness.checks import security as sec

        self.assertEqual(ap.evaluate_claude_settings("p", None).state, "malformed")
        self.assertEqual(ap.evaluate_claude_settings("p", "x").state, "malformed")
        self.assertEqual(ap.evaluate_claude_settings(
            "p", {"permissions": {"defaultMode": "bypassPermissions"}}).state,
            "dangerous_allow")
        self.assertEqual(ap.evaluate_claude_settings(
            "p", {"permissions": {"defaultMode": "plan", "deny": ["*"]}}).state, "safe")
        self.assertEqual(ap.evaluate_generic_policy("p", None, "").state, "malformed")
        self.assertEqual(
            ap.evaluate_generic_policy("p", {"deny": ["Read(.env*)"]},
                                       "deny reading .env secrets").state,
            "safe")

        self.assertIsNone(sec._parse_permissions_markdown(""))
        self.assertIsNone(sec._parse_permissions_markdown("# No fence\n"))
        self.assertIsNone(sec._parse_permissions_markdown("```json\n{bad}\n```\n"))
        self.assertEqual(
            sec._parse_permissions_markdown("```json\n{\"deny\":[\"Bash\"]}\n```\n"),
            {"deny": ["Bash"]},
        )

        md = (
            "# Permissions\n\n```json\n"
            '{"permissions":{"deny":["Read(.env*)","Read(**/*.pem)","Read(**/*.key)",'
            '"Read(~/.ssh/**)","Read(~/.aws/**)","Read(~/.kube/**)"]}}\n'
            "```\n"
        )
        self.assertEqual(self.s(security.agent_permissions(self.ctx({
            ".agents/shared/permissions.md": md,
        }))), Status.PASS)

    def test_taskdisc_review_latency_edge_branches(self):
        import json

        pulls_p1 = "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=1"
        pulls_p2 = "repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=50&page=2"
        prs = [
            {"number": 1, "merged_at": "2026-06-02T00:00:00Z"},  # missing created_at -> skipped
            {
                "number": 2,
                "merged_at": "2026-06-02T00:00:00Z",
                "created_at": "2026-06-01T00:00:00",  # naive -> localized to UTC
            },
            {
                "number": 3,
                "merged_at": "2026-06-02T00:00:00Z",
                "created_at": "2026-06-01T00:00:00Z",
            },
            *[{
                "number": i,
                "merged_at": "2026-06-02T00:00:00Z",
                "created_at": "2026-06-01T00:00:00Z",
            } for i in (4, 5, 6, 7)],
        ]
        extra = {pulls_p1: json.dumps(prs), pulls_p2: "[]"}
        # PR 1: no created_at -> skipped (reviews still fetched first); PR 2: naive review
        # time; PR 3: no reviews (absent) -> skipped.
        extra["repos/o/r/pulls/1/reviews?per_page=100"] = "[]"
        extra["repos/o/r/pulls/2/reviews?per_page=100"] = json.dumps(
            [{"submitted_at": "2026-06-01T12:00:00"}]
        )
        extra["repos/o/r/pulls/3/reviews?per_page=100"] = "[]"
        for i in (4, 5, 6, 7):
            extra[f"repos/o/r/pulls/{i}/reviews?per_page=100"] = json.dumps(
                [{"submitted_at": "2026-06-01T12:00:00Z"}]
            )
        # Valid latencies: 2,4,5,6,7 (PR1 missing created, PR3 missing review) -> 12h median.
        self.assertEqual(self.s(taskdisc.review_latency(self.ctx(
            {}, gh=_gh_available(extra)))), Status.PASS)

        # A malformed PR entry makes the whole observation unreadable -> blocking unknown.
        bad = {pulls_p1: json.dumps(["not-a-dict"]), pulls_p2: "[]"}
        self.assertEqual(self.s(taskdisc.review_latency(self.ctx(
            {}, gh=_gh_available(bad)))), Status.UNKNOWN)

    def test_observability_slo_artifact_variants(self):
        # sloth.yml / nobl9 / terraform `_slo"` artifact discovery
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({
            "sloth.yml": "version: prometheus/v1\nservice: api\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({
            "prod.nobl9.yaml": "apiVersion: n9/v1alpha\nkind: SLO\n",
        }))), Status.FAIL)
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({
            "infra/slo.tf": 'resource "datadog_service_level_objective" "api_slo" {}\n',
        }))), Status.FAIL)

        # Artifact also matches wiring globs → skipped; empty wiring file skipped;
        # a non-matching wiring file is ignored; tool-name mention in Dockerfile wires it.
        # infra/other.tf exercises the false branch of the `_slo"` terraform matcher.
        # Chart.yaml sorts before Dockerfile so the non-match tool-name continue branch is hit.
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({
            ".github/workflows/slo.yml": self._OPENSLO,
            "infra/other.tf": 'resource "null_resource" "x" {}\n',
            "Makefile": "",
            "Chart.yaml": "apiVersion: v2\nname: demo\ndescription: chart without slo tooling\n",
            "Dockerfile": "RUN sloth generate -f openslo.yaml\n",
        }))), Status.PASS)

        # Empty Makefile alone is encountered (and continued) when it is the only wiring candidate.
        self.assertEqual(self.s(observability.slo_definitions(self.ctx({
            "openslo/availability.yaml": self._OPENSLO,
            "Makefile": "",
        }))), Status.FAIL)


class TestLoopCoverageGaps(CheckCase):
    FILLED = (
        "# Loop Runs\n\nDocument how loop-runs artifacts are stored and reviewed by maintainers.\n"
    )

    def test_contains_artifact_language_ci_and_log(self):
        self.assertTrue(loop._contains_artifact_language("See the CI status.", ["ci"]))
        self.assertTrue(loop._contains_artifact_language("Attach the log please.", ["log"]))
        self.assertTrue(loop._contains_artifact_language("Attach the logs please.", ["log"]))
        # Evidence + CI only (no screenshot/video/loop-runs) hits the word-boundary path.
        text = (
            "# Pull Request\n\n"
            "Include evidence and CI status so reviewers can trust this change.\n"
        )
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({
            ".github/pull_request_template.md": text,
        }))), Status.PASS)
        log_text = (
            "# Pull Request\n\n"
            "Include evidence and a log for reviewers evaluating this change.\n"
        )
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({
            ".github/pull_request_template.md": log_text,
        }))), Status.PASS)

    def test_unfilled_and_missing_contract_branches(self):
        thin = "# x\n"
        self.assertEqual(self.s(loop.rules_index(self.ctx({
            ".omp/rules/README.md": thin,
        }))), Status.FAIL)
        self.assertEqual(self.s(loop.denylist(self.ctx({
            ".omp/rules/denylist.md": thin,
        }))), Status.FAIL)
        self.assertEqual(self.s(loop.signal_schema(self.ctx({
            "signals/README.md": thin,
        }))), Status.FAIL)

        missing_terms = (
            "# Signal Schema Documentation\n\n"
            "This describes the envelope shape for loop signals in detail.\n\n"
            "```json\n{\"hello\": 1}\n```\n"
        )
        v = loop.signal_schema(self.ctx({"signals/README.md": missing_terms}))
        self.assertEqual(v.status, Status.FAIL)
        self.assertIn("missing schema term", v.rationale)

        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({
            ".omp/commands/pr-artifact-template.md": thin,
        }))), Status.FAIL)
        self.assertEqual(self.s(loop.pr_artifact_template(self.ctx({}))), Status.FAIL)

        v = loop.prompt_contracts(self.ctx({
            ".omp/commands/goal.md": thin,
            ".omp/commands/loop.md": self.FILLED,
        }))
        self.assertEqual(v.status, Status.FAIL)
        self.assertIn(".omp/commands/goal.md", v.rationale)

        # One thin skill must be skipped (if ok is false) while three filled skills still pass.
        files = {
            ".omp/skills/a/SKILL.md": self.FILLED,
            ".omp/skills/b/SKILL.md": self.FILLED,
            ".omp/skills/c/SKILL.md": self.FILLED,
            ".omp/skills/thin/SKILL.md": thin,
        }
        self.assertEqual(self.s(loop.skills_present(self.ctx(files))), Status.PASS)


class TestDocsCoverageGaps(CheckCase):
    def test_skills_without_agent_skills_topic(self):
        # GitHub available and topics present but lacking 'agent-skills': no T2 evidence.
        v = docs.skills(self.ctx(
            {"skills/demo/SKILL.md": "# Demo\n\nA reusable skill artifact for agents.\n"},
            gh={"repos/o/r/topics": '{"names":["python"]}'}))
        self.assertEqual(self.s(v), Status.PASS)
        self.assertEqual(len(v.evidence), 1)

    def test_doc_freshness_absent_history_and_unreadable_file_date(self):
        # Empty commit-date output: confirmed absence of history, not an error.
        v = docs.doc_freshness(self.ctx({"README.md": "# x"}, git={
            ("log", "-1", "--format=%cI"): "",
        }))
        self.assertEqual(self.s(v), Status.UNKNOWN)

        # A modal per-file history failure surfaces as unknown, never as freshness.
        def runner(args):
            if tuple(args) == ("log", "-1", "--format=%cI"):
                return BoundedProcessResult(
                    ProcessState.OK, returncode=0,
                    stdout="2026-06-01T00:00:00+00:00\n")
            return BoundedProcessResult(ProcessState.TIMEOUT)

        v = docs.doc_freshness(self.ctx({"README.md": "# x"}, git=runner))
        self.assertEqual(self.s(v), Status.UNKNOWN)

    def test_mcp_servers_ok_branches(self):
        self.assertFalse(docs._mcp_servers_ok({"mcpServers": {}}))
        self.assertFalse(docs._mcp_servers_ok({"mcpServers": "not-a-dict"}))
        self.assertTrue(docs._mcp_servers_ok(
            {"mcpServers": {"s": {"command": "npx"}}}))
        self.assertTrue(docs._mcp_servers_ok(
            {"mcpServers": {"s": {"url": "https://example.com"}}}))

    def test_llms_has_ref_branches(self):
        self.assertTrue(docs._llms_has_ref("\n\nSee https://example.com/docs\n"))
        self.assertTrue(docs._llms_has_ref("docs/guide.md"))

    def test_mcp_entry_issues_matrix(self):
        expect = [
            ({"command": "npx", "url": "https://example.com"},
             "exactly one local-command or remote-URL transport is required"),
            ({"command": "sh -c 'run thing'"},
             "command uses a shell launcher or operators"),
            ({"command": "run && deploy"},
             "command uses a shell launcher or operators"),
            ({"command": "line1\nline2"},
             "command is not a nonempty one-line string"),
            ({"command": "npx", "args": "oops"},
             "argv entries must be bounded one-line strings"),
            ({"command": "x", "args": ["--token", "abc123token"]},
             "literal secret value after a secret flag"),
            ({"command": "x", "args": ["ghp_secretvalue"]},
             "literal credential token in argv"),
            ({"command": "x", "env": "nope"}, "env must be an object"),
            ({"command": "x", "env": {"A": 1}}, "env values must be strings"),
            ({"command": "x", "env": {"API_KEY": "literal"}},
             "literal secret in env value"),
            ({"url": "not-a-url"}, "remote URL is malformed"),
            ({"url": "http://example.com"},
             "remote URL requires HTTPS (HTTP loopback excepted)"),
            ({"url": "https://user@example.com"},
             "remote URL must not carry userinfo/query/fragment"),
            ({"url": "https://example.com", "headers": "nope"},
             "headers must be an object"),
            ({"url": "https://example.com", "headers": {"X": 1}},
             "header values must be strings"),
            ({"url": "https://example.com",
              "headers": {"Authorization": "Bearer abc"}},
             "header values must be placeholders"),
        ]
        for cfg, message in expect:
            with self.subTest(message=message):
                self.assertIn(message, docs._mcp_entry_issues("s", cfg))
        # A secret-looking arg with no preceding secret flag and no token prefix is fine.
        self.assertEqual(
            docs._mcp_entry_issues("s", {"command": "x",
                                         "args": ["user", "my password"]}), [])
        # Placeholder env/header values satisfy the secret rules (loop-continue arcs).
        self.assertEqual(docs._mcp_entry_issues(
            "s", {"command": "x", "env": {"API_KEY": "${API_KEY}"}}), [])
        self.assertEqual(docs._mcp_entry_issues(
            "s", {"url": "https://example.com",
                  "headers": {"Authorization": "Bearer <TOKEN>"}}), [])

    def test_mcp_config_kind_branches(self):
        self.assertEqual(docs._mcp_config_kind("p", None), ("config_invalid", []))
        kind, _issues = docs._mcp_config_kind(
            "p", {"servers": {"s": {"command": "npx"}}})
        self.assertEqual(kind, "ok")
        kind, issues = docs._mcp_config_kind("p", {"mcpServers": {"s": {
            "command": "x", "env": {"API_KEY": "literal"}}}})
        self.assertEqual(kind, "literal_secret")
        self.assertIn("literal secret in env value", issues)
        kind, _issues = docs._mcp_config_kind("p", {"mcpServers": {"s": {
            "url": "http://example.com"}}})
        self.assertEqual(kind, "transport_unsafe")

    def test_machine_context_llms_markdown_ref_and_thin_fallback(self):
        v = docs.machine_context(self.ctx({
            "llms.txt": ("# Project context for language model consumers of this repo.\n"
                         "See docs/guide.md\n"
                         "docs/guide.md\n"),
            "docs/guide.md": "# Guide\n\nDetailed documentation lives here for agents.\n",
        }))
        self.assertEqual((self.s(v), v.reason_code),
                         (Status.PASS, "docs.machine_context.fallback_configured"))

        v = docs.machine_context(self.ctx({"llms.txt": "tiny"}))
        self.assertEqual((self.s(v), v.reason_code),
                         (Status.FAIL, "docs.machine_context.fallback_incomplete"))

    def test_context_map_reference_edge_branches(self):
        refs, invalid = docs._context_map_references(
            "# A\n\n[anchor](#section) and [self](AGENTS.md) and "
            "[q](docs/x.md?raw=1)\n")
        self.assertEqual(refs, ["docs/x.md"])
        self.assertEqual(invalid, [])

    def test_agent_context_map_indeterminate_target(self):
        ctx = self.ctx({
            "AGENTS.md": "# A\n\n## B\n\nSee [guide](docs/guide.md) for everything.\n",
            "docs/guide.md": "# Guide\n\nValid text that is definitely long enough.\n",
        })
        (ctx.root / "docs/guide.md").write_bytes(b"\xff\xfe invalid utf-8 " * 8)
        v = docs.agent_context_map(ctx)
        self.assertEqual((self.s(v), v.reason_code),
                         (Status.UNKNOWN, "docs.agent_context_map.indeterminate"))


if __name__ == "__main__":
    unittest.main()
