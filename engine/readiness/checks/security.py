"""Security & Governance checks."""
from __future__ import annotations

import json
import re

from ..parsers import load_jsonc, strip_jsonc
from ._helpers import adep, agrep, atool, ev, failed, passed, skipped


def branch_protection(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read branch protection.")
    if ctx.github.branch_protected():
        return passed("Default branch is protected.", [ev("branch protection enabled", tier="T2")])
    return failed("Default branch is not protected.")


def secret_scanning(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read secret scanning.")
    enabled = ctx.github.secret_scanning_enabled()
    if enabled:
        return passed("Secret scanning / push protection enabled.", [ev("secret scanning enabled", tier="T2")])
    return failed("Secret scanning not enabled.")


def codeowners(ctx):
    files = ctx.static.glob(["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"])
    if files:
        return passed(f"CODEOWNERS present: {files[0]}", [ev("CODEOWNERS", source=files[0])])
    return failed("Missing CODEOWNERS.")


def dependency_update_automation(ctx):
    files = ctx.static.glob([".github/dependabot.yml", ".github/dependabot.yaml",
                             "renovate.json", ".renovaterc", ".renovaterc.json", ".github/renovate.json"])
    if files:
        return passed(f"Dependency update automation: {files[0]}", [ev("dependabot/renovate", source=files[0])])
    return failed("No dependency update automation (Dependabot/Renovate).")


def automated_security_review(ctx):
    files = ctx.static.glob([".github/workflows/codeql*.yml", ".github/workflows/codeql*.yaml",
                             ".github/workflows/*security*.yml", ".github/workflows/*semgrep*.yml",
                             ".semgrep.yml", ".snyk"])
    if files:
        return passed(f"Automated security review: {files[0]}", [ev("SAST/CodeQL config", source=files[0])])
    dep = adep(ctx, ["bandit", "semgrep", "snyk"])
    if dep or atool(ctx, "bandit"):
        return passed(f"Security scanning tool configured: {dep or 'bandit'}")
    return failed("No automated security review (CodeQL/Semgrep/Snyk/Bandit).")


def gitignore_comprehensive(ctx):
    patterns = ctx.static.gitignore_patterns()
    if not patterns:
        return failed("No .gitignore.")
    blob = "\n".join(patterns).lower()
    has_secret = any(k in blob for k in [".env", "secret", ".pem", "credential", "*.key"])
    has_artifact = any(k in blob for k in ["node_modules", "__pycache__", "dist", "build",
                                           "target", "*.pyc", ".venv", "venv", ".coverage"])
    if has_secret and has_artifact:
        return passed("Gitignore covers secrets and build/cache artifacts.", [ev(".gitignore", source=".gitignore")])
    missing = []
    if not has_secret:
        missing.append("secrets (e.g. .env)")
    if not has_artifact:
        missing.append("build/cache artifacts")
    return failed("Gitignore missing patterns for: " + ", ".join(missing))


def security_md(ctx):
    files = ctx.static.glob(["SECURITY.md", ".github/SECURITY.md", "docs/SECURITY.md"])
    if files:
        return passed(f"SECURITY.md present: {files[0]}", [ev("SECURITY.md", source=files[0])])
    return failed("Missing SECURITY.md.")


# --- Factory-parity security depth (advisory; T0) ------------------------------------

_RENOVATE_FILES = ["renovate.json", "renovate.json5", ".renovaterc", ".renovaterc.json",
                   ".github/renovate.json", ".gitlab/renovate.json"]


def dependency_min_age(ctx):
    for f in ctx.static.glob(_RENOVATE_FILES):
        low = (ctx.static.read(f) or "").lower()
        if "minimumreleaseage" in low or "stabilitydays" in low:
            return passed("Dependency minimum release age configured (Renovate).",
                          [ev("minimumReleaseAge", source=f)])
    pkg = ctx.static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict):
        blob = json.dumps(pkg.get("renovate") or {}).lower()
        if "minimumreleaseage" in blob or "stabilitydays" in blob:
            return passed("Dependency minimum release age configured (package.json renovate).",
                          [ev("minimumReleaseAge", source="package.json")])
    return failed("No dependency minimum-release-age policy (Renovate minimumReleaseAge/stabilityDays).")


_SCRUB_WIRING = [r"redact\s*[:(]|redaction|sanitize_?(log|message|event|data)|maskFields|"
                 r"mask_fields|before_?[Ss]end|filter_sensitive|scrub_?(log|data|event)"]


def log_scrubbing(ctx):
    wiring = agrep(ctx, _SCRUB_WIRING)
    if wiring:
        return passed("Sensitive-data log scrubbing wired.", [ev("log scrubbing", source=str(wiring))])
    return failed("No sensitive-data log scrubbing (redaction/sanitizer wired into logging).")


_SECRET_MGR_DEPS = ["@aws-sdk/client-secrets-manager", "hvac", "doppler-sdk",
                    "@azure/keyvault-secrets", "@google-cloud/secret-manager"]


def secrets_management(ctx):
    for f in ctx.static.glob([".github/workflows/*.yml", ".github/workflows/*.yaml"]):
        low = (ctx.static.read(f) or "").lower()
        if "${{ secrets." in low or "vault" in low or "doppler" in low \
                or "secretsmanager" in low or "secret-manager" in low or "keyvault" in low:
            return passed(f"Managed secrets referenced in CI: {f}", [ev("CI secrets / manager", source=f)])
    dep = adep(ctx, _SECRET_MGR_DEPS)
    if dep:
        return passed(f"Secrets-manager SDK configured: {dep}", [ev("secrets-manager SDK")])
    return failed("No managed-secrets usage (vault/doppler/cloud secret manager / CI secrets).")


_DAST_TOKENS = ("zaproxy", "owasp/zap", "zap-baseline", "zap-full-scan",
                "stackhawk", "nuclei", "dastardly")


def dast(ctx):
    for f in ctx.static.glob([".github/workflows/*.yml", ".github/workflows/*.yaml"]):
        low = (ctx.static.read(f) or "").lower()
        if any(t in low for t in _DAST_TOKENS):
            return passed(f"DAST scanning workflow: {f}", [ev("DAST workflow", source=f)])
    return failed("No DAST scanning workflow (OWASP ZAP/StackHawk/Nuclei).")


# --- Agent least-privilege config (advisory) -----------------------------------------


def _is_unbounded_perm(entry) -> bool:
    if not isinstance(entry, str):
        return False
    return entry == "*" or entry.endswith("(*)") or entry.endswith(":*")


def _permissions_policy_ok(data) -> bool:
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("permissions"), dict):
        perms = data["permissions"]
    elif "allow" in data or "deny" in data:
        perms = data
    else:
        return False
    deny = perms.get("deny") if isinstance(perms.get("deny"), list) else []
    allow = perms.get("allow") if isinstance(perms.get("allow"), list) else []
    if deny:
        return True
    if allow and not any(_is_unbounded_perm(e) for e in allow):
        return True
    return False


def _parse_permissions_markdown(text):
    """Extract a JSON/JSONC object from a fenced code block in a permissions markdown file."""
    if not text:
        return None
    m = re.search(r"```(?:jsonc?|JSONC?)?\s*\n(.*?)```", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(strip_jsonc(m.group(1)))
    except (json.JSONDecodeError, ValueError):
        return None


def agent_permissions(ctx):
    """Pass when shared agent permissions define a deny list or a non-unbounded allow list.

    Accepts ``.claude/settings.json`` (never ``settings.local.json``) or
    ``.agents/**/permissions*.{json,md}``.
    """
    candidates = []
    if ctx.static.glob([".claude/settings.json"]):
        candidates.append(".claude/settings.json")
    candidates.extend(ctx.static.glob([".agents/**/permissions*.json"]))
    candidates.extend(ctx.static.glob([".agents/**/permissions*.md"]))
    candidates = [
        c for c in candidates
        if not c.endswith("settings.local.json") and c != ".claude/settings.local.json"
    ]
    if not candidates:
        return failed(
            "Missing agent permissions config "
            "(.claude/settings.json or .agents/**/permissions*)."
        )
    saw_parse_failure = False
    for path in candidates:
        if path.endswith(".md"):
            data = _parse_permissions_markdown(ctx.static.read(path) or "")
            if data is None:
                saw_parse_failure = True
                continue
        else:
            data = load_jsonc(ctx.root / path)
            if data is None:
                saw_parse_failure = True
                continue
        if _permissions_policy_ok(data):
            return passed(
                f"Agent permissions policy present: {path}.",
                [ev("agent permissions", source=path, tier="T0")],
            )
    if saw_parse_failure:
        return failed("Agent permissions file(s) present but could not be parsed.")
    return failed(
        "Agent permissions present but missing non-empty deny or non-unbounded allow list."
    )
