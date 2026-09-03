"""Gap derivation: what the scan could not determine, and what it costs.

The score answers "where is this repository weak". This module answers the question a
blocked reader asks next: **"which of these results are weak only because the scanner was
missing an input a human could supply in one sentence?"**

Three kinds, and the boundary between them is the honest part:

- ``detection`` — the scanner classified the repository (or one app in it) with too little
  evidence to trust, or found competing evidence and silently picked a winner. A pin makes
  the affected criteria evaluable; the engine then judges them normally.
- ``config`` — a check needs a value that cannot be inferred (a verify command a human
  designates, a CI budget a team decides). Supplying it lets the check run.
- ``capability`` — a data source the scan could not reach (an unauthenticated ``gh``).
  Restoring access, not answering a question, is the fix.

What this module deliberately does **not** do: turn a plain failure into a question. If the
scanner looked for a linter config and found none, that is a finding with a fix, not a gap.
Offering to "declare" it would make the tool a waiver mill and the score a negotiation.
``waivable`` is set only where the evidence genuinely lives outside the repository, and even
then the answer path is a disclosed waiver that is *excluded* from the gate, never a pass.

Everything here is derived from the report after scoring. No gap can move a number.
"""
from __future__ import annotations

from .detect import UNKNOWN_THRESHOLD, VALID_PIN_TYPES
from .model import Gap, Status
from .score import NOT_OPTED_IN_LOOP, load_registry

_CONFIG_PATH = ".ra1/config.json"
_WAIVERS_PATH = ".ra1/waivers.json"

_CHOICE_ID_RE = __import__("re").compile(r"[a-z][a-z0-9_.:-]{0,127}\Z")


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _choice(cid: str, label: str, effect: str) -> dict:
    assert _CHOICE_ID_RE.match(cid), cid
    assert effect in ("record", "external_action", "leave_unanswered"), effect
    return {"id": cid, "label": label, "effect": effect}


def _boolean_choices() -> list:
    return [_choice("boolean.yes", "Yes", "record"),
            _choice("boolean.no", "No", "record")]


def _type_choices(types) -> list:
    return [_choice(t, t.capitalize(), "record") for t in types]


def _verify_command_candidates(static) -> list:
    """Strict §5.1.2 verification candidates visible in the current bounded scan.

    Each entry is ``(command, choice_id)`` with the opaque command hash — repository text
    never reaches the choice payload.
    """
    import re
    candidates = []
    for path in static.glob(["Makefile", "Justfile", "justfile", "Taskfile.yml",
                             "Taskfile.yaml"]):
        text = static.read(path) or ""
        launcher = "make" if path == "Makefile" else ("just" if "ustfile" in path
                                                      else "task")
        for name in ("check", "verify", "validate"):
            if re.search(r"(?m)^\s*" + name + r"\s*:", text):
                command = f"{launcher} {name}"
                candidates.append((command, "command." + _sha(command)))
    pkg = static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and isinstance(pkg.get("scripts"), dict):
        for name in ("check", "verify", "validate"):
            if name in pkg["scripts"]:
                command = f"npm run {name}"
                candidates.append((command, "command." + _sha(command)))
    if static.has_dep("pytest") or static.has_tool_config("pytest") or \
            static.glob(["tests/**", "test/**"]):
        command = "python -m pytest"
        candidates.append((command, "command." + _sha(command)))
    for path in static.glob(["scripts/check*", "scripts/verify*"]):
        candidates.append((path, "command." + _sha(path)))
    seen, out = set(), []
    for command, cid in candidates:
        if command not in seen and len(out) < 16:
            seen.add(command)
            out.append((command, cid))
    return out

# Criterion id -> the config value that would let it run. Authored, because the mapping is
# between a check's intent and a config key, and neither the rationale text nor the registry
# records it. `test_gaps.py` asserts every id here exists in the registry, so the table
# cannot rot silently as checks are renamed or retired.
_CONFIG_GAPS = {
    "build.check_command": {
        "id": "config.acdc.verify_command",
        "path": "acdc.verify_command",
        "kind_of_value": "string",
        "statuses": (Status.FAIL, Status.UNKNOWN),
        "question": "Which single command verifies a change in this repository "
                    "(lint + typecheck + tests in one entrypoint)?",
        "why": "Names the one command an agent runs before it claims a change works. "
               "Unset, the check has to guess from conventional script names.",
    },
    "build.ci_duration_budget": {
        "id": "config.ci_budget_minutes",
        "path": "ci_budget_minutes",
        "kind_of_value": "integer (minutes)",
        "statuses": (Status.UNKNOWN,),
        "question": "What is the longest acceptable CI wall-clock time, in minutes, "
                    "before you would call the pipeline too slow for agent iteration?",
        "why": "There is no universal budget, so the check cannot judge duration without "
               "the number your team would actually defend.",
    },
}


def _and_list(items) -> str:
    """'a', 'a and b', 'a, b, and c' — the question is read by a person."""
    items = [str(i) for i in items]
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _next_locked_level(report) -> int:
    """The level a reader is trying to clear: one above the highest achieved."""
    return ((report.score.level if report.score else 0) or 0) + 1


def _detection_gaps(report, config) -> list[Gap]:
    d = report.detection
    if d is None:
        return []
    detect_cfg = config.get("detect") if isinstance(config.get("detect"), dict) else {}
    out = []

    # Type-dependent criteria the registry marks as narrow; an unknown type is exactly why
    # they report `unknown` instead of being silently skipped (score.py::_type_match).
    narrow = {c["id"] for c in load_registry()
              if (c.get("applies_when") or {}).get("project_types", ["*"]) != ["*"]}
    stuck = [r for r in report.results
             if r.id in narrow and r.status == Status.UNKNOWN]

    if (not d.is_monorepo and d.project_type == "unknown"
            and detect_cfg.get("project_type") is None):
        out.append(Gap(
            id="detect.project_type",
            kind="detection",
            question="What kind of project is this repository — a library, service, "
                     "frontend, CLI, data pipeline, or infrastructure?",
            why="Type-dependent criteria report `unknown` rather than guess, so pinning "
                "the type is what lets the engine judge them.",
            answer={"file": _CONFIG_PATH, "path": "detect.project_type",
                    "kind_of_value": "enum"},
            evidence=list(d.signals),
            options=sorted(VALID_PIN_TYPES),
            blocks=[r.id for r in stuck],
            blocked_gating=sum(1 for r in stuck if r.gating),
            levels=sorted({r.level for r in stuck if r.gating}),
            recordable=True,
            input_kind="single_choice",
            choices=_type_choices(sorted(VALID_PIN_TYPES)),
        ))

    # Competing strong signals: the scanner picked the head of the ranked list and the tail
    # never surfaced, so criteria for the losing type were skipped with no trace.
    contested = [c for c in d.candidates if c.get("type") != "unknown"]
    if (len(contested) > 1 and not d.is_monorepo
            and detect_cfg.get("project_type") is None
            and not detect_cfg.get("surfaces")):
        types = [c["type"] for c in contested]
        out.append(Gap(
            id="detect.project_type.contested",
            recordable=True,
            input_kind="multi_choice",
            choices=_type_choices(types),
            kind="detection",
            question=f"This directory shows evidence of {_and_list(types)}. Which of those "
                     f"does it actually serve? Declare every surface that applies.",
            why=f"The scan treats it as `{types[0]}` alone, so criteria that apply only to "
                f"{_and_list(types[1:])} were skipped without saying so. Declaring several "
                f"surfaces makes all of their criteria apply.",
            answer={"file": _CONFIG_PATH, "path": "detect.surfaces",
                    "kind_of_value": "list of enum (one or more)"},
            evidence=[c["signal"] for c in contested],
            options=types,
        ))

    for app in d.apps if d.is_monorepo else []:
        if app.type_confidence >= UNKNOWN_THRESHOLD or (detect_cfg.get("apps") or {}).get(
                app.path) is not None:
            continue
        app_stuck = [r for r in report.results
                     if r.app_path == app.path and r.status == Status.UNKNOWN]
        out.append(Gap(
            id=f"detect.app_type.{_sha(app.path)}",
            kind="detection",
            question=f"What kind of application is `{app.path}` — a library, service, "
                     f"frontend, CLI, data pipeline, or infrastructure?",
            why="Its type could not be evidenced, so type-dependent criteria for that app "
                "cannot be judged.",
            answer={"file": _CONFIG_PATH, "path": f"detect.apps.{app.path}",
                    "kind_of_value": "enum"},
            evidence=[c["signal"] for c in app.type_candidates],
            options=sorted(VALID_PIN_TYPES),
            blocks=[r.id for r in app_stuck],
            blocked_gating=sum(1 for r in app_stuck if r.gating),
            levels=sorted({r.level for r in app_stuck if r.gating}),
            recordable=True,
            input_kind="single_choice",
            choices=_type_choices(sorted(VALID_PIN_TYPES)),
        ))
    return out


def _config_gaps(report, config, static=None) -> list[Gap]:
    out = []
    for cid, spec in _CONFIG_GAPS.items():
        result = next((r for r in report.results if r.id == cid), None)
        if result is None or result.status not in spec["statuses"]:
            continue
        if _configured(config, spec["path"]):
            continue
        recordable, input_kind, choices, value = True, "single_choice", [], None
        if spec["id"] == "config.acdc.verify_command":
            candidates = _verify_command_candidates(static) if static is not None else []
            if candidates:
                choices = [_choice(cid_, cmd, "record") for cmd, cid_ in candidates]
            else:
                recordable, input_kind = False, "unrecordable"
            choices.append(_choice("leave_unanswered", "Leave unanswered",
                                   "leave_unanswered"))
        elif spec["id"] == "config.ci_budget_minutes":
            input_kind = "integer"
            value = {"type": "integer", "minimum": 1, "maximum": 1440}
        out.append(Gap(
            id=spec["id"],
            kind="config",
            question=spec["question"],
            why=spec["why"],
            answer={"file": _CONFIG_PATH, "path": spec["path"],
                    "kind_of_value": spec["kind_of_value"]},
            evidence=[result.rationale] if result.rationale else [],
            blocks=[cid],
            blocked_gating=1 if result.gating else 0,
            levels=[result.level] if result.gating else [],
            recordable=recordable,
            input_kind=input_kind,
            choices=choices,
            value=value,
        ))
    return out


def _configured(config, dotted: str) -> bool:
    """True when the config already carries a value at `a.b.c`."""
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return node is not None


def _opt_in_gaps(report, config) -> list[Gap]:
    # Presence, not truthiness: an explicit `false` is a decision the developer already made,
    # and re-asking it every run is how an interview becomes nagging.
    if "loop_ready" in config:
        return []
    stuck = [r for r in report.results
             if r.status == Status.SKIPPED and r.rationale == NOT_OPTED_IN_LOOP]
    if not stuck:
        return []
    return [Gap(
        id="config.loop_ready",
        kind="config",
        question="Does this repository run an autonomous agent loop whose signals, "
                 "budgets, and stop conditions should be scored?",
        why=f"{len(stuck)} loop-readiness criteria are skipped until the repository opts "
            f"in, so they neither help nor hurt the score today.",
        answer={"file": _CONFIG_PATH, "path": "loop_ready", "kind_of_value": "boolean"},
        evidence=[NOT_OPTED_IN_LOOP],
        options=[True, False],
        blocks=[r.id for r in stuck],
        blocked_gating=sum(1 for r in stuck if r.gating),
        levels=sorted({r.level for r in stuck if r.gating}),
        recordable=True,
        input_kind="single_choice",
        choices=_boolean_choices(),
    )]


# Engine-authored rationale marker. Safe to match on: these strings are written by our own
# checks when the collector is unavailable, never by the scanned repository.
_NO_GITHUB = "github api"


def _capability_gaps(report) -> list[Gap]:
    if report.github_available:
        return []
    stuck = [r for r in report.results
             if r.status in (Status.SKIPPED, Status.UNKNOWN)
             and _NO_GITHUB in (r.rationale or "").lower()]
    if not stuck:
        return []
    return [Gap(
        id="capability.github",
        kind="capability",
        question="Can this scan reach the GitHub API — is `gh` authenticated for this "
                 "repository, or is the project hosted somewhere else?",
        why=f"{len(stuck)} criteria that live in repository settings rather than in files "
            f"cannot be read at all without it.",
        answer={"action": "authenticate the gh CLI (`gh auth login`), then re-run",
                "file": _WAIVERS_PATH,
                "kind_of_value": "restored access, or a disclosed waiver per criterion"},
        evidence=sorted({r.rationale for r in stuck if r.rationale})[:3],
        blocks=[r.id for r in stuck],
        blocked_gating=sum(1 for r in stuck if r.gating),
        levels=sorted({r.level for r in stuck if r.gating}),
        # The only honest waiver case in the catalogue: the evidence is real but lives in a
        # host the scan cannot see, so a disclosed exclusion beats both a guess and a fail.
        waivable=True,
        recordable=True,
        input_kind="single_choice",
        choices=[
            _choice("github.restore_access",
                    "I will restore GitHub API access, then re-scan", "external_action"),
            _choice("github.non_github_host",
                    "This project is not hosted on GitHub.com (record disclosed waivers)",
                    "record"),
            _choice("leave_unanswered", "Leave unanswered", "leave_unanswered"),
        ],
    )]


def derive_gaps(report, config=None, static=None) -> list[Gap]:
    """Every unanswered question in this report, highest leverage first.

    Ordering is deterministic and mirrors `score._recommendations`: whatever blocks the gate
    the reader is currently trying to clear comes first, then raw gating weight, then total
    criteria touched, then id. Two runs over the same repository produce the same order.
    """
    if report is None:
        return []
    config = config if isinstance(config, dict) else {}
    gaps = (_detection_gaps(report, config) + _config_gaps(report, config, static)
            + _opt_in_gaps(report, config) + _capability_gaps(report))
    next_level = _next_locked_level(report)

    def key(g: Gap):
        at_next = 0 if next_level in g.levels else 1
        return (at_next, -g.blocked_gating, -len(g.blocks), g.id)

    return sorted(gaps, key=key)
