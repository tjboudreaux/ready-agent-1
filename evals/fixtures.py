"""Labeled-fixture evals: per-criterion false-positive / false-negative rates.

Each fixture under ``evals/fixtures/*.json`` is a manifest — a files dict (materialized to a
temp dir at run time, mirroring ``tests/_util.make_repo``) plus expected per-criterion verdicts.
Fixtures are JSON manifests rather than literal repo trees so their contents never leak into
the parent repo's own readiness globs.

Classification per (criterion, fixture):
- expected ``fail`` but engine says ``pass``  -> **false positive** (undeserved credit — the
  dangerous direction; a wrong pass inflates the score)
- expected ``pass`` but engine says ``fail``  -> **false negative**
- any other mismatch (skipped/unknown/waived) -> **applicability error** (a wrong skip also
  inflates the score)

``python3 -m evals.fixtures`` runs the corpus, prints a JSON summary, and exits non-zero if
``evals/thresholds.json`` is breached or a gating criterion has no fixture coverage. This is
the graduation gate: a criterion only graduates advisory->gating once it is covered here with
zero FP/FN (see references/pillars.md).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "engine") not in sys.path:
    sys.path.insert(0, str(_REPO / "engine"))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.json"

SCOREABLE = {"pass", "fail"}


def load_fixtures(fixtures_dir=None):
    """Load every ``*.json`` manifest in the fixtures dir, sorted by name."""
    d = Path(fixtures_dir) if fixtures_dir else FIXTURES_DIR
    out = []
    for p in sorted(d.glob("*.json")):
        with open(p, encoding="utf-8") as fh:
            fx = json.load(fh)
        fx.setdefault("name", p.stem)
        out.append(fx)
    return out


def materialize(fixture, dest):
    """Write the fixture's files dict into ``dest``; optionally git-init for T1 coverage."""
    dest = Path(dest)
    for rel, content in fixture.get("files", {}).items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if fixture.get("git_init"):
        for args in (["git", "init", "-q"],
                     ["git", "add", "-A"],
                     ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@eval",
                      "commit", "-q", "-m", "fixture"]):
            subprocess.run(args, cwd=dest, capture_output=True, timeout=30, check=True)


def compare(expected, actual):
    """Pure classification of expected vs actual statuses for one fixture.

    Returns {criterion_id: "correct" | "fp" | "fn" | "applicability"} for every expected id.
    """
    out = {}
    for cid, exp in expected.items():
        act = actual.get(cid)
        if act == exp:
            out[cid] = "correct"
        elif exp == "fail" and act == "pass":
            out[cid] = "fp"
        elif exp == "pass" and act == "fail":
            out[cid] = "fn"
        else:
            out[cid] = "applicability"
    return out


def run_fixture(fixture):
    """Materialize, run the deterministic engine (no GitHub), classify against expectations."""
    from readiness.run import analyze

    with tempfile.TemporaryDirectory(prefix="ra1-eval-") as tmp:
        materialize(fixture, tmp)
        report = analyze(tmp, {"no_github": True})
    actual = {r.id: r.status.value for r in report.results}
    result = {
        "name": fixture["name"],
        "classification": compare(fixture.get("expected", {}), actual),
        "actual": actual,
        "level": report.score.level,
    }
    want_detect = (fixture.get("detect") or {}).get("project_type")
    if want_detect is not None:
        result["detect_ok"] = report.detection.project_type == want_detect
        result["detected"] = report.detection.project_type
    want_level = fixture.get("expected_level")
    if want_level is not None:
        result["level_ok"] = report.score.level == want_level
    return result


def score_fixtures(fixtures=None):
    """Run the corpus and aggregate per-criterion FP/FN/applicability counts."""
    fixtures = fixtures if fixtures is not None else load_fixtures()
    per = {}
    runs = []
    detection_errors = []
    level_errors = []
    for fx in fixtures:
        r = run_fixture(fx)
        runs.append(r)
        for cid, cls in r["classification"].items():
            slot = per.setdefault(cid, {"fp": 0, "fn": 0, "applicability": 0, "n": 0})
            slot["n"] += 1
            if cls in ("fp", "fn", "applicability"):
                slot[cls] += 1
        if r.get("detect_ok") is False:
            detection_errors.append({"fixture": r["name"], "detected": r["detected"]})
        if r.get("level_ok") is False:
            level_errors.append({"fixture": r["name"], "level": r["level"]})
    totals = {
        "fixtures": len(runs),
        "expectations": sum(s["n"] for s in per.values()),
        "fp": sum(s["fp"] for s in per.values()),
        "fn": sum(s["fn"] for s in per.values()),
        "applicability": sum(s["applicability"] for s in per.values()),
    }
    return {"per_criterion": per, "totals": totals, "runs": runs,
            "detection_errors": detection_errors, "level_errors": level_errors}


def load_thresholds(path=None):
    with open(Path(path) if path else THRESHOLDS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def check_thresholds(summary, thresholds, registry):
    """Return a list of human-readable violations (empty = gate passes)."""
    violations = []
    t = summary["totals"]
    n = max(t["expectations"], 1)
    if t["fp"] / n > thresholds.get("max_fp_rate", 0.0):
        violations.append(f"false-positive rate {t['fp']}/{n} exceeds max_fp_rate")
    if t["fn"] / n > thresholds.get("max_fn_rate", 0.0):
        violations.append(f"false-negative rate {t['fn']}/{n} exceeds max_fn_rate")
    if t["applicability"] / n > thresholds.get("max_applicability_rate", 0.0):
        violations.append(f"applicability-error rate {t['applicability']}/{n} exceeds max")
    covered = set(summary["per_criterion"])
    for crit in registry:
        if crit.get("gating", True) and crit["id"] not in covered:
            violations.append(f"gating criterion '{crit['id']}' has no fixture coverage")
    for cid, slot in summary["per_criterion"].items():
        if slot["fp"] or slot["fn"]:
            violations.append(f"criterion '{cid}': fp={slot['fp']} fn={slot['fn']}")
    violations.extend(f"detection mismatch in fixture '{d['fixture']}' (got {d['detected']})"
                      for d in summary["detection_errors"])
    violations.extend(f"level mismatch in fixture '{l['fixture']}' (got {l['level']})"
                      for l in summary["level_errors"])
    return violations


def main(argv=None):  # pragma: no cover - CLI shell around tested pieces
    from readiness.score import load_registry

    summary = score_fixtures()
    violations = check_thresholds(summary, load_thresholds(), load_registry())
    out = {"per_criterion": summary["per_criterion"], "totals": summary["totals"],
           "violations": violations}
    print(json.dumps(out, indent=2))
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
