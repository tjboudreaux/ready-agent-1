"""Scenario runner: drive a model_fn with the skill contract, then apply contracts + judge.

Every scenario is dispatched by its validated ``skill`` discriminator; there is no generic
fallback after validation. Unit tests inject a mock model_fn. The live run
(`python3 evals/runner.py [gemini|codex]`) shells to a real model.
``model_fn(prompt) -> str``.
"""
from __future__ import annotations

import json
import sys

from . import contracts
from . import judge as judgemod
from .scenarios import all_scenarios


def build_prompt(scenario: dict) -> str:
    return contracts.build_prompt(scenario)


def run_scenario(scenario: dict, model_fn, judge_model_fn=None) -> dict:
    skill = contracts.scenario_skill(scenario)
    engine = scenario["engine"]
    output = model_fn(build_prompt(scenario))
    checks = contracts.run_contract_checks(skill, engine, output)
    verdict = judgemod.judge(engine, output, judge_model_fn, skill=skill) \
        if judge_model_fn else None
    # Deterministic contract checks are the blocking gates; judge output is recorded as an
    # advisory diagnostic and never changes this scenario's pass/fail.
    passed = contracts.all_passed(checks)
    return {"name": scenario["name"], "skill": skill,
            "kind": scenario.get("kind", "positive"),
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
