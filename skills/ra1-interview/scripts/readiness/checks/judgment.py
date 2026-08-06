"""Agent-graded (T4) judgment criteria.

These never affect the deterministic score. The engine cannot assess them, so it returns UNKNOWN
to signal that the agent layer should render qualitative commentary. Suppression (``off``) is
applied by the scorer via the judgments config *before* this runs, so a silenced judgment never
reaches here. See ``engine/readiness/judgments.py``.
"""
from __future__ import annotations

from ._helpers import unknown


def assess(ctx):
    return unknown("Agent-graded judgment; assess qualitatively (advisory, never scored).")
