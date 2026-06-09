"""Scenario runner: drive a model_fn with the agent contract, then apply contracts + judge.

Unit tests inject a mock model_fn. The live run (`python3 evals/runner.py [gemini|codex]`)
shells to a real model. ``model_fn(prompt) -> str``.
"""
from __future__ import annotations

import json
import sys

from . import contracts, judge as judgemod
from .scenarios import all_scenarios

SKILL_CONTRACT = """You are the readiness-report skill. A deterministic engine has ALREADY scored a
repository; its JSON report is given below. Produce a readiness report that:
1. Includes a fenced ```json block containing the engine's `score` object EXACTLY — copy level,
   level_name, pass_rate, gating_passed, gating_total, and pillars without changing any number.
2. Adds a brief human-readable summary.
3. Adds an "## Advisory" section (explicitly non-gating) grounded ONLY in the findings below.

Hard rules: never claim a higher Level than the engine; never describe a failing criterion as
passing; never invent criteria, evidence, or results; and do NOT assert that any specific criterion
is "gating" or "non-gating" — only the engine's own data decides that, so do not add such claims."""


def build_prompt(scenario: dict) -> str:
    return (SKILL_CONTRACT
            + "\n\nENGINE REPORT (JSON):\n"
            + json.dumps(scenario["engine"], indent=2)
            + "\n\nProduce the readiness report now.")


def run_scenario(scenario: dict, model_fn, judge_model_fn=None) -> dict:
    engine = scenario["engine"]
    output = model_fn(build_prompt(scenario))
    checks = contracts.run_contract_checks(engine, output)
    verdict = judgemod.judge(engine, output, judge_model_fn) if judge_model_fn else None
    passed = contracts.all_passed(checks) and (verdict is None or judgemod.verdict_ok(verdict))
    return {"name": scenario["name"], "kind": scenario.get("kind", "positive"),
            "passed": passed, "checks": checks, "judge": verdict}


def run_all(scenarios, model_fn, judge_model_fn=None) -> list:
    return [run_scenario(s, model_fn, judge_model_fn) for s in scenarios]


def summarize(results: list) -> dict:
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed,
            "results": results}


# --------------------------------------------------------------- real model functions (live)
def gemini_model(prompt: str) -> str:  # pragma: no cover - subprocess boundary
    import subprocess
    p = subprocess.run(["gemini", "--approval-mode", "plan", "-p", prompt],
                       capture_output=True, text=True, timeout=240)
    return p.stdout


def codex_model(prompt: str) -> str:  # pragma: no cover - subprocess boundary
    import subprocess
    p = subprocess.run(["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-"],
                       input=prompt, capture_output=True, text=True, timeout=360)
    return p.stdout


def main(argv=None):  # pragma: no cover - live entrypoint
    argv = argv if argv is not None else sys.argv[1:]
    model_name = argv[0] if argv else "gemini"
    model_fn = {"gemini": gemini_model, "codex": codex_model}[model_name]
    results = run_all(all_scenarios(), model_fn, judge_model_fn=model_fn)
    summary = summarize(results)
    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
