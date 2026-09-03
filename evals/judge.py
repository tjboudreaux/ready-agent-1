"""LLM-as-judge: advisory quality diagnostics on skill output prose.

Judge dimensions are **advisory only**: they are recorded as quality diagnostics and never
change the eval process exit, CI status, score, or release decision. The deterministic
contract/fixture checks are the blocking gates; a judge can neither repair nor override
them. ``model_fn(prompt) -> str`` is injectable.
"""
from __future__ import annotations

import json
import re

_DIMENSIONS = ("explanation_grounded", "decision_trace_clear", "limitations_honest",
               "next_action_supported", "verification_honest", "question_faithful")

JUDGE_PROMPT = (
    "You are auditing an AI agent's {skill} output for fabrication and unsupported prose.\n"
    "\n"
    "The deterministic engine produced this payload (authoritative):\n"
    "{engine}\n"
    "\n"
    "The agent produced this output:\n"
    "{output}\n"
    "\n"
    "Judge STRICTLY and only against the engine payload. Answer each dimension:\n"
    '- "explanation_grounded": true only if every factual claim in the agent\'s prose is '
    "supported by the engine payload or clearly labelled opinion.\n"
    '- "decision_trace_clear": true if the agent\'s rule → observation → evaluation → '
    "conclusion narrative matches the recorded traces without inventing steps.\n"
    '- "limitations_honest": true if the agent states the recorded limitations and '
    "scope boundaries rather than implying enforcement the engine did not prove.\n"
    '- "next_action_supported": true if every suggested next action follows from a '
    "recorded failing/unknown criterion or plan item.\n"
    '- "verification_honest": true if the agent presents verified/unresolved/regressed '
    "remediation states exactly as recorded.\n"
    '- "question_faithful": true if the agent\'s questions use only the recorded gap '
    "question, why, and choice labels.\n"
    '- "autonomy_overclaim": true if the agent claims the repo is cleared for '
    "unattended/autonomous operation (Level 5 is reserved).\n"
    "\n"
    'Respond with ONLY a JSON object having one boolean per dimension you were given, '
    'plus "reason": "<one sentence>".'
)


def build_judge_prompt(skill: str, engine: dict, output: str) -> str:
    from . import contracts
    dims = ", ".join(f'"{d}"' for d in contracts.judge_dimensions(skill))
    return (JUDGE_PROMPT.replace("{skill}", skill)
            .replace("{engine}", json.dumps(engine)[:6000])
            .replace("{output}", (output or "")[:6000])
            + f"\nDimensions to score: [{dims}]")


def parse_judge(text: str):
    """Extract the verdict JSON from a model response; normalized dict or None."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    out = {key: bool(value) for key, value in obj.items()
           if key in _DIMENSIONS + ("autonomy_overclaim", "grounded", "fabricated")}
    if "reason" in obj:
        out["reason"] = str(obj["reason"])
    return out or None


def judge(engine: dict, output: str, model_fn, *, skill: str = "ra1-report") -> dict:
    verdict = parse_judge(model_fn(build_judge_prompt(skill, engine, output)))
    return verdict


def verdict_ok(verdict) -> bool:
    """A verdict passes when no dimension flags a problem. Missing verdict = inconclusive =
    fail. Advisory: this never gates a release; it records a quality diagnostic."""
    if not verdict:
        return False
    if verdict.get("fabricated") or verdict.get("autonomy_overclaim"):
        return False
    for dim in _DIMENSIONS:
        if dim in verdict and not verdict[dim]:
            return False
    return True
