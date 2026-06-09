"""Shared helpers for check functions.

A check is ``fn(ctx) -> Verdict``. It reads from collectors on the context and returns a
Verdict with cited evidence. ``aglob``/``adep`` look in the application directory first and
fall back to the repo root so shared monorepo config at the root still counts.
"""
from __future__ import annotations

from ..model import Evidence, Status, Verdict


def ev(summary, source="", tier="T0", detail=""):
    return Evidence(summary=summary, source=source, tier=tier, detail=detail)


def passed(rationale, evidence=None):
    return Verdict(Status.PASS, rationale, evidence or [])


def failed(rationale, evidence=None):
    return Verdict(Status.FAIL, rationale, evidence or [])


def unknown(rationale, evidence=None):
    return Verdict(Status.UNKNOWN, rationale, evidence or [])


def skipped(rationale, evidence=None):
    return Verdict(Status.SKIPPED, rationale, evidence or [])


def aglob(ctx, patterns):
    """Glob within the app dir; fall back to repo root for shared config."""
    hits = ctx.app_static().glob(patterns)
    if hits or ctx.app.path == ".":
        return hits
    return ctx.static.glob(patterns)


def adep(ctx, names):
    found = ctx.app_static().has_dep(names)
    if found:
        return found
    if ctx.app.path != ".":
        return ctx.static.has_dep(names)
    return None


def atool(ctx, name):
    return ctx.app_static().has_tool_config(name) or (
        ctx.app.path != "." and ctx.static.has_tool_config(name)
    )
