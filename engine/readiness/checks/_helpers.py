"""Shared helpers for check functions.

A check is ``fn(ctx) -> Verdict``. It reads from collectors on the context and returns a
Verdict with cited evidence. ``aglob``/``adep`` look in the application directory first and
fall back to the repo root so shared monorepo config at the root still counts.
"""
from __future__ import annotations

import re

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


_SOURCE_EXTS = ("py", "js", "ts", "tsx", "jsx", "go", "java", "rb", "cs")


def agrep(ctx, patterns, exts=_SOURCE_EXTS, limit=400):
    """Return the first app source file whose text matches any regex in ``patterns``, else None.

    Used for *wiring* evidence: a dependency or config file proves something is available, but only
    a usage site in source proves it is actually wired in. Searches the app dir, falling back to the
    repo root for single-app repos, and skips vendored/build dirs via the collector's ignore list."""
    compiled = [re.compile(p) for p in patterns]
    coll = ctx.app_static()
    globs = [f"**/*.{e}" for e in exts]
    files = coll.glob(globs)
    if not files and ctx.app.path != ".":
        coll = ctx.static
        files = coll.glob(globs)
    for f in files[:limit]:
        text = coll.read(f) or ""
        if any(r.search(text) for r in compiled):
            return f
    return None
