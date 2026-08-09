"""Documentation checks (mostly repository-scoped)."""
from __future__ import annotations

import re
from datetime import datetime

from .. import parsers, safe_io
from ._helpers import (
    PLACEHOLDER_RE,
    acdc_config,
    adep,
    aglob,
    check_needles,
    ev,
    failed,
    filled,
    passed,
    tool_invoked,
    unknown,
)


def readme(ctx):
    hits = ctx.static.glob(["README.md", "README.rst", "README.txt", "readme.md"])
    if not hits:
        return failed("No README found.")
    text = ctx.static.read(hits[0]) or ""
    if len(text) < 300:
        return failed(f"README too thin ({len(text)} chars).")
    if text.count("#") < 2 and "```" not in text:
        return failed("README lacks sections or examples.")
    return passed(f"README present and substantive ({len(text)} chars).",
                  [ev("README", source=hits[0])])


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
    return passed(f"AGENTS.md is well-formed ({headings} sections, {lines} lines).",
                  [ev("AGENTS.md structure", source="AGENTS.md")])


def skills(ctx):
    artifacts = ctx.static.glob([
        "skills/*/SKILL.md", "SKILL.md", ".claude/skills/*/SKILL.md",
        ".agents/skills/*/SKILL.md", "plugins/*/skills/*/SKILL.md", ".claude-plugin/plugin.json",
    ])
    if not artifacts:
        return failed("No agent skill artifacts (skills/*/SKILL.md or root SKILL.md).")
    evidence = [ev(f"skill artifact {artifacts[0]}", source=artifacts[0])]
    if ctx.github.available:
        topics = ctx.github.topics()
        if topics.state == "present" and "agent-skills" in topics.value:
            evidence.append(ev("repo topic 'agent-skills' (published)", tier="T2"))
    return passed(f"Provides reusable agent skills ({len(artifacts)} artifact(s)).", evidence)


def doc_freshness(ctx):
    ref_obs = ctx.git.most_recent_commit_iso()
    if ref_obs.state != "present":
        if ref_obs.state == "unreadable":
            return unknown("Git history could not be read safely.")
        return unknown("No git history to assess documentation freshness.")
    ref = ref_obs.value
    checked = []
    for d in ("README.md", "AGENTS.md", "docs/README.md"):
        if ctx.static.glob([d]):
            dt_obs = ctx.git.file_last_commit_iso(d)
            if dt_obs.state == "present":
                checked.append((d, dt_obs.value))
            elif dt_obs.state == "unreadable":
                return unknown("Git history could not be read safely.")
    if not checked:
        return unknown("No tracked key docs to assess.")
    try:
        ref_dt = datetime.fromisoformat(ref)
        stale = [d for d, dt in checked if abs((ref_dt - datetime.fromisoformat(dt)).days) > 180]
    except ValueError:
        return unknown("Unparseable commit timestamps.")
    if stale:
        return failed(f"Docs stale (>180 days before latest commit): {', '.join(stale)}")
    return passed("Key docs updated within 180 days of the latest commit.",
                  [ev("doc vs latest-commit dates", tier="T1")])


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


_AGENT_INSTRUCTION_FILES = [
    "AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "GEMINI.md",
    ".github/copilot-instructions.md", ".cursorrules", ".cursor/rules/*.md",
    ".cursor/rules/*.mdc", ".windsurfrules",
]
_VERIFY_HEADING_RE = re.compile(
    r"(?im)^#{1,6}[^\n]*\b(verif\w*|test\w*|check\w*|validat\w*|lint\w*)\b"
)
_VERIFY_IMPERATIVE_RE = re.compile(
    r"(?i)\b(run|execute)\b[^.\n]{0,120}\b(tests?|lint\w*|checks?|verif\w*|type-?check\w*)"
)
_RUNNABLE_PHRASES = (
    "make ", "npm test", "npm run check", "npm run verify", "npm run validate",
    "npm run lint", "npm run test", "yarn test", "yarn lint", "pnpm test",
    "pnpm lint", "python3 -m", "python -m", "go test", "go vet", "cargo test",
    "cargo clippy", "ra1 report", "sonar analyze",
)


def _line_number(text, offset):
    return text.count("\n", 0, offset)


def _runnable_command_spans(text):
    spans = []
    patterns = (re.compile(r"(?ms)```[^\n]*\n(.*?)```"), re.compile(r"(?<!`)`([^`\n]+)`(?!`)"))
    for pattern in patterns:
        for match in pattern.finditer(text):
            command = match.group(1)
            low = command.lower()
            if check_needles(command) or any(phrase in low for phrase in _RUNNABLE_PHRASES):
                spans.append((_line_number(text, match.start()), _line_number(text, match.end())))
    return spans


def _has_local_verify_contract(text):
    spans = _runnable_command_spans(text)
    if not spans:
        return False
    for heading in _VERIFY_HEADING_RE.finditer(text):
        heading_line = _line_number(text, heading.start())
        if any(heading_line < start <= heading_line + 10 for start, _ in spans):
            return True
    lines = text.splitlines()
    for imperative in _VERIFY_IMPERATIVE_RE.finditer(text):
        line = _line_number(text, imperative.start())
        next_nonblank = None
        for index in range(line + 1, len(lines)):
            if lines[index].strip():
                next_nonblank = index
                break
        allowed = {line}
        if next_nonblank is not None:
            allowed.add(next_nonblank)
        if any(start in allowed for start, _ in spans):
            return True
    return False


def agent_verify_contract(ctx):
    configured = acdc_config(ctx).get("instruction_files")
    configured_patterns = (
        [item for item in configured if isinstance(item, str)]
        if isinstance(configured, list) else []
    )
    configured_files = set(ctx.static.glob(configured_patterns))
    files = ctx.static.glob(_AGENT_INSTRUCTION_FILES + configured_patterns)
    if not files:
        return failed("No agent instruction file (AGENTS.md/CLAUDE.md/.cursor rules) "
                       "to carry a verification contract.")
    for path in files:
        if _has_local_verify_contract(ctx.static.read(path) or ""):
            evidence = [ev("verification contract", source=path)]
            if path in configured_files:
                evidence.append(ev("acdc.instruction_files",
                                   source=".ra1/config.json"))
            return passed(f"{path} instructs agents to verify with a runnable command.", evidence)
    return failed(
        "Agent instruction files never direct the agent to verify its changes "
        "with a runnable command (AC/DC Guide stage)."
    )


_ARCH_FILES = ["docs/architecture*.md", "ARCHITECTURE.md", "docs/adr/**", "docs/decisions/**",
               "doc/architecture*.md", "CONTEXT.md", "docs/design*.md"]


def architecture_doc(ctx):
    """Architecture documentation must be substantive (>=200 chars), not an empty stub."""
    for f in ctx.static.glob(_ARCH_FILES):
        if len(ctx.static.read(f) or "") >= 200:
            return passed(f"Architecture documentation present: {f}",
                           [ev("architecture doc", source=f)])
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


_MCP_CONFIG_PATHS = (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json",
                     ".gemini/settings.json")
_SHELL_LAUNCH_RE = re.compile(
    r"(?i)^\s*(?:sh|bash|zsh|cmd|powershell|pwsh)\b.*\s(-c|/c|-command)\b")
_SECRET_LITERAL_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|pwd|bearer|authorization|auth)")
_PLACEHOLDER_VALUE_RE = re.compile(
    r"(?i)^\$\{[A-Za-z_][A-Za-z0-9_]*\}$|^\$\{env:[A-Za-z_][A-Za-z0-9_]*\}$|"
    r"^bearer\s+<[A-Za-z_][A-Za-z0-9_-]*>$")
_URL_RE = re.compile(r"^(https?)://([^\s/?#]+)([^\s]*)$", re.IGNORECASE)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
_SECRET_FLAG_RE = re.compile(
    r"(?i)^(--?[A-Za-z-]*(?:api[-_]?key|token|secret|password|auth)[A-Za-z-]*)$")


def _mcp_entry_issues(name: str, cfg) -> list[str]:
    """Bounded issues for one MCP server entry; empty means structurally acceptable."""
    if not isinstance(cfg, dict):
        return ["entry not an object"]
    command = cfg.get("command")
    url = cfg.get("url") or cfg.get("serverUrl")
    has_command = isinstance(command, str) and bool(command.strip())
    has_url = isinstance(url, str) and bool(url.strip())
    issues = []
    if has_command == has_url:
        issues.append("exactly one local-command or remote-URL transport is required")
    if has_command:
        if _SHELL_LAUNCH_RE.match(command) or any(
                op in command for op in ("&&", "||", ";", "|", "`", "$(")):
            issues.append("command uses a shell launcher or operators")
        if len(command) > 512 or "\n" in command:
            issues.append("command is not a nonempty one-line string")
        argv = cfg.get("args")
        if argv is not None:
            if not isinstance(argv, list) or any(
                    not isinstance(a, str) or "\n" in a or len(a) > 512 for a in argv):
                issues.append("argv entries must be bounded one-line strings")
            else:
                for index, arg in enumerate(argv):
                    if _SECRET_LITERAL_RE.search(arg) and not _PLACEHOLDER_VALUE_RE.match(arg):
                        # A secret-looking *value* must be a placeholder, never a literal.
                        if index > 0 and _SECRET_FLAG_RE.match(argv[index - 1]):
                            issues.append("literal secret value after a secret flag")
                        elif arg.startswith(("ghp_", "sk-", "AKIA", "eyJ")):
                            issues.append("literal credential token in argv")
    # Sensitive env values are checked for every entry, whatever its transport shape.
    env = cfg.get("env")
    if env is not None:
        if not isinstance(env, dict):
            issues.append("env must be an object")
        else:
            for ekey, evalue in env.items():
                if not isinstance(evalue, str):
                    issues.append("env values must be strings")
                elif _SECRET_LITERAL_RE.search(str(ekey)) and \
                        not _PLACEHOLDER_VALUE_RE.match(evalue):
                    issues.append("literal secret in env value")
    if has_url:
        match = _URL_RE.match(url.strip())
        if not match:
            issues.append("remote URL is malformed")
        else:
            scheme, host, rest = match.groups()
            if scheme.lower() != "https" and host.split(":")[0] not in _LOOPBACK_HOSTS:
                issues.append("remote URL requires HTTPS (HTTP loopback excepted)")
            if "@" in host or "?" in rest or "#" in rest:
                issues.append("remote URL must not carry userinfo/query/fragment")
        headers = cfg.get("headers")
        if headers is not None:
            if not isinstance(headers, dict):
                issues.append("headers must be an object")
            else:
                for _hkey, hvalue in headers.items():
                    if not isinstance(hvalue, str):
                        issues.append("header values must be strings")
                    elif not _PLACEHOLDER_VALUE_RE.match(hvalue):
                        issues.append("header values must be placeholders")
    return issues


def _mcp_config_kind(path: str, data) -> tuple[str, list[str]]:
    """Evaluate one MCP config: (state, issue-categories)."""
    if not isinstance(data, dict):
        return "config_invalid", []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = data.get("servers")  # VS Code `servers` form
    if not isinstance(servers, dict) or not servers:
        return "no_servers", []
    issues = []
    has_secret = False
    has_transport = False
    for name, cfg in servers.items():
        entry_issues = _mcp_entry_issues(str(name), cfg)
        for issue in entry_issues:
            if "literal secret" in issue or "literal credential" in issue:
                has_secret = True
            elif "HTTPS" in issue or "userinfo" in issue:
                has_transport = True
        issues.extend(entry_issues)
    if has_secret:
        return "literal_secret", sorted(set(issues))
    if has_transport:
        return "transport_unsafe", sorted(set(issues))
    if issues:
        return "config_invalid", sorted(set(issues))
    return "ok", []


_HTTPS_REF_RE = re.compile(r"https://[^\s)>\"'\]]+")


def _llms_reference_ok(ctx, text: str) -> str:
    """llms.txt fallback: needs ≥1 HTTPS reference or safe existing relative Markdown ref."""
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if _HTTPS_REF_RE.search(s):
            return "https"
        if s.endswith(".md") and not s.startswith("/") and "\\" not in s \
                and ".." not in s.split("/"):
            obs = ctx.static.read_repo_file(s)
            if obs.state is safe_io.RepoReadState.OK:
                return "markdown"
    return ""


def machine_context(ctx):
    """Structural machine-context configuration: MCP configs, else the llms.txt fallback.

    Pass claims only structural configuration shape; it does not prove server
    availability, package/version authenticity, tool semantics, effective permissions,
    or instruction safety.
    """
    limitation = ("Recognized MCP/llms structure does not prove server availability, "
                  "package/version authenticity, tool semantics, permissions, or "
                  "instruction safety.")
    configs = [p for p in _MCP_CONFIG_PATHS if ctx.static.exists_any([p])]
    if not configs:
        if ctx.static.glob(["llms.txt"]):
            ok, rationale = filled(ctx, "llms.txt", "llms.txt")
            text = ctx.static.read("llms.txt") or ""
            if not ok:
                return failed(
                    "llms.txt fallback is thin or placeholder.",
                    reason_code="docs.machine_context.fallback_incomplete",
                    limitations=[limitation])
            if _llms_reference_ok(ctx, text):
                return passed(
                    "Machine context via a filled llms.txt with an HTTPS or safe local "
                    "Markdown reference.",
                    [ev("llms.txt", source="llms.txt", tier="T0")],
                    reason_code="docs.machine_context.fallback_configured",
                    limitations=[limitation])
            return failed(
                "llms.txt carries no HTTPS reference or safe local Markdown reference.",
                reason_code="docs.machine_context.fallback_incomplete",
                limitations=[limitation])
        return failed(
            "No machine-readable context (MCP config or filled llms.txt with an HTTPS "
            "or safe local reference).",
            reason_code="docs.machine_context.missing",
            limitations=[limitation])
    kinds = []
    for path in configs:
        data = parsers.loads_jsonc(ctx.static.read(path) or "")
        kind, _issues = _mcp_config_kind(path, data)
        kinds.append((path, kind))
    # One malformed/unsafe config can never be masked by a safe one.
    for _rank, kind in enumerate(("literal_secret", "transport_unsafe", "config_invalid")):
        for path, candidate in kinds:
            if candidate == kind:
                return failed(
                    f"MCP configuration has a {kind.replace('_', ' ')} problem.",
                    [ev("MCP config", source=path, tier="T0")],
                    reason_code=f"docs.machine_context.{kind}",
                    limitations=[limitation])
    if any(candidate == "ok" for _path, candidate in kinds):
        return passed(
            f"MCP machine context configured ({len(configs)} config file(s)).",
            [ev("MCP config", source=p, tier="T0") for p, k in kinds if k == "ok"],
            reason_code="docs.machine_context.configured",
            limitations=[limitation])
    return failed(
        "MCP config files carry no structurally valid server entry.",
        [ev("MCP config", source=kinds[0][0], tier="T0")],
        reason_code="docs.machine_context.config_invalid",
        limitations=[limitation])


# --- Progressive-disclosure context map (advisory) --------------------------------------

_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)[^)]*\)")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")


def _context_map_references(text: str) -> tuple[list[str], list[str]]:
    """(references, invalid) from root AGENTS.md: local .md links and backticked paths.

    Ignores http(s)/mailto targets, bare anchors, and non-Markdown targets; strips
    query/fragment; rejects absolute paths and ``..`` traversal as invalid references.
    """
    references, invalid = [], []
    candidates = list(_MD_LINK_RE.finditer(text))
    candidates += list(_BACKTICK_RE.finditer(text))
    for match in candidates:
        raw = match.group(1).strip()
        if raw.startswith(("http://", "https://", "mailto:")):
            continue
        target = raw.split("#", 1)[0].split("?", 1)[0].strip()
        if not target:
            continue
        if not target.lower().endswith(".md"):
            continue
        if target.lower() == "agents.md":
            continue
        if target.startswith("/") or target.startswith("~") or "\\" in target \
                or re.match(r"^[A-Za-z]:", target) or ".." in target.split("/"):
            invalid.append("traversal/absolute reference")
            continue
        references.append(target)
    return sorted(set(references)), invalid


def agent_context_map(ctx):
    """Root AGENTS.md must reference present, filled, local Markdown documentation."""
    text = ctx.static.read("AGENTS.md")
    if text is None:
        return failed(
            "No root AGENTS.md to carry a documentation context map.",
            reason_code="docs.agent_context_map.no_reference",
            limitations=["Referenced documentation presence/filledness does not prove "
                         "correctness or freshness."])
    references, invalid = _context_map_references(text)
    if not references and not invalid:
        return failed(
            "AGENTS.md references no local Markdown documentation (progressive "
            "disclosure).",
            reason_code="docs.agent_context_map.no_reference",
            limitations=["Referenced documentation presence/filledness does not prove "
                         "correctness or freshness."])
    if invalid:
        return failed(
            f"AGENTS.md contains {len(invalid)} invalid documentation reference(s) "
            "(absolute or escaping).",
            reason_code="docs.agent_context_map.invalid_reference",
            limitations=["Referenced documentation presence/filledness does not prove "
                         "correctness or freshness."])
    missing, thin, indeterminate = [], [], []
    resolved = []
    for ref in references:
        obs = ctx.static.read_repo_file(ref)
        if obs.state is safe_io.RepoReadState.OK:
            stripped = obs.text.strip()
            if len(stripped) < 40 or PLACEHOLDER_RE.search(obs.text):
                thin.append(ref)
            else:
                resolved.append(ref)
        elif obs.state is safe_io.RepoReadState.MISSING:
            missing.append(ref)
        else:
            indeterminate.append(ref)
    if missing:
        return failed(
            f"AGENTS.md references {len(missing)} missing documentation target(s): "
            + ", ".join(missing[:3]) + ".",
            reason_code="docs.agent_context_map.missing_target",
            limitations=["Referenced documentation presence/filledness does not prove "
                         "correctness or freshness."])
    if thin:
        return failed(
            f"AGENTS.md references {len(thin)} thin or placeholder documentation "
            "target(s).",
            reason_code="docs.agent_context_map.thin_target",
            limitations=["Referenced documentation presence/filledness does not prove "
                         "correctness or freshness."])
    if indeterminate:
        return unknown(
            f"{len(indeterminate)} documentation target(s) could not be read safely.",
            reason_code="docs.agent_context_map.indeterminate",
            limitations=["Files or candidate sets beyond documented byte, depth, entry, or "
                         "match caps are reported unavailable/unknown rather than "
                         "inspected."])
    return passed(
        f"AGENTS.md maps to {len(resolved)} present, filled local documentation file(s).",
        [ev("context map target", source=ref, tier="T0") for ref in resolved[:6]],
        reason_code="docs.agent_context_map.complete",
        limitations=["Referenced documentation presence/filledness does not prove "
                     "correctness or freshness; this proves reachability/progressive "
                     "disclosure, not instruction correctness or runtime retrieval."])
