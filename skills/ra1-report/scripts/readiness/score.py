"""Scoring: applicability/skip, per-application aggregation, waivers, and level gating.

This is the canonical, reproducible score. Given the same repo state + engine/registry
version it is identical on every machine and in CI — there is no agent and no execution here.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime
from pathlib import Path

from .context import Context
from .model import (LEVEL_NAMES, App, CriterionResult, Evidence, LevelScore,
                    ScoreSummary, Status)

_REGISTRY_PATH = Path(__file__).resolve().parent / "criteria" / "registry.json"


def load_registry(path=None):
    with open(Path(path) if path else _REGISTRY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_check(ref):
    mod_name, fn_name = ref.rsplit(".", 1)
    module = importlib.import_module("readiness.checks." + mod_name)
    return getattr(module, fn_name)


def load_waivers(root, options):
    if options.get("waivers") is not None:
        data = options["waivers"]
    else:
        wf = Path(root) / ".agents" / "readiness" / "waivers.json"
        if not wf.exists():
            return {}
        try:
            data = json.loads(wf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    out = {}
    now = options.get("now")
    for w in (data or []):
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
    return out


def _type_match(applies_types, actual_type):
    if "*" in applies_types:
        return "match"
    if actual_type == "unknown":
        return "unknown"
    return "match" if actual_type in applies_types else "skip"


def _lang_match(applies_langs, actual_langs):
    if "*" in applies_langs:
        return True
    return bool({a.lower() for a in applies_langs} & {l.lower() for l in actual_langs})


def _ctx(root, detection, static, git, github, app, options):
    return Context(root=Path(root), detection=detection, static=static, git=git,
                   github=github, app=app, options=options,
                   exec=(options or {}).get("_exec"))


def _base(crit):
    return dict(
        id=crit["id"], title=crit["title"], pillar=crit["pillar"], level=crit["level"],
        scope=crit.get("scope", "repository"), gating=crit.get("gating", True),
        fixable=bool((crit.get("fix") or {}).get("autofixable")),
        fix_kind=(crit.get("fix") or {}).get("kind", ""),
    )


def _eval_criterion(crit, root, detection, static, git, github, waivers, options, done):
    base = _base(crit)
    cid = crit["id"]
    aw = crit.get("applies_when", {})
    types = aw.get("project_types", ["*"])
    langs = aw.get("languages", ["*"])
    requires = aw.get("requires", [])

    opt_in = aw.get("opt_in")
    if opt_in == "loop_ready" and not detection.opt_in.get("loop_ready"):
        return CriterionResult(status=Status.SKIPPED, rationale="not opted into loop readiness", app_path=".", **base)
    if opt_in is not None and opt_in != "loop_ready":
        return CriterionResult(status=Status.UNKNOWN, rationale=f"Unsupported opt-in '{opt_in}'.", app_path=".", **base)

    if cid in waivers:
        reason = waivers[cid].get("reason", "")
        return CriterionResult(status=Status.WAIVED, rationale=f"Waived: {reason}".strip(), app_path=".", **base)

    for req in requires:
        if done.get(req) != Status.PASS:
            return CriterionResult(status=Status.SKIPPED, rationale=f"Prerequisite '{req}' not satisfied.", app_path=".", **base)

    check = _resolve_check(crit["check"])

    if base["scope"] == "application":
        apps = detection.apps or [App(path=".", languages=detection.languages, deploy_surface=detection.project_type)]
        per = []
        for app in apps:
            tm = _type_match(types, app.deploy_surface)
            if tm == "skip":
                continue
            if not _lang_match(langs, app.languages or detection.languages):
                continue
            if tm == "unknown":
                per.append((app, None))
            else:
                per.append((app, check(_ctx(root, detection, static, git, github, app, options))))
        return _aggregate(base, per)

    # repository scope
    tm = _type_match(types, detection.project_type)
    app = App(path=".", languages=detection.languages, deploy_surface=detection.project_type)
    if tm == "skip":
        return CriterionResult(status=Status.SKIPPED, rationale="Not applicable to this project type.", app_path=".", **base)
    if not _lang_match(langs, detection.languages):
        return CriterionResult(status=Status.SKIPPED, rationale="No matching language.", app_path=".", **base)
    if tm == "unknown":
        return CriterionResult(status=Status.UNKNOWN, rationale="Project type unknown; applicability undetermined.", app_path=".", **base)
    v = check(_ctx(root, detection, static, git, github, app, options))
    return CriterionResult(status=v.status, rationale=v.rationale, evidence=list(v.evidence), app_path=".", **base)


def _aggregate(base, per):
    if not per:
        return CriterionResult(status=Status.SKIPPED, rationale="Not applicable to any application.", app_path=".", **base)
    evidence, fails, unknown_apps = [], [], []
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
        for e in verdict.evidence:
            label = f"[{app.path}] {e.summary}" if multi else e.summary
            evidence.append(Evidence(summary=label, tier=e.tier, source=e.source, detail=e.detail))
    total = len(per)
    app_path = per[0][0].path if total == 1 else "*"

    if fails:
        crit = [a.path for a in fails if a.prod_facing is True]
        note = f"{passes}/{total} application(s) pass."
        note += (" Production-facing failing: " + ", ".join(crit) + ".") if crit else \
                (" Failing: " + ", ".join(a.path for a in fails) + ".")
        return CriterionResult(status=Status.FAIL, rationale=note, evidence=evidence, app_path=app_path, **base)
    if unknown_apps and passes == 0:
        return CriterionResult(status=Status.UNKNOWN, rationale=f"Undetermined for {', '.join(unknown_apps)}.", evidence=evidence, app_path=app_path, **base)
    if passes > 0:
        return CriterionResult(status=Status.PASS, rationale=f"{passes}/{total} application(s) pass.", evidence=evidence, app_path=app_path, **base)
    return CriterionResult(status=Status.SKIPPED, rationale="Skipped for all applications.", evidence=evidence, app_path=app_path, **base)


def summarize(results, registry=None):
    gating = [r for r in results if r.gating]
    levels = []
    overall = 0
    blocked = False
    for L in range(1, 6):
        defined = [r for r in gating if r.level == L]
        applicable = [r for r in defined if r.status not in (Status.SKIPPED, Status.WAIVED)]
        passed = [r for r in applicable if r.status == Status.PASS]
        if blocked or not defined:
            achieved = False
        else:
            ratio = (len(passed) / len(applicable)) if applicable else 1.0
            achieved = ratio >= 0.8
        if achieved:
            overall = L
        else:
            blocked = True
        levels.append(LevelScore(level=L, name=LEVEL_NAMES[L], passed=len(passed),
                                 total=len(applicable), achieved=achieved))

    applicable_all = [r for r in gating if r.status not in (Status.SKIPPED, Status.WAIVED)]
    passed_all = [r for r in applicable_all if r.status == Status.PASS]
    pillars = {}
    for r in applicable_all:
        p = pillars.setdefault(r.pillar, {"passed": 0, "total": 0})
        p["total"] += 1
        if r.status == Status.PASS:
            p["passed"] += 1
    return ScoreSummary(
        level=overall,
        level_name=LEVEL_NAMES.get(overall, "None") if overall else "None",
        pass_rate=(len(passed_all) / len(applicable_all)) if applicable_all else 0.0,
        gating_passed=len(passed_all),
        gating_total=len(applicable_all),
        levels=levels,
        pillars=pillars,
    )


def evaluate(root, detection, static, git, github, options=None):
    options = options or {}
    registry = load_registry(options.get("registry_path"))
    waivers = load_waivers(root, options)
    results, done = [], {}
    for crit in registry:
        result = _eval_criterion(crit, root, detection, static, git, github, waivers, options, done)
        done[result.id] = result.status
        results.append(result)
    return results, summarize(results, registry)
