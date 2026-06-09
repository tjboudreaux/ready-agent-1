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

    def test_run_contract_checks(self):
        checks = contracts.run_contract_checks(self.engine, good_output(self.engine))
        self.assertTrue(contracts.all_passed(checks))
        bad = contracts.run_contract_checks(self.engine, "prose with no score block at all")
        self.assertFalse(contracts.all_passed(bad))
        self.assertFalse(bad["has_score_block"])


if __name__ == "__main__":
    unittest.main()
