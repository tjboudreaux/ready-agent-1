"""Scenario runner: three-skill prompt dispatch, deterministic gates, advisory judge."""
import json
import unittest

from evals import contracts, judge, runner
from evals.scenarios import all_scenarios


def _engine_from_prompt(prompt):
    marker = "ENGINE PAYLOAD (JSON):"
    body = prompt[prompt.index(marker) + len(marker):]
    body = body.rsplit("\n\nProduce", 1)[0]
    return json.loads(body)


def good_model(prompt):
    """A compliant skill: copies the canonical payload verbatim and adds grounded prose."""
    engine = _engine_from_prompt(prompt)
    if "fix_contract" in engine:
        return ("```json\n" + json.dumps(engine["fix_contract"]) + "\n```\n"
                "## Summary\n"
                + "Outcome recorded by the deterministic engine; details above. " * 4)
    if "answer_contract" in engine:
        return ("```json\n" + json.dumps(engine["answer_contract"]) + "\n```\n"
                "## Summary\n"
                + "Outcome recorded by the deterministic engine; details above. " * 4)
    return ("```json\n" + json.dumps(engine["score"]) + "\n```\n"
            "## Evidence explanations\n\n```json\n"
            + json.dumps(contracts.expected_explanation_block(engine)) + "\n```\n"
            "## T4 Advisory\n"
            + "Grounded, non-gating guidance based strictly on findings. " * 6)


def no_block_model(prompt):
    return "Here is some prose with no machine-readable score block. " * 20


def score_only_model(prompt):
    """Copies the score but omits the required evidence-explanations block."""
    engine = _engine_from_prompt(prompt)
    return ("```json\n" + json.dumps(engine["score"]) + "\n```\n"
            "## T4 Advisory\n" + "Grounded, non-gating guidance based strictly on findings. " * 6)


def inflating_model(prompt):
    engine = _engine_from_prompt(prompt)
    s = dict(engine["score"])
    s["level"] = s["level"] + 1
    return ("```json\n" + json.dumps(s) + "\n```\n"
            "## Evidence explanations\n\n```json\n"
            + json.dumps(contracts.expected_explanation_block(engine)) + "\n```\n"
            "## T4 Advisory\nThis repo is great. " + "Grounded note. " * 20)


def grounded_judge(prompt):
    return ('{"explanation_grounded": true, "decision_trace_clear": true, '
            '"limitations_honest": true, "next_action_supported": true, '
            '"verification_honest": true, "question_faithful": true, '
            '"autonomy_overclaim": false, "fabricated": false, '
            '"reason": "matches findings"}')


def fabricating_judge(prompt):
    return ('verdict: {"explanation_grounded": false, "limitations_honest": false, '
            '"autonomy_overclaim": true, "fabricated": true, '
            '"reason": "inflated the level"}')


def _scenario(skill, name=None):
    for scenario in all_scenarios():
        if scenario["skill"] == skill and (name is None or scenario["name"] == name):
            return scenario
    raise AssertionError(f"no scenario for {skill!r} {name!r}")


class TestParseJudge(unittest.TestCase):
    def test_valid(self):
        v = judge.parse_judge('{"explanation_grounded": true, "decision_trace_clear": true, '
                              '"limitations_honest": true, "next_action_supported": true, '
                              '"autonomy_overclaim": false, "reason": "ok"}')
        self.assertEqual(v, {"explanation_grounded": True, "decision_trace_clear": True,
                             "limitations_honest": True, "next_action_supported": True,
                             "autonomy_overclaim": False, "reason": "ok"})

    def test_embedded(self):
        self.assertIsNotNone(judge.parse_judge(
            'blah {"explanation_grounded": true, "fabricated": false} tail'))

    def test_invalid(self):
        self.assertIsNone(judge.parse_judge("not json"))
        self.assertIsNone(judge.parse_judge("{not valid json}"
                                            ))  # braces match but json.loads fails
        self.assertIsNone(judge.parse_judge("{}"))  # no recognized keys
        self.assertIsNone(judge.parse_judge('{"unrelated_key": true}'))

    def test_missing_verdict_fails_verdict_ok(self):
        self.assertFalse(judge.verdict_ok(None))
        self.assertFalse(judge.verdict_ok({}))

    def test_verdict_ok(self):
        self.assertTrue(judge.verdict_ok({"explanation_grounded": True,
                                          "limitations_honest": True, "reason": ""}))
        self.assertFalse(judge.verdict_ok({"explanation_grounded": False, "reason": ""}))
        self.assertFalse(judge.verdict_ok({"verification_honest": False, "reason": ""}))
        self.assertFalse(judge.verdict_ok({"question_faithful": False, "reason": ""}))
        self.assertFalse(judge.verdict_ok({"fabricated": True, "reason": ""}))

    def test_autonomy_overclaim_fails_verdict(self):
        v = judge.parse_judge('{"explanation_grounded": true, "fabricated": false, '
                              '"autonomy_overclaim": true}')
        self.assertTrue(v["autonomy_overclaim"])
        self.assertFalse(judge.verdict_ok(v))
        ok = judge.parse_judge('{"explanation_grounded": true, "fabricated": false, '
                               '"autonomy_overclaim": false}')
        self.assertTrue(judge.verdict_ok(ok))


class TestBuildPrompt(unittest.TestCase):
    def test_dispatches_report(self):
        prompt = runner.build_prompt(_scenario("ra1-report"))
        self.assertIn("You are the ra1-report skill.", prompt)
        self.assertIn("ENGINE PAYLOAD (JSON):", prompt)
        self.assertIn("never claim a higher Level", prompt)
        self.assertIn("## Evidence explanations", prompt)

    def test_dispatches_fix(self):
        prompt = runner.build_prompt(_scenario("ra1-fix"))
        self.assertIn("You are the ra1-fix skill.", prompt)
        self.assertIn("fix_contract", prompt)

    def test_dispatches_interview(self):
        prompt = runner.build_prompt(_scenario("ra1-interview"))
        self.assertIn("You are the ra1-interview skill.", prompt)
        self.assertIn("answer_contract", prompt)

    def test_prompt_carries_only_scenario_engine_payload(self):
        scenario = _scenario("ra1-report")
        engine = _engine_from_prompt(runner.build_prompt(scenario))
        self.assertEqual(engine, scenario["engine"])

    def test_scenario_skill_discriminator(self):
        self.assertEqual(contracts.scenario_skill({"engine": {}}), "ra1-report")  # legacy default
        self.assertEqual(contracts.scenario_skill({"skill": "ra1-fix", "engine": {}}), "ra1-fix")
        with self.assertRaises(ValueError):
            contracts.scenario_skill({"skill": "ra1-portfolio", "engine": {}})
        with self.assertRaises(ValueError):
            runner.build_prompt({"skill": "ra1-portfolio", "engine": {}})


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.scenario = all_scenarios()[0]

    def test_good_model_passes(self):
        r = runner.run_scenario(self.scenario, good_model)
        self.assertTrue(r["passed"])
        self.assertTrue(all(r["checks"].values()))
        self.assertEqual(r["skill"], "ra1-report")
        self.assertIsNone(r["judge"])

    def test_good_model_passes_every_skill(self):
        results = runner.run_all(all_scenarios(), good_model)
        self.assertEqual({r["skill"] for r in results}, set(contracts.SKILLS))
        for r in results:
            with self.subTest(scenario=r["name"]):
                self.assertTrue(r["passed"], msg=json.dumps(r["checks"]))

    def test_no_block_model_fails(self):
        r = runner.run_scenario(self.scenario, no_block_model)
        self.assertFalse(r["passed"])
        self.assertFalse(r["checks"]["has_score_block"])

    def test_missing_explanations_fail(self):
        r = runner.run_scenario(self.scenario, score_only_model)
        self.assertFalse(r["passed"])
        self.assertTrue(r["checks"]["score_matches"])
        self.assertFalse(r["checks"]["explanations_present"])
        self.assertFalse(r["checks"]["explanations_match"])

    def test_inflating_model_fails_score_match(self):
        r = runner.run_scenario(self.scenario, inflating_model)
        self.assertFalse(r["passed"])
        self.assertFalse(r["checks"]["score_matches"])

    def test_judge_is_advisory_only(self):
        # A fabricating judge is recorded as a diagnostic but never flips scenario pass/fail:
        # deterministic contract checks are the only blocking gates.
        ok = runner.run_scenario(self.scenario, good_model, judge_model_fn=grounded_judge)
        self.assertTrue(ok["passed"])
        self.assertTrue(judge.verdict_ok(ok["judge"]))
        flagged = runner.run_scenario(self.scenario, good_model, judge_model_fn=fabricating_judge)
        self.assertTrue(flagged["passed"])
        self.assertFalse(judge.verdict_ok(flagged["judge"]))

    def test_run_all_and_summarize(self):
        results = runner.run_all(all_scenarios(), good_model)
        summary = runner.summarize(results)
        self.assertEqual(summary["total"], len(all_scenarios()))
        self.assertEqual(summary["passed"], len(all_scenarios()))
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["results"], results)


if __name__ == "__main__":
    unittest.main()
