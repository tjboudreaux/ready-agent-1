#!/usr/bin/env python3
"""Thin entrypoint for the coverage gate.

All logic lives in ``engine/readiness/coverage_gate.py`` so it stays unit-tested; this
script only puts the engine on the path and delegates to :func:`main`. See
``docs/PLAN-roadmap-factory-parity.md`` Phase 0.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from readiness.coverage_gate import main  # noqa: E402

if __name__ == "__main__":  # pragma: no cover - thin entrypoint
    raise SystemExit(main())
