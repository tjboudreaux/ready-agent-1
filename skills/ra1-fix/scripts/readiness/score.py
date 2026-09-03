"""Scoring: applicability/skip, per-application aggregation, waivers, and level gating.

This is the canonical, reproducible score. Given the same repo state + engine/registry
version it is identical on every machine and in CI — there is no agent and no execution here.

Every runtime path attaches a deterministic decision trace (never model chain-of-thought)
through one result-finalization helper. Explanation payloads — traces, limitations,
rationale — never feed ``summarize``, ``_gate``, criterion status, or ``history.delta``
arithmetic.
"""
from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path

from . import parsers, safe_io
from .context import Context
from .model import (
    _REASON_CODE_RE,
    LEVEL_NAMES,
    REQUIRED_REASON_CODES,
    App,
    CriterionResult,
    DecisionStep,
    DecisionTrace,
    Evidence,
    LevelScore,
    ScoreSummary,
    Status,
    _empty_evidence_coverage,
    finalize_public_result,
    validate_decision_trace,
)

_REGISTRY_PATH = Path(__file__).resolve().parent / "criteria" / "registry.json"

# The exact rationale for a loop criterion the repository has not opted into. Shared so the
# gaps layer can recognize it structurally instead of pattern-matching prose.
NOT_OPTED_IN_LOOP = "not opted into loop readiness"

WAIVER_RATIONALE = "Waived by policy; the free-form reason remains in the source policy."
WAIVER_LIMITATION = "Free-form waiver reason is intentionally omitted from the report."

# Scorer-caught repository read refusal maps to this suffix family for required checks.
_INDETERMINATE_SUFFIXES = ("observation_indeterminate", "indeterminate",
                           "observation_unreadable", "applicability_indeterminate",
                           "syntax_indeterminate", "template_indeterminate",
                           "instructions_indeterminate", "discovery_indeterminate")


def load_registry(path=None):
    text = parsers.read_engine_text(Path(path) if path else _REGISTRY_PATH)
    if text is None:
        raise ValueError("engine registry unreadable")
    return parsers.strict_load_json(text, require_object=False)


def _resolve_check(ref):
    mod_name, fn_name = ref.rsplit(".", 1)
    module = importlib.import_module("readiness.checks." + mod_name)
    return getattr(module, fn_name)


def load_waivers(root, options):
    """Load active waivers. Returns ``(waivers, invalid)``: an unsafe/malformed policy file
    is ``invalid`` so the caller marks the global repository-indeterminate state."""
    deps = options.get("_deps") or {}
    if deps.get("waivers") is not None:
        data = deps["waivers"]
    else:
        from .detect import read_policy_json
        static = root if hasattr(root, "read_repo_file") else None
        if static is None:
            from .collectors.static import StaticCollector
            static = StaticCollector(root)
        state, data = read_policy_json(static, ".ra1/waivers.json")
        if state == "missing":
            return {}, False
        if state == "invalid":
            return {}, True
    out = {}
    now = deps.get("now")
    for w in (data or []):
        if not isinstance(w, dict):
            continue
        cid = w.get("id")
        if not cid:
            continue
        expires = w.get("expires")
        if now and expires:
            try:
                if datetime.fromisoformat(expires) < datetime.fromisoformat(now):
                    continue  # expired -> criterion re-activates
            except ValueError:
                pass
        out[cid] = w
    return out, False


def _type_match(applies_types, actual):
    """Applicability against one surface or several.

    A fullstack directory declares `["service", "frontend"]`, and a criterion applies when
    *any* declared surface matches: the union, because the app genuinely owes both sets of
    practices. `unknown` only survives when nothing is known, so one confident surface beside
    an ambiguous one no longer drags the criterion into `unknown`.
    """
    actual_types = [actual] if isinstance(actual, str) else list(actual) or ["unknown"]
    if "*" in applies_types:
        return "match"
    if any(t in applies_types for t in actual_types):
        return "match"
    if any(t == "unknown" for t in actual_types):
        return "unknown"
    return "skip"


def _lang_match(applies_langs, actual_langs):
    if "*" in applies_langs:
        return True
    return bool({a.lower() for a in applies_langs} & {lang.lower() for lang in actual_langs})


def _ctx(root, detection, static, git, github, app, options):
    return Context(root=Path(root), detection=detection, static=static, git=git,
                   github=github, app=app, options=options,
                   exec=(options or {}).get("_exec"))


def _base(crit):
    acdc = crit.get("acdc") or {}
    return dict(
        id=crit["id"], title=crit["title"], pillar=crit["pillar"], level=crit["level"],
        scope=crit.get("scope", "repository"),
        gating=crit.get("gating", True) and crit.get("decide") != "agent",
        fixable=bool((crit.get("fix") or {}).get("autofixable")),
        fix_kind=(crit.get("fix") or {}).get("kind", ""),
        acdc_stage=acdc.get("stage", ""),
        acdc_loop=acdc.get("loop", ""),
    )


def _decision_trace(crit, status, rationale, evidence, *,
                    reason_code, limitations=None) -> DecisionTrace:
    """The bounded, lossless four-stage chain: rule → observation → evaluation → conclusion.

    Trace references evidence indexes rather than copying fields, so it adds no payload.
    """
    steps = [
        DecisionStep(
            kind="rule", code="rule.applied",
            message=f"Criterion {crit['id']} ({crit['title']}) is evaluated by "
                    f"{crit['check']}.",
        ),
    ]
    if evidence:
        steps.append(DecisionStep(
            kind="observation", code="evidence.observed",
            message=f"Observed {len(evidence)} cited evidence item(s).",
            evidence_refs=list(range(len(evidence))),
        ))
    steps.append(DecisionStep(kind="evaluation", code=reason_code, message=rationale))
    steps.append(DecisionStep(kind="conclusion", code=f"conclusion.{status.value}",
                              message=f"Result: {status.value}."))
    merged = []
    for limitation in (limitations or []):
        if isinstance(limitation, str) and limitation and limitation not in merged:
            merged.append(limitation)
    return DecisionTrace(reason_code=reason_code, rule_ref=crit["check"], steps=steps,
                         limitations=merged)


def _reason_code_for(crit, verdict, status: Status) -> str:
    """The direct-check reason code: check-authored typed code, else ``check.<status>``.

    A missing, malformed, wrong-prefix, or unallowlisted code on an invoked required check
    is an engine contract error: it is preserved here and makes ``Report.to_dict()`` fail
    closed, never fall back to prose or ``check.<status>``.
    """
    cid = crit["id"]
    code = verdict.reason_code if verdict is not None else ""
    if not code:
        return f"check.{status.value}"
    if cid in REQUIRED_REASON_CODES:
        return code  # validated at canonical projection time
    if _REASON_CODE_RE.match(code) and code.startswith(cid + ".") \
            and len(code.encode("utf-8", "replace")) <= 128 and code.isascii():
        return code
    return f"check.{status.value}"


def _finalize_result(crit, base, status, rationale, evidence, *,
                     reason_code, limitations=None, app_path=".",
                     passed_apps=0, evaluated_apps=0) -> CriterionResult:
    """The one result-finalization helper: every runtime path gets a non-empty trace.

    Public text is finalized first; the trace is built only from finalized payloads so no
    renderer or imported-report consumer ever sees raw repository prose.
    """
    result = CriterionResult(status=status, rationale=rationale, evidence=list(evidence),
                             app_path=app_path, passed_apps=passed_apps,
                             evaluated_apps=evaluated_apps, **base)
    finalized = finalize_public_result(result)
    finalized.decision_trace = _decision_trace(
        crit, finalized.status, finalized.rationale, finalized.evidence,
        reason_code=reason_code, limitations=limitations)
    return finalized


def _read_refusal_code(crit) -> str:
    """Reason code for a scorer-caught repository input refusal from a check."""
    cid = crit["id"]
    if cid in REQUIRED_REASON_CODES:
        for suffix in _INDETERMINATE_SUFFIXES:
            if suffix in REQUIRED_REASON_CODES[cid]:
                return f"{cid}.{suffix}"
    return "input.repository_unreadable"


def _eval_criterion(crit, root, detection, static, git, github, waivers, options, done):
    base = _base(crit)
    cid = crit["id"]
    aw = crit.get("applies_when", {})
    types = aw.get("project_types", ["*"])
    langs = aw.get("languages", ["*"])
    requires = aw.get("requires", [])

    if detection.repository_indeterminate:
        reason = detection.indeterminate_reason or "input.repository_indeterminate"
        if reason == "input.legacy_policy_path":
            rationale = ("Legacy .agents/readiness policy files are present; move them to "
                         ".ra1/ (see docs) before scoring.")
        else:
            rationale = ("Repository configuration or manifest input could not be read "
                         "safely; applicability cannot be determined.")
        return _finalize_result(
            crit, base, Status.UNKNOWN, rationale, [], reason_code=reason,
            limitations=["Files or candidate sets beyond documented bounds are reported "
                         "unavailable rather than inspected."])

    opt_in = aw.get("opt_in")
    if opt_in == "loop_ready" and not detection.opt_in.get("loop_ready"):
        return _finalize_result(crit, base, Status.SKIPPED, NOT_OPTED_IN_LOOP, [],
                                reason_code="applicability.not_opted_in")
    if opt_in is not None and opt_in != "loop_ready":
        return _finalize_result(crit, base, Status.UNKNOWN,
                                f"Unsupported opt-in '{opt_in}'.", [],
                                reason_code="applicability.unsupported_opt_in")

    if cid in waivers:
        return _finalize_result(crit, base, Status.WAIVED, WAIVER_RATIONALE, [],
                                reason_code="waiver.active",
                                limitations=[WAIVER_LIMITATION])

    if crit.get("decide") == "agent":
        from .detect import load_readiness_config
        from .judgments import decide as _judgment_decide
        sev, _reason = _judgment_decide(load_readiness_config(static, options), cid)
        if sev == "off":
            return _finalize_result(
                crit, base, Status.WAIVED,
                "Suppressed by policy; the free-form reason remains in the source policy.",
                [], reason_code="judgment.suppressed",
                limitations=["Free-form suppression reason is intentionally omitted from "
                             "the report."])

    for req in requires:
        if done.get(req) != Status.PASS:
            return _finalize_result(crit, base, Status.SKIPPED,
                                    f"Prerequisite '{req}' not satisfied.", [],
                                    reason_code="prerequisite.unmet")

    check = _resolve_check(crit["check"])

    if base["scope"] == "application":
        apps = detection.apps or [
            App(path=".", languages=detection.languages, deploy_surface=detection.project_type)
        ]
        per = []
        for app in apps:
            tm = _type_match(types, app.match_surfaces())
            if tm == "skip":
                continue
            if not _lang_match(langs, app.languages or detection.languages):
                continue
            if tm == "unknown":
                per.append((app, None))
            else:
                per.append((app, _run_check(check, crit, root, detection, static, git,
                                            github, app, options)))
        return _aggregate(crit, base, per)

    # repository scope
    tm = _type_match(types, detection.match_surfaces())
    app = App(path=".", languages=detection.languages,
              deploy_surface=detection.project_type, surfaces=list(detection.surfaces))
    if tm == "skip":
        return _finalize_result(crit, base, Status.SKIPPED,
                                "Not applicable to this project type.", [],
                                reason_code="applicability.project_type_mismatch")
    if not _lang_match(langs, detection.languages):
        return _finalize_result(crit, base, Status.SKIPPED, "No matching language.", [],
                                reason_code="applicability.language_mismatch")
    if tm == "unknown":
        return _finalize_result(crit, base, Status.UNKNOWN,
                                "Project type unknown; applicability undetermined.", [],
                                reason_code="applicability.project_type_unknown")
    verdict = _run_check(check, crit, root, detection, static, git, github, app, options)
    return _finalize_result(
        crit, base, verdict.status, verdict.rationale, verdict.evidence,
        reason_code=_reason_code_for(crit, verdict, verdict.status),
        limitations=verdict.limitations)


def _run_check(check, crit, root, detection, static, git, github, app, options):
    """Invoke one check; a repository input refusal becomes a blocking unknown verdict."""
    from .model import Verdict
    try:
        return check(_ctx(root, detection, static, git, github, app, options))
    except safe_io.RepositoryInputError:
        return Verdict(
            Status.UNKNOWN,
            "Repository input could not be read safely; the check cannot be evaluated.",
            [], limitations=["Files or candidate sets beyond documented bounds or safety "
                             "rules are reported unavailable rather than inspected."],
            reason_code=_read_refusal_code(crit))


def _status_counts(status):
    """Repository-scope N/M: one assessable unit, counted only when applicable."""
    if status == Status.PASS:
        return 1, 1
    if status in (Status.FAIL, Status.UNKNOWN):
        return 0, 1
    return 0, 0  # skipped / waived -> not applicable


def _app_label(path):
    """Human name for an application path. The root app is "." , which reads as a stray

    period when a sentence ends right after it ("Undetermined for ..").
    """
    return "the repository root" if path == "." else path


def _aggregate(crit, base, per):
    """Aggregate per-application verdicts into one result with one bounded trace.

    Preserves the aggregate rationale and prefixed evidence, merges per-app limitations in
    stable application order, and builds the trace from the final evidence/result — never
    from private per-app working state.
    """
    if not per:
        return _finalize_result(crit, base, Status.SKIPPED,
                                "Not applicable to any application.", [],
                                reason_code="applicability.no_applications")
    evidence, fails, unknown_apps = [], [], []
    limitations = []
    passes = skips = 0
    multi = len(per) > 1
    for app, verdict in per:
        if verdict is None:
            unknown_apps.append(app.path)
            continue
        if verdict.status == Status.PASS:
            passes += 1
        elif verdict.status == Status.SKIPPED:
            skips += 1
        elif verdict.status == Status.FAIL:
            fails.append(app)
        else:
            unknown_apps.append(app.path)
        for limitation in getattr(verdict, "limitations", []) or []:
            if limitation and limitation not in limitations:
                limitations.append(limitation)
        for e in verdict.evidence:
            label = f"[{app.path}] {e.summary}" if multi else e.summary
            evidence.append(Evidence(summary=label, tier=e.tier, source=e.source,
                                     detail=e.detail))
    total = len(per)
    app_path = per[0][0].path if total == 1 else "*"

    if fails:
        critical = [_app_label(a.path) for a in fails if a.prod_facing is True]
        note = f"{passes}/{total} application(s) pass."
        note += (" Production-facing failing: " + ", ".join(critical) + ".") if critical \
                else (" Failing: " + ", ".join(_app_label(a.path) for a in fails) + ".")
        return _finalize_result(crit, base, Status.FAIL, note, evidence,
                                reason_code="aggregate.fail", limitations=limitations,
                                app_path=app_path, passed_apps=passes,
                                evaluated_apps=total)
    if unknown_apps:
        named = ", ".join(_app_label(p) for p in unknown_apps)
        if passes > 0:
            rationale = f"{passes}/{total} application(s) pass; undetermined for {named}."
        else:
            rationale = f"Undetermined for {named}."
        return _finalize_result(crit, base, Status.UNKNOWN, rationale, evidence,
                                reason_code="aggregate.unknown", limitations=limitations,
                                app_path=app_path, passed_apps=passes,
                                evaluated_apps=total)
    if passes > 0:
        return _finalize_result(crit, base, Status.PASS,
                                f"{passes}/{total} application(s) pass.", evidence,
                                reason_code="aggregate.pass", limitations=limitations,
                                app_path=app_path, passed_apps=passes,
                                evaluated_apps=total)
    return _finalize_result(crit, base, Status.SKIPPED,
                            "Skipped for all applications.", evidence,
                            reason_code="aggregate.skipped", limitations=limitations,
                            app_path=app_path, passed_apps=passes, evaluated_apps=total)


_EFFORT_RANK = {"scaffold": 0, "github_setting": 1, "propose": 2, "": 3}


def _recommendations(results, level, limit=3):
    """Deterministic top next-actions: gating failures/unknowns, the next locked level first,
    then ascending level and lowest effort. Capped at ``limit``.
    """
    candidates = [r for r in results if r.gating and r.status in (Status.FAIL, Status.UNKNOWN)]
    next_level = (level or 0) + 1

    def key(r):
        at_next = 0 if r.level == next_level else 1
        return (at_next, r.level, _EFFORT_RANK.get(r.fix_kind, 9), r.id)

    return [{"id": r.id, "title": r.title, "pillar": r.pillar, "level": r.level,
             "status": r.status.value, "fix_kind": r.fix_kind, "rationale": r.rationale}
            for r in sorted(candidates, key=key)[:limit]]


def _next_gate_actions(results, levels):
    """Every gating fail/unknown at the first unachieved defined Level, fully sorted."""
    first_unachieved = None
    for lvl in levels:
        if lvl.defined and not lvl.achieved:
            first_unachieved = lvl.level
            break
    if first_unachieved is None:
        return []
    blockers = [r for r in results
                if r.gating and r.level == first_unachieved
                and r.status in (Status.FAIL, Status.UNKNOWN)]
    blockers.sort(key=lambda r: (r.level, r.pillar, r.id))
    return [{"id": r.id, "title": r.title, "pillar": r.pillar, "level": r.level,
             "status": r.status.value, "fix_kind": r.fix_kind, "rationale": r.rationale}
            for r in blockers]


def _evidence_coverage(results) -> dict:
    """The exact schema-v3 evidence coverage partition over deterministic report results.

    Computed once over gating and advisory criteria together; agent-authored advisory
    judgments never enter it. Discrepancies are surfaced as contract defects, never
    repaired or coerced.
    """
    coverage = _empty_evidence_coverage()
    for result in results:
        coverage["status_counts"][result.status.value] += 1
        if result.evidence:
            coverage["results_with_evidence"] += 1
        coverage["evidence_items"] += len(result.evidence)
        for item in result.evidence:
            if item.tier in coverage["evidence_items_by_tier"]:
                coverage["evidence_items_by_tier"][item.tier] += 1
            # An unexpected tier remains in evidence_items but absent from tier subcounts:
            # a visible coverage contract defect, never coerced.
        trace = result.decision_trace
        valid = False
        if trace is not None and trace.steps:
            probe = result.to_dict()
            valid = not validate_decision_trace(probe)
        if valid:
            coverage["results_with_decision_trace"] += 1
            if sum(1 for s in trace.steps if s.kind == "rule") == 1:
                coverage["results_with_rule_step"] += 1
            if trace.limitations:
                coverage["results_with_limitations"] += 1
            observation = next((s for s in trace.steps if s.kind == "observation"), None)
            if observation is not None:
                coverage["evidence_items_referenced"] += len(set(
                    observation.evidence_refs))
    coverage["evidence_items_unreferenced"] = (
        coverage["evidence_items"] - coverage["evidence_items_referenced"])
    return coverage


def summarize(results, registry=None):
    gating = [r for r in results if r.gating]
    levels = []
    overall = 0
    blocked = False
    for L in range(1, 6):
        defined = [r for r in gating if r.level == L]
        applicable = [r for r in defined if r.status not in (Status.SKIPPED, Status.WAIVED)]
        passed = [r for r in applicable if r.status == Status.PASS]
        defined_total = len(defined)
        defined_bool = defined_total > 0
        if blocked or not defined_bool or not applicable:
            # An undefined Level, or a defined Level with every criterion skipped/waived,
            # is never achieved — zero evaluated evidence cannot clear a gate.
            achieved = False
        else:
            ratio = len(passed) / len(applicable)
            achieved = ratio >= 0.8
        if achieved:
            overall = L
        else:
            blocked = True
        levels.append(LevelScore(level=L, name=LEVEL_NAMES[L], passed=len(passed),
                                 total=len(applicable), achieved=achieved,
                                 defined=defined_bool, defined_total=defined_total))

    applicable_all = [r for r in gating if r.status not in (Status.SKIPPED, Status.WAIVED)]
    passed_all = [r for r in applicable_all if r.status == Status.PASS]
    pillars = {}
    for r in applicable_all:
        p = pillars.setdefault(r.pillar, {"passed": 0, "total": 0})
        p["total"] += 1
        if r.status == Status.PASS:
            p["passed"] += 1
    max_available = max((r.level for r in gating), default=0)
    return ScoreSummary(
        level=overall,
        level_name=LEVEL_NAMES.get(overall, "None") if overall else "None",
        pass_rate=(len(passed_all) / len(applicable_all)) if applicable_all else 0.0,
        gating_passed=len(passed_all),
        gating_total=len(applicable_all),
        levels=levels,
        pillars=pillars,
        recommendations=_recommendations(results, overall),
        max_available_level=max_available,
        next_gate_actions=_next_gate_actions(results, levels),
        evidence_coverage=_evidence_coverage(results),
    )


def evaluate(root, detection, static, git, github, options=None):
    options = options or {}
    deps = options.get("_deps") or {}
    registry = load_registry(deps.get("registry_path"))
    waivers, waivers_invalid = load_waivers(static, options)
    if waivers_invalid:
        detection.repository_indeterminate = True
        if not detection.indeterminate_reason:
            detection.indeterminate_reason = "input.repository_indeterminate"
    results, done = [], {}
    for crit in registry:
        result = _eval_criterion(crit, root, detection, static, git, github, waivers,
                                 options, done)
        if result.scope != "application":
            result.passed_apps, result.evaluated_apps = _status_counts(result.status)
        done[result.id] = result.status
        results.append(result)
    return results, summarize(results, registry)
