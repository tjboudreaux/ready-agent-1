"""Opt-in loop-readiness advisory checks.

These checks are deliberately T0 structural checks: they validate that repo-owned
contracts and documentation artifacts exist and are filled, not that loop tooling
enforces or proves any runtime behavior.
"""
from __future__ import annotations

import re

from ._helpers import ev, failed, passed

LOOP_SKILL_MIN = 3
_PLACEHOLDER_RE = re.compile(
    r"(?i)(TODO|FIXME|TBD|<!--|\[(?:TODO|FIXME|TBD|project|owner|path[^\]]*|command[^\]]*|describe[^\]]*|replace[^\]]*|bracketed[^\]]*)\])"
)


def _first(ctx, patterns) -> str | None:
    hits = ctx.static.glob(patterns)
    return sorted(hits)[0] if hits else None


def _filled(ctx, path, label, min_chars=40) -> tuple[bool, str]:
    text = ctx.static.read(path)
    if text is None:
        return False, f"{label} unreadable: {path}."
    stripped = text.strip()
    if not stripped:
        return False, f"{label} is empty: {path}."
    if len(stripped) < min_chars:
        return False, f"{label} too thin ({len(stripped)} chars): {path}."
    if _PLACEHOLDER_RE.search(text):
        return False, f"{label} contains placeholder text: {path}."
    return True, f"{label} present and filled: {path}."


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
    return passed(".omp/rules/README.md documents loop rules or denylist.", [ev("loop rules index", source=path, tier="T0")])


def denylist(ctx):
    path = _first(ctx, [".omp/rules/denylist.md"])
    if not path:
        return failed("Missing .omp/rules/denylist.md denylist.")
    ok, rationale = _filled(ctx, path, ".omp/rules/denylist.md")
    if not ok:
        return failed(rationale)
    text = ctx.static.read(path) or ""
    has_bullet = any(line.lstrip().startswith(("- ", "* ", "+ ")) for line in text.splitlines())
    if not (has_bullet or _contains_any(text, ["deny", "block", "never"])):
        return failed(".omp/rules/denylist.md must include a deny/block/never policy term or bullet.")
    return passed(".omp/rules/denylist.md contains a starter blocked-action policy.", [ev("loop denylist", source=path, tier="T0")])


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
    return passed("signals/README.md documents the minimal signal schema.", [ev("signal schema", source=path, tier="T0")])


def pr_artifact_template(ctx):
    primary = _first(ctx, [".omp/commands/pr-artifact-template.md"])
    if primary:
        ok, rationale = _filled(ctx, primary, ".omp/commands/pr-artifact-template.md")
        if not ok:
            return failed(rationale)
        return passed(".omp/commands/pr-artifact-template.md is filled.", [ev("PR artifact template", source=primary, tier="T0")])

    fallback = _first(ctx, [".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md"])
    if not fallback:
        return failed("Missing PR artifact evidence template: expected .omp/commands/pr-artifact-template.md or artifact-specific GitHub PR template.")
    ok, rationale = _filled(ctx, fallback, "GitHub PR template")
    if not ok:
        return failed(rationale)
    text = ctx.static.read(fallback) or ""
    evidence_terms = ["artifact", "evidence"]
    artifact_terms = ["screenshot", "video", "log", "ci", "test output", ".agents/artifacts", "loop-runs"]
    if not (_contains_any(text, evidence_terms) and _contains_artifact_language(text, artifact_terms)):
        return failed("GitHub PR template lacks artifact/evidence-specific language.")
    return passed("GitHub PR template includes artifact evidence language.", [ev("artifact-specific PR template", source=fallback, tier="T0")])


def skills_present(ctx):
    paths = ctx.static.glob([".omp/skills/*/SKILL.md"])
    filled = []
    for path in paths:
        ok, _rationale = _filled(ctx, path, path)
        if ok:
            filled.append(path)
    if len(filled) < LOOP_SKILL_MIN:
        return failed(f"Only {len(filled)} OMP loop skill artifact(s) found (<3).")
    return passed(f"Found {len(filled)} filled OMP loop skill artifacts.", [ev("OMP loop skill", source=path, tier="T0") for path in filled])


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
        return failed(f"Missing or unfilled loop prompt contract(s): {', '.join(missing_or_unfilled)}.")
    return passed("Loop goal and loop prompt contracts are filled.", evidence)


def architecture_doc(ctx):
    return _pass_filled(ctx, ["ARCHITECTURE.md", "docs/ARCHITECTURE.md", "docs/architecture.md"], "architecture doc")


def domain_docs(ctx):
    paths = ctx.static.glob(["domains/*/README.md"])
    filled = []
    for path in paths:
        ok, _rationale = _filled(ctx, path, path)
        if ok:
            filled.append(path)
    if not filled:
        return failed("No filled domains/*/README.md domain docs found.")
    return passed(f"Found {len(filled)} filled domain README doc(s).", [ev("domain README", source=path, tier="T0") for path in filled])
