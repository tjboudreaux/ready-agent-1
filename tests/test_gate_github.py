"""Gate-closing branch coverage for readiness/collectors/github.py.

Complements test_github2.py: lazy toolchain resolution, neutral-cwd handle reuse, and
the defensive present-observation pagination break (unreachable through the real
``_api``, exercised via a patched instance exactly as test_github2.py patches
collector internals). New file by ownership convention.
"""
from __future__ import annotations

import unittest

from readiness.collectors._observation import present
from readiness.collectors.github import GithubCollector

from tests._util import gh_runner

ORIGIN = ("github.com", "o", "r")


class TestLazyToolchainResolution(unittest.TestCase):
    def test_toolchain_resolved_on_first_availability(self):
        collector = GithubCollector("/tmp/x", origin=ORIGIN, auth=object())
        observation = collector.availability()
        self.assertIsNotNone(collector._toolchain)
        # gh may or may not be installed on the host; both outcomes are valid.
        self.assertIn(observation.state, ("present", "unavailable"))


class TestNeutralCwdHandleReuse(unittest.TestCase):
    def test_second_call_reuses_open_handle(self):
        collector = GithubCollector("/tmp/x")
        self.addCleanup(collector.close)
        first = collector._cwd()
        second = collector._cwd()
        self.assertEqual(first, second)
        self.assertIsNotNone(collector._cwd_dir)


class TestMergedPrsPresentObservationBreak(unittest.TestCase):
    def test_present_observation_stops_pagination(self):
        collector = GithubCollector("/tmp/x", origin=ORIGIN, runner=gh_runner({}))
        self.addCleanup(collector.close)
        collector._api = lambda endpoint, *, kind: present(())
        observation = collector.recent_merged_prs()
        self.assertEqual(observation.state, "present")
        self.assertEqual(observation.value, ())


if __name__ == "__main__":
    unittest.main()
