"""Gate-closing branch coverage for checks modules (security/build/taskdisc/loop)
and checks/_agent_policy.

Complements test_checks.py and test_new_criteria.py: every fixture here targets one
previously-uncovered line or arc. New file by ownership convention — do not merge.
"""
from __future__ import annotations

import unittest
from unittest import mock

from readiness.checks import _agent_policy, build, loop, security, taskdisc
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.context import Context
from readiness.detect import detect
from readiness.model import Status

from tests._util import fake_runner, gh_runner, make_repo, rmtree

ORIGIN = ("github.com", "o", "r")
GIT_AVAIL = {("rev-parse", "--is-inside-work-tree"): "true\n"}
CHECK_IGNORE = ("check-ignore", "-v", "--no-index", "--",
                ".ra1/reports/.ra1-ignore-probe", ".ra1/config.json",
                ".ra1/waivers.json")


class GateCase(unittest.TestCase):
    def ctx(self, files, gh=None, git=None, options=None):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        git_runner = git if callable(git) else fake_runner(git or {})
        return Context(
            root=root, detection=det, static=static,
            git=GitCollector(root, runner=git_runner, static=static),
            github=GithubCollector(
                root,
                origin=ORIGIN if gh is not None else (),
                runner=gh_runner(gh or {})),
            app=det.apps[0],
            options=options or {},
        )

    def unreadable(self, ctx, *paths):
        """Patch ``ctx.static.read`` so the named paths read as missing (None)."""
        original = ctx.static.read
        patcher = mock.patch.object(
            ctx.static, "read",
            side_effect=lambda p: None if p in paths else original(p))
        self.addCleanup(patcher.stop)
        return patcher.start()


# --------------------------------------------------------------------------- security
class TestGitignoreBoundary(GateCase):
    GITIGNORE = {".gitignore": ".env\nnode_modules\n"}

    def test_policy_inputs_ignored_fails(self):
        # Only .ra1/waivers.json is matched, so the first loop iteration also takes
        # the `policy not in matched` False arc before the failure.
        ctx = self.ctx(self.GITIGNORE, git={
            CHECK_IGNORE: ".gitignore:9:.ra1/waivers.json\t.ra1/waivers.json\n",
        })
        verdict = security.gitignore_comprehensive(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("fail", "security.gitignore_comprehensive.policy_inputs_ignored"))

    def test_negated_probe_rule_fails(self):
        ctx = self.ctx(self.GITIGNORE, git={
            CHECK_IGNORE: ".gitignore:3:!/.ra1/reports/\t.ra1/reports/.ra1-ignore-probe\n",
        })
        verdict = security.gitignore_comprehensive(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("fail", "security.gitignore_comprehensive.report_output_unprotected"))


class TestAgentPermissionsBranches(GateCase):
    def test_unreadable_shared_file_is_unknown(self):
        ctx = self.ctx({".claude/settings.json": '{"permissions": {}}'})
        self.unreadable(ctx, ".claude/settings.json")
        verdict = security.agent_permissions(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "security.agent_permissions.observation_indeterminate"))

    def test_generic_json_files_malformed_and_valid(self):
        # One malformed generic permissions JSON (evaluate None branch) and one safe
        # generic JSON (evaluate data branch): a safe file never masks a malformed one.
        ctx = self.ctx({
            ".agents/a/permissions.json": "{not json",
            ".agents/b/permissions.json":
                '{"permissions": {"deny": ["Read(.env)"]},'
                ' "note": "never delete secrets"}',
        })
        verdict = security.agent_permissions(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("fail", "security.agent_permissions.malformed"))


class TestAgentConfigOwnershipBranches(GateCase):
    def test_no_codeowners_fails(self):
        verdict = security.agent_config_ownership(self.ctx({"README.md": "# x"}))
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("fail", "security.agent_config_ownership.targets_unowned"))

    def test_unreadable_codeowners_is_unknown(self):
        ctx = self.ctx({".github/CODEOWNERS": "* @team\n"})
        self.unreadable(ctx, ".github/CODEOWNERS")
        verdict = security.agent_config_ownership(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "security.agent_config_ownership.discovery_indeterminate"))

    def test_target_overflow_is_unknown(self):
        ctx = self.ctx({".github/CODEOWNERS": "* @team\n"})
        fake_targets = [f".cursor/rules/rule{i:03d}.mdc" for i in range(257)]
        with mock.patch.object(_agent_policy, "agent_control_paths",
                               return_value=fake_targets):
            verdict = security.agent_config_ownership(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "security.agent_config_ownership.discovery_indeterminate"))


class TestProvenanceBranches(GateCase):
    INDETERMINATE_WORKFLOW = {
        ".github/workflows/pub.yml":
            "jobs:\n  release:\n    steps:\n"
            "      - run: npm publish 2>&1 | tee publish.log\n",
    }

    def test_indeterminate_intent_is_unknown(self):
        verdict = security.supply_chain_provenance(
            self.ctx(self.INDETERMINATE_WORKFLOW))
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "security.supply_chain_provenance.syntax_indeterminate"))

    def test_candidate_overflow_is_unknown(self):
        from readiness.checks import _workflow_policy

        ctx = self.ctx({"README.md": "# x"})
        with mock.patch.object(_workflow_policy, "artifact_publication_intent",
                               return_value="present"), \
                mock.patch.object(_workflow_policy, "provenance_candidates",
                                  return_value=("overflow", [])):
            verdict = security.supply_chain_provenance(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "security.supply_chain_provenance.syntax_indeterminate"))


# --------------------------------------------------------------------------- build
class TestBuildBranches(GateCase):
    def test_agentic_development_unreadable_git(self):
        ctx = self.ctx({}, git={
            ("rev-parse", "--is-inside-work-tree"): "not-a-boolean\n",
        })
        verdict = build.agentic_development(ctx)
        self.assertEqual(verdict.status, Status.UNKNOWN)
        self.assertIn("could not be read", verdict.rationale)

    def test_release_automation_indeterminate(self):
        ctx = self.ctx(TestProvenanceBranches.INDETERMINATE_WORKFLOW)
        verdict = build.release_automation(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "build.release_automation.applicability_indeterminate"))

    def test_ci_runs_tests_unreadable_runs(self):
        ctx = self.ctx({"tests/test_x.py": "def test_x():\n    pass\n"}, gh={
            "repos/o/r/actions/workflows?per_page=100": '{"workflows": [{}]}',
            "repos/o/r/actions/runs?per_page=20": ("{}", 500),
        })
        verdict = build.ci_runs_tests(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "build.ci_runs_tests.observation_unreadable"))

    def test_ci_duration_budget_unreadable_runs(self):
        ctx = self.ctx({".ra1/config.json": '{"ci_budget_minutes": 30}'}, gh={
            "repos/o/r/actions/runs?per_page=20": ("{}", 500),
        })
        verdict = build.ci_duration_budget(ctx)
        self.assertEqual(verdict.status, Status.UNKNOWN)
        self.assertIn("durations could not be read", verdict.rationale)

    def test_small_batches_empty_churn(self):
        ctx = self.ctx({}, git={
            **GIT_AVAIL,
            ("log", "-50", "--no-merges", "--numstat", "--format=%H"): "",
        })
        verdict = build.small_batches(ctx)
        self.assertEqual(verdict.status, Status.UNKNOWN)
        self.assertIn("No git history", verdict.rationale)

    def test_integration_frequency_empty_dates(self):
        ctx = self.ctx({}, git={
            **GIT_AVAIL,
            ("log", "-200", "--format=%cI"): "",
        })
        verdict = build.integration_frequency(ctx)
        self.assertEqual(verdict.status, Status.UNKNOWN)
        self.assertIn("No git history", verdict.rationale)


class TestParseVerifyCommand(GateCase):
    def test_non_string_or_control_input(self):
        self.assertEqual(build._parse_verify_command(None), ("malformed", ""))
        self.assertEqual(build._parse_verify_command("make test\u202e"),
                         ("malformed", ""))

    def test_shlex_value_error(self):
        self.assertEqual(build._parse_verify_command('make "unclosed'),
                         ("malformed", ""))

    def test_empty_or_too_many_tokens(self):
        self.assertEqual(build._parse_verify_command("   "), ("malformed", ""))
        self.assertEqual(build._parse_verify_command("make a b c"),
                         ("malformed", ""))

    def test_operator_tokens(self):
        self.assertEqual(build._parse_verify_command("make foo;bar"),
                         ("malformed", ""))
        self.assertEqual(build._parse_verify_command("make A=b"),
                         ("malformed", ""))


# --------------------------------------------------------------------------- taskdisc
class TestBacklogAndReviewBranches(GateCase):
    def test_backlog_health_no_open_issues(self):
        ctx = self.ctx({"README.md": "# x"}, gh={
            "repos/o/r/issues?state=open&per_page=50": "[]",
        })
        verdict = taskdisc.backlog_health(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("pass", "taskdisc.backlog_health.healthy"))
        self.assertIn("No open issues", verdict.rationale)

    def test_review_latency_unreadable_reviews(self):
        ctx = self.ctx({"README.md": "# x"}, gh={
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc"
            "&per_page=50&page=1":
                '[{"number": 5, "merged_at": "2026-01-01T00:00:00Z",'
                ' "created_at": "2026-01-01T00:00:00Z"}]',
            "repos/o/r/pulls?state=closed&sort=updated&direction=desc"
            "&per_page=50&page=2": "[]",
            "repos/o/r/pulls/5/reviews?per_page=100": ("{}", 500),
        })
        verdict = taskdisc.review_latency(ctx)
        self.assertEqual(verdict.status, Status.UNKNOWN)
        self.assertIn("reviews could not be read", verdict.rationale)


class TestPrEvidenceBranches(GateCase):
    def test_unreadable_template_is_unknown(self):
        ctx = self.ctx({".github/pull_request_template.md": "## Summary\n\nWhat.\n"})
        self.unreadable(ctx, ".github/pull_request_template.md")
        verdict = taskdisc.pr_evidence_contract(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("unknown", "taskdisc.pr_evidence_contract.template_indeterminate"))

    def test_closest_candidate_kept_when_later_is_worse(self):
        # Second candidate misses more groups, so `best_missing` is not replaced
        # (the `len(missing) < len(best_missing[1])` False arc).
        ctx = self.ctx({
            ".github/pull_request_template.md":
                "## Summary\n\nWhat and why.\n\n## Test plan\n\nTests.\n\n"
                "## Risk\n\nBlast radius.\n",
            "docs/pull_request_template.md": "## Summary\n\nWhat.\n",
        })
        verdict = taskdisc.pr_evidence_contract(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("fail", "taskdisc.pr_evidence_contract.sections_incomplete"))
        self.assertIn("recovery", verdict.rationale)
        self.assertNotIn("verification", verdict.rationale)


class TestSharedInstructionBranches(GateCase):
    FOUR = (
        "- Use a separate worktree or task branch for each concurrent agent task.\n"
        "- Coordinate with other agents before touching the same or overlapping files.\n"
        "- Re-read and preserve unexpected user or concurrent changes.\n"
        "- After merging or integrating, run the full test suite to verify.\n"
    )

    def test_glob_match_that_reads_as_missing_is_skipped(self):
        ctx = self.ctx({".cursor/rules/x.mdc": "anything"})
        self.unreadable(ctx, ".cursor/rules/x.mdc")
        self.assertEqual(taskdisc._shared_instruction_texts(ctx), [])

    def test_numbered_statement_form(self):
        statements = taskdisc._normative_statements(
            "1. Use a separate worktree for each concurrent task\n")
        self.assertEqual(statements,
                         ["Use a separate worktree for each concurrent task"])

    def test_non_contributing_file_in_passing_scan(self):
        # CLAUDE.md matches no protocol group, so the contributing-evidence loop
        # takes its False arc while the scan still passes on AGENTS.md alone.
        ctx = self.ctx({
            "AGENTS.md": "# Agents\n\n## Build\n\npytest\n\n## Concurrent\n" + self.FOUR,
            "CLAUDE.md": "# Notes\n\n## Build\n\npytest only.\n",
        })
        verdict = taskdisc.concurrent_agent_protocol(ctx)
        self.assertEqual(
            (verdict.status.value, verdict.reason_code),
            ("pass", "taskdisc.concurrent_agent_protocol.complete"))
        self.assertEqual([e.source for e in verdict.evidence], ["AGENTS.md"])


# --------------------------------------------------------------------------- loop
class TestStatementGovernsPolarity(GateCase):
    PUSH_NEEDLE = loop._DENYLIST_FAMILIES[2][1]

    def test_permissive_without_normative_fails(self):
        self.assertFalse(loop._statement_governs(
            "All push operations are allowed.", self.PUSH_NEEDLE))

    def test_reversed_never_block_fails(self):
        self.assertFalse(loop._statement_governs(
            "Never block deploy operations.", self.PUSH_NEEDLE))

    def test_do_not_allow_fails(self):
        self.assertFalse(loop._statement_governs(
            "Do not allow merge without review.", self.PUSH_NEEDLE))


# --------------------------------------------------------------------------- _agent_policy
class TestMissingSecretDeniesDeadArc(GateCase):
    def test_second_pass_match_skips_append(self):
        """The inner ``needle.search`` re-check (arc 167->163) is unreachable with a
        stable deny list: the outer ``any`` is a superset of the inner one. A two-pass
        iterable whose second pass over the first family carries a matching rule
        exercises the guard."""
        sequence = iter([
            ["unrelated-rule"],  # deny-all probe
            ["unrelated-rule"],  # family 1 outer any: no match -> enter the guard
            ["Read(.env)"],      # family 1 inner any: match -> arc 167->163, no append
            ["unrelated-rule"],  # family 2 outer: no match
            ["unrelated-rule"],  # family 2 inner: no match -> append
            ["unrelated-rule"],  # family 3 outer: no match
            ["unrelated-rule"],  # family 3 inner: no match -> append
        ])

        class FlipFlop:
            def __iter__(self):
                return iter(next(sequence))

        missing = _agent_policy._missing_secret_denies(FlipFlop())
        self.assertEqual(missing, ("private-key material", "credential directories"))


if __name__ == "__main__":
    unittest.main()
