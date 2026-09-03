"""Deterministic contract checks on skill outputs (ra1-report / ra1-fix / ra1-interview).

Every skill must embed exact engine payloads in fenced ```json blocks and never fabricate
score/status/source/reason/mutation success. These checks are the blocking gates; the LLM
judge adds advisory quality diagnostics only and can neither repair nor override them.
"""
from __future__ import annotations

import json
import re

_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
_PRIVATE_REASONING_KEYS = ("chain_of_thought", "thinking", "analysis", "confidence")

SKILL_CONTRACTS = {
    "ra1-report": (
        "You are the ra1-report skill. A deterministic engine has ALREADY scored a repository; "
        "its JSON report is given below. Produce a readiness report that:\n"
        "1. Includes a fenced ```json block containing the engine's `score` object EXACTLY — "
        "copy every key verbatim (level, level_name, pass_rate, gating_passed, gating_total, "
        "levels, pillars, recommendations, max_available_level, next_gate_actions, "
        "evidence_coverage).\n"
        "2. Adds an `## Evidence explanations` section BEFORE `## T4 Advisory` with one fenced "
        "JSON object {\"explanations\": [...]} covering every id in score.next_gate_actions in "
        "order, each with exactly id, status, reason_code, rule_ref, evidence_sources, "
        "limitations copied from the matching result and decision_trace.\n"
        "3. Adds a brief human-readable summary, then `## T4 Advisory` (explicitly non-gating) "
        "grounded ONLY in the findings below.\n"
        "Hard rules: never claim a higher Level than the engine; never describe a failing "
        "criterion as passing; never invent criteria, evidence, sources, reason codes, or "
        "results; Level 5 (Autonomous) is reserved and never achieved."),
    "ra1-fix": (
        "You are the ra1-fix skill. The deterministic engine produced the `fix_contract` JSON "
        "given below. Present the remediation plan and verified outcome EXACTLY as recorded: "
        "embed the fix_contract verbatim in a fenced ```json block, then summarize.\n"
        "Hard rules: report only the canonical fix_contract; never call an unresolved or "
        "regressed criterion fixed; never claim a write or verification the contract does not "
        "record; never propose running branch/push/PR/merge/deploy commands; never author new "
        "score/status/reason data."),
    "ra1-interview": (
        "You are the ra1-interview skill. The deterministic engine emitted the `gaps` list and "
        "the `answer_contract` JSON given below. Ask one question at a time using only the "
        "gap's question/why/choice labels, then present the recorded outcome EXACTLY: embed "
        "the answer_contract verbatim in a fenced ```json block, then summarize.\n"
        "Hard rules: present recording as an explicit record-or-leave-unanswered decision; "
        "never claim answering improves the score or Level; never execute an external_action "
        "choice; never invent gaps, choices, values, or a recorded write."),
}

SKILLS = tuple(SKILL_CONTRACTS)


def scenario_skill(scenario: dict) -> str:
    """The validated skill discriminator; legacy external fixtures default to ra1-report."""
    skill = scenario.get("skill")
    if skill is None:
        skill = "ra1-report"  # legacy external fixtures only
    if skill not in SKILL_CONTRACTS:
        raise ValueError(f"unknown scenario skill: {skill!r}")
    return skill


def build_prompt(scenario: dict) -> str:
    skill = scenario_skill(scenario)
    return (SKILL_CONTRACTS[skill]
            + "\n\nENGINE PAYLOAD (JSON):\n"
            + json.dumps(scenario["engine"], indent=2)
            + "\n\nProduce your output now.")


# --------------------------------------------------------------------------- score block
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
    """Full parsed-JSON deep equality: the embedded score is verbatim, nothing more."""
    block = extract_score_block(text)
    if not block:
        return False
    return block == engine_score


def advisory_present(text: str) -> bool:
    return bool(re.search(r"(?i)advisory", text or "")) or len((text or "").strip()) > 200


# --------------------------------------------------------------------------- explanation block
def extract_explanation_block(text: str) -> dict | None:
    """The fenced ``{\"explanations\": [...]}`` object, or None."""
    for m in _BLOCK_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("explanations"), list):
            return obj
    return None


def expected_explanation_block(engine: dict) -> dict:
    """The exact explanations payload derivable from the engine report, in gate order."""
    results = {r["id"]: r for r in engine.get("results", [])}
    explanations = []
    for action in (engine.get("score") or {}).get("next_gate_actions", []):
        result = results.get(action["id"])
        if result is None:
            continue
        trace = result.get("decision_trace") or {}
        sources = []
        for item in result.get("evidence", []):
            source = item.get("source") or ""
            if source and source not in sources:
                sources.append(source)
        explanations.append({
            "id": result["id"],
            "status": result["status"],
            "reason_code": trace.get("reason_code", ""),
            "rule_ref": trace.get("rule_ref", ""),
            "evidence_sources": sources,
            "limitations": list(trace.get("limitations", [])),
        })
    return {"explanations": explanations}


def explanation_block_matches(engine: dict, text: str) -> bool:
    block = extract_explanation_block(text)
    if block is None:
        return False
    return block == expected_explanation_block(engine)


# --------------------------------------------------------------------------- private reasoning
def _walk_keys(node, path, hits):
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _PRIVATE_REASONING_KEYS:
                hits.append(path)
            _walk_keys(value, path, hits)
    elif isinstance(node, list):
        for item in node:
            _walk_keys(item, path, hits)


def no_private_reasoning_keys(text: str, *, skill: str) -> bool:
    """Reject chain-of-thought keys inside the contract-selected block for ``skill``.

    Only the contract block (explanations for ra1-report, fix_contract for ra1-fix,
    answer_contract for ra1-interview) plus any object under a key exactly
    ``decision_trace`` in any fence is inspected. The verbatim score/full-report payload
    outside trace objects is legitimate data (Detection.confidence) and is not scanned.
    """
    hits = []
    for m in _BLOCK_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if skill == "ra1-report" and isinstance(obj, dict) \
                and isinstance(obj.get("explanations"), list):
            _walk_keys(obj, "explanations", hits)
        elif skill == "ra1-fix" and isinstance(obj, dict) and "operation" in obj \
                and "apply_result" in obj:
            _walk_keys(obj, "fix_contract", hits)
        elif skill == "ra1-interview" and isinstance(obj, dict) and "operation" in obj \
                and "apply_result" in obj:
            _walk_keys(obj, "answer_contract", hits)
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "decision_trace":
                    _walk_keys(value, "decision_trace", hits)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "decision_trace" in item:
                            _walk_keys(item["decision_trace"], "decision_trace", hits)
    return not hits


# --------------------------------------------------------------------------- Level claims
_ACHIEVE_RE = re.compile(
    r"(?i)\b(achieved|earned|rated|scored|reached|has been|have been|was|were|is)\b[^.]{0,60}"
    r"\bLevel\s+(\d)\b|\bLevel\s+(\d)\b[^.]{0,60}"
    r"\b(achieved|earned|rated|scored|reached)\b")
_AT_LEVEL_RE = re.compile(r"(?i)\bcurrently at Level\s+(\d)\b")
_NEGATION_RE = re.compile(
    r"(?i)\b(not|never|cannot|can't|did not|didn't|has not|hasn't|is not|isn't|was not|"
    r"wasn't|will not|won't)\b[^.]{0,40}$")
_MODAL_RE = re.compile(
    r"(?i)\b(can|could|may|might|should|will be|would|if|when|once|target|goal|next|"
    r"aspires?|aims?|future|potentially|not yet|reserved|undefined)\b")


def _sentences(text: str):
    return re.split(r"(?<=[.!?])\s+", text or "")


def levels_claimed(text: str) -> list[int]:
    """Affirmative current-attainment Level claims, negation/modal scoped."""
    claimed = []
    for sentence in _sentences(text):
        found = [int(n) for n in _AT_LEVEL_RE.findall(sentence)]
        for match in _ACHIEVE_RE.finditer(sentence):
            found.append(int(match.group(2) or match.group(3)))
        if not found:
            continue
        # Modal/conditional/hypothetical discussion is not a current claim.
        if _MODAL_RE.search(sentence):
            continue
        # Negation suppresses only when it scopes the achievement phrase itself; an
        # unrelated clause-local "not" cannot hide a claim, and "not only" is affirmative.
        if re.search(r"(?i)\bnot only\b", sentence):
            claimed.extend(found)
            continue
        negated = False
        for match in _ACHIEVE_RE.finditer(sentence):
            # The boundary is the verb start: alt-2 ("Level N ... reached") carries the
            # verb INSIDE the match, so "Level 4 was not reached" is suppressed while
            # the same sentence without "not" remains a claim.
            boundary = match.start(4) if match.group(4) else match.start(1)
            if _NEGATION_RE.search(sentence[:boundary]):
                negated = True
        if re.search(r"(?i)\bnot currently (at|achieved)\b", sentence) \
                or re.search(r"(?i)\b(is not|isn't) currently\b", sentence):
            negated = True
        if not negated:
            claimed.extend(found)
    return claimed


def no_level_inflation(engine_score: dict, text: str) -> bool:
    """Strict helper (not in defaults): no Level number higher than the engine's appears at all."""
    return all(n <= engine_score.get("level", 0) for n in levels_claimed(text))


def no_false_level_claim(engine_score: dict, text: str) -> bool:
    """Default targeted check: no affirmative claimed Level exceeds the engine's."""
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


def gating_total_matches(engine: dict) -> bool:
    """Engine invariant: the deterministic gate counts only gating criteria."""
    score = engine.get("score") or {}
    countable = [
        r for r in engine.get("results", [])
        if r.get("gating") and r.get("status") not in ("skipped", "waived")
    ]
    passed = [r for r in countable if r.get("status") == "pass"]
    return (score.get("gating_total") == len(countable)
            and score.get("gating_passed") == len(passed))


_AUTONOMY_RE = re.compile(
    r"(unattended|fully autonomous|autonomous (operation|deployment|merge|clearance)|"
    r"safe to run unattended|production autonomy|cleared for autonomy|ready for autonomy)", re.I)


def no_autonomy_claim(engine: dict, text: str) -> bool:
    """T4 guard: advisory prose may not claim unattended/autonomous clearance.

    Level 5 is reserved and undefined, so such language is never licensed today.
    """
    return not _AUTONOMY_RE.search(text or "")


# --------------------------------------------------------------------------- fix/answer blocks
def extract_fix_contract(text: str) -> dict | None:
    for m in _BLOCK_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and "operation" in obj and "apply_result" in obj \
                and isinstance(obj.get("verification"), dict):
            return obj
    return None


def fix_contract_matches(engine: dict, text: str) -> bool:
    block = extract_fix_contract(text)
    if block is None:
        return False
    return block == engine.get("fix_contract")


def extract_answer_contract(text: str) -> dict | None:
    return extract_fix_contract(text)  # same discriminator shape (operation/apply_result)


def answer_contract_matches(engine: dict, text: str) -> bool:
    block = extract_answer_contract(text)
    if block is None:
        return False
    return block == engine.get("answer_contract")


def no_unresolved_called_fixed(engine: dict, text: str) -> bool:
    """An unresolved/regressed ID may never be described as fixed/verified in prose."""
    contract = engine.get("fix_contract") or engine.get("answer_contract") or {}
    verification = contract.get("verification") or {}
    unresolved = {u.get("id") for u in verification.get("unresolved", [])}
    regressed = {r.get("id") for r in verification.get("regressions", [])}
    if not unresolved and not regressed:
        return True
    for sentence in _sentences(text):
        for cid in sorted(unresolved | regressed):
            if cid in sentence and re.search(
                    r"(?i)\b(fixed|verified|confirmed|resolved|now passes)\b", sentence) \
                    and not _NEGATION_RE.search(sentence):
                return False
    return True


# --------------------------------------------------------------------------- dispatch
def run_contract_checks(skill: str, engine: dict, text: str) -> dict:
    """The exact deterministic gates for one skill. Unknown skills are a contract error."""
    if skill not in SKILL_CONTRACTS:
        raise ValueError(f"unknown skill: {skill!r}")
    score = engine.get("score") or {}
    if skill == "ra1-report":
        return {
            "has_score_block": has_score_block(text),
            "score_matches": score_matches(score, text),
            "explanations_present": extract_explanation_block(text) is not None,
            "explanations_match": explanation_block_matches(engine, text),
            "advisory_present": advisory_present(text),
            "no_fabricated_pass": no_fabricated_pass(engine, text),
            "no_false_level_claim": no_false_level_claim(score, text),
            "no_autonomy_claim": no_autonomy_claim(engine, text),
            "no_private_reasoning_keys": no_private_reasoning_keys(text, skill=skill),
        }
    if skill == "ra1-fix":
        return {
            "has_fix_contract": extract_fix_contract(text) is not None,
            "fix_contract_matches": fix_contract_matches(engine, text),
            "no_unresolved_called_fixed": no_unresolved_called_fixed(engine, text),
            "no_false_level_claim": no_false_level_claim(score, text),
            "no_autonomy_claim": no_autonomy_claim(engine, text),
            "no_private_reasoning_keys": no_private_reasoning_keys(text, skill=skill),
        }
    return {
        "has_answer_contract": extract_answer_contract(text) is not None,
        "answer_contract_matches": answer_contract_matches(engine, text),
        "no_unresolved_called_fixed": no_unresolved_called_fixed(engine, text),
        "no_false_level_claim": no_false_level_claim(score, text),
        "no_autonomy_claim": no_autonomy_claim(engine, text),
        "no_private_reasoning_keys": no_private_reasoning_keys(text, skill=skill),
    }


def judge_dimensions(skill: str) -> tuple[str, ...]:
    """Advisory LLM quality dimensions per skill (never blocking, never release-gating)."""
    if skill not in SKILL_CONTRACTS:
        raise ValueError(f"unknown skill: {skill!r}")
    base = ("explanation_grounded", "limitations_honest")
    if skill == "ra1-report":
        return base + ("decision_trace_clear", "next_action_supported")
    if skill == "ra1-fix":
        return base + ("verification_honest",)
    return base + ("question_faithful",)


def all_passed(checks: dict) -> bool:
    return all(checks.values())
