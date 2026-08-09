"""Deterministic skill-output contracts: score/explanation blocks, Level claims, guards."""
import json
import unittest

from evals import contracts
from evals.scenarios import all_scenarios


def good_output(engine):
    """A compliant ra1-report output: verbatim score, exact explanations, advisory prose."""
    s = engine["score"]
    explanations = contracts.expected_explanation_block(engine)
    return ("# Agent Readiness Report\n\n```json\n" + json.dumps(s) + "\n```\n\n"
            "## Evidence explanations\n\n```json\n" + json.dumps(explanations) + "\n```\n\n"
            "## T4 Advisory\n\n" + "Grounded, non-gating guidance based strictly on findings. " * 6)


def good_fix_output(engine):
    """A compliant ra1-fix output: verbatim fix_contract plus grounded prose."""
    return ("# Remediation outcome\n\n```json\n" + json.dumps(engine["fix_contract"])
            + "\n```\n\n## Summary\n\n"
            + "Outcome recorded by the deterministic engine; details above. " * 5)


def good_interview_output(engine):
    """A compliant ra1-interview output: verbatim answer_contract plus grounded prose."""
    return ("# Recorded answer\n\n```json\n" + json.dumps(engine["answer_contract"])
            + "\n```\n\n## Summary\n\n"
            + "Outcome recorded by the deterministic engine; details above. " * 5)


def _scenario(skill, name=None):
    for scenario in all_scenarios():
        if scenario["skill"] == skill and (name is None or scenario["name"] == name):
            return scenario
    raise AssertionError(f"no scenario for {skill!r} {name!r}")


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

    def test_explanation_block_is_not_a_score_block(self):
        # The explanations fence lacks level/gating_total and must not be picked as the score.
        text = '```json\n{"explanations": []}\n```'
        self.assertIsNone(contracts.extract_score_block(text))


class TestScoreBlockChecks(unittest.TestCase):
    def setUp(self):
        self.engine = all_scenarios()[0]["engine"]
        self.score = self.engine["score"]

    def test_score_matches_positive(self):
        self.assertTrue(contracts.score_matches(self.score, good_output(self.engine)))

    def test_score_matches_negative_altered(self):
        bad = good_output(self.engine).replace(f'"gating_passed": {self.score["gating_passed"]}',
                                               '"gating_passed": 999')
        self.assertFalse(contracts.score_matches(self.score, bad))

    def test_score_matches_rejects_altered_pass_rate(self):
        score = dict(self.score)
        score["pass_rate"] = score.get("pass_rate", 0) + 0.01
        bad = "# Agent Readiness Report\n\n```json\n" + json.dumps(score) + "\n```\n"
        self.assertFalse(contracts.score_matches(self.score, bad))

    def test_score_matches_rejects_extra_key(self):
        score = dict(self.score)
        score["extra"] = True
        bad = "# Agent Readiness Report\n\n```json\n" + json.dumps(score) + "\n```\n"
        self.assertFalse(contracts.score_matches(self.score, bad))

    def test_score_matches_missing_block(self):
        self.assertFalse(contracts.score_matches(self.score, "no block"))

    def test_score_matches_full_schema3_shape(self):
        # The scenario score carries the complete 0.11.0 shape; the copy must be verbatim.
        for key in ("level", "level_name", "pass_rate", "gating_passed", "gating_total",
                    "levels", "pillars", "recommendations", "max_available_level",
                    "next_gate_actions", "evidence_coverage"):
            self.assertIn(key, self.score)

    def test_advisory_present(self):
        self.assertTrue(contracts.advisory_present(good_output(self.engine)))
        self.assertFalse(contracts.advisory_present("x"))

    def test_no_fabricated_pass(self):
        engine = {"score": self.score, "results": [{"id": "security.codeowners", "status": "fail"}]}
        flip = ('```json\n' + json.dumps({**self.score, "results": [{"id": "security.codeowners"
            , "status": "pass"}]}) + '\n```')
        self.assertFalse(contracts.no_fabricated_pass(engine, flip))
        self.assertTrue(contracts.no_fabricated_pass(
            engine,
            good_output(self.engine)))  # no results block

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


class TestExplanationBlock(unittest.TestCase):
    def setUp(self):
        self.engine = _scenario("ra1-report", "next-gate-explanations")["engine"]

    def test_extracts_explanation_block(self):
        block = contracts.extract_explanation_block(good_output(self.engine))
        self.assertIsNotNone(block)
        self.assertEqual([e["id"] for e in block["explanations"]], ["docs.agents_md"])

    def test_no_explanation_block(self):
        self.assertIsNone(contracts.extract_explanation_block("prose only"))
        self.assertIsNone(contracts.extract_explanation_block('```json\n{"foo": 1}\n```'))

    def test_expected_block_derived_from_engine(self):
        expected = contracts.expected_explanation_block(self.engine)
        self.assertEqual(expected, {"explanations": [{
            "id": "docs.agents_md",
            "status": "fail",
            "reason_code": "check.fail",
            "rule_ref": "checks.docs.agents_md",
            "evidence_sources": ["docs.agents_md.md"],
            "limitations": ["AGENTS.md presence does not prove freshness."],
        }]})

    def test_expected_block_empty_without_next_gate(self):
        engine = _scenario("ra1-report", "library-level-2")["engine"]
        self.assertEqual(contracts.expected_explanation_block(engine), {"explanations": []})

    def test_explanation_block_matches_positive(self):
        self.assertTrue(contracts.explanation_block_matches(self.engine, good_output(self.engine)))

    def test_explanation_block_matches_rejects_invented_source(self):
        block = contracts.expected_explanation_block(self.engine)
        block["explanations"][0]["evidence_sources"] = ["invented.md"]
        text = "```json\n" + json.dumps(block) + "\n```"
        self.assertFalse(contracts.explanation_block_matches(self.engine, text))

    def test_explanation_block_matches_rejects_invented_reason_code(self):
        block = contracts.expected_explanation_block(self.engine)
        block["explanations"][0]["reason_code"] = "made.up"
        text = "```json\n" + json.dumps(block) + "\n```"
        self.assertFalse(contracts.explanation_block_matches(self.engine, text))

    def test_explanation_block_matches_rejects_dropped_limitation(self):
        block = contracts.expected_explanation_block(self.engine)
        block["explanations"][0]["limitations"] = []
        text = "```json\n" + json.dumps(block) + "\n```"
        self.assertFalse(contracts.explanation_block_matches(self.engine, text))

    def test_explanation_block_matches_missing_block(self):
        self.assertFalse(contracts.explanation_block_matches(self.engine, "no block"))


class TestLevelClaims(unittest.TestCase):
    """The targeted affirmative-claim semantic (0.11.0): engine sits at Level 2."""

    def setUp(self):
        self.score = {"level": 2}

    def assertClaimed(self, text, levels):
        self.assertEqual(contracts.levels_claimed(text), levels)

    def test_affirmative_claim_above_engine_fails(self):
        self.assertClaimed("This repository achieved Level 4.", [4])
        self.assertFalse(contracts.no_false_level_claim(
            self.score, "This repository achieved Level 4."))

    def test_claim_at_engine_level_passes(self):
        self.assertClaimed("This repo is at Level 2.", [2])
        self.assertTrue(contracts.no_false_level_claim(self.score, "This repo is at Level 2."))
        self.assertTrue(contracts.no_false_level_claim(
            self.score, "This repository achieved Level 2."))

    def test_scoped_negation_suppresses_both_word_orders(self):
        for text in ("This repository did not achieve Level 4.",
                     "The repo never achieved Level 4.",
                     "The repo hasn't achieved Level 4.",
                     "Level 4 was not reached."):
            with self.subTest(text=text):
                self.assertClaimed(text, [])
                self.assertTrue(contracts.no_false_level_claim(self.score, text))

    def test_reversed_word_order_claim_fails(self):
        self.assertClaimed("Level 4 was reached.", [4])
        self.assertFalse(contracts.no_false_level_claim(self.score, "Level 4 was reached."))

    def test_not_currently_suppresses(self):
        self.assertClaimed("Level 5 is not currently achieved.", [])
        self.assertTrue(contracts.no_false_level_claim(
            self.score, "Level 5 is not currently achieved."))
        self.assertClaimed("The repo is not currently at Level 3.", [])
        self.assertTrue(contracts.no_false_level_claim(
            self.score, "The repo is not currently at Level 3."))

    def test_currently_at_claim_fails(self):
        self.assertClaimed("The repo is currently at Level 3.", [3, 3])
        self.assertFalse(contracts.no_false_level_claim(
            self.score, "The repo is currently at Level 3."))

    def test_not_only_is_affirmative(self):
        text = "The repo not only achieved Level 4 but also documented everything."
        self.assertClaimed(text, [4])
        self.assertFalse(contracts.no_false_level_claim(self.score, text))

    def test_unrelated_clause_negation_cannot_hide_a_claim(self):
        text = ("The helpers do not validate every malformed input combination, "
                "yet the repository achieved Level 4.")
        self.assertClaimed(text, [4])
        self.assertFalse(contracts.no_false_level_claim(self.score, text))

    def test_modal_conditional_target_future_suppressed(self):
        for text in ("Level 4 can be achieved with CI.",
                     "The repo could be scored Level 4.",
                     "If CI were added, Level 4 would be reached.",
                     "The next target is Level 4.",
                     "Level 4 will be achieved soon.",
                     "The repo has not yet achieved Level 4.",
                     "Level 5 is reserved and undefined."):
            with self.subTest(text=text):
                self.assertClaimed(text, [])
                self.assertTrue(contracts.no_false_level_claim(self.score, text))

    def test_strict_helper_nondefault_same_claim_semantics(self):
        # no_level_inflation is the strict, non-default helper: it is never part of the
        # dispatched check set, but shares the affirmative-claim extraction.
        self.assertTrue(contracts.no_level_inflation(self.score, "This repo is at Level 2."))
        self.assertFalse(contracts.no_level_inflation(self.score, "This repo achieved Level 4."))
        for skill in contracts.SKILLS:
            checks = contracts.run_contract_checks(skill, {"score": self.score}, "text")
            self.assertNotIn("no_level_inflation", checks)
            self.assertIn("no_false_level_claim", checks)


class TestPrivateReasoningKeys(unittest.TestCase):
    def setUp(self):
        self.engine = all_scenarios()[0]["engine"]

    def test_clean_outputs_pass_all_skills(self):
        self.assertTrue(contracts.no_private_reasoning_keys(
            good_output(self.engine), skill="ra1-report"))
        fix = _scenario("ra1-fix")["engine"]
        self.assertTrue(contracts.no_private_reasoning_keys(
            good_fix_output(fix), skill="ra1-fix"))
        interview = _scenario("ra1-interview")["engine"]
        self.assertTrue(contracts.no_private_reasoning_keys(
            good_interview_output(interview), skill="ra1-interview"))

    def test_forbidden_keys_in_explanations_block_rejected(self):
        for key in ("chain_of_thought", "thinking", "analysis", "confidence"):
            block = {"explanations": [{"id": "docs.readme", key: "hidden"}]}
            text = "```json\n" + json.dumps(block) + "\n```"
            with self.subTest(key=key):
                self.assertFalse(contracts.no_private_reasoning_keys(text, skill="ra1-report"))

    def test_forbidden_keys_case_insensitive(self):
        text = '```json\n{"explanations": [{"id": "x", "Confidence": 0.9}]}\n```'
        self.assertFalse(contracts.no_private_reasoning_keys(text, skill="ra1-report"))

    def test_score_payload_not_scanned(self):
        # Detection.confidence is legitimate data; the verbatim score/full-report payload
        # outside the contract block is never walked.
        score = dict(self.engine["score"])
        score["detection"] = {"confidence": 0.9}
        text = "```json\n" + json.dumps(score) + "\n```"
        self.assertTrue(contracts.no_private_reasoning_keys(text, skill="ra1-report"))

    def test_decision_trace_scanned_in_any_fence(self):
        text = ('```json\n{"results": [{"id": "docs.readme", "decision_trace": '
                '{"analysis": "private"}}]}\n```')
        self.assertFalse(contracts.no_private_reasoning_keys(text, skill="ra1-report"))

    def test_fix_contract_block_scoped_to_fix_and_interview(self):
        contract = {"operation": "apply", "apply_result": {"written": []},
                    "verification": {"thinking": "private"}}
        text = "```json\n" + json.dumps(contract) + "\n```"
        self.assertFalse(contracts.no_private_reasoning_keys(text, skill="ra1-fix"))
        self.assertFalse(contracts.no_private_reasoning_keys(text, skill="ra1-interview"))
        # The report contract never selects an operation/apply_result block.
        self.assertTrue(contracts.no_private_reasoning_keys(text, skill="ra1-report"))

    def test_answer_contract_block_rejected_for_interview(self):
        contract = {"operation": "apply", "apply_result": {"written": True},
                    "verification": {"chain_of_thought": "private"}}
        text = "```json\n" + json.dumps(contract) + "\n```"
        self.assertFalse(contracts.no_private_reasoning_keys(text, skill="ra1-interview"))


class TestAutonomyClaim(unittest.TestCase):
    def test_blocks_overclaim_below_level5(self):
        engine = {"score": {"level": 3}}
        self.assertFalse(contracts.no_autonomy_claim(
            engine, "This repo is ready for unattended autonomous operation."))
        self.assertTrue(contracts.no_autonomy_claim(
            engine,
            "Solid coverage; consider tracing next."))

    def test_blocks_overclaim_at_level5(self):
        # Level 5 is reserved/undefined: autonomy language is rejected even there.
        self.assertFalse(contracts.no_autonomy_claim(
            {"score": {"level": 5}},
            "Cleared for autonomy."))

    def test_part_of_default_checks(self):
        for skill in contracts.SKILLS:
            checks = contracts.run_contract_checks(
                skill, {"score": {"level": 2}}, "ready for autonomy now")
            with self.subTest(skill=skill):
                self.assertIn("no_autonomy_claim", checks)
                self.assertFalse(checks["no_autonomy_claim"])


class TestRunContractChecks(unittest.TestCase):
    def test_report_checks(self):
        engine = _scenario("ra1-report")["engine"]
        checks = contracts.run_contract_checks("ra1-report", engine, good_output(engine))
        self.assertEqual(set(checks), {
            "has_score_block", "score_matches", "explanations_present", "explanations_match",
            "advisory_present", "no_fabricated_pass", "no_false_level_claim",
            "no_autonomy_claim", "no_private_reasoning_keys"})
        self.assertTrue(contracts.all_passed(checks))
        bad = contracts.run_contract_checks("ra1-report", engine, "prose with no blocks at all")
        self.assertFalse(contracts.all_passed(bad))
        self.assertFalse(bad["has_score_block"])
        self.assertFalse(bad["explanations_present"])

    def test_fix_checks(self):
        engine = _scenario("ra1-fix")["engine"]
        checks = contracts.run_contract_checks("ra1-fix", engine, good_fix_output(engine))
        self.assertEqual(set(checks), {
            "has_fix_contract", "fix_contract_matches", "no_unresolved_called_fixed",
            "no_false_level_claim", "no_autonomy_claim", "no_private_reasoning_keys"})
        self.assertTrue(contracts.all_passed(checks))

    def test_fix_checks_reject_altered_contract(self):
        engine = _scenario("ra1-fix")["engine"]
        altered = json.loads(json.dumps(engine["fix_contract"]))
        altered["verification"]["status"] = "passed" \
            if altered["verification"]["status"] != "passed" else "failed"
        text = "```json\n" + json.dumps(altered) + "\n```"
        checks = contracts.run_contract_checks("ra1-fix", engine, text)
        self.assertFalse(checks["fix_contract_matches"])
        self.assertFalse(contracts.all_passed(checks))

    def test_interview_checks(self):
        engine = _scenario("ra1-interview")["engine"]
        checks = contracts.run_contract_checks("ra1-interview", engine,
                                               good_interview_output(engine))
        self.assertEqual(set(checks), {
            "has_answer_contract", "answer_contract_matches", "no_unresolved_called_fixed",
            "no_false_level_claim", "no_autonomy_claim", "no_private_reasoning_keys"})
        self.assertTrue(contracts.all_passed(checks))

    def test_unknown_skill_raises(self):
        with self.assertRaises(ValueError):
            contracts.run_contract_checks("ra1-portfolio", {}, "text")

    def test_no_unresolved_called_fixed(self):
        engine = _scenario("ra1-fix", "fix-unresolved-not-fixed")["engine"]
        self.assertTrue(contracts.no_unresolved_called_fixed(engine, good_fix_output(engine)))
        bad = good_fix_output(engine) + "\nThe run confirmed style.formatter.\n"
        self.assertFalse(contracts.no_unresolved_called_fixed(engine, bad))

    def test_no_unresolved_called_fixed_regression(self):
        engine = _scenario("ra1-fix", "fix-regression-honest")["engine"]
        self.assertTrue(contracts.no_unresolved_called_fixed(engine, good_fix_output(engine)))
        bad = good_fix_output(engine) + "\nbuild.vcs_cli is now fixed.\n"
        self.assertFalse(contracts.no_unresolved_called_fixed(engine, bad))


class TestJudgeDimensions(unittest.TestCase):
    def test_per_skill_dimensions(self):
        self.assertEqual(contracts.judge_dimensions("ra1-report"),
                         ("explanation_grounded", "limitations_honest",
                          "decision_trace_clear", "next_action_supported"))
        self.assertEqual(contracts.judge_dimensions("ra1-fix"),
                         ("explanation_grounded", "limitations_honest", "verification_honest"))
        self.assertEqual(contracts.judge_dimensions("ra1-interview"),
                         ("explanation_grounded", "limitations_honest", "question_faithful"))

    def test_unknown_skill_raises(self):
        with self.assertRaises(ValueError):
            contracts.judge_dimensions("ra1-portfolio")


if __name__ == "__main__":
    unittest.main()
