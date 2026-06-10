"""Labeled-fixture eval pipeline: classification, thresholds, graduation gate, parity."""
import unittest

from evals.fixtures import (check_thresholds, compare, load_fixtures, load_thresholds,
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
