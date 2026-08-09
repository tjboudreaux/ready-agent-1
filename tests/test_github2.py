"""Branch-focused tests for readiness.collectors.github: protection projection,
identity/availability gating, request plumbing caps, per-endpoint wrong-shape
mapping, and the strict HTTP envelope parser.

Complements test_collectors.py; every spawn goes through the fake bounded runners
from tests._util — no real ``gh`` process is launched.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from readiness import process
from readiness.collectors import github
from readiness.collectors.github import GithubCollector, _parse_envelope, _project_protection

from ._util import bpr, envelope, gh_runner

ORIGIN = ("github.com", "o", "r")


def _collector(responses=None, **kw):
    gh = GithubCollector("/tmp/x", origin=ORIGIN,
                         runner=gh_runner(responses or {}), **kw)
    return gh


# --------------------------------------------------------------------------- protection projection
class TestProjectProtection(unittest.TestCase):
    def test_minimal_defaults(self):
        record = _project_protection({})
        self.assertEqual(record.required_approving_review_count, 0)
        self.assertFalse(record.require_code_owner_reviews)
        self.assertFalse(record.allow_force_pushes)
        self.assertFalse(record.allow_deletions)

    def test_reviews_not_a_dict_defaults(self):
        record = _project_protection({"required_pull_request_reviews": []})
        self.assertEqual(record.required_approving_review_count, 0)

    def test_bad_review_count(self):
        for bad in ("2", -1, True):
            with self.subTest(bad=bad):
                self.assertIsNone(_project_protection(
                    {"required_pull_request_reviews":
                     {"required_approving_review_count": bad}}))

    def test_bad_code_owner_flag(self):
        self.assertIsNone(_project_protection(
            {"required_pull_request_reviews": {"require_code_owner_reviews": "yes"}}))

    def test_status_checks_not_a_dict_defaults(self):
        record = _project_protection({"required_status_checks": []})
        self.assertEqual(record.status_contexts, ())

    def test_bad_contexts_or_checks(self):
        self.assertIsNone(_project_protection(
            {"required_status_checks": {"contexts": "ci"}}))
        self.assertIsNone(_project_protection(
            {"required_status_checks": {"checks": "ci"}}))

    def test_check_names_projected(self):
        record = _project_protection({
            "required_status_checks": {
                "contexts": ["ci", 7],
                "checks": [{"context": "lint"}, {"context": 9}, "junk"],
            },
            "allow_force_pushes": {"enabled": True},
            "allow_deletions": {"enabled": True},
        })
        self.assertEqual(record.status_contexts, ("ci",))
        self.assertEqual(record.status_checks, ("lint",))
        self.assertTrue(record.allow_force_pushes)
        self.assertTrue(record.allow_deletions)


# -------------------------------------------------------------------------- identity & availability
class TestIdentityAndAvailability(unittest.TestCase):
    def test_identity_rejections(self):
        cases = [
            (),
            ("gitlab.com", "o", "r"),
            ("github.com", "-bad", "r"),
            ("github.com", "o", "bad/repo"),
            ("github.com", "o", "."),
            ("github.com", "o", ".."),
        ]
        for origin in cases:
            with self.subTest(origin=origin):
                gh = GithubCollector("/tmp/x", origin=origin, runner=gh_runner({}))
                self.assertIsNone(gh.slug)
                self.assertEqual(gh.availability().state, "unavailable")
                self.assertIsNone(gh._endpoint("/topics"))

    def test_slug_and_endpoint(self):
        gh = _collector()
        self.assertEqual(gh.slug, "o/r")
        self.assertEqual(gh._endpoint("/topics"), "repos/o/r/topics")

    def test_availability_without_runner(self):
        # no runner: auth authority is required before any toolchain lookup
        gh = GithubCollector("/tmp/x", origin=ORIGIN)
        obs = gh.availability()
        self.assertEqual(obs.state, "unavailable")
        self.assertEqual(obs.reason, "no usable github auth authority")
        # an auth authority but a toolchain without gh
        gh = GithubCollector("/tmp/x", origin=ORIGIN, auth=object(), toolchain={})
        obs = gh.availability()
        self.assertEqual(obs.state, "unavailable")
        self.assertEqual(obs.reason, "engine gh unavailable")
        # auth plus a resolved gh entry is available without spawning
        gh = GithubCollector("/tmp/x", origin=ORIGIN, auth=object(),
                             toolchain={process.ToolId.GH: "/usr/bin/gh"})
        self.assertEqual(gh.availability().state, "present")

    def test_neutral_cwd_lifecycle(self):
        gh = _collector()
        handle = gh._cwd()
        self.assertIsInstance(handle, int)
        self.assertIsNotNone(gh._cwd_dir)
        gh.close()
        self.assertIsNone(gh._cwd_handle)
        self.assertIsNone(gh._cwd_dir)


# --------------------------------------------------------------------------- spawn / api plumbing
class TestSpawnAndApi(unittest.TestCase):
    def test_legacy_runner_shape_rejected(self):
        gh = GithubCollector("/tmp/x", origin=ORIGIN, runner=lambda argv: "str")
        with self.assertRaises(TypeError):
            gh.repo()

    def test_real_spawn_path_uses_bounded_process(self):
        auth = SimpleNamespace(env=lambda proxy: {"GH_TOKEN": "x"})
        gh = GithubCollector(
            "/tmp/x", origin=ORIGIN, auth=auth,
            toolchain={process.ToolId.GH: "/usr/bin/gh"})
        with mock.patch.object(process, "run_bounded_process",
                               return_value=bpr(envelope('{"full_name": "o/r"}'), 0)
                               ) as spawn:
            obs = gh.repo()
        self.assertEqual(obs.state, "present")
        self.assertEqual(spawn.call_args[0][0], process.ToolId.GH)
        gh.close()

    def test_request_cap(self):
        with mock.patch.object(github, "MAX_GITHUB_REQUESTS_PER_SCAN", 0):
            obs = _collector().repo()
        self.assertEqual(obs.state, "unreadable")
        self.assertEqual(obs.reason, "github request cap reached")

    def test_process_state_mapping(self):
        def runner_with(state):
            def run(args):
                return process.BoundedProcessResult(state, returncode=None)
            return run

        gh = GithubCollector("/tmp/x", origin=ORIGIN,
                             runner=runner_with(process.ProcessState.UNSUPPORTED))
        self.assertEqual(gh.repo().state, "unavailable")
        gh = GithubCollector("/tmp/x", origin=ORIGIN,
                             runner=runner_with(process.ProcessState.TIMEOUT))
        obs = gh.repo()
        self.assertEqual(obs.state, "unreadable")
        self.assertEqual(obs.reason, "gh process timeout")

    def test_malformed_envelope(self):
        gh = GithubCollector("/tmp/x", origin=ORIGIN, runner=lambda argv: bpr("junk"))
        self.assertEqual(gh.repo().reason, "malformed gh envelope")

    def test_total_byte_cap(self):
        with mock.patch.object(github, "MAX_GITHUB_TOTAL_BYTES", 1):
            obs = _collector({"repos/o/r": '{"full_name": "o/r"}'}).repo()
        self.assertEqual(obs.state, "unreadable")
        self.assertEqual(obs.reason, "github total byte cap reached")

    def test_branch_protection_404_is_absent_elsewhere_unreadable(self):
        obs = _collector().branch_protection_details("main")  # unmapped -> 404
        self.assertEqual(obs.state, "absent")
        obs = _collector().repo()  # 404 on a non-protection endpoint
        self.assertEqual(obs.state, "unreadable")
        self.assertEqual(obs.reason, "github status 404")


# --------------------------------------------------------------------------- branch encoding
class TestEncodeBranch(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(GithubCollector._encode_branch("feature/x"), "feature%2Fx")

    def test_rejections(self):
        for bad in (None, "", "x" * 1025, "has space", "a..b", "a//b", "a@{b",
                    "a.lock", "a\\b", "/lead", "trail/", "trail."):
            with self.subTest(bad=bad):
                self.assertIsNone(GithubCollector._encode_branch(bad))


# ------------------------------------------------------------------------ per-endpoint wrong shapes
class TestRepoAndDefaultBranch(unittest.TestCase):
    def test_repo_not_an_object(self):
        obs = _collector({"repos/o/r": "[]"}).repo()
        self.assertEqual(obs.reason, "repo response not an object")

    def test_repo_identity_mismatch(self):
        obs = _collector({"repos/o/r": '{"full_name": "other/repo"}'}).repo()
        self.assertEqual(obs.reason, "repository identity mismatch")

    def test_default_branch_missing_or_invalid(self):
        obs = _collector({"repos/o/r": '{"full_name": "o/r"}'}).default_branch()
        self.assertEqual(obs.reason, "default branch missing")
        obs = _collector(
            {"repos/o/r": '{"full_name": "o/r", "default_branch": "bad..ref"}'}
        ).default_branch()
        self.assertEqual(obs.reason, "default branch invalid")


class TestTopics(unittest.TestCase):
    def test_endpoint_failure_passthrough(self):
        responses = {"repos/o/r/topics": ("{}", 403)}
        obs = _collector(responses).topics()
        self.assertEqual(obs.state, "unreadable")
        self.assertEqual(obs.reason, "github status 403")

    def test_repo_fallback(self):
        responses = {
            "repos/o/r/topics": "{}",  # success but wrong shape for topics
            "repos/o/r": '{"full_name": "o/r", "topics": ["a", "b"]}',
        }
        obs = _collector(responses).topics()
        self.assertEqual(obs.state, "present")
        self.assertEqual(obs.value, ("a", "b"))

    def test_fallback_unreadable(self):
        # topics wrong shape and the repo fallback is unreadable too
        obs = _collector({"repos/o/r/topics": "{}"}).topics()
        self.assertEqual(obs.reason, "topics response wrong shape")


class TestBranchProtection(unittest.TestCase):
    def test_invalid_branch(self):
        obs = _collector().branch_protection_details("bad ref")
        self.assertEqual(obs.reason, "branch invalid")

    def test_protection_not_an_object(self):
        responses = {"repos/o/r/branches/main/protection": "[]"}
        obs = _collector(responses).branch_protection_details("main")
        self.assertEqual(obs.reason, "protection response not an object")

    def test_protection_wrong_shape(self):
        responses = {
            "repos/o/r/branches/main/protection":
                '{"required_pull_request_reviews": '
                '{"required_approving_review_count": "two"}}',
        }
        obs = _collector(responses).branch_protection_details("main")
        self.assertEqual(obs.reason, "protection response wrong shape")

    def test_protected_wrapper_states(self):
        record = ('{"required_pull_request_reviews": '
                  '{"required_approving_review_count": 1.5}}')  # wrong shape
        responses = {"repos/o/r/branches/main/protection": record}
        obs = _collector(responses).branch_protected("main")
        self.assertEqual(obs.state, "unreadable")  # wrong-shape details pass through
        responses = {"repos/o/r/branches/main/protection": "{}"}
        obs = _collector(responses).branch_protected("main")
        self.assertEqual(obs.state, "present")
        self.assertIs(obs.value, True)


class TestRunsLabelsIssues(unittest.TestCase):
    def test_runs_failure_passthrough(self):
        responses = {"repos/o/r/actions/runs?per_page=20": ("{}", 500)}
        obs = _collector(responses).recent_runs()
        self.assertEqual(obs.reason, "github status 500")

    def test_run_record_wrong_shape(self):
        responses = {"repos/o/r/actions/runs?per_page=20":
                     '{"workflow_runs": ["x"]}'}
        obs = _collector(responses).recent_runs()
        self.assertEqual(obs.reason, "run record wrong shape")

    def test_label_record_wrong_shape(self):
        obs = _collector({"repos/o/r/labels?per_page=100": '[{"name": 1}]'}).labels()
        self.assertEqual(obs.reason, "label record wrong shape")

    def test_issue_record_wrong_shape(self):
        obs = _collector(
            {"repos/o/r/issues?state=open&per_page=50": '["x"]'}).open_issues()
        self.assertEqual(obs.reason, "issue record wrong shape")

    def test_issues_skip_pull_requests(self):
        body = '[{"pull_request": {}, "labels": [], "milestone": null, "body": ""}]'
        obs = _collector({"repos/o/r/issues?state=open&per_page=50": body}) \
            .open_issues()
        self.assertEqual(obs.state, "present")
        self.assertEqual(obs.value, ())


class TestMergedPrs(unittest.TestCase):
    def _endpoint(self, page):
        return (f"repos/o/r/pulls?state=closed&sort=updated&direction=desc"
                f"&per_page=50&page={page}")

    def test_first_page_failure_passthrough(self):
        obs = _collector({self._endpoint(1): ("{}", 403)}).recent_merged_prs()
        self.assertEqual(obs.reason, "github status 403")

    def test_partial_page_failure(self):
        page1 = '[{"number": 7, "merged_at": "2026-01-01", "created_at": ""}]'
        responses = {self._endpoint(1): page1, self._endpoint(2): ("{}", 500)}
        obs = _collector(responses).recent_merged_prs()
        self.assertEqual(obs.reason, "partial merged-pr page failure")

    def test_pulls_wrong_shape(self):
        obs = _collector({self._endpoint(1): "{}"}).recent_merged_prs()
        self.assertEqual(obs.reason, "pulls response wrong shape")

    def test_pr_record_wrong_shape(self):
        obs = _collector({self._endpoint(1): '["x"]'}).recent_merged_prs()
        self.assertEqual(obs.reason, "pr record wrong shape")

    def test_unmerged_prs_skipped_and_invalid_number(self):
        body = '[{"number": 1, "merged_at": null}]'
        responses = {self._endpoint(1): body, self._endpoint(2): "[]"}
        obs = _collector(responses).recent_merged_prs()
        self.assertEqual(obs.state, "present")
        self.assertEqual(obs.value, ())
        body = '[{"number": "7", "merged_at": "2026-01-01"}]'
        obs = _collector({self._endpoint(1): body}).recent_merged_prs()
        self.assertEqual(obs.reason, "pr number invalid")


class TestFirstReview(unittest.TestCase):
    def test_invalid_pr_number(self):
        for bad in (0, -1, 2_147_483_648, "7", True):
            with self.subTest(bad=bad):
                obs = _collector().pr_first_review_iso(bad)
                self.assertEqual(obs.reason, "pr number invalid")

    def test_reviews_wrong_shape(self):
        obs = _collector({"repos/o/r/pulls/7/reviews?per_page=100": "{}"}) \
            .pr_first_review_iso(7)
        self.assertEqual(obs.reason, "reviews response wrong shape")

    def test_review_record_wrong_shape(self):
        obs = _collector({"repos/o/r/pulls/7/reviews?per_page=100": '["x"]'}) \
            .pr_first_review_iso(7)
        self.assertEqual(obs.reason, "review record wrong shape")

    def test_empty_submitted_times_are_absent(self):
        obs = _collector({"repos/o/r/pulls/7/reviews?per_page=100":
                          '[{"submitted_at": ""}]'}).pr_first_review_iso(7)
        self.assertEqual(obs.state, "absent")


# --------------------------------------------------------------------------- envelope parser
class TestParseEnvelope(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(_parse_envelope(envelope("{}")), (200, "{}"))

    def test_rejections(self):
        cases = {
            "empty": "",
            "non-string": None,
            "no separator": "HTTP/2 200 OK\r\ncontent-type: x",
            "not http": "XYZ\r\n\r\n{}",
            "missing status": "HTTP/2\r\n\r\n{}",
            "non-numeric status": "HTTP/2 OK\r\n\r\n{}",
            "status out of range": "HTTP/2 99\r\n\r\n{}",
            "bad header": "HTTP/2 200 OK\r\nbadheader\r\n\r\n{}",
            "redirect chain": envelope(envelope("{}")),
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                self.assertIsNone(_parse_envelope(text))


if __name__ == "__main__":
    unittest.main()
