"""The interview answer engine: one typed answer, one bounded policy edit, verified.

Every ``answer`` run rederives the gap and its internal target mapping from the live scan
— gap/choice IDs in argv are opaque engine constants, and no question/evidence/user prose
ever selects a value, path, or command. ``--apply`` requires a clean/known Git state and a
complete T0/T1 baseline, performs exactly one bounded create/merge on the reviewable
``.ra1`` policy inputs, rescans with the same offline scope, and reports honestly —
including answers that expose failures or lower the Level.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import safe_io
from .detect import VALID_PIN_TYPES

CONFIG_TARGET = ".ra1/config.json"
WAIVERS_TARGET = ".ra1/waivers.json"
_MINUTES_GAP = "config.ci_budget_minutes"


def empty_answer_contract(operation: str) -> dict:
    return {
        "operation": operation,
        "gap_id": "",
        "target_kind": "config",
        "target": CONFIG_TARGET,
        "apply_result": {"written": False, "created": False},
        "verification": {
            "status": "not_run",
            "errors": [],
            "gap_resolved": False,
            "status_changes": [],
            "waived_ids": [],
            "remaining_gap_ids": [],
            "score": {"from": {}, "to": {}},
            "decision_successful": False,
        },
    }


def _find_gap(report, gap_id: str):
    for gap in report.gaps:
        if gap.id == gap_id:
            return gap
    return None


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve(recording):
    """Validate a recording request against the live scan. Returns (plan, error).

    ``plan`` carries the engine-rederived mutation; nothing user-supplied reaches it.
    """
    gap = recording["gap"]
    choices = recording["choices"]
    minutes = recording["minutes"]
    if minutes is not None:
        if gap.id != _MINUTES_GAP:
            return None, "minutes_gap_mismatch"
        if gap.input_kind != "integer":
            return None, "stale_gap"
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            return None, "value_out_of_range"
        return {"kind": "config", "path": "ci_budget_minutes", "value": minutes}, ""
    if not choices:
        return None, "choice_required"
    choice_ids = {c["id"]: c for c in gap.choices}
    for choice in choices:
        if choice not in choice_ids:
            return None, "stale_choice"
        if choice_ids[choice]["effect"] != "record":
            return None, "choice_not_recordable"
    if not gap.recordable:
        return None, "gap_unrecordable"
    if gap.input_kind == "multi_choice":
        pass  # repeated --choice is allowed only for multi-enum surfaces gaps
    elif len(choices) > 1:
        return None, "multi_value_rejected"

    if gap.id == "config.loop_ready":
        return {"kind": "config", "path": "loop_ready",
                "value": choices[0] == "boolean.yes"}, ""
    if gap.id == "detect.project_type":
        if choices[0] not in VALID_PIN_TYPES:
            return None, "stale_choice"
        return {"kind": "config", "path": "detect.project_type", "value": choices[0]}, ""
    if gap.id == "detect.project_type.contested":
        if any(c not in VALID_PIN_TYPES for c in choices):
            return None, "stale_choice"
        return {"kind": "config", "path": "detect.surfaces",
                "value": sorted(set(choices))}, ""
    if gap.id.startswith("detect.app_type."):
        app_path = recording["app_paths"].get(gap.id)
        if app_path is None or choices[0] not in VALID_PIN_TYPES:
            return None, "stale_gap"
        return {"kind": "config", "path": f"detect.apps.{app_path}",
                "value": choices[0]}, ""
    if gap.id == "config.acdc.verify_command":
        command = recording["commands"].get(choices[0])
        if command is None:
            return None, "stale_choice"
        return {"kind": "config", "path": "acdc.verify_command", "value": command}, ""
    if gap.id == "config.ci_budget_minutes":
        return None, "value_out_of_range"  # budget needs --minutes, not --choice
    if gap.id == "capability.github" and choices[0] == "github.non_github_host":
        waivable = [r["id"] for r in recording["results"]
                    if r["id"] in set(gap.blocks)]
        return {"kind": "waiver", "ids": sorted(waivable)}, ""
    return None, "stale_gap"


def _apply_config(root_auth, dotted: str, value):
    """Merge one rederived dotted key into .ra1/config.json via the policy authority."""

    def mutate(parsed):
        data = dict(parsed or {})
        parts = dotted.split(".")
        node = data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
        return data

    return safe_io.merge_rooted_policy_json(root_auth, CONFIG_TARGET, mutate)


def _apply_waivers(root_auth, ids: list):
    """Append exact engine-authored waiver IDs to .ra1/waivers.json (no free-form text)."""

    def mutate(parsed):
        data = list(parsed or [])
        existing = {w.get("id") for w in data if isinstance(w, dict)}
        for cid in ids:
            if cid not in existing:
                data.append({"id": cid})
        return data

    return safe_io.merge_rooted_policy_json(root_auth, WAIVERS_TARGET, mutate)


def _policy_target_unignored(root, target: str) -> bool:
    from .collectors.git import GitCollector
    collector = GitCollector(root)
    obs = collector.check_ignore((target,))
    collector.close()
    if obs.state != "present":
        return False  # unreadable ignore state: refuse (fail closed)
    return not any(path == target for _s, _l, _p, path in obs.value)


def run_answer(args) -> int:
    """One gap, one value, one verified bounded edit. Emits one exact answer_contract."""
    from .run import AnalyzeOptions, analyze
    root = Path(args.project)
    applying = bool(getattr(args, "apply", False))

    baseline = analyze(root, AnalyzeOptions())
    baseline_dict = baseline.to_dict()
    gap = _find_gap(baseline, getattr(args, "gap_id", ""))
    if gap is None:
        return _emit_fail(args, "stale_gap", gap_id=getattr(args, "gap_id", ""))

    from .gaps import _verify_command_candidates
    static_commands = {cid: cmd for cmd, cid in
                       _verify_command_candidates(_static_for(root))}
    app_paths = {f"detect.app_type.{_sha(a.path)}": a.path
                 for a in (baseline.detection.apps if baseline.detection else [])}
    recording = {
        "gap": gap,
        "choices": list(getattr(args, "choice", None) or []),
        "minutes": getattr(args, "minutes", None),
        "app_paths": app_paths,
        "commands": static_commands,
        "results": baseline_dict["results"],
    }
    plan, error = _resolve(recording)
    if plan is None:
        return _emit_fail(args, error, gap_id=gap.id)
    target_kind = plan["kind"]
    target = CONFIG_TARGET if target_kind == "config" else WAIVERS_TARGET

    if not _policy_target_unignored(root, target):
        return _emit_fail(args, "policy_target_ignored", gap_id=gap.id,
                          target_kind=target_kind, target=target)

    if not applying:
        contract = empty_answer_contract("plan")
        contract.update({"gap_id": gap.id, "target_kind": target_kind,
                         "target": target})
        contract["verification"]["score"] = {
            "from": baseline_dict["score"], "to": baseline_dict["score"]}
        contract["verification"]["remaining_gap_ids"] = [
            g.id for g in baseline.gaps if g.id != gap.id]
        _emit(args, contract)
        return 0

    # --- apply: clean/known Git + complete T0/T1 baseline before any write.
    from .fix.recipes import worktree_dirty
    dirty = worktree_dirty(root)
    if dirty is not False:
        return _emit_fail(args, "worktree_not_clean", gap_id=gap.id,
                          target_kind=target_kind, target=target)
    provenance = baseline_dict["assessment_provenance"]["invocation"]
    if not (provenance["static"]["collection_complete"]
            and provenance["git"]["collection_complete"]):
        return _emit_fail(args, "baseline_evidence_incomplete", gap_id=gap.id,
                          target_kind=target_kind, target=target)

    try:
        root_auth = safe_io.acquire_root(root)
    except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
        return _emit_fail(args, "root_unavailable", gap_id=gap.id,
                          target_kind=target_kind, target=target)
    try:
        if target_kind == "config":
            created, _value = _apply_config(root_auth, plan["path"], plan["value"])
        else:
            created, _value = _apply_waivers(root_auth, plan["ids"])
    except (OSError, safe_io.RepositoryInputError):
        root_auth.close()
        return _emit_fail(args, "policy_merge_refused", gap_id=gap.id,
                          target_kind=target_kind, target=target)
    root_auth.close()

    # --- mandatory same-scope rescan.
    verified = analyze(root, AnalyzeOptions())
    verified_dict = verified.to_dict()
    verified_prov = verified_dict["assessment_provenance"]["invocation"]
    if not (verified_prov["static"]["collection_complete"]
            and verified_prov["git"]["collection_complete"]):
        return _emit_fail(args, "verified_evidence_incomplete", gap_id=gap.id,
                          target_kind=target_kind, target=target, written=True,
                          created=created)

    remaining = _find_gap(verified, gap.id)
    status_changes = []
    before_by_id = {r["id"]: r for r in baseline_dict["results"]}
    for r in verified_dict["results"]:
        before = before_by_id.get(r["id"])
        if before and before["status"] != r["status"]:
            status_changes.append({
                "id": r["id"], "from": before["status"], "to": r["status"],
                "from_reason_code": (before.get("decision_trace") or {})
                .get("reason_code", ""),
                "to_reason_code": (r.get("decision_trace") or {}).get("reason_code", ""),
            })
    waived_ids = sorted(plan["ids"]) if target_kind == "waiver" else []
    contract = empty_answer_contract("apply")
    contract.update({"gap_id": gap.id, "target_kind": target_kind, "target": target})
    contract["apply_result"] = {"written": True, "created": bool(created)}
    verification = contract["verification"]
    verification["status"] = "passed"
    verification["gap_resolved"] = remaining is None
    verification["status_changes"] = status_changes
    verification["waived_ids"] = waived_ids
    verification["remaining_gap_ids"] = [g.id for g in verified.gaps if g.id != gap.id]
    verification["score"] = {"from": baseline_dict["score"],
                             "to": verified_dict["score"]}
    verification["decision_successful"] = remaining is None or bool(waived_ids)
    _emit(args, contract)
    if remaining is not None and not waived_ids:
        sys.stderr.write("ra1 answer: the policy edit was recorded, but the gap persists "
                         "after rescan\n")
        return 1
    return 0


def _static_for(root):
    from .collectors.static import StaticCollector
    return StaticCollector(root)


def _emit_fail(args, category: str, *, gap_id: str = "", target_kind: str = "config",
               target: str = CONFIG_TARGET, written: bool = False,
               created: bool = False) -> int:
    contract = empty_answer_contract("apply" if getattr(args, "apply", False) else "plan")
    contract.update({"gap_id": gap_id, "target_kind": target_kind, "target": target})
    contract["apply_result"] = {"written": written, "created": created}
    contract["verification"]["status"] = "failed"
    contract["verification"]["errors"] = [category]
    _emit(args, contract)
    sys.stderr.write(f"ra1 answer: {category.replace('_', ' ')}\n")
    return 1


def _emit(args, contract) -> None:
    import json
    sys.stdout.write(json.dumps(contract, indent=2) + "\n")
