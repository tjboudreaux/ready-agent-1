"""Focused edge-path coverage for the Git collector (engine/readiness/collectors/git.py).

Covers authority admission refusals, the typed runner boundary, modal process failures,
and the lossless log/status/show/rev-list observation branches. Injected runners always
return :class:`readiness.process.BoundedProcessResult`; real authority acquisition is
replaced by typed mocks so refusal branches stay deterministic.
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock

from readiness import process, safe_io
from readiness.collectors.git import GitCollector
from readiness.process import BoundedProcessResult, ProcessState

from tests._util import fake_runner

_NO_GIT = process.Toolchain(((process.ToolId.PYTHON_SHIM, sys.executable),))
_WITH_GIT = process.Toolchain(((process.ToolId.PYTHON_SHIM, sys.executable),
                               (process.ToolId.GIT, "/usr/bin/git")))


class TestGitAdmitPaths(unittest.TestCase):
    def test_injected_authority_skips_acquisition(self):
        g = GitCollector("/tmp/whatever", authority=object())
        self.assertEqual(g.origin_identity(), ())
        self.assertFalse(g.origin_malformed())
        self.assertEqual(g.metadata_profile(), "")

    def test_toolchain_without_git_is_unavailable(self):
        g = GitCollector("/tmp/whatever", toolchain=_NO_GIT)
        self.assertEqual(g.availability().state, "unavailable")
        self.assertFalse(g.available())

    def test_unsupported_platform_profile_is_unavailable(self):
        with mock.patch.object(process, "git_resource_profile", return_value=None):
            g = GitCollector("/tmp/whatever", toolchain=_WITH_GIT)
            self.assertEqual(g.availability().state, "unavailable")

    def test_root_acquisition_failure_is_unreadable(self):
        with mock.patch.object(safe_io, "acquire_root", side_effect=OSError("boom")):
            g = GitCollector("/tmp/whatever", toolchain=_WITH_GIT)
            self.assertEqual(g.availability().state, "unreadable")

    def test_legacy_runner_result_raises_typeerror(self):
        g = GitCollector("/tmp/whatever", runner=lambda args: "legacy-scalar")
        with self.assertRaises(TypeError):
            g.head_sha()

    def test_refusal_run_maps_unsupported_and_spawn_error(self):
        g = GitCollector("/tmp/whatever", toolchain=_NO_GIT)
        self.assertEqual(g.head_sha().state, "unavailable")

        with mock.patch.object(safe_io, "acquire_root", side_effect=OSError("boom")):
            g2 = GitCollector("/tmp/whatever", toolchain=_WITH_GIT)
            self.assertEqual(g2.head_sha().state, "unreadable")

    def test_malformed_rev_parse_output_is_unreadable(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("rev-parse", "--is-inside-work-tree"): "maybe\n",
        }))
        self.assertEqual(g.availability().state, "unreadable")
        self.assertFalse(g.available())


class TestGitFactEdges(unittest.TestCase):
    def test_most_recent_commit_absent_on_empty_dates(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("log", "-1", "--format=%cI"): "",
        }))
        self.assertEqual(g.most_recent_commit_iso().state, "absent")

    def test_commit_count_for_path_kind_branches(self):
        ok_empty = mock.Mock(state=safe_io.RepoDiscoveryState.OK, paths=())

        # Exact-match discovery degraded: the kind is indeterminate, never guessed.
        static = mock.Mock()
        static.glob_repo_files.return_value = mock.Mock(
            state=safe_io.RepoDiscoveryState.UNREADABLE, paths=())
        g = GitCollector("/tmp/whatever", runner=fake_runner({}), static=static)
        self.assertEqual(g.commit_count_for("src").state, "unreadable")

        # Beneath-discovery degraded after a clean empty exact match: still indeterminate.
        static2 = mock.Mock()
        static2.glob_repo_files.side_effect = [
            ok_empty,
            mock.Mock(state=safe_io.RepoDiscoveryState.OVERFLOW, paths=()),
        ]
        g2 = GitCollector("/tmp/whatever", runner=fake_runner({}), static=static2)
        self.assertEqual(g2.commit_count_for("src").state, "unreadable")

        # Beneath-discovery finds files: the path is a directory (rev-list count).
        static3 = mock.Mock()
        static3.glob_repo_files.side_effect = [
            ok_empty,
            mock.Mock(state=safe_io.RepoDiscoveryState.OK, paths=("src/a.py",)),
        ]
        g3 = GitCollector("/tmp/whatever", runner=fake_runner({
            ("rev-list", "--count", "HEAD", "--", "src"): "3\n",
        }), static=static3)
        self.assertEqual(g3.commit_count_for("src").value, 3)

        # Both discoveries empty: the name heuristic decides without I/O (file form).
        static4 = mock.Mock()
        static4.glob_repo_files.side_effect = [ok_empty, ok_empty]
        g4 = GitCollector("/tmp/whatever", runner=fake_runner({
            ("log", "--follow", "--format=%H", "HEAD", "--", "src"): "h1\nh2\n",
        }), static=static4)
        self.assertEqual(g4.commit_count_for("src").value, 2)

    def test_is_ancestor_outcomes(self):
        g_false = GitCollector("/tmp/whatever", runner=fake_runner({
            ("merge-base", "--is-ancestor", "a", "b"): ("", 1),
        }))
        obs = g_false.is_ancestor("a", "b")
        self.assertEqual((obs.state, obs.value), ("present", False))

        g_err = GitCollector("/tmp/whatever", runner=fake_runner({
            ("merge-base", "--is-ancestor", "a", "b"): ("", 128),
        }))
        self.assertEqual(g_err.is_ancestor("a", "b").state, "unreadable")

        g_modal = GitCollector(
            "/tmp/whatever",
            runner=lambda args: BoundedProcessResult(ProcessState.TIMEOUT))
        self.assertEqual(g_modal.is_ancestor("a", "b").state, "unreadable")


class TestGitWorktreeCommandEdges(unittest.TestCase):
    def test_status_porcelain_nonzero_and_modal_failures(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("status", "--porcelain"): ("", 1),
        }))
        self.assertEqual(g.status_porcelain().state, "unreadable")

        g2 = GitCollector(
            "/tmp/whatever",
            runner=lambda args: BoundedProcessResult(ProcessState.UNSUPPORTED))
        self.assertEqual(g2.status_porcelain().state, "unavailable")

    def test_status_porcelain_view_refusal_is_unreadable(self):
        auth = mock.Mock(spec=safe_io.GitSnapshotAuthority)
        auth.ensure_full_view.side_effect = safe_io.RepositoryInputError("nope")
        g = GitCollector("/tmp/whatever", authority=auth,
                         runner=fake_runner({("status", "--porcelain"): ""}))
        self.assertEqual(g.status_porcelain().state, "unreadable")

    def test_check_ignore_blank_lines_and_malformed_records(self):
        g = GitCollector("/tmp/whatever", runner=fake_runner({
            ("check-ignore", "-v", "--no-index", "--", ".env"):
                ".gitignore:1:.env\t.env\n\n",
        }))
        obs = g.check_ignore((".env",))
        self.assertEqual(obs.state, "present")
        self.assertEqual(obs.value, ((".gitignore", "1", ".env", ".env"),))

        g2 = GitCollector("/tmp/whatever", runner=fake_runner({
            ("check-ignore", "-v", "--no-index", "--", ".env"): "garbage-line\n",
        }))
        self.assertEqual(g2.check_ignore((".env",)).state, "unreadable")

    def test_check_ignore_view_refusal_is_unreadable(self):
        auth = mock.Mock(spec=safe_io.GitSnapshotAuthority)
        auth.ensure_gitignore_view.side_effect = safe_io.RepositoryInputError("nope")
        g = GitCollector("/tmp/whatever", authority=auth, runner=fake_runner({}))
        self.assertEqual(g.check_ignore((".env",)).state, "unreadable")


if __name__ == "__main__":
    unittest.main()
