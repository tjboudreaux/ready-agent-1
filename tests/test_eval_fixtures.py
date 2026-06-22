"""Labeled-fixture eval pipeline: classification, thresholds, graduation gate, parity."""
import unittest

from evals.contracts import gating_total_matches
from evals.fixtures import (build_fixture, canned_github_runner, check_thresholds, compare,
                            load_fixtures, load_thresholds, materialize, run_fixture,
                            score_fixtures)
from evals.parity import compare_parity, score_parity
from readiness.score import load_registry


class TestCompare(unittest.TestCase):
    def test_classification_directions(self):
        expected = {"a": "pass", "b": "fail", "c": "skipped", "d": "pass", "e": "fail"}
        actual = {"a": "pass", "b": "pass", "c": "fail", "d": "fail", "e": "fail"}
        cls = compare(expected, actual)
        self.assertEqual(cls["a"], "correct")
        self.assertEqual(cls["b"], "fp")            # undeserved pass
        self.assertEqual(cls["c"], "applicability")  # wrong skip direction
        self.assertEqual(cls["d"], "fn")            # undeserved fail
        self.assertEqual(cls["e"], "correct")

    def test_missing_actual_is_applicability(self):
        cls = compare({"a": "pass"}, {})
        self.assertEqual(cls["a"], "applicability")


class TestThresholds(unittest.TestCase):
    def _summary(self, fp=0, fn=0, applicability=0):
        per = {"docs.readme": {"fp": fp, "fn": fn, "applicability": applicability, "n": 1}}
        totals = {"fixtures": 1, "expectations": 1, "fp": fp, "fn": fn,
                  "applicability": applicability}
        return {"per_criterion": per, "totals": totals,
                "detection_errors": [], "level_errors": []}

    def test_seeded_fp_violates(self):
        registry = [{"id": "docs.readme", "gating": True}]
        violations = check_thresholds(self._summary(fp=1), load_thresholds(), registry)
        self.assertTrue(any("false-positive" in v for v in violations))
        self.assertTrue(any("docs.readme" in v for v in violations))

    def test_uncovered_gating_criterion_violates(self):
        registry = [{"id": "docs.readme", "gating": True}, {"id": "x.uncovered", "gating": True}]
        violations = check_thresholds(self._summary(), load_thresholds(), registry)
        self.assertTrue(any("x.uncovered" in v and "no fixture coverage" in v for v in violations))

    def test_advisory_criterion_needs_no_coverage(self):
        registry = [{"id": "docs.readme", "gating": True}, {"id": "x.advisory", "gating": False}]
        violations = check_thresholds(self._summary(), load_thresholds(), registry)
        self.assertEqual(violations, [])


class TestParityCompare(unittest.TestCase):
    def test_agree_and_diverge(self):
        agree, diverge = compare_parity({"a": "pass", "b": "fail"}, {"a": "pass", "b": "pass"})
        self.assertEqual(agree, ["a"])
        self.assertEqual(diverge, [{"criterion": "b", "factory": "fail", "engine": "pass"}])



class TestFixtureBuilders(unittest.TestCase):
    def test_build_fixture_full(self):
        fx = build_fixture("x", {"README.md": "# hi"}, expected={"docs.readme": "pass"},
                           detect={"project_type": "library"}, expected_level=1,
                           git_init=True, github={"repo view": "{}"})
        self.assertEqual(fx["name"], "x")
        self.assertEqual(fx["expected"], {"docs.readme": "pass"})
        self.assertEqual(fx["detect"], {"project_type": "library"})
        self.assertEqual(fx["expected_level"], 1)
        self.assertTrue(fx["git_init"])
        self.assertIn("github", fx)

    def test_build_fixture_minimal(self):
        self.assertEqual(build_fixture("y", {}), {"name": "y", "files": {}})


class TestCannedGithub(unittest.TestCase):
    def test_runner_returns_canned_and_none(self):
        runner = canned_github_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner": "o/r"}',
            "api repos/o/r": '{"default_branch": "main"}',
        })
        self.assertEqual(runner(["repo", "view", "--json", "nameWithOwner"]),
                         '{"nameWithOwner": "o/r"}')
        self.assertEqual(runner(["api", "repos/o/r"]), '{"default_branch": "main"}')
        self.assertIsNone(runner(["api", "unknown"]))

    def test_runner_makes_collector_available(self):
        from readiness.collectors.github import GithubCollector
        runner = canned_github_runner({
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner": "o/r"}',
        })
        gc = GithubCollector("/tmp", runner=runner)
        self.assertTrue(gc.available)
        self.assertEqual(gc.slug, "o/r")


class TestRunFixtureBranches(unittest.TestCase):
    def test_detect_match_and_mismatch(self):
        files = {"package.json": '{"name": "x", "version": "1.0.0"}'}
        probe = run_fixture(build_fixture("probe", files, detect={"project_type": "__none__"}))
        self.assertFalse(probe["detect_ok"])  # mismatch branch
        matched = run_fixture(build_fixture("m", files, detect={"project_type": probe["detected"]}))
        self.assertTrue(matched["detect_ok"])  # match branch

    def test_level_match_and_mismatch(self):
        files = {"README.md": "# hi"}
        probe = run_fixture(build_fixture("p", files, expected_level=-1))
        self.assertFalse(probe["level_ok"])
        matched = run_fixture(build_fixture("m", files, expected_level=probe["level"]))
        self.assertTrue(matched["level_ok"])

    def test_canned_github_path_runs_offline(self):
        fx = build_fixture("gh", {"README.md": "# hi"}, github={
            ("repo", "view", "--json", "nameWithOwner"): '{"nameWithOwner": "o/r"}'})
        r = run_fixture(fx)
        self.assertIn("actual", r)


class TestScoreFixturesAggregation(unittest.TestCase):
    def test_aggregates_correct_and_errors(self):
        good = build_fixture("g", {"README.md": "# hi"}, expected={"docs.readme": "pass"})
        bad = build_fixture("b", {"README.md": "# hi"},
                            detect={"project_type": "__none__"}, expected_level=99)
        summary = score_fixtures([good, bad])
        self.assertEqual(summary["totals"]["fixtures"], 2)
        self.assertTrue(summary["detection_errors"])
        self.assertTrue(summary["level_errors"])


class TestThresholdRates(unittest.TestCase):
    def _summary(self, fp=0, fn=0, app=0, n=1):
        per = {"docs.readme": {"fp": fp, "fn": fn, "applicability": app, "n": n}}
        totals = {"expectations": n, "fp": fp, "fn": fn, "applicability": app, "fixtures": 1}
        return {"per_criterion": per, "totals": totals, "runs": [],
                "detection_errors": [], "level_errors": []}

    def test_fn_rate_violation(self):
        violations = check_thresholds(self._summary(fn=1), load_thresholds(), [])
        self.assertTrue(any("false-negative" in v for v in violations))

    def test_applicability_rate_violation(self):
        violations = check_thresholds(self._summary(app=1), load_thresholds(), [])
        self.assertTrue(any("applicability-error" in v for v in violations))


class TestEngineInvariants(unittest.TestCase):
    def _analyze(self, files):
        import tempfile
        from readiness.run import analyze
        with tempfile.TemporaryDirectory(prefix="ra1-inv-") as tmp:
            materialize(build_fixture("inv", files), tmp)
            return analyze(tmp, {"no_github": True}).to_dict()

    def test_advisory_failures_do_not_change_gate(self):
        engine = self._analyze({"README.md": "# hi"})
        self.assertTrue(gating_total_matches(engine))
        # Non-trivial: there is at least one advisory (gating:false) result in the report.
        self.assertTrue(any(not r["gating"] for r in engine["results"]))

    def test_passes_are_evidence_driven_and_absent_features_do_not_pass(self):
        readme = ("# Project\n\n" + "This is a substantive README with real content. " * 8
                  + "\n\n## Usage\n\nRun the thing and read the output.\n")
        engine = self._analyze({"README.md": readme})
        by_id = {r["id"]: r for r in engine["results"]}
        self.assertEqual(by_id["docs.readme"]["status"], "pass")
        self.assertTrue(by_id["docs.readme"]["evidence"])
        self.assertNotEqual(by_id["security.codeowners"]["status"], "pass")


class TestRealCorpus(unittest.TestCase):
    """The graduation gate, enforced inside the unit suite: the committed corpus must be
    green (zero FP/FN/applicability, every gating criterion covered, parity above floor)."""

    @classmethod
    def setUpClass(cls):
        cls.summary = score_fixtures()

    def test_corpus_has_zero_violations(self):
        violations = check_thresholds(self.summary, load_thresholds(), load_registry())
        self.assertEqual(violations, [])

    def test_every_gating_criterion_covered(self):
        covered = set(self.summary["per_criterion"])
        gating = {c["id"] for c in load_registry() if c.get("gating", True)}
        self.assertEqual(gating - covered, set())

    def test_corpus_exercises_both_verdict_directions(self):
        statuses = set()
        for fx in load_fixtures():
            statuses.update(fx.get("expected", {}).values())
        self.assertIn("pass", statuses)
        self.assertIn("fail", statuses)

    def test_parity_above_floor(self):
        parity = score_parity()
        floor = load_thresholds().get("parity_min_agree", 0.85)
        self.assertIsNotNone(parity["agree_rate"])
        self.assertGreaterEqual(parity["agree_rate"], floor)

    def test_pillars_doc_matches_registry(self):
        from pathlib import Path
        doc = (Path(__file__).resolve().parent.parent / "references" / "pillars.md").read_text(
            encoding="utf-8")
        for crit in load_registry():
            if crit.get("gating", True):
                self.assertIn(f"`{crit['id']}`", doc,
                              f"gating criterion {crit['id']} missing from references/pillars.md")


if __name__ == "__main__":
    unittest.main()
