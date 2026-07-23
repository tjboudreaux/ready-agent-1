"""Documentation checks (mostly repository-scoped)."""
from __future__ import annotations

import re
from datetime import datetime

from ..parsers import load_jsonc
from ._helpers import PLACEHOLDER_RE, adep, aglob, ev, failed, filled, passed, skipped, tool_invoked, unknown


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


# --- Factory-parity documentation depth (advisory; T0) ------------------------------

_DOCGEN_DEPS = ["typedoc", "sphinx", "mkdocs", "mkdocs-material", "docusaurus",
                "@docusaurus/core", "redoc-cli", "@redocly/cli", "pdoc", "jsdoc", "compodoc"]
_DOCGEN_CFG = ["mkdocs.yml", "mkdocs.yaml", "docusaurus.config.*", "typedoc.json",
               "docs/conf.py", "**/conf.py", ".redocly.yaml", "redocly.yaml"]


def auto_generation(ctx):
    cfg = ctx.static.glob(_DOCGEN_CFG)
    tool = adep(ctx, _DOCGEN_DEPS) or (cfg[0] if cfg else None)
    if not tool:
        return failed("No documentation generator (typedoc/sphinx/mkdocs/docusaurus).")
    wiring = tool_invoked(ctx, _DOCGEN_DEPS)
    if wiring:
        return passed(f"Documentation generation wired: {tool}.",
                      [ev("doc generator", source=str(tool)), ev("invocation", source=wiring)])
    return failed(f"Doc generator present ({tool}) but not wired into CI/build.")


def agents_md_ci_validation(ctx):
    if not ctx.static.glob(["AGENTS.md"]):
        return failed("No AGENTS.md to validate.")
    for f in ctx.static.glob([".github/workflows/*.yml", ".github/workflows/*.yaml",
                              ".pre-commit-config.yaml", ".pre-commit-config.yml"]):
        if "agents.md" in (ctx.static.read(f) or "").lower():
            return passed(f"AGENTS.md validated in CI: {f}", [ev("AGENTS.md CI check", source=f)])
    return failed("AGENTS.md present but no CI job validates its commands.")


_ARCH_FILES = ["docs/architecture*.md", "ARCHITECTURE.md", "docs/adr/**", "docs/decisions/**",
               "doc/architecture*.md", "CONTEXT.md", "docs/design*.md"]


def architecture_doc(ctx):
    """Architecture documentation must be substantive (>=200 chars), not an empty stub."""
    for f in ctx.static.glob(_ARCH_FILES):
        if len(ctx.static.read(f) or "") >= 200:
            return passed(f"Architecture documentation present: {f}", [ev("architecture doc", source=f)])
    return failed("No architecture documentation (ARCHITECTURE.md / docs/architecture / ADRs).")


# --- DORA / AI-capability documentation proxies (advisory) ---------------------------

_AI_HEADING_RE = re.compile(
    r"(?im)^#{1,4}\s.*\b(AI (policy|usage|stance)|agent policy)\b"
)
_AI_TOOL_RE = re.compile(r"(?i)\b(copilot|claude|cursor|codex|gemini|agent)\b")
_AI_PERM_RE = re.compile(r"(?i)\b(allowed|prohibited|must not|may use|approved)\b")


def _text_filled(text, min_chars=40) -> bool:
    stripped = (text or "").strip()
    if not stripped or len(stripped) < min_chars:
        return False
    if PLACEHOLDER_RE.search(text or ""):
        return False
    return True


def _ai_signal(text) -> bool:
    return bool(_AI_TOOL_RE.search(text or "") or _AI_PERM_RE.search(text or ""))


def _heading_sections(text, heading_re):
    for m in heading_re.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        if line_end < 0:
            line_end = len(text)
        line = text[line_start:line_end]
        level = len(line) - len(line.lstrip("#"))
        body_start = line_end + 1 if line_end < len(text) else len(text)
        rest = text[body_start:]
        next_h = re.compile(rf"(?m)^#{{1,{level}}}\s")
        m2 = next_h.search(rest)
        body = rest[: m2.start()] if m2 else rest
        yield body


def ai_stance(ctx):
    """Pass on a filled AI policy artifact or AGENTS/CONTRIBUTING heading section
    that includes a tool/agent or permission signal."""
    accepted = ("AI_POLICY.md", "docs/ai-policy.md", "AGENTS.md", "CONTRIBUTING.md")
    seen_invalid = []
    for path in ("AI_POLICY.md", "docs/ai-policy.md"):
        if not ctx.static.glob([path]):
            continue
        ok, rationale = filled(ctx, path, "AI policy")
        text = ctx.static.read(path) or ""
        if ok and _ai_signal(text):
            return passed(rationale, [ev("AI stance", source=path, tier="T0")])
        seen_invalid.append(path)
    for path in ("AGENTS.md", "CONTRIBUTING.md"):
        text = ctx.static.read(path)
        if not text:
            continue
        for body in _heading_sections(text, _AI_HEADING_RE):
            if _text_filled(body) and _ai_signal(body):
                return passed(
                    f"AI stance section present in {path}.",
                    [ev("AI stance", source=path, tier="T0")],
                )
            seen_invalid.append(path)
            break
    if seen_invalid:
        return failed(
            "AI stance artifact present but thin/empty or missing tool/permission signal: "
            f"{', '.join(seen_invalid)}. Accepted locations: {', '.join(accepted)}."
        )
    return failed(
        "No filled AI stance policy "
        f"(accepted: {', '.join(accepted)})."
    )


def _mcp_servers_ok(data) -> bool:
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return False
    for cfg in servers.values():
        if not isinstance(cfg, dict):
            continue
        if str(cfg.get("command") or "").strip() or str(cfg.get("url") or "").strip():
            return True
    return False


def _llms_has_ref(text) -> bool:
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.search(r"https?://", s) or "/" in s or s.endswith(".md"):
            return True
    return False


def machine_context(ctx):
    """Pass on MCP config with a real server entry, or a filled root llms.txt with URLs/paths.

    AGENTS.md alone does not pass.
    """
    mcp_paths = [".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json", ".gemini/settings.json"]
    for path in mcp_paths:
        if not ctx.static.glob([path]):
            continue
        data = load_jsonc(ctx.root / path)
        if _mcp_servers_ok(data):
            return passed(
                f"MCP machine context configured: {path}.",
                [ev("MCP config", source=path, tier="T0")],
            )
    if ctx.static.glob(["llms.txt"]):
        ok, rationale = filled(ctx, "llms.txt", "llms.txt")
        text = ctx.static.read("llms.txt") or ""
        if ok and _llms_has_ref(text):
            return passed(rationale, [ev("llms.txt", source="llms.txt", tier="T0")])
    return failed(
        "No machine-readable context (MCP config with command/url server, or filled llms.txt "
        "with URL/path lines). AGENTS.md alone does not pass."
    )
