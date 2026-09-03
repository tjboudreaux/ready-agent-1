"""Criterion graduation benchmark: deterministic corpus metrics for manual gate graduation.

Eligibility is necessary, never sufficient: a criterion becomes gating only through a
maintainer-authored ADR and reviewed release change. This tool cannot mutate the registry
and cannot prove the required two-human label review — it reports that prerequisite
separately as ``review_status: "unverified_external"``.

Quantitative policy (all required): ≥100 reviewed cases, ≥30 expected ``pass``, ≥30
expected blocking (``fail``|``unknown``), ≥5 named ecosystems, pass precision ≥ 0.99,
exact four-status accuracy ≥ 0.95, and zero predicted ``pass`` for expected non-pass
cases tagged ``adversarial: true`` or severity ``high``|``critical``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(_ROOT / "engine"))

_STATUSES = ("pass", "fail", "unknown", "skipped")
_BLOCKING = ("fail", "unknown")
_ECOSYSTEMS = ("python", "node", "go", "rust", "java", "ruby", "php", "swift", "monorepo",
               "docs-only", "infra", "mixed")
_SEVERITIES = ("low", "medium", "high", "critical")

_MIN_CASES = 100
_MIN_EXPECTED_PASS = 30
_MIN_EXPECTED_BLOCKING = 30
_MIN_ECOSYSTEMS = 5
_MIN_PRECISION = 0.99
_MIN_ACCURACY = 0.95

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "corpus"


def _error(errors: list, message: str) -> None:
    errors.append(message)


def validate_labels(data, *, fixture_root: Path = FIXTURE_ROOT) -> list[str]:
    """Bounded label-corpus validation. Returns authored error strings (empty when valid)."""
    errors: list[str] = []
    if not isinstance(data, dict) or set(data.keys()) != {"schema_version", "cases"}:
        return ["label file root must be {schema_version, cases}"]
    if data["schema_version"] != "1":
        return ["schema_version must be '1'"]
    cases = data["cases"]
    if not isinstance(cases, list):
        return ["cases must be a list"]
    seen_ids = set()
    for i, case in enumerate(cases):
        ctx = f"cases[{i}]"
        if not isinstance(case, dict):
            _error(errors, f"{ctx}: not an object")
            continue
        required = {"id", "criterion_id", "fixture", "ecosystem", "expected",
                    "adversarial", "severity"}
        if set(case.keys()) != required:
            _error(errors, f"{ctx}: keys not exact {sorted(required)}")
            continue
        if not isinstance(case["id"], str) or not case["id"]:
            _error(errors, f"{ctx}: id invalid")
        elif case["id"] in seen_ids:
            _error(errors, f"{ctx}: duplicate id {case['id']!r}")
        else:
            seen_ids.add(case["id"])
        if not isinstance(case["criterion_id"], str) or "." not in case["criterion_id"]:
            _error(errors, f"{ctx}: criterion_id invalid")
        fixture = case["fixture"]
        if not isinstance(fixture, str) or fixture.startswith("/") or "\\" in fixture \
                or ".." in fixture.split("/"):
            _error(errors, f"{ctx}: fixture must be a repository-relative safe path")
        else:
            target = fixture_root / fixture
            if not target.is_dir():
                _error(errors, f"{ctx}: fixture root does not exist: {fixture}")
        if case["ecosystem"] not in _ECOSYSTEMS:
            _error(errors, f"{ctx}: unknown ecosystem {case['ecosystem']!r}")
        if case["expected"] not in _STATUSES:
            _error(errors, f"{ctx}: unknown expected status {case['expected']!r}")
        if type(case["adversarial"]) is not bool:
            _error(errors, f"{ctx}: adversarial must be a bool")
        if case["severity"] not in _SEVERITIES:
            _error(errors, f"{ctx}: unknown severity {case['severity']!r}")
    return errors


def _evaluate_case(case, *, fixture_root: Path) -> str | None:
    """Run the check against the fixture. Returns the predicted status, or None on error."""
    import sys as _sys
    root = Path(__file__).resolve().parent.parent
    if str(root / "engine") not in _sys.path:
        _sys.path.insert(0, str(root / "engine"))
    from readiness import score
    from readiness.collectors.git import GitCollector
    from readiness.collectors.github import GithubCollector
    from readiness.collectors.static import StaticCollector
    from readiness.context import Context
    from readiness.detect import detect
    from readiness.model import App
    from readiness.process import BoundedProcessResult, ProcessState

    registry = score.load_registry()
    crit = next((c for c in registry if c["id"] == case["criterion_id"]), None)
    if crit is None:
        return None
    fixture = fixture_root / case["fixture"]
    static = StaticCollector(fixture)
    detection = detect(fixture, static)
    app = detection.apps[0] if detection.apps else App(path=".")

    def _no_git(args):
        return BoundedProcessResult(ProcessState.NONZERO, returncode=128)

    ctx = Context(root=fixture, detection=detection, static=static,
                  git=GitCollector(fixture, runner=_no_git),
                  github=GithubCollector(fixture), app=app, options={})
    check = score._resolve_check(crit["check"])
    try:
        verdict = check(ctx)
    except Exception:
        return None
    return verdict.status.value


def evaluate_candidate(criterion_id: str, cases: list, *, fixture_root: Path = FIXTURE_ROOT,
                       evaluate_case=None) -> dict:
    """The deterministic eligibility artifact for one candidate criterion."""
    evaluate_case = evaluate_case or _evaluate_case
    own = [c for c in cases if c["criterion_id"] == criterion_id]
    confusion = {p: {e: 0 for e in _STATUSES} for p in _STATUSES}
    per_ecosystem: dict = {}
    offending = []
    expected_pass = expected_blocking = 0
    for case in own:
        expected = case["expected"]
        if expected == "pass":
            expected_pass += 1
        if expected in _BLOCKING:
            expected_blocking += 1
        predicted = evaluate_case(case, fixture_root=fixture_root)
        if predicted is None:
            offending.append({"id": case["id"], "reason": "evaluation error"})
            continue
        confusion[predicted][expected] += 1
        eco = per_ecosystem.setdefault(case["ecosystem"], {"total": 0, "correct": 0})
        eco["total"] += 1
        if predicted == expected:
            eco["correct"] += 1
        if predicted == "pass" and expected != "pass" and (
                case["adversarial"] or case["severity"] in ("high", "critical")):
            offending.append({"id": case["id"], "reason": "false pass on "
                              f"{'adversarial' if case['adversarial'] else case['severity']}"
                              f" case (expected {expected})"})
    predicted_pass = sum(confusion["pass"].values())
    true_pass = confusion["pass"]["pass"]
    total = sum(sum(row.values()) for row in confusion.values())
    correct = sum(confusion[s][s] for s in _STATUSES)
    precision = (true_pass / predicted_pass) if predicted_pass else 0.0
    accuracy = (correct / total) if total else 0.0
    ecosystems = sorted(per_ecosystem)
    reasons = []
    if len(own) < _MIN_CASES:
        reasons.append(f"cases {len(own)} < {_MIN_CASES}")
    if expected_pass < _MIN_EXPECTED_PASS:
        reasons.append(f"expected pass {expected_pass} < {_MIN_EXPECTED_PASS}")
    if expected_blocking < _MIN_EXPECTED_BLOCKING:
        reasons.append(f"expected blocking {expected_blocking} < {_MIN_EXPECTED_BLOCKING}")
    if len(ecosystems) < _MIN_ECOSYSTEMS:
        reasons.append(f"ecosystems {len(ecosystems)} < {_MIN_ECOSYSTEMS}")
    if not predicted_pass or precision < _MIN_PRECISION:
        reasons.append(f"pass precision {round(precision, 4)} < {_MIN_PRECISION}")
    if not total or accuracy < _MIN_ACCURACY:
        reasons.append(f"accuracy {round(accuracy, 4)} < {_MIN_ACCURACY}")
    if offending:
        reasons.append(f"{len(offending)} offending case(s)")
    return {
        "criterion_id": criterion_id,
        "eligible": not reasons,
        "reasons": reasons,
        "review_status": "unverified_external",
        "metrics": {
            "cases": len(own),
            "expected_pass": expected_pass,
            "expected_blocking": expected_blocking,
            "ecosystems": ecosystems,
            "pass_precision": round(precision, 4),
            "accuracy": round(accuracy, 4),
            "confusion": confusion,
            "per_ecosystem": {
                eco: {"total": v["total"], "correct": v["correct"],
                      "accuracy": round(v["correct"] / v["total"], 4) if v["total"] else 0.0}
                for eco, v in sorted(per_ecosystem.items())},
        },
        "offending_cases": offending,
    }


def _load(path) -> object:
    from readiness import parsers
    try:
        return parsers.strict_load_json(Path(path).read_text(encoding="utf-8"),
                                        require_object=True)
    except (OSError, parsers.StrictJsonError) as exc:
        raise SystemExit(f"label file unreadable: {exc}") from exc


def main(argv=None, *, labels_path=None, fixture_root=FIXTURE_ROOT,
         evaluate_case=None) -> int:
    """CLI entrypoint with keyword-only test injection (the same philosophy as
    ``AnalyzeDependencies``): callers may supply a labels file, a fixture root, and a
    deterministic ``evaluate_case`` stub without changing the CLI grammar."""
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) == 2 and argv[0] == "--validate":
        data = _load(argv[1])
        errors = validate_labels(data, fixture_root=fixture_root)
        artifact = {"mode": "validate", "valid": not errors, "errors": errors,
                    "review_status": "unverified_external"}
        print(json.dumps(artifact, indent=2))
        return 0 if not errors else 2
    if len(argv) == 2 and argv[0] == "--candidate":
        if labels_path is None:
            labels_path = Path(__file__).resolve().parent / "criterion_labels.json"
        data = _load(labels_path)
        errors = validate_labels(data, fixture_root=fixture_root)
        if errors:
            print(json.dumps({"mode": "candidate", "valid": False, "errors": errors,
                              "review_status": "unverified_external"}, indent=2))
            return 2
        artifact = evaluate_candidate(argv[1], data["cases"], fixture_root=fixture_root,
                                      evaluate_case=evaluate_case)
        print(json.dumps(artifact, indent=2))
        return 0 if artifact["eligible"] else 1
    sys.stderr.write("usage: criterion_benchmark --validate <labels.json>\n"
                     "       criterion_benchmark --candidate <exact-criterion-id>\n")
    return 2


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
