"""Documentation checks (mostly repository-scoped)."""
from __future__ import annotations

from datetime import datetime

from ._helpers import adep, aglob, ev, failed, passed, skipped, unknown


def readme(ctx):
    hits = ctx.static.glob(["README.md", "README.rst", "README.txt", "readme.md"])
    if not hits:
        return failed("No README found.")
    text = ctx.static.read(hits[0]) or ""
    if len(text) < 300:
        return failed(f"README too thin ({len(text)} chars).")
    if text.count("#") < 2 and "```" not in text:
        return failed("README lacks sections or examples.")
    return passed(f"README present and substantive ({len(text)} chars).", [ev("README", source=hits[0])])


def agents_md(ctx):
    if ctx.static.glob(["AGENTS.md"]):
        return passed("AGENTS.md present at repo root.", [ev("AGENTS.md", source="AGENTS.md")])
    return failed("Missing root AGENTS.md (agent briefing file).")


def agents_md_validation(ctx):
    text = ctx.static.read("AGENTS.md")
    if not text:
        return failed("AGENTS.md unreadable.")
    headings = text.count("\n#") + (1 if text.startswith("#") else 0)
    lines = text.count("\n") + 1
    if headings < 2:
        return failed("AGENTS.md lacks structure (fewer than 2 headings).")
    if lines > 400:
        return failed(f"AGENTS.md too long ({lines} lines; keep it high-signal).")
    return passed(f"AGENTS.md is well-formed ({headings} sections, {lines} lines).", [ev("AGENTS.md structure", source="AGENTS.md")])


def skills(ctx):
    artifacts = ctx.static.glob([
        "skills/*/SKILL.md", "SKILL.md", ".claude/skills/*/SKILL.md",
        ".agents/skills/*/SKILL.md", "plugins/*/skills/*/SKILL.md", ".claude-plugin/plugin.json",
    ])
    if not artifacts:
        return failed("No agent skill artifacts (skills/*/SKILL.md or root SKILL.md).")
    evidence = [ev(f"skill artifact {artifacts[0]}", source=artifacts[0])]
    if ctx.github.available and "agent-skills" in ctx.github.topics():
        evidence.append(ev("repo topic 'agent-skills' (published)", tier="T2"))
    return passed(f"Provides reusable agent skills ({len(artifacts)} artifact(s)).", evidence)


def doc_freshness(ctx):
    ref = ctx.git.most_recent_commit_iso()
    if not ref:
        return unknown("No git history to assess documentation freshness.")
    checked = []
    for d in ("README.md", "AGENTS.md", "docs/README.md"):
        if ctx.static.glob([d]):
            dt = ctx.git.file_last_commit_iso(d)
            if dt:
                checked.append((d, dt))
    if not checked:
        return unknown("No tracked key docs to assess.")
    try:
        ref_dt = datetime.fromisoformat(ref)
        stale = [d for d, dt in checked if abs((ref_dt - datetime.fromisoformat(dt)).days) > 180]
    except ValueError:
        return unknown("Unparseable commit timestamps.")
    if stale:
        return failed(f"Docs stale (>180 days before latest commit): {', '.join(stale)}")
    return passed("Key docs updated within 180 days of the latest commit.", [ev("doc vs latest-commit dates", tier="T1")])


def api_schema_docs(ctx):
    hits = aglob(ctx, ["openapi.yaml", "openapi.json", "openapi/*", "swagger.yaml", "swagger.json",
                       "**/openapi*.y*ml", "**/openapi*.json", "**/schema.graphql", "**/*.graphql"])
    if hits:
        return passed(f"API schema present: {hits[0]}", [ev("API schema", source=hits[0])])
    dep = adep(ctx, ["drf-spectacular", "fastapi", "springdoc-openapi", "strawberry-graphql",
                     "@nestjs/swagger", "graphql"])
    if dep:
        return passed(f"API framework with schema generation: {dep}")
    return failed("No API schema docs (OpenAPI/Swagger/GraphQL).")
