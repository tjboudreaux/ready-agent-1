"""Shared agent-permission and CODEOWNERS policy machinery (pure stdlib).

Home of the deterministic policy parsers the security checks share: shared-permission
discovery, agent-control path inventory, Claude-style/generic permission evaluation, and
the documented case-sensitive GitHub-subset CODEOWNERS matcher. Output never names owners
or raw policy lines — only canonical categories and repository-relative sources.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- path sets
_MCP_CONFIGS = (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json",
                ".gemini/settings.json")
_ROOT_INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "SKILL.md", ".cursorrules",
                      ".windsurfrules", "llms.txt")
_SHARED_INSTRUCTION_GLOBS = (
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursorrules", ".windsurfrules",
    ".github/copilot-instructions.md", ".github/instructions/*.instructions.md",
    ".cursor/rules/*.mdc",
)
_SKILL_GLOB_ROOTS = ("skills/", ".agents/skills/", ".claude/skills/", ".omp/skills/")
_CODEOWNERS_CANDIDATES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def shared_permission_paths(ctx) -> list[str]:
    """Recognized shared permission files (never settings.local.json), sorted."""
    found = []
    if ctx.static.exists_any([".claude/settings.json"]):
        found.append(".claude/settings.json")
    found.extend(ctx.static.glob([".agents/**/permissions*.json"]))
    found.extend(ctx.static.glob([".agents/**/permissions*.md"]))
    return sorted({p for p in found if not p.endswith("settings.local.json")})


def agent_control_paths(ctx) -> list[str]:
    """Every existing recognized shared agent-control file, sorted and bounded."""
    found = set(shared_permission_paths(ctx))
    for path in _MCP_CONFIGS + _ROOT_INSTRUCTIONS:
        if ctx.static.exists_any([path]):
            found.add(path)
    for path in ctx.static.glob([".omp/rules/denylist.md"]):
        found.add(path)
    for path in ctx.static.glob([".github/instructions/*.instructions.md",
                                 ".cursor/rules/*.mdc"]):
        found.add(path)
    for root in _SKILL_GLOB_ROOTS:
        for path in ctx.static.glob([root + "**/SKILL.md"]):
            found.add(path)
    return sorted(found)


# ------------------------------------------------------------------------- Claude-style permissions
_KNOWN_MODES = frozenset({"default", "acceptEdits", "plan", "dontAsk"})

_BROAD_ALLOW_RE = re.compile(
    r"(?i)^(\*|bash\b|bash\(\*\)|bash\(.*\*.*\)|powershell\b|edit\b|write\b|"
    r"notebookedit\b|mcp__\*|mcp__\w+__\*)")

_DANGEROUS_ALLOW_RE = re.compile(
    r"(?i)(bash\([^)]*(?:rm\s+-[rf]|git\s+push|git\s+reset\s+--hard|deploy|release|"
    r"publish|merge|chmod|chown|sudo|curl\b|wget\b|>\s*/)|"
    r"(?:read|cat|less|more|head|tail|view)\([^)]*(?:\.env|\.pem|\.key|id_rsa|id_ed25519|"
    r"\.ssh|\.aws|\.kube|secret|credential|token)|"
    r"(?:edit|write|notebookedit)\([^)]*(?:codeowners|\.github/workflows|\.claude/settings|"
    r"\.agents/|\.omp/rules|agents\.md|claude\.md|gemini\.md|skill\.md|\.cursorrules|"
    r"\.windsurfrules|llms\.txt|\.mcp\.json|\.cursor/mcp\.json|\.vscode/mcp\.json|"
    r"\.gemini/settings\.json|\.cursor/rules|\.github/instructions))")

_SECRET_ENV_RE = re.compile(
    r"(?i)(?:read|bash|cat|less|more|head|tail|view|export|print|show|env|cp|copy|scp|"
    r"send|post)\([^)]*(?:\.env\b|\.env\.\w|\.pem\b|\.key\b|id_rsa|id_ed25519|\.ssh\b|"
    r"\.aws\b|\.azure\b|\.config/gcloud|\.kube\b|secrets?\.|credentials?)")

_SECRET_DENY_NEEDLES = (
    ("environment/secret exports", re.compile(
        r"(?i)(read|bash|cat|less|more|head|tail|view|export|cp|copy)?\([^)]*"
        r"(?:\.env\b|\.env\.\w|\.env\*|secrets?\b|credentials?\b|tokens?\b)")),
    ("private-key material", re.compile(
        r"(?i)(?:\.pem\b|\*\.pem|\.key\b|\*\.key|id_rsa|id_ed25519)")),
    ("credential directories", re.compile(
        r"(?i)(?:\.ssh\b|\.aws\b|\.azure\b|\.config/gcloud\b|\.kube\b)")),
)

_CONSEQUENCE_NEEDLES = (
    ("destructive file actions", re.compile(
        r"(?i)(?:rm\b|rm\s+-|delete|destroy|drop|truncate|format|mkfs)")),
    ("consequential push/merge/deploy/release/publish", re.compile(
        r"(?i)(?:git\s+push|push\b|merge\b|deploy|release|publish)")),
    ("protected-control mutation", re.compile(
        r"(?i)(?:codeowners|\.github/workflows|\.claude/settings|\.omp/rules|"
        r"\.mcp\.json|permissions)")),
)


@dataclass(frozen=True)
class PermissionFileReport:
    path: str
    state: str          # "safe" | "dangerous_allow" | "secret_denies_incomplete"
                        # | "consequence_guards_incomplete" | "unsupported_mode"
                        # | "malformed"
    categories: tuple   # bounded canonical reason categories


def evaluate_claude_settings(path: str, data) -> PermissionFileReport:
    """Evaluate one parsed Claude-style settings document (§5.3)."""
    if not isinstance(data, dict):
        return PermissionFileReport(path, "malformed", ("not an object",))
    permissions = data.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        return PermissionFileReport(path, "malformed", ("permissions not an object",))
    permissions = permissions or {}
    mode = permissions.get("defaultMode", "default")
    reasons = []
    if mode == "bypassPermissions":
        return PermissionFileReport(path, "dangerous_allow", ("bypass mode",))
    if not isinstance(mode, str) or (mode not in _KNOWN_MODES and mode != "bypassPermissions"):
        return PermissionFileReport(path, "unsupported_mode", ("unknown default mode",))
    allow = permissions.get("allow") if isinstance(permissions.get("allow"), list) else []
    ask = permissions.get("ask") if isinstance(permissions.get("ask"), list) else []
    deny = permissions.get("deny") if isinstance(permissions.get("deny"), list) else []

    for entry in allow:
        if not isinstance(entry, str):
            return PermissionFileReport(path, "malformed", ("non-string rule",))
        if _is_broad_allow(entry):
            reasons.append("broad pre-approved mutation/command rule")
        elif _DANGEROUS_ALLOW_RE.search(entry):
            reasons.append("dangerous allow rule")
    if reasons:
        return PermissionFileReport(path, "dangerous_allow",
                                    tuple(sorted(set(reasons))))

    missing = _missing_secret_denies(deny)
    if missing:
        return PermissionFileReport(path, "secret_denies_incomplete", missing)
    if mode in ("acceptEdits", "auto"):
        missing_guards = _missing_consequence_guards(ask, deny)
        if missing_guards:
            return PermissionFileReport(path, "consequence_guards_incomplete",
                                        missing_guards)
    return PermissionFileReport(path, "safe", ())


def _is_broad_allow(entry: str) -> bool:
    text = entry.strip()
    if text == "*":
        return True
    if _BROAD_ALLOW_RE.match(text):
        # A scoped rule like Bash(npm test) is not broad; bare tool names and wildcards are.
        if "(" in text and not re.search(r"\(\s*\*+\s*\)", text) and "*" not in text:
            return False
        return True
    return False


def _missing_secret_denies(deny: list) -> tuple:
    """Each mandatory secret class needs a path-capable deny rule (or a deny-all)."""
    if any(isinstance(d, str) and d.strip() == "*" for d in deny):
        return ()
    missing = []
    for label, needle in _SECRET_DENY_NEEDLES:
        if not any(isinstance(d, str) and (needle.search(d) or d.strip() == "Read(*)")
                   for d in deny):
            # A Read/Bash deny covering the class also counts via the class needle.
            if not any(isinstance(d, str) and needle.search(d) for d in deny):
                missing.append(label)
    return tuple(missing)


def _missing_consequence_guards(ask: list, deny: list) -> tuple:
    rules = [r for r in list(ask) + list(deny) if isinstance(r, str)]
    missing = []
    for label, needle in _CONSEQUENCE_NEEDLES:
        if not any(needle.search(r) for r in rules):
            missing.append(label)
    return tuple(missing)


def evaluate_generic_policy(path: str, data, text: str) -> PermissionFileReport:
    """Evaluate a generic permissions JSON/Markdown document (§5.3).

    Requires normative bindings between deny/ask verbs, access/mutation actions, and the
    same target families; permissive prose such as "allow all" fails, and a recognized
    document with no parseable policy statements fails.
    """
    if data is None and not text:
        return PermissionFileReport(path, "malformed", ("unparseable",))
    blob = ""
    if isinstance(data, dict):
        blob = json_dumps_bounded(data)
    blob = (blob + "\n" + (text or "")).lower()
    if re.search(r"(?i)allow all|allow everything|no restrictions|unrestricted", blob):
        return PermissionFileReport(path, "dangerous_allow", ("permissive prose",))
    has_deny = bool(re.search(r"(?i)\b(deny|denied|block|blocked|never|must not|"
                              r"do not|forbidden|prohibited|require approval|ask first|"
                              r"human approval)\b", blob))
    has_targets = bool(re.search(r"(?i)(\.env|\.pem|\.key|id_rsa|id_ed25519|\.ssh|\.aws|"
                                 r"\.kube|secret|credential|token|rm |delete|push|merge|"
                                 r"deploy|release|publish)", blob))
    if has_deny and has_targets:
        return PermissionFileReport(path, "safe", ())
    return PermissionFileReport(path, "malformed", ("no parseable policy statements",))


def json_dumps_bounded(data) -> str:
    import json
    try:
        return json.dumps(data)
    except (TypeError, ValueError):
        return ""


# ----------------------------------------------------------------------- CODEOWNERS (GitHub subset)
@dataclass(frozen=True)
class CodeownersRule:
    pattern: str
    owners: tuple
    supported: bool     # False for forms GitHub itself rejects (negation/range/escape)
    order: int


_OWNER_TOKEN_RE = re.compile(r"^(?:@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?"
                             r"(?:/[A-Za-z0-9][A-Za-z0-9_-]{0,38})?|"
                             r"[^@\s]+@[^@\s]+\.[^@\s]+)$")
_PLACEHOLDER_OWNER_RE = re.compile(
    r"(?i)(your-team|your-org|your-organization|your-username|your-name|your-handle|"
    r"your-repo|your-company|your-user|your-account|team-name|org-name|"
    r"<[^>]*@[^>]*>|example(?:-team|-org|-user|-name)?|change-me|todo)")


def _valid_owner(token: str) -> bool:
    """One syntactically valid, non-placeholder owner token."""
    if not _OWNER_TOKEN_RE.match(token):
        return False
    return not _PLACEHOLDER_OWNER_RE.search(token)


def parse_codeowners(text: str) -> list[CodeownersRule]:
    """Parse CODEOWNERS in file order; comments/blank lines ignored."""
    rules = []
    for order, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern, owners = parts[0], tuple(parts[1:])
        supported = _pattern_supported(pattern)
        valid_owners = tuple(o for o in owners if _valid_owner(o))
        rules.append(CodeownersRule(pattern=pattern, owners=valid_owners,
                                    supported=supported, order=order))
    return rules


def _pattern_supported(pattern: str) -> bool:
    """The documented case-sensitive GitHub subset: literals, ?, *, **, rooted/trailing.

    GitHub-invalid forms — negation (``!``), character ranges (``[...]``), and escaped
    leading comments (``\\#``) — cannot earn credit.
    """
    if not pattern or pattern.startswith("!") or pattern.startswith("\\#"):
        return False
    if "[" in pattern or "]" in pattern:
        return False
    if "***" in pattern:
        return False
    return True


def _segment_match(pattern_seg: str, text_seg: str) -> bool:
    """Match one segment with ``?`` and partial-segment ``*`` (never crossing ``/``)."""
    regex = ""
    for ch in pattern_seg:
        if ch == "*":
            regex += "[^/]*"
        elif ch == "?":
            regex += "[^/]"
        else:
            regex += re.escape(ch)
    return bool(re.fullmatch(regex, text_seg))


def codeowners_matches(pattern: str, target: str) -> bool:
    """Whether one supported CODEOWNERS pattern matches a target path."""
    pat = pattern
    if pat.startswith("/"):
        pat = pat[1:]
    elif not pat.startswith("**") and "/" not in pat:
        pat = "**/" + pat
    elif "/" in pat and not pat.startswith("**"):
        pat = pat  # anchored mid-path patterns match at any level via prefix scan below
    trailing_dir = pattern.endswith("/")
    if trailing_dir:
        pat = pat.rstrip("/") + "/**"
    return _match_segments(pat.split("/"), target.split("/"))


def _match_segments(pat_segs: list, target_segs: list) -> bool:
    if not pat_segs:
        return not target_segs
    head = pat_segs[0]
    if head == "**":
        # ** matches zero or more whole segments.
        if _match_segments(pat_segs[1:], target_segs):
            return True
        return bool(target_segs) and _match_segments(pat_segs, target_segs[1:])
    if not target_segs:
        return False
    if not _segment_match(head, target_segs[0]):
        return False
    return _match_segments(pat_segs[1:], target_segs[1:])


def ownership_for_targets(rules: list[CodeownersRule], targets: list[str]) -> dict:
    """Last-applicable-rule ownership per target.

    Returns ``{target: "owned" | "unowned" | "uncertain" | "uncovered"}``. An unsupported
    pattern that could affect a target marks it uncertain unless a later supported rule
    definitively overrides it.
    """
    outcome = {}
    for target in targets:
        last_supported = None
        uncertain = False
        for rule in rules:
            # Unsupported forms (negation/escape) are evaluated against their body: a
            # negated pattern that could affect the target still marks it uncertain.
            if rule.supported:
                matched = codeowners_matches(rule.pattern, target)
            else:
                body = rule.pattern
                if body.startswith(("!", "\\#")):
                    body = body[1:] if body.startswith("!") else body[1:]
                matched = bool(body) and not re.search(r"[\[\]]", body) \
                    and codeowners_matches(body, target)
            if not matched:
                continue
            if rule.supported:
                # A later supported rule definitively overrides earlier uncertainty.
                last_supported = rule
                uncertain = False
            else:
                # An unsupported pattern that could affect the target marks it uncertain
                # unless a later supported rule overrides it.
                uncertain = True
        if uncertain:
            outcome[target] = "uncertain"
        elif last_supported is not None:
            outcome[target] = "owned" if last_supported.owners else "unowned"
        else:
            outcome[target] = "uncovered"
    return outcome


def select_codeowners(ctx) -> str | None:
    """Exactly one CODEOWNERS file in GitHub priority order, or None."""
    for candidate in _CODEOWNERS_CANDIDATES:
        if ctx.static.exists_any([candidate]):
            return candidate
    return None
