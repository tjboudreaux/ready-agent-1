"""The fix engine: plan and verified-apply remediation from a readiness scan.

Safety model:
- AUTO-APPLY only genuinely-missing *config* scaffolds, via exclusive create-only writes;
  an existing target is a skipped proposal, never an overwrite.
- PROPOSE prose/tests as drafts (the engine never writes them — the skill drafts for human
  review).
- GITHUB settings are a manual checklist, never auto-applied and never bundled with code.
- ``--apply`` always performs fresh baseline → mutation → same-option rescan → comparable
  regression decision. There is no optional verify flag, no force flag, and no rollback:
  a retained file is reported explicitly, never silently deleted.

Safety buckets (auto/propose/github/manual) are unchanged in meaning; every bucket item
carries the rich common schema with criterion IDs and engine explanations.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import parsers, safe_io, score

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"

# criterion id -> (target path in repo, template filename in templates/)
STATIC_SCAFFOLDS = {
    "build.ci_present": (".github/workflows/readiness.yml", "ci/readiness.yml"),
    "security.security_md": ("SECURITY.md", "SECURITY.md"),
    "taskdisc.issue_templates": (
        ".github/ISSUE_TEMPLATE/bug_report.md", "ISSUE_TEMPLATE/bug_report.md"),
    "taskdisc.pr_templates": (".github/pull_request_template.md", "pull_request_template.md"),
    "security.dependency_update_automation": (".github/dependabot.yml", "dependabot.yml"),
    "devenv.devcontainer": (".devcontainer/devcontainer.json", "devcontainer.json"),
    "style.precommit_hooks": (".pre-commit-config.yaml", "precommit-config.yaml"),
    # CODEOWNERS stays a human proposal: security.codeowners passes on presence, and a
    # commented-placeholder stub must never flip the prerequisite or auto-confirm
    # security.agent_config_ownership (which rejects placeholder owners).
    "devenv.env_template": (".env.example", "env.example"),
    "loop.loop_runs_dir": ("loop-runs/README.md", "loop/loop-runs-README.md"),
    "loop.denylist": (".omp/rules/denylist.md", "loop/denylist.md"),
    "loop.signal_schema": ("signals/README.md", "loop/signals-README.md"),
    "loop.pr_artifact_template": (
        ".omp/commands/pr-artifact-template.md", "loop/pr-artifact-template.md"),
    "security.gitignore_comprehensive": (".gitignore", "gitignore.ra1"),
}

_GH_COMMANDS = {
    "security.branch_protection": (
        "gh api -X PUT repos/{owner}/{repo}/branches/{branch}/protection "
        "-f required_pull_request_reviews… (review first)"),
    "security.branch_protection_depth": (
        "Review branch protection: require ≥1 approving review + code-owner review + ≥1 "
        "required status check; disable force pushes and branch deletions."),
    "security.secret_scanning": "Enable in Settings → Code security & analysis (or via gh api).",
    "taskdisc.issue_labeling": (
        "gh label create 'priority:high' --color B60205 ; gh label create 'area:core' …"),
    "taskdisc.backlog_health": "Triage open issues and apply labels.",
}

# Deterministic --instructions grammar: phrases map to pillars, nothing else is interpreted.
_PILLAR_ALIASES = {
    "docs": "Documentation", "documentation": "Documentation", "readme": "Documentation",
    "security": "Security & Governance", "governance": "Security & Governance",
    "style": "Style & Validation", "lint": "Style & Validation",
    "validation": "Style & Validation",
    "build": "Build System", "ci": "Build System",
    "test": "Testing", "tests": "Testing", "testing": "Testing",
    "dev": "Dev Environment", "devenv": "Dev Environment", "environment": "Dev Environment",
    "task": "Task Discovery", "backlog": "Task Discovery", "discovery": "Task Discovery",
}
_INSTR_RE = re.compile(r"(prioriti[sz]e|do not touch|don't touch|skip|avoid)\s+([a-z]+)", re.I)


def _match_pillar(target, known_pillars):
    for word in re.findall(r"[a-z]+", target.lower()):
        pillar = _PILLAR_ALIASES.get(word)
        if pillar and pillar in known_pillars:
            return pillar
    return None


def parse_instructions(text, known_pillars):
    """Map the small documented keyword grammar to pillar focus.

    Returns ``{pillar_prioritize, pillar_exclude, unsupported}``. Free-form text that matches no
    grammar phrase is reported as ``unsupported`` (annotated, never silently filtering the plan).
    """
    prioritize, exclude, recognized = set(), set(), False
    for m in _INSTR_RE.finditer(text or ""):
        verb = m.group(1).lower().replace("'", "")
        pillar = _match_pillar(m.group(2), known_pillars)
        if not pillar:
            continue
        recognized = True
        (prioritize if verb.startswith("prioriti") else exclude).add(pillar)
    unsupported = bool((text or "").strip()) and not recognized
    return {"pillar_prioritize": prioritize, "pillar_exclude": exclude,
            "unsupported": unsupported}


def _focus_excludes(r, crit, focus, include):
    """True when a failing criterion should be dropped from the plan given the focus filters.

    Precedence: explicit ``--include``/``--exclude`` win over instruction-derived pillar filters.
    Advisory (non-gating) work that is not a safe scaffold requires explicit inclusion.
    """
    cid = r["id"]
    if cid in (focus.get("exclude") or set()):
        return True
    if include and cid not in include:
        return True
    if cid in include:
        return False  # explicit include overrides instruction-derived and advisory rules
    if crit.get("pillar", "") in (focus.get("pillar_exclude") or set()):
        return True
    kind = (crit.get("fix") or {}).get("kind", "")
    if r.get("gating") is False and kind != "scaffold":
        return True  # non-scaffold advisory/prose needs explicit --include
    return False


def _prioritize(plan, pillars, by_id):
    pillars = set(pillars or [])
    if not pillars:
        return
    def rank(cid):
        return 0 if (by_id.get(cid) or {}).get("pillar") in pillars else 1
    for bucket in ("auto", "propose", "github", "manual"):
        plan[bucket].sort(key=lambda it: rank(it["id"]))


def _languages(report):
    return (report.get("detection") or {}).get("languages", [])


def resolve_scaffold(cid, langs):
    if cid in STATIC_SCAFFOLDS:
        return STATIC_SCAFFOLDS[cid]
    if cid == "style.linter_config":
        return (".eslintrc.json", "eslintrc.json") if "npm" in langs else ("ruff.toml", "ruff.toml")
    if cid == "style.formatter":
        return ((".prettierrc.json", "prettierrc.json") if "npm" in langs
                else ("ruff.toml", "ruff.toml"))
    return None


def _result_by_id(report_dict) -> dict:
    return {r["id"]: r for r in report_dict.get("results", [])}


def _explanation(result) -> dict:
    """The engine-owned explanation for one criterion, exactly five keys."""
    trace = result.get("decision_trace") or {}
    refs = []
    for step in trace.get("steps", []):
        if step.get("kind") == "observation":
            for index in step.get("evidence_refs", []):
                if 0 <= index < len(result.get("evidence", [])):
                    e = result["evidence"][index]
                    refs.append({"summary": e["summary"], "tier": e["tier"],
                                 "source": e["source"]})
    return {
        "status": result["status"],
        "reason_code": trace.get("reason_code", ""),
        "rule_ref": trace.get("rule_ref", ""),
        "evidence_citations": refs,
        "limitations": list(trace.get("limitations", [])),
    }


def _rich_item(cids, results_by_id, *, title=None):
    """One normalized bucket item with sorted unique IDs, joined rationales, explanations."""
    cids = sorted(set(cids))
    first = cids[0]
    rationales = {cid: (results_by_id.get(cid) or {}).get("rationale", "")
                  for cid in cids}
    rationale = rationales[first] if len(cids) == 1 else "; ".join(
        f"{cid}: {rationales[cid]}" for cid in cids)
    return {
        "id": first,
        "title": title or (results_by_id.get(first) or {}).get("title", ""),
        "rationale": rationale,
        "criterion_ids": cids,
        "rationales": rationales,
        "explanations": {cid: _explanation(results_by_id[cid]) for cid in cids
                         if cid in results_by_id},
    }


def build_plan(root, report, registry=None, focus=None):
    """The normalized fix plan: rich bucket items with shared-target merges."""
    root = Path(root)
    registry = registry or score.load_registry()
    by_id = {c["id"]: c for c in registry}
    results_by_id = _result_by_id(report)
    langs = _languages(report)
    focus = focus or {}
    include = set(focus.get("include") or [])
    plan = {"auto": [], "propose": [], "github": [], "manual": []}
    auto_by_target: dict = {}
    for r in report.get("results", []):
        if r.get("status") != "fail":
            continue
        crit = by_id.get(r["id"]) or {}
        if _focus_excludes(r, crit, focus, include):
            continue
        fix = crit.get("fix") or {}
        kind = fix.get("kind", "")
        if kind == "scaffold":
            res = resolve_scaffold(r["id"], langs)
            if not res:
                plan["manual"].append(_rich_item([r["id"]], results_by_id))
                continue
            target, template = res
            if target in auto_by_target:
                auto_by_target[target][1].append(r["id"])
                continue
            auto_by_target[target] = [(target, template), [r["id"]]]
        elif kind == "propose":
            item = _rich_item([r["id"]], results_by_id)
            template = (crit.get("fix") or {}).get("template")
            if template:
                item["template"] = template
            plan["propose"].append(item)
        elif kind == "github_setting":
            item = _rich_item([r["id"]], results_by_id)
            item["command"] = _GH_COMMANDS.get(r["id"],
                                               "Configure in repository settings.")
            plan["github"].append(item)
        else:
            plan["manual"].append(_rich_item([r["id"]], results_by_id))
    exists_cache: dict = {}
    for target, (scaffold, cids) in sorted(auto_by_target.items()):
        _t, template = scaffold
        if target not in exists_cache:
            exists_cache[target] = _target_nonempty(root, target)
        item = _rich_item(cids, results_by_id)
        item.update({"target": target, "template": template,
                     "exists": exists_cache[target]})
        plan["auto"].append(item)
    _prioritize(plan, focus.get("pillar_prioritize"), by_id)
    return plan


def _target_nonempty(root: Path, target: str) -> bool:
    try:
        auth = safe_io.acquire_root(root)
    except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
        return True  # unreadable root: treat as existing (never write)
    try:
        obs = safe_io.read_rooted_regular(auth, target, max_bytes=4096)
        return obs.state is safe_io.RepoReadState.OK and bool(obs.data.strip())
    finally:
        auth.close()


def apply_plan(root, plan, templates_dir=None, write=True):
    """Create-only scaffold application. Every entry keeps its complete criterion IDs.

    ``.gitignore`` is create-if-missing only (an existing file is a skipped proposal —
    never an automatic append/rewrite).
    """
    root = Path(root)
    templates_dir = Path(templates_dir or TEMPLATES_DIR)
    written, skipped = [], []
    if not write:
        return {"written": written, "skipped": skipped}
    try:
        auth = safe_io.acquire_root(root)
    except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
        return {"written": written,
                "skipped": [{"target": item["target"],
                             "criterion_ids": list(item["criterion_ids"])}
                            for item in plan["auto"]]}
    try:
        tpl_auth = safe_io.acquire_root(templates_dir)
        try:
            for item in plan["auto"]:
                target = item["target"]
                ids = list(item["criterion_ids"])
                if item["exists"]:
                    skipped.append({"target": target, "criterion_ids": ids})
                    continue
                template_obs = safe_io.read_rooted_regular(tpl_auth, item["template"])
                if template_obs.state is not safe_io.RepoReadState.OK:
                    skipped.append({"target": target, "criterion_ids": ids})
                    continue
                if safe_io.create_rooted_exclusive(auth, target, template_obs.data,
                                                   mode=0o644):
                    written.append({"target": target, "criterion_ids": ids})
                else:
                    skipped.append({"target": target, "criterion_ids": ids})
        finally:
            tpl_auth.close()
    finally:
        auth.close()
    return {"written": written, "skipped": skipped}


def worktree_dirty(root, *, git_collector=None):
    """True|False|None (indeterminate) dirty state via the safe Git authority."""
    from ..collectors.git import GitCollector
    collector = git_collector or GitCollector(root)
    obs = collector.status_porcelain()
    if git_collector is None:
        collector.close()
    if obs.state != "present":
        return None
    return bool(obs.value.strip())


def format_plan(plan, result=None, dry_run=True, notes=None):
    lines = [f"# ra1-fix plan{' (dry run — no files written)' if dry_run else ''}", ""]
    lines.append("## Auto-apply (safe config scaffolds)")
    if not plan["auto"]:
        lines.append("- (none)")
    written_targets = {w["target"] for w in (result or {}).get("written", [])}
    for item in plan["auto"]:
        if result and item["target"] in written_targets:
            state = "written"
        elif item["exists"]:
            state = "exists → skipped"
        else:
            state = "would create"
        ids = ", ".join(item["criterion_ids"])
        lines.append(f"- `{item['target']}` ({ids}) — {state}")
        lines.append(f"  - {item['rationale']}")
    if plan["propose"]:
        lines += ["", "## Propose (drafts for human review — NOT auto-written)"]
        for item in plan["propose"]:
            lines.append(f"- {item['id']}: {item['title']} — {item['rationale']}")
    if plan["github"]:
        lines += ["",
                  "## GitHub settings (apply manually, confirm first — never bundled with code)"]
        for item in plan["github"]:
            lines.append(f"- {item['id']}: {item['command']}")
    if plan["manual"]:
        lines += ["", "## Manual"]
        for item in plan["manual"]:
            lines.append(f"- {item['id']}: {item['title']} — {item['rationale']}")
    if notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in notes]
    lines += ["", "## Verify",
              "- Re-run `ra1 report` with the same settings to confirm the new level."]
    return "\n".join(lines) + "\n"


def _focus(args, registry):
    parsed = parse_instructions(getattr(args, "instructions", None),
                                {c.get("pillar", "") for c in registry})
    focus = {
        "include": set(getattr(args, "include", None) or []),
        "exclude": set(getattr(args, "exclude", None) or []),
        "pillar_exclude": parsed["pillar_exclude"],
        "pillar_prioritize": parsed["pillar_prioritize"],
    }
    notes = []
    if parsed["unsupported"]:
        notes.append("instructions not recognized as focus grammar; no filtering applied: "
                     f"{getattr(args, 'instructions', None)!r}")
    return focus, notes


# --------------------------------------------------------------------------- contracts
def empty_fix_contract(operation: str) -> dict:
    return {
        "operation": operation,
        "plan": {"auto": [], "propose": [], "github": [], "manual": []},
        "apply_result": {"written": [], "skipped": []},
        "verification": {
            "status": "not_run",
            "errors": [],
            "confirmed_ids": [],
            "unresolved": [],
            "regressions": [],
            "level": {"from": 0, "to": 0},
            "decision_successful": False,
        },
    }


def _dedupe_append(errors: list, category: str) -> None:
    if category not in errors:
        errors.append(category)


@dataclass
class FixRun:
    """One apply run's shared state (baseline/plan/apply/verify)."""

    baseline: object = None
    verified: object = None
    delta: object = None
    plan: dict = field(default_factory=lambda: {"auto": [], "propose": [], "github": [],
                                                "manual": []})
    apply_result: dict = field(default_factory=lambda: {"written": [], "skipped": []})
    errors: list = field(default_factory=list)
    confirmed_ids: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    regressions: list = field(default_factory=list)
    level_from: int = 0
    level_to: int = 0
    decision_successful: bool = False


def _verification(run: FixRun, status: str) -> dict:
    return {
        "status": status,
        "errors": list(run.errors),
        "confirmed_ids": sorted(run.confirmed_ids),
        "unresolved": sorted(run.unresolved, key=lambda u: u["id"]),
        "regressions": list(run.regressions),
        "level": {"from": run.level_from, "to": run.level_to},
        "decision_successful": run.decision_successful,
    }


def _plan_contract(run: FixRun) -> dict:
    return {"operation": "plan", "plan": run.plan,
            "apply_result": {"written": [], "skipped": []},
            "verification": _verification(run, "not_run")}


def _apply_contract(run: FixRun, status: str) -> dict:
    return {"operation": "apply", "plan": run.plan, "apply_result": run.apply_result,
            "verification": _verification(run, status)}


def run_fix(args) -> int:
    """The one apply contract: fresh baseline → create-only mutation → rescan → delta."""
    from .. import history
    from ..run import AnalyzeDependencies, AnalyzeOptions, analyze
    root = Path(args.project)
    registry = score.load_registry()
    focus, notes = _focus(args, registry)

    # --- transparency source (optional): imported reports never choose writes.
    source_report = None
    source_label = ""
    if getattr(args, "report", None):
        obs = safe_io.read_explicit_regular(args.report)
        if obs.state is not safe_io.RepoReadState.OK:
            sys.stderr.write("ra1 fix: --report file is not a readable regular file.\n")
            return 2
        try:
            candidate = parsers.strict_load_json(obs.data)
        except parsers.StrictJsonError:
            sys.stderr.write("ra1 fix: --report is not valid bounded JSON.\n")
            return 2
        schema = str(candidate.get("schema_version"))
        if schema == "1":
            errors = __import__("readiness.model", fromlist=["validate_legacy_fix_report_v1"]) \
                .validate_legacy_fix_report_v1(candidate)
            if errors:
                sys.stderr.write("ra1 fix: --report is not a valid schema-1 report.\n")
                return 2
            if getattr(args, "apply", False):
                sys.stderr.write("ra1 fix: schema-1 reports are plan-only transparency "
                                 "inputs; --apply is rejected.\n")
                return 2
            source_report, source_label = candidate, "schema1 (plan-only)"
        elif schema in ("2", "3"):
            from .. import model
            errors = model.validate_imported_report(candidate, schema)
            if errors:
                sys.stderr.write("ra1 fix: --report failed strict validation.\n")
                return 2
            source_report, source_label = candidate, f"schema{schema}"
    elif getattr(args, "latest", False):
        path = history.current_source_path(root, getattr(args, "reports_dir", None))
        source = history.admit_history_source("current", path)
        if source is None:
            sys.stderr.write("ra1 fix: no current reports root; run `ra1 report "
                             "--store-history` first.\n")
            return 2
        try:
            source_report, reason = history.resolve_latest(source, root)
        finally:
            source.close()
        if source_report is None:
            sys.stderr.write(f"ra1 fix: {reason}\n")
            return 2
        source_label = "latest stored report"

    applying = bool(getattr(args, "apply", False))
    if not applying:
        # Plan mode: fresh in-memory scan, read-only, one plan contract.
        baseline = analyze(root, AnalyzeOptions())
        plan = build_plan(root, baseline.to_dict(), registry=registry, focus=focus)
        run = FixRun(baseline=baseline, plan=plan)
        run.level_from = run.level_to = baseline.score.level if baseline.score else 0
        contract = _plan_contract(run)
        _emit(args, contract, plan, dry_run=True, notes=notes,
              source_label=source_label, source_report=source_report,
              baseline=baseline)
        return 0

    # --- apply: dirty/indeterminate refusal before any scan or write.
    dirty = worktree_dirty(root)
    if dirty is not False:
        sys.stderr.write("ra1 fix: working tree has uncommitted changes or Git status is "
                         "indeterminate; commit/stash first (there is no bypass).\n")
        return 1
    from .. import process
    proxy = None
    github_auth = None
    if getattr(args, "host_proxy", False):
        try:
            proxy = process.capture_host_proxy_authority(True, __import__("os").environ)
        except process.HostProxyError:
            sys.stderr.write("ra1: invalid host proxy environment\n")
            return 1
    if getattr(args, "github", False):
        github_auth = process.capture_github_auth_authority(__import__("os").environ)
    options = AnalyzeOptions(github=bool(getattr(args, "github", False)))
    deps = AnalyzeDependencies(host_proxy_authority=proxy,
                               github_auth_authority=github_auth)

    baseline = analyze(root, options, deps=deps)
    run = FixRun(baseline=baseline)
    run.level_from = baseline.score.level if baseline.score else 0
    baseline_dict = baseline.to_dict()
    provenance = baseline_dict["assessment_provenance"]["invocation"]
    if baseline_dict["detection"] and baseline_dict["detection"].get(
            "repository_indeterminate"):
        run.errors.append("repository_indeterminate")
        return _fail_apply(args, run, "baseline repository input is indeterminate")
    if not (provenance["static"]["collection_complete"]
            and provenance["git"]["collection_complete"]):
        run.errors.append("baseline_evidence_incomplete")
        return _fail_apply(args, run, "baseline static/Git evidence is incomplete")
    if getattr(args, "github", False) \
            and not provenance["github"]["collection_complete"]:
        run.errors.append("baseline_github_incomplete")
        return _fail_apply(args, run, "requested GitHub evidence is incomplete")

    # Source → baseline transparency (never selects writes).
    if source_report is not None:
        from ..collectors.git import GitCollector
        collector = GitCollector(root)
        comparison = history.delta(source_report, baseline_dict, git_collector=collector)
        collector.close()
        if not comparison["comparable"]:
            notes.append(f"source report not comparable: {comparison['reason']}")
        elif comparison["criteria_changes"]:
            notes.append("source report is stale (repository state drifted since it was "
                         "written)")

    plan = build_plan(root, baseline_dict, registry=registry, focus=focus)
    run.plan = plan
    run.apply_result = apply_plan(root, plan, write=True)

    # --- mandatory same-option rescan.
    verified = analyze(root, options, deps=deps)
    run.verified = verified
    verified_dict = verified.to_dict()
    run.level_to = verified.score.level if verified.score else 0
    verified_prov = verified_dict["assessment_provenance"]["invocation"]
    if not (verified_prov["static"]["collection_complete"]
            and verified_prov["git"]["collection_complete"]):
        run.errors.append("verified_evidence_incomplete")
        return _fail_apply(args, run, "verified static/Git evidence is incomplete")
    if getattr(args, "github", False) \
            and not verified_prov["github"]["collection_complete"]:
        run.errors.append("verified_github_incomplete")
        return _fail_apply(args, run, "requested verified GitHub evidence is incomplete")

    from ..collectors.git import GitCollector
    collector = GitCollector(root)
    delta = history.delta(baseline_dict, verified_dict, git_collector=collector)
    collector.close()
    run.delta = delta
    if not delta["comparable"]:
        run.errors.append("delta_incomparable:" + delta["reason"])
        return _fail_apply(args, run, "authoritative remediation delta is incomparable")

    # --- verification decision.
    verified_by_id = _result_by_id(verified_dict)
    written_ids = sorted({cid for w in run.apply_result["written"]
                          for cid in w["criterion_ids"]})
    for cid in written_ids:
        result = verified_by_id.get(cid)
        if result and result["status"] == "pass":
            run.confirmed_ids.append(cid)
        else:
            run.unresolved.append({
                "id": cid,
                "status": (result or {}).get("status", "unknown"),
                "reason_code": ((result or {}).get("decision_trace") or {})
                .get("reason_code", ""),
            })
    for cid in delta["newly_failing"]:
        run.regressions.append({"id": cid,
                                "from": next(c["from"] for c in delta["criteria_changes"]
                                             if c["id"] == cid),
                                "to": "fail"})
    for change in delta["criteria_changes"]:
        if change["from"] == "pass" and change["to"] in ("unknown", "skipped", "waived"):
            run.regressions.append({"id": change["id"], "from": "pass",
                                    "to": change["to"]})
    failed = (run.errors or run.unresolved or delta["newly_failing"]
              or run.regressions or run.level_to < run.level_from)
    if failed:
        if run.unresolved:
            run.errors.append("written_criteria_unresolved")
        if delta["newly_failing"] or run.regressions:
            run.errors.append("regression_detected")
        if run.level_to < run.level_from:
            run.errors.append("level_decreased")
        return _fail_apply(args, run, "verification failed")
    run.decision_successful = True
    contract = _apply_contract(run, "passed")
    _emit(args, contract, plan, dry_run=False, notes=notes, result=run.apply_result,
          run=run)
    return 0


def _fail_apply(args, run: FixRun, message: str) -> int:
    """Serialize a valid failed apply contract before exit 1 (never a rollback)."""
    run.decision_successful = False
    if not run.errors:
        run.errors.append("apply_failed")
    contract = _apply_contract(run, "failed")
    _emit(args, contract, run.plan, dry_run=False, result=run.apply_result, run=run,
          failure_note=message)
    if getattr(args, "format", "markdown") == "markdown":
        sys.stderr.write(f"ra1 fix: {message} (created files are retained; inspect or "
                         "revert with version control)\n")
    return 1


def _emit(args, contract, plan, *, dry_run, notes=None, result=None, source_label="",
          source_report=None, baseline=None, run: FixRun | None = None,
          failure_note: str = ""):
    import json
    if getattr(args, "format", "markdown") == "json":
        sys.stdout.write(json.dumps(contract, indent=2) + "\n")
        return
    out = format_plan(plan, result=result, dry_run=dry_run, notes=notes)
    if source_label:
        out += f"\n_Source: {source_label} (transparency only — never selects writes)._\n"
    if run is not None and run.delta and run.delta.get("comparable"):
        from .. import report as report_mod
        delta_payload = {"from": "before remediation", "to": "verified rescan",
                         **run.delta}
        out += "\n## Remediation delta (before remediation → verified rescan)\n\n"
        out += report_mod.render_history_diff(delta_payload)
        if run.confirmed_ids:
            out += "\nConfirmed fixed: " + ", ".join(sorted(run.confirmed_ids)) + ".\n"
        if run.unresolved:
            out += "Unresolved written: " + ", ".join(
                f"{u['id']} ({u['status']})" for u in run.unresolved) + ".\n"
        if run.regressions:
            out += "Regressions: " + ", ".join(
                f"{r['id']} ({r['from']}→{r['to']})" for r in run.regressions) + ".\n"
    if failure_note:
        out += f"\n**{failure_note}.** Created files are retained; inspect or revert " \
               "them with normal version-control tools.\n"
    sys.stdout.write(out)


def load_report(args, root):
    """Old explicit report path (transparency only); kept for schema1/2/3 dry runs."""
    path = Path(args.report)
    obs = safe_io.read_explicit_regular(path)
    if obs.state is not safe_io.RepoReadState.OK:
        return None
    try:
        return parsers.strict_load_json(obs.data)
    except parsers.StrictJsonError:
        return None
