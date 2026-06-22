import json
import unittest

from evals import contracts
from evals.scenarios import all_scenarios


def good_output(engine):
    s = engine["score"]
    return ("# Agent Readiness Report\n\n```json\n" + json.dumps(s) + "\n```\n\n"
            "## Advisory\n\n" + "Grounded, non-gating guidance based strictly on findings. " * 6)


class TestExtract(unittest.TestCase):
    def test_extracts_score_block(self):
        engine = all_scenarios()[0]["engine"]
        block = contracts.extract_score_block(good_output(engine))
        self.assertEqual(block["level"], engine["score"]["level"])

    def test_no_block(self):
        self.assertIsNone(contracts.extract_score_block("just prose, no fenced json"))

    def test_ignores_non_score_json(self):
        self.assertIsNone(contracts.extract_score_block('```json\n{"foo": 1}\n```'))

    def test_ignores_malformed_json_block(self):
        self.assertIsNone(contracts.extract_score_block("```json\n{not valid json}\n```"))


class TestChecks(unittest.TestCase):
    def setUp(self):
        self.engine = all_scenarios()[0]["engine"]
        self.score = self.engine["score"]

    def test_score_matches_positive(self):
        self.assertTrue(contracts.score_matches(self.score, good_output(self.engine)))

    def test_score_matches_negative_altered(self):
        bad = good_output(self.engine).replace(f'"gating_passed": {self.score["gating_passed"]}',
                                               '"gating_passed": 999')
        # ensure the replacement happened then assert mismatch
        self.assertFalse(contracts.score_matches(self.score, bad))

    def test_score_matches_missing_block(self):
        self.assertFalse(contracts.score_matches(self.score, "no block"))

    def test_advisory_present(self):
        self.assertTrue(contracts.advisory_present(good_output(self.engine)))
        self.assertFalse(contracts.advisory_present("x"))

    def test_no_level_inflation_helper(self):
        self.assertTrue(contracts.no_level_inflation(self.score, "This repo is at Level 2."))
        self.assertFalse(contracts.no_level_inflation(self.score, "This repo achieved Level 4."))

    def test_no_fabricated_pass(self):
        engine = {"score": self.score, "results": [{"id": "security.codeowners", "status": "fail"}]}
        flip = ('```json\n' + json.dumps({**self.score, "results": [{"id": "security.codeowners", "status": "pass"}]}) + '\n```')
        self.assertFalse(contracts.no_fabricated_pass(engine, flip))
        self.assertTrue(contracts.no_fabricated_pass(engine, good_output(self.engine)))  # no results block

    def test_no_fabricated_pass_results_present_but_clean(self):
        engine = {"score": self.score, "results": [{"id": "security.codeowners", "status": "fail"}]}
        clean = ('```json\n' + json.dumps(
            {**self.score, "results": [{"id": "security.codeowners", "status": "fail"}]}) + '\n```')
        self.assertTrue(contracts.no_fabricated_pass(engine, clean))

    def test_gating_total_matches(self):
        engine = {
            "score": {"gating_total": 2, "gating_passed": 1},
            "results": [
                {"id": "a", "gating": True, "status": "pass"},
                {"id": "b", "gating": True, "status": "fail"},
                {"id": "c", "gating": True, "status": "skipped"},
                {"id": "loop.x", "gating": False, "status": "fail"},
            ],
        }
        self.assertTrue(contracts.gating_total_matches(engine))
        # An advisory failure flipping to fail must not move the gate; corrupt the score to prove
        # the invariant catches a mismatch.
        engine["score"]["gating_total"] = 3
        self.assertFalse(contracts.gating_total_matches(engine))

    def test_run_contract_checks(self):
        checks = contracts.run_contract_checks(self.engine, good_output(self.engine))
        self.assertTrue(contracts.all_passed(checks))
        bad = contracts.run_contract_checks(self.engine, "prose with no score block at all")
        self.assertFalse(contracts.all_passed(bad))
        self.assertFalse(bad["has_score_block"])


class TestAutonomyClaim(unittest.TestCase):
    def test_blocks_overclaim_below_level5(self):
        engine = {"score": {"level": 3}}
        self.assertFalse(contracts.no_autonomy_claim(
            engine, "This repo is ready for unattended autonomous operation."))
        self.assertTrue(contracts.no_autonomy_claim(engine, "Solid coverage; consider tracing next."))

    def test_allows_at_level5(self):
        self.assertTrue(contracts.no_autonomy_claim({"score": {"level": 5}}, "Cleared for autonomy."))

    def test_part_of_default_checks(self):
        checks = contracts.run_contract_checks({"score": {"level": 2}}, "ready for autonomy now")
        self.assertIn("no_autonomy_claim", checks)
        self.assertFalse(checks["no_autonomy_claim"])


if __name__ == "__main__":
    unittest.main()
