"""Deterministic contract checks on a sheldon-report agent's output.

The agent is required to embed the engine's `score` object verbatim in a fenced ```json block.
These checks guard the machine-readable score; prose fabrication is handled by the LLM judge.
"""
from __future__ import annotations

import json
import re

_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_LEVEL_RE = re.compile(r"[Ll]evel\s+(\d)")


def extract_score_block(text: str):
    """Return the first fenced json object that looks like a score block, or None."""
    for m in _BLOCK_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and "level" in obj and "gating_total" in obj:
            return obj
    return None


def has_score_block(text: str) -> bool:
    return extract_score_block(text) is not None


def score_matches(engine_score: dict, text: str) -> bool:
    block = extract_score_block(text)
    if not block:
        return False
    return (
        block.get("level") == engine_score.get("level")
        and block.get("gating_passed") == engine_score.get("gating_passed")
        and block.get("gating_total") == engine_score.get("gating_total")
    )


def advisory_present(text: str) -> bool:
    return bool(re.search(r"(?i)advisory", text or "")) or len((text or "").strip()) > 200


def levels_claimed(text: str):
    return [int(n) for n in _LEVEL_RE.findall(text or "")]


def no_level_inflation(engine_score: dict, text: str) -> bool:
    """Strict helper (not in defaults): no Level number higher than the engine's appears at all."""
    return all(n <= engine_score.get("level", 0) for n in levels_claimed(text))


def no_fabricated_pass(engine: dict, text: str) -> bool:
    """If the output embeds a results list, no engine-failing criterion may be marked passing."""
    block = extract_score_block(text)
    if not block or "results" not in block:
        return True  # nothing to contradict
    failing = {r["id"] for r in engine.get("results", []) if r.get("status") == "fail"}
    for r in block.get("results", []):
        if r.get("id") in failing and r.get("status") == "pass":
            return False
    return True


def run_contract_checks(engine: dict, text: str) -> dict:
    score = engine.get("score") or {}
    return {
        "has_score_block": has_score_block(text),
        "score_matches": score_matches(score, text),
        "advisory_present": advisory_present(text),
        "no_fabricated_pass": no_fabricated_pass(engine, text),
    }


def all_passed(checks: dict) -> bool:
    return all(checks.values())
