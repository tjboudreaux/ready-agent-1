"""Opt-in loop-readiness advisory checks.

These checks are deliberately T0 structural checks: they validate that repo-owned
contracts and documentation artifacts exist and are filled, not that loop tooling
enforces or proves any runtime behavior.
"""
from __future__ import annotations

import re

from ._helpers import _filled, ev, failed, passed

LOOP_SKILL_MIN = 3

def _first(ctx, patterns) -> str | None:
    hits = ctx.static.glob(patterns)
    return sorted(hits)[0] if hits else None

def _pass_filled(ctx, patterns, label, min_chars=40):
    path = _first(ctx, patterns)
    if not path:
        expected = patterns if isinstance(patterns, list) else [patterns]
        return failed(f"Missing {label}: expected {', '.join(expected)}.")
    ok, rationale = _filled(ctx, path, label, min_chars=min_chars)
    if not ok:
        return failed(rationale)
    return passed(rationale, [ev(label, source=path, tier="T0")])

def _contains_terms(text, terms):
    lower = text.lower()
    return all(term.lower() in lower for term in terms)

def _contains_any(text, terms):
    lower = text.lower()
    return any(term.lower() in lower for term in terms)

def _contains_artifact_language(text, terms):
    lower = text.lower()
    for term in terms:
        if term == "ci":
            if re.search(r"(?<![A-Za-z0-9_])ci(?![A-Za-z0-9_])", text, re.IGNORECASE):
                return True
        elif term == "log":
            if re.search(r"(?<![A-Za-z0-9_])logs?(?![A-Za-z0-9_])", text, re.IGNORECASE):
                return True
        elif term.lower() in lower:
            return True
    return False

def loop_runs_dir(ctx):
    return _pass_filled(ctx, ["loop-runs/README.md", "loop-runs/readme.md"], "loop-runs/README.md")

def rules_index(ctx):
    path = _first(ctx, [".omp/rules/README.md"])
    if not path:
        return failed("Missing .omp/rules/README.md rules index.")
    ok, rationale = _filled(ctx, path, ".omp/rules/README.md")
    if not ok:
        return failed(rationale)
    text = ctx.static.read(path) or ""
    if not _contains_any(text, ["rules", "denylist"]):
        return failed(".omp/rules/README.md must mention rules or denylist.")
    return passed(".omp/rules/README.md documents loop rules or denylist.",
                  [ev("loop rules index", source=path, tier="T0")])

_DENYLIST_FAMILIES = (
    ("secrets/credentials exfiltration", re.compile(
        r"(?i)(secret|credential|environment (file|variable)|\.env|token|private key|"
        r"password|api key)\b")),
    ("destructive operations", re.compile(
        r"(?i)(destructi|delet|destroy|drop|truncat|\brm\b|remov|wipe|format)")),
    ("push/merge/deploy/release/publish", re.compile(
        r"(?i)(push|merge|deploy|release|publish)\b")),
    ("control bypass", re.compile(
        r"(?i)(ci\b|tests?\b|security scanning|branch protection|audit logging|"
        r"kill switch|disable|bypass|skip)\b")),
)
_NORMATIVE_RE = re.compile(
    r"(?i)\b(deny|denied|block|blocked|never|do not|don't|must not|must never|"
    r"require human (approval|confirmation)|requires? human (approval|confirmation)|"
    r"only with (human )?(approval|confirmation))\b")
_PERMISSIVE_RE = re.compile(
    r"(?i)\b(allow|allowed|permit|permitted|may|can|free to|encouraged)\b")


def _denylist_statements(text: str) -> list[str]:
    """List items and standalone policy sentences, stripped of fences/comments/headings."""
    from .taskdisc import _normative_statements
    return _normative_statements(text)


def _statement_governs(statement: str, family_needle) -> bool:
    """A normative control phrase must govern the family action in the same statement,
    with no permissive/reversed polarity."""
    if not family_needle.search(statement):
        return False
    if _PERMISSIVE_RE.search(statement) and not _NORMATIVE_RE.search(statement):
        return False
    # Reversed polarity: "never block deploys" / "do not allow X" inverts the control.
    if re.search(r"(?i)\bnever (block|deny|prevent)\b", statement):
        return False
    if re.search(r"(?i)\bdo not (allow|permit)\b", statement):
        return False
    return bool(_NORMATIVE_RE.search(statement))


def denylist(ctx):
    path = _first(ctx, [".omp/rules/denylist.md"])
    if not path:
        return failed("Missing .omp/rules/denylist.md denylist.",
                      reason_code="loop.denylist.missing")
    ok, rationale = _filled(ctx, path, ".omp/rules/denylist.md")
    if not ok:
        return failed(rationale, reason_code="loop.denylist.missing")
    text = ctx.static.read(path) or ""
    statements = _denylist_statements(text)
    matched = set()
    for statement in statements:
        for index, (_name, needle) in enumerate(_DENYLIST_FAMILIES):
            if index in matched:
                continue
            if _statement_governs(statement, needle):
                matched.add(index)
    missing = [name for i, (name, _n) in enumerate(_DENYLIST_FAMILIES)
               if i not in matched]
    if missing:
        return failed(
            "Denylist is missing normative statement(s) for: " + ", ".join(missing) + ".",
            [ev("loop denylist", source=path, tier="T0")],
            reason_code="loop.denylist.families_incomplete",
            limitations=["Documented deny statements are structural policy evidence, not "
                         "runtime enforcement."])
    return passed(
        "Denylist covers secrets exfiltration, destructive operations, "
        "push/merge/deploy/release/publish, and control bypass.",
        [ev("loop denylist", source=path, tier="T0")],
        reason_code="loop.denylist.complete",
        limitations=["Documented deny statements are structural policy evidence, not "
                     "runtime enforcement."])

def signal_schema(ctx):
    path = _first(ctx, ["signals/README.md"])
    if not path:
        return failed("Missing signals/README.md signal schema.")
    ok, rationale = _filled(ctx, path, "signals/README.md")
    if not ok:
        return failed(rationale)
    text = ctx.static.read(path) or ""
    required = ["schema_version", "signal", "source", "timestamp", "evidence"]
    if "```" not in text:
        return failed("signals/README.md must include a fenced code block.")
    if not _contains_terms(text, required):
        missing = [term for term in required if term.lower() not in text.lower()]
        return failed(f"signals/README.md missing schema term(s): {', '.join(missing)}.")
    return passed("signals/README.md documents the minimal signal schema.",
                  [ev("signal schema", source=path, tier="T0")])

def pr_artifact_template(ctx):
    primary = _first(ctx, [".omp/commands/pr-artifact-template.md"])
    if primary:
        ok, rationale = _filled(ctx, primary, ".omp/commands/pr-artifact-template.md")
        if not ok:
            return failed(rationale)
        return passed(".omp/commands/pr-artifact-template.md is filled.",
                       [ev("PR artifact template", source=primary, tier="T0")])

    fallback = _first(ctx, [".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md"])
    if not fallback:
        return failed("Missing PR artifact evidence template: expected "
                       ".omp/commands/pr-artifact-template.md or artifact-specific "
                       "GitHub PR template.")
    ok, rationale = _filled(ctx, fallback, "GitHub PR template")
    if not ok:
        return failed(rationale)
    text = ctx.static.read(fallback) or ""
    evidence_terms = ["artifact", "evidence"]
    artifact_terms = ["screenshot", "video", "log", "ci", "test output",
                      ".agents/artifacts", "loop-runs"]
    if not (_contains_any(text, evidence_terms)
            and _contains_artifact_language(text, artifact_terms)):
        return failed("GitHub PR template lacks artifact/evidence-specific language.")
    return passed("GitHub PR template includes artifact evidence language.",
                  [ev("artifact-specific PR template", source=fallback, tier="T0")])

def skills_present(ctx):
    paths = ctx.static.glob([".omp/skills/*/SKILL.md"])
    filled = []
    for path in paths:
        ok, _rationale = _filled(ctx, path, path)
        if ok:
            filled.append(path)
    if len(filled) < LOOP_SKILL_MIN:
        return failed(f"Only {len(filled)} OMP loop skill artifact(s) found (<3).")
    return passed(f"Found {len(filled)} filled OMP loop skill artifacts.",
                  [ev("OMP loop skill", source=path, tier="T0") for path in filled])

def prompt_contracts(ctx):
    required = [".omp/commands/goal.md", ".omp/commands/loop.md"]
    missing_or_unfilled = []
    evidence = []
    for path in required:
        if not ctx.static.glob([path]):
            missing_or_unfilled.append(path)
            continue
        ok, _rationale = _filled(ctx, path, path)
        if not ok:
            missing_or_unfilled.append(path)
        else:
            evidence.append(ev("loop prompt contract", source=path, tier="T0"))
    if missing_or_unfilled:
        return failed(
            f"Missing or unfilled loop prompt contract(s): {', '.join(missing_or_unfilled)}."
        )
    return passed("Loop goal and loop prompt contracts are filled.", evidence)

def architecture_doc(ctx):
    return _pass_filled(
        ctx,
        ["ARCHITECTURE.md", "docs/ARCHITECTURE.md", "docs/architecture.md"],
        "architecture doc",
    )

def domain_docs(ctx):
    paths = ctx.static.glob(["domains/*/README.md"])
    filled = []
    for path in paths:
        ok, _rationale = _filled(ctx, path, path)
        if ok:
            filled.append(path)
    if not filled:
        return failed("No filled domains/*/README.md domain docs found.")
    return passed(f"Found {len(filled)} filled domain README doc(s).",
                  [ev("domain README", source=path, tier="T0") for path in filled])
