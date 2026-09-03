"""Gate-closing branch coverage for evals/contracts.py.

Complements test_contracts.py: every case targets one previously-uncovered line or arc
in the fence-parsing and explanation-block helpers. New file by ownership convention.
"""
from __future__ import annotations

import unittest

from evals import contracts

BAD_FENCE = "```json\n{invalid,}\n```"


class TestExtractExplanationBlock(unittest.TestCase):
    def test_invalid_json_fence_is_skipped(self):
        self.assertIsNone(contracts.extract_explanation_block(BAD_FENCE))
        # ...and a later valid fence still wins.
        text = BAD_FENCE + "\n```json\n{\"explanations\": []}\n```"
        self.assertEqual(contracts.extract_explanation_block(text),
                         {"explanations": []})


class TestExpectedExplanationBlock(unittest.TestCase):
    def test_action_without_matching_result_is_skipped(self):
        engine = {"results": [], "score": {"next_gate_actions": [{"id": "a.b"}]}}
        self.assertEqual(contracts.expected_explanation_block(engine),
                         {"explanations": []})

    def test_empty_and_duplicate_sources_deduped(self):
        # The second/third evidence items take the `source and source not in sources`
        # False arc (empty string, then duplicate).
        engine = {
            "results": [{
                "id": "a.b", "status": "fail",
                "decision_trace": {"reason_code": "check.fail",
                                   "rule_ref": "checks.m.f", "limitations": []},
                "evidence": [{"source": "src/x"}, {"source": ""}, {"source": "src/x"}],
            }],
            "score": {"next_gate_actions": [{"id": "a.b"}]},
        }
        block = contracts.expected_explanation_block(engine)
        self.assertEqual(block["explanations"][0]["evidence_sources"], ["src/x"])


class TestNoPrivateReasoningKeys(unittest.TestCase):
    def test_invalid_json_fence_is_skipped(self):
        self.assertTrue(contracts.no_private_reasoning_keys(BAD_FENCE,
                                                            skill="ra1-report"))

    def test_non_dict_fence_is_skipped(self):
        self.assertTrue(contracts.no_private_reasoning_keys("```json\n[1, 2]\n```",
                                                            skill="ra1-report"))

    def test_decision_trace_key_is_walked(self):
        bad = '```json\n{"decision_trace": {"thinking": "secret"}}\n```'
        self.assertFalse(contracts.no_private_reasoning_keys(bad, skill="ra1-fix"))
        good = '```json\n{"decision_trace": {"reason_code": "check.fail"}}\n```'
        self.assertTrue(contracts.no_private_reasoning_keys(good, skill="ra1-fix"))


class TestExtractFixContract(unittest.TestCase):
    def test_invalid_json_fence_is_skipped(self):
        self.assertIsNone(contracts.extract_fix_contract(BAD_FENCE))

    def test_dict_without_discriminator_is_skipped(self):
        self.assertIsNone(contracts.extract_fix_contract('```json\n{"foo": 1}\n```'))
        text = ('```json\n{"foo": 1}\n```\n'
                '```json\n{"operation": "apply", "apply_result": {},'
                ' "verification": {}}\n```')
        self.assertEqual(contracts.extract_fix_contract(text),
                         {"operation": "apply", "apply_result": {},
                          "verification": {}})


if __name__ == "__main__":
    unittest.main()
