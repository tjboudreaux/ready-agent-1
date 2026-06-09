import json
import unittest

from evals import judge, runner
from evals.scenarios import all_scenarios


def _engine_from_prompt(prompt):
    marker = "ENGINE REPORT (JSON):"
    body = prompt[prompt.index(marker) + len(marker):]
    body = body.rsplit("\n\nProduce", 1)[0]
    return json.loads(body)


def good_model(prompt):
    engine = _engine_from_prompt(prompt)
    s = engine["score"]
    return "```json\n" + json.dumps(s) + "\n```\n## Advisory\n" + ("grounded note. " * 30)


def no_block_model(prompt):
    return "Here is some prose with no machine-readable score block. " * 20


def inflating_model(prompt):
    engine = _engine_from_prompt(prompt)
    s = dict(engine["score"])
    s["level"] = s["level"] + 1
    return "```json\n" + json.dumps(s) + "\n```\n## Advisory\nThis repo is great."


def grounded_judge(prompt):
    return '{"grounded": true, "fabricated": false, "reason": "matches findings"}'


def fabricating_judge(prompt):
    return 'verdict: {"grounded": false, "fabricated": true, "reason": "inflated the level"}'


class TestParseJudge(unittest.TestCase):
    def test_valid(self):
        v = judge.parse_judge('{"grounded": true, "fabricated": false, "reason": "ok"}')
        self.assertEqual(v, {"grounded": True, "fabricated": False, "reason": "ok"})

    def test_embedded(self):
        self.assertIsNotNone(judge.parse_judge('blah {"grounded": true, "fabricated": false} tail'))

    def test_invalid(self):
        self.assertIsNone(judge.parse_judge("not json"))
        self.assertIsNone(judge.parse_judge('{"grounded": true}'))  # missing fabricated

    def test_verdict_ok(self):
        self.assertTrue(judge.verdict_ok({"grounded": True, "fabricated": False, "reason": ""}))
        self.assertFalse(judge.verdict_ok({"grounded": True, "fabricated": True, "reason": ""}))
        self.assertFalse(judge.verdict_ok(None))


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.scenario = all_scenarios()[0]

    def test_build_prompt(self):
        prompt = runner.build_prompt(self.scenario)
        self.assertIn("ENGINE REPORT (JSON):", prompt)
        self.assertIn("never claim a higher Level", prompt)

    def test_good_model_passes(self):
        r = runner.run_scenario(self.scenario, good_model)
        self.assertTrue(r["passed"])
        self.assertTrue(all(r["checks"].values()))

    def test_no_block_model_fails(self):
        r = runner.run_scenario(self.scenario, no_block_model)
        self.assertFalse(r["passed"])
        self.assertFalse(r["checks"]["has_score_block"])

    def test_inflating_model_fails_score_match(self):
        r = runner.run_scenario(self.scenario, inflating_model)
        self.assertFalse(r["passed"])
        self.assertFalse(r["checks"]["score_matches"])

    def test_judge_gates_pass(self):
        ok = runner.run_scenario(self.scenario, good_model, judge_model_fn=grounded_judge)
        self.assertTrue(ok["passed"])
        self.assertTrue(judge.verdict_ok(ok["judge"]))
        bad = runner.run_scenario(self.scenario, good_model, judge_model_fn=fabricating_judge)
        self.assertFalse(bad["passed"])  # contracts pass but judge flags fabrication

    def test_run_all_and_summarize(self):
        results = runner.run_all(all_scenarios(), good_model)
        summary = runner.summarize(results)
        self.assertEqual(summary["total"], len(all_scenarios()))
        self.assertEqual(summary["failed"], 0)


if __name__ == "__main__":
    unittest.main()
