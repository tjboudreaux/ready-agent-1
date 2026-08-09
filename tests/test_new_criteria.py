"""Labeled fixtures for the 0.11.0 advisory criteria and deepened deterministic checks.

Every fixture records expected status and exact reason code; no fixture asserts free-form
rationale as a policy key.
"""
from __future__ import annotations

import unittest

from readiness.checks import _workflow_policy, docs, security, taskdisc
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.context import Context
from readiness.detect import detect

from tests._util import fake_runner, gh_runner, make_repo, rmtree

FILLED = "\n\n" + ("Detailed maintainer documentation. " * 8)


def _ctx(files, *, gh=None, git_responses=None):
    root = make_repo(files)
    static = StaticCollector(root)
    det = detect(root, static)
    github = gh if gh is not None else GithubCollector(root)
    ctx = Context(root=root, detection=det, static=static,
                  git=GitCollector(root, runner=fake_runner(git_responses or {})),
                  github=github, app=det.apps[0], options={})
    return root, ctx


def _gh(payloads):
    return GithubCollector("/x", origin=("github.com", "o", "r"),
                           runner=gh_runner(payloads))


class TestAgentContextMap(unittest.TestCase):
    def _run(self, files):
        root, ctx = _ctx(files)
        verdict = docs.agent_context_map(ctx)
        self.addCleanup(rmtree, root)
        return verdict

    def test_complete(self):
        v = self._run({
            "AGENTS.md": "# Agents\n\n## Build\n\npytest (see [dev guide](docs/dev.md))\n",
            "docs/dev.md": "# Dev\n" + FILLED,
        })
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "docs.agent_context_map.complete"))

    def test_no_file_and_no_reference(self):
        v = self._run({"AGENTS.md": "# Agents\n\n## Build\n\npytest\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "docs.agent_context_map.no_reference"))
        v = self._run({"README.md": "# x"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "docs.agent_context_map.no_reference"))

    def test_invalid_reference(self):
        v = self._run({"AGENTS.md": "# A\n\n## B\n\nSee [escape](../outside.md) or "
                                    "[absolute](/etc/passwd.md)\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "docs.agent_context_map.invalid_reference"))

    def test_missing_target(self):
        v = self._run({"AGENTS.md": "# A\n\n## B\n\nSee [gone](docs/gone.md)\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "docs.agent_context_map.missing_target"))

    def test_thin_and_placeholder_target(self):
        v = self._run({"AGENTS.md": "# A\n\n## B\n\nSee [thin](docs/thin.md)\n",
                       "docs/thin.md": "short\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "docs.agent_context_map.thin_target"))
        v = self._run({"AGENTS.md": "# A\n\n## B\n\nSee [stub](docs/stub.md)\n",
                       "docs/stub.md": "# Stub\n\nTODO: write this section later please.\n"
                                       + FILLED})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "docs.agent_context_map.thin_target"))

    def test_backtick_reference_and_external_ignored(self):
        v = self._run({
            "AGENTS.md": "# A\n\n## B\n\nRead `docs/guide.md`; also "
                         "[external](https://example.com/x.md) and "
                         "[mail](mailto:a@b.c)\n",
            "docs/guide.md": "# Guide\n" + FILLED,
        })
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "docs.agent_context_map.complete"))


class TestPrEvidenceContract(unittest.TestCase):
    FULL = ("## Summary\n\nWhat and why.\n\n## Verification\n\nTests and evidence.\n\n"
            "## Risk and rollback\n\nBlast radius and revert plan.\n")

    def _run(self, files):
        root, ctx = _ctx(files)
        verdict = taskdisc.pr_evidence_contract(ctx)
        self.addCleanup(rmtree, root)
        return verdict

    def test_all_locations(self):
        for rel in ("pull_request_template.md", "PULL_REQUEST_TEMPLATE.md",
                    "docs/pull_request_template.md", ".github/pull_request_template.md",
                    ".github/PULL_REQUEST_TEMPLATE/review.md"):
            v = self._run({rel: self.FULL})
            self.assertEqual((v.status.value, v.reason_code),
                             ("pass", "taskdisc.pr_evidence_contract.complete"),
                             f"location {rel}")

    def test_missing_groups_reports_closest(self):
        v = self._run({".github/pull_request_template.md": "## Summary\n\nWhat.\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "taskdisc.pr_evidence_contract.sections_incomplete"))
        self.assertIn("verification", v.rationale)

    def test_no_template(self):
        v = self._run({"README.md": "# x"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "taskdisc.pr_evidence_contract.sections_incomplete"))


class TestConcurrentAgentProtocol(unittest.TestCase):
    FOUR = (
        "- Use a separate worktree or task branch for each concurrent agent task.\n"
        "- Coordinate with other agents before touching the same or overlapping files.\n"
        "- Re-read and preserve unexpected user or concurrent changes.\n"
        "- After merging or integrating, run the full test suite to verify.\n"
    )

    def _run(self, files):
        root, ctx = _ctx(files)
        verdict = taskdisc.concurrent_agent_protocol(ctx)
        self.addCleanup(rmtree, root)
        return verdict

    def test_complete_single_file(self):
        v = self._run({"AGENTS.md": "# Agents\n\n## Build\n\npytest\n\n## Concurrent\n"
                       + self.FOUR})
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "taskdisc.concurrent_agent_protocol.complete"))

    def test_complete_split_files(self):
        v = self._run({
            "AGENTS.md": "# A\n\n## B\n\npytest\n\n"
                         "- Use a separate worktree or task branch per concurrent task.\n"
                         "- Coordinate with other agents before touching the same files.\n",
            "CLAUDE.md": "# C\n\n## B\n\npytest\n\n"
                         "- Never overwrite unexpected user or concurrent changes; "
                         "re-read first and preserve them.\n"
                         "- After merging, run the canonical full test suite to verify "
                         "integration.\n",
        })
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "taskdisc.concurrent_agent_protocol.complete"))

    def test_one_statement_cannot_cover_two_groups(self):
        v = self._run({"AGENTS.md": "# A\n\n## B\n\npytest\n\n"
                       "- Use a separate worktree per concurrent task; after merging, run "
                       "the full test suite to verify integration.\n"
                       "- Coordinate with other agents before touching shared files.\n"
                       "- Re-read and preserve unexpected user or concurrent changes.\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "taskdisc.concurrent_agent_protocol.families_incomplete"))
        self.assertIn("distinct", v.rationale)

    def test_missing_family_named(self):
        v = self._run({"AGENTS.md": "# A\n\n## B\n\npytest\n\n"
                       "- Use a separate worktree or task branch per task.\n"
                       "- Coordinate with other agents before touching the same files.\n"
                       "- After merging, run the full test suite to verify.\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "taskdisc.concurrent_agent_protocol.families_incomplete"))
        self.assertIn("preservation", v.rationale)

    def test_fence_and_comment_decoys(self):
        v = self._run({"AGENTS.md": "# A\n\n## B\n\npytest\n\n"
                       "```\n- Use a separate worktree per concurrent task.\n```\n"
                       "<!-- - Coordinate before touching shared files. -->\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "taskdisc.concurrent_agent_protocol.families_incomplete"))

    def test_instructions_missing(self):
        v = self._run({"README.md": "# x"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "taskdisc.concurrent_agent_protocol.instructions_missing"))


class TestBranchProtectionDepth(unittest.TestCase):
    FULL = {
        "required_pull_request_reviews": {
            "required_approving_review_count": 1, "require_code_owner_reviews": True},
        "required_status_checks": {"contexts": ["ci/test"], "checks": []},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }

    def _run(self, protection):
        import json
        payloads = {
            "repos/o/r": '{"full_name":"o/r","default_branch":"main"}',
            "repos/o/r/branches/main/protection": json.dumps(protection)
            if not isinstance(protection, tuple) else protection,
        }
        root, ctx = _ctx({"README.md": "# x"}, gh=_gh(payloads))
        verdict = security.branch_protection_depth(ctx)
        self.addCleanup(rmtree, root)
        return verdict

    def test_complete(self):
        v = self._run(self.FULL)
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "security.branch_protection_depth.complete"))

    def test_each_missing_control(self):
        import copy
        cases = [
            ("reviews", lambda p: p["required_pull_request_reviews"].update(
                required_approving_review_count=0)),
            ("code owner", lambda p: p["required_pull_request_reviews"].update(
                require_code_owner_reviews=False)),
            ("status checks", lambda p: p.update(required_status_checks={
                "contexts": [], "checks": []})),
            ("force pushes", lambda p: p["allow_force_pushes"].update(enabled=True)),
            ("deletions", lambda p: p["allow_deletions"].update(enabled=True)),
        ]
        for name, mutate in cases:
            protection = copy.deepcopy(self.FULL)
            mutate(protection)
            v = self._run(protection)
            self.assertEqual((v.status.value, v.reason_code),
                             ("fail", "security.branch_protection_depth.controls_incomplete"),
                             name)

    def test_absent_404_is_fail(self):
        v = self._run(("{}", 404))
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.branch_protection_depth.not_protected"))

    def test_unavailable_offline(self):
        root, ctx = _ctx({"README.md": "# x"})
        v = security.branch_protection_depth(ctx)
        rmtree(root)
        self.assertEqual((v.status.value, v.reason_code),
                         ("skipped", "security.branch_protection_depth.github_unavailable"))

    def test_unreadable_t2(self):
        root, ctx = _ctx({"README.md": "# x"}, gh=_gh({
            "repos/o/r": '{"full_name":"o/r","default_branch":"main"}',
            "repos/o/r/branches/main/protection": ("{}", 500),
        }))
        v = security.branch_protection_depth(ctx)
        rmtree(root)
        self.assertEqual((v.status.value, v.reason_code),
                         ("unknown", "security.branch_protection_depth.observation_unreadable"))
        self.assertNotIn("not protected", v.rationale)


class TestAgentConfigOwnership(unittest.TestCase):
    def _run(self, files):
        root, ctx = _ctx(files)
        verdict = security.agent_config_ownership(ctx)
        self.addCleanup(rmtree, root)
        return verdict

    def test_complete_wildcard(self):
        v = self._run({"CODEOWNERS": "* @team\n", "AGENTS.md": "# A\n\n## B\n\npytest\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "security.agent_config_ownership.complete"))

    def test_unowned_target(self):
        v = self._run({"CODEOWNERS": "docs/** @docs\n",
                       "AGENTS.md": "# A\n\n## B\n\npytest\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.agent_config_ownership.targets_unowned"))

    def test_empty_owner_rule(self):
        v = self._run({"CODEOWNERS": "* \n",
                       "AGENTS.md": "# A\n\n## B\n\npytest\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.agent_config_ownership.targets_unowned"))

    def test_uncertain_unsupported_pattern(self):
        v = self._run({"CODEOWNERS": "* @team\n!AGENTS.md\n",
                       "AGENTS.md": "# A\n\n## B\n\npytest\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("unknown", "security.agent_config_ownership.targets_uncertain"))

    def test_later_supported_rule_overrides_uncertainty(self):
        v = self._run({"CODEOWNERS": "!AGENTS.md\n* @team\n",
                       "AGENTS.md": "# A\n\n## B\n\npytest\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "security.agent_config_ownership.complete"))

    def test_priority_order(self):
        v = self._run({
            ".github/CODEOWNERS": "AGENTS.md @x\n",
            "CODEOWNERS": "* @y\n",
            "AGENTS.md": "# A\n\n## B\n\npytest\n",
        })
        # .github/CODEOWNERS wins; AGENTS.md owned; the CODEOWNERS file itself is a target.
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.agent_config_ownership.targets_unowned"))


class TestSupplyChainProvenance(unittest.TestCase):
    ATTEST = """permissions:
  contents: read
jobs:
  attest:
    permissions:
      contents: read
      id-token: write
      attestations: write
    steps:
      - uses: actions/attest-build-provenance@v2
        with:
          subject-path: dist/*
"""

    SLSA = """jobs:
  gen:
    permissions:
      actions: read
      id-token: write
    uses: slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0
    with:
      base64-subjects: "${{ needs.build.outputs.digests }}"
"""

    def _run(self, files):
        root, ctx = _ctx(files)
        verdict = security.supply_chain_provenance(ctx)
        self.addCleanup(rmtree, root)
        return verdict

    def test_attest_action_pass(self):
        v = self._run({".releaserc": "{}", ".github/workflows/prov.yml": self.ATTEST})
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "security.supply_chain_provenance.complete"))

    def test_slsa_generic_pass(self):
        v = self._run({".releaserc": "{}", ".github/workflows/prov.yml": self.SLSA})
        self.assertEqual((v.status.value, v.reason_code),
                         ("pass", "security.supply_chain_provenance.complete"))

    def test_missing_id_token_fails(self):
        v = self._run({".releaserc": "{}",
                       ".github/workflows/prov.yml": self.ATTEST.replace(
                           "      id-token: write\n", "")})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.supply_chain_provenance.wiring_incomplete"))

    def test_missing_subject_fails(self):
        v = self._run({".releaserc": "{}",
                       ".github/workflows/prov.yml": self.ATTEST.replace(
                           "          subject-path: dist/*\n", "")})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.supply_chain_provenance.wiring_incomplete"))

    def test_absent_intent_skips(self):
        v = self._run({"README.md": "# x"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("skipped", "security.supply_chain_provenance.not_applicable"))

    def test_intent_without_provenance_fails(self):
        v = self._run({".releaserc": "{}"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.supply_chain_provenance.wiring_incomplete"))

    def test_literal_false_job_excluded(self):
        v = self._run({".releaserc": "{}",
                       ".github/workflows/prov.yml": "jobs:\n  x:\n    if: false\n"
                       "    steps:\n      - uses: actions/attest-build-provenance@v2\n"
                       "        with:\n          subject-path: dist/*\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("fail", "security.supply_chain_provenance.wiring_incomplete"))

    def test_unsupported_yaml_is_unknown(self):
        v = self._run({".releaserc": "{}",
                       ".github/workflows/prov.yml": "jobs:\n  x: &anchor\n    steps: []\n"
                       "  y: *anchor\n"})
        self.assertEqual((v.status.value, v.reason_code),
                         ("unknown", "security.supply_chain_provenance.syntax_indeterminate"))


class TestWorkflowPolicyParser(unittest.TestCase):
    def test_workflow_permissions_replaced_by_job(self):
        view = _workflow_policy.parse_workflow(
            "permissions:\n  contents: write\njobs:\n  a:\n    permissions:\n"
            "      contents: read\n    steps: []\n")
        job = view.jobs[0]
        self.assertEqual(dict(_workflow_policy.effective_permissions(view, job)),
                         {"contents": "read"})

    def test_upload_assets_false_needs_no_contents_write(self):
        wf = """jobs:
  gen:
    permissions:
      actions: read
      id-token: write
    uses: slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0
    with:
      upload-assets: false
      base64-subjects: abc
"""
        view = _workflow_policy.parse_workflow(wf)
        self.assertIsNotNone(view)

    def test_publish_token_grammar(self):
        self.assertTrue(_workflow_policy._run_is_publish("npm publish --access public"))
        self.assertTrue(_workflow_policy._run_is_publish("TOKEN=$x cargo publish"))
        self.assertIsNone(_workflow_policy._run_is_publish("npm test && npm publish"))
        self.assertFalse(_workflow_policy._run_is_publish("npm test"))


if __name__ == "__main__":
    unittest.main()
