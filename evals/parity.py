"""Offline parity harness vs Factory.ai's public readiness-report shape.

Fixtures whose manifest carries a ``parity`` block declare, per criterion, the verdict
Factory's public FastAPI/Express readiness reports would give for an equivalently-shaped
repo. The expectations are **hand-curated and offline by design** — Factory's published
numbers conflict with each other (8 pillars/60+ vs "100+ signals"), so we do not fetch or
scrape at eval time; we compare against a reviewed snapshot and report agreement with cited
divergences instead of claiming exact parity.

``python3 -m evals.parity`` prints a JSON summary and exits non-zero when the agreement
rate drops below ``parity_min_agree`` in ``evals/thresholds.json``.
"""
from __future__ import annotations

import json
import sys

from .fixtures import load_fixtures, load_thresholds, run_fixture


def compare_parity(parity_criteria, actual):
    """Pure comparison: {criterion: factory_verdict} vs engine actual statuses."""
    agree, diverge = [], []
    for cid, factory in parity_criteria.items():
        ours = actual.get(cid)
        if ours == factory:
            agree.append(cid)
        else:
            diverge.append({"criterion": cid, "factory": factory, "engine": ours})
    return agree, diverge


def score_parity(fixtures=None):
    fixtures = fixtures if fixtures is not None else load_fixtures()
    results = []
    agree_total = diverge_total = 0
    for fx in fixtures:
        parity = fx.get("parity")
        if not parity:
            continue
        actual = run_fixture(fx)["actual"]
        agree, diverge = compare_parity(parity.get("criteria", {}), actual)
        agree_total += len(agree)
        diverge_total += len(diverge)
        results.append({"fixture": fx["name"], "source": parity.get("source", ""),
                        "agree": len(agree), "diverge": len(diverge), "divergences": diverge})
    n = agree_total + diverge_total
    return {"fixtures": results, "agree": agree_total, "diverge": diverge_total,
            "agree_rate": (agree_total / n) if n else None}


def main(argv=None):  # pragma: no cover - CLI shell around tested pieces
    summary = score_parity()
    thresholds = load_thresholds()
    floor = thresholds.get("parity_min_agree", 0.85)
    ok = summary["agree_rate"] is None or summary["agree_rate"] >= floor
    summary["ok"] = ok
    print(json.dumps(summary, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
