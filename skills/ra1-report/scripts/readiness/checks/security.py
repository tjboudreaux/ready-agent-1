"""Security & Governance checks."""
from __future__ import annotations

import json
import re

from .. import parsers
from ..parsers import strip_jsonc
from ._helpers import adep, agrep, atool, ev, failed, passed, skipped, unknown


def branch_protection(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read branch protection.",
                       reason_code="security.branch_protection.github_unavailable")
    obs = ctx.github.branch_protected()
    if obs.state == "unreadable":
        return unknown("Branch protection could not be read; not verified.",
                       reason_code="security.branch_protection.observation_unreadable",
                       limitations=["The selected GitHub control was not verified."])
    if obs.state == "present" and obs.value:
        return passed("Default branch is protected.", [ev("branch protection enabled", tier="T2")],
                      reason_code="security.branch_protection.protected")
    return failed("Default branch is not protected.",
                  reason_code="security.branch_protection.not_protected")


def secret_scanning(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read secret scanning.",
                       reason_code="security.secret_scanning.github_unavailable")
    obs = ctx.github.secret_scanning_enabled()
    if obs.state == "unreadable":
        return unknown("Secret scanning state could not be read; not verified.",
                       reason_code="security.secret_scanning.observation_unreadable",
                       limitations=["The selected GitHub control was not verified."])
    if obs.state == "present" and obs.value:
        return passed("Secret scanning / push protection enabled.",
                       [ev("secret scanning enabled", tier="T2")],
                       reason_code="security.secret_scanning.enabled")
    return failed("Secret scanning not enabled.",
                  reason_code="security.secret_scanning.disabled")


def codeowners(ctx):
    files = ctx.static.glob(["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"])
    if files:
        return passed(f"CODEOWNERS present: {files[0]}", [ev("CODEOWNERS", source=files[0])])
    return failed("Missing CODEOWNERS.")


def dependency_update_automation(ctx):
    files = ctx.static.glob([".github/dependabot.yml", ".github/dependabot.yaml",
                             "renovate.json", ".renovaterc", ".renovaterc.json",
                             ".github/renovate.json"])
    if files:
        return passed(f"Dependency update automation: {files[0]}",
                       [ev("dependabot/renovate", source=files[0])])
    return failed("No dependency update automation (Dependabot/Renovate).")


def automated_security_review(ctx):
    files = ctx.static.glob([".github/workflows/codeql*.yml", ".github/workflows/codeql*.yaml",
                             ".github/workflows/*security*.yml", ".github/workflows/*semgrep*.yml",
                             ".semgrep.yml", ".snyk"])
    if files:
        return passed(f"Automated security review: {files[0]}",
                       [ev("SAST/CodeQL config", source=files[0])])
    dep = adep(ctx, ["bandit", "semgrep", "snyk"])
    if dep or atool(ctx, "bandit"):
        return passed(f"Security scanning tool configured: {dep or 'bandit'}")
    return failed("No automated security review (CodeQL/Semgrep/Snyk/Bandit).")


def gitignore_comprehensive(ctx):
    """Git ignore coverage plus the exact generated-output/policy boundary (§5.1.3).

    ``/.ra1/reports/`` must be ignored by a final positive rule from the root safe regular
    ``.gitignore``, while ``.ra1/config.json`` and ``.ra1/waivers.json`` stay unignored.
    Ignore configuration reduces accidental commits; it is not commit-policy enforcement.
    """
    limitation = ("Ignore configuration reduces accidental commits but does not prove "
                  "commit-policy enforcement.")
    gitignore_text = ctx.static.read(".gitignore")
    patterns = [ln.strip() for ln in (gitignore_text or "").splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
    if not patterns:
        return failed("No .gitignore.",
                      reason_code="security.gitignore_comprehensive.missing",
                      limitations=[limitation])
    blob = "\n".join(patterns).lower()
    has_secret = any(k in blob for k in [".env", "secret", ".pem", "credential", "*.key"])
    has_artifact = any(k in blob for k in ["node_modules", "__pycache__", "dist", "build",
                                           "target", "*.pyc", ".venv", "venv", ".coverage"])
    if not (has_secret and has_artifact):
        missing = []
        code = "security.gitignore_comprehensive.secrets_incomplete"
        if not has_secret:
            missing.append("secrets (e.g. .env)")
        if not has_artifact:
            if not missing:
                code = "security.gitignore_comprehensive.artifacts_incomplete"
            missing.append("build/cache artifacts")
        return failed("Gitignore missing patterns for: " + ", ".join(missing),
                      reason_code=code, limitations=[limitation])
    obs = ctx.git.check_ignore((".ra1/reports/.ra1-ignore-probe", ".ra1/config.json",
                                ".ra1/waivers.json"))
    if obs.state != "present":
        return unknown(
            "Git ignore results could not be read; the generated-output boundary is "
            "unverified.",
            reason_code="security.gitignore_comprehensive.observation_indeterminate",
            limitations=[limitation])
    matched = {path: (source, pattern) for source, _lineno, pattern, path in obs.value}
    for policy in (".ra1/config.json", ".ra1/waivers.json"):
        if policy in matched:
            return failed(
                "Team-owned policy files must not be ignored: " + policy + ".",
                [ev(".gitignore", source=".gitignore")],
                reason_code="security.gitignore_comprehensive.policy_inputs_ignored",
                limitations=[limitation])
    probe = matched.get(".ra1/reports/.ra1-ignore-probe")
    if not probe or probe[0] != ".gitignore" or probe[1].startswith("!"):
        return failed(
            "Generated reports are not isolated: ignore exactly `/.ra1/reports/` from the "
            "root .gitignore.",
            [ev(".gitignore", source=".gitignore")],
            reason_code="security.gitignore_comprehensive.report_output_unprotected",
            limitations=[limitation])
    return passed("Gitignore covers secrets, artifacts, and the generated-output boundary.",
                  [ev(".gitignore", source=".gitignore")],
                  reason_code="security.gitignore_comprehensive.complete",
                  limitations=[limitation])


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
    return failed("No dependency minimum-release-age policy "
                  "(Renovate minimumReleaseAge/stabilityDays).")


_SCRUB_WIRING = [r"redact\s*[:(]|redaction|sanitize_?(log|message|event|data)|maskFields|"
                 r"mask_fields|before_?[Ss]end|filter_sensitive|scrub_?(log|data|event)"]


def log_scrubbing(ctx):
    wiring = agrep(ctx, _SCRUB_WIRING)
    if wiring:
        return passed("Sensitive-data log scrubbing wired.",
                       [ev("log scrubbing", source=str(wiring))])
    return failed("No sensitive-data log scrubbing (redaction/sanitizer wired into logging).")


_SECRET_MGR_DEPS = ["@aws-sdk/client-secrets-manager", "hvac", "doppler-sdk",
                    "@azure/keyvault-secrets", "@google-cloud/secret-manager"]


def secrets_management(ctx):
    for f in ctx.static.glob([".github/workflows/*.yml", ".github/workflows/*.yaml"]):
        low = (ctx.static.read(f) or "").lower()
        if "${{ secrets." in low or "vault" in low or "doppler" in low \
                or "secretsmanager" in low or "secret-manager" in low or "keyvault" in low:
            return passed(f"Managed secrets referenced in CI: {f}",
                           [ev("CI secrets / manager", source=f)])
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
    """Every recognized shared permission file must be parseable and safe (§5.3).

    Accepts ``.claude/settings.json`` (never ``settings.local.json``) and generic
    ``.agents/**/permissions*.{json,md}``. A safe file can never mask an unsafe,
    malformed, or unreadable one. This is static repository policy shape — not effective
    runtime enforcement, identity, or sandbox proof.
    """
    from ._agent_policy import (
        evaluate_claude_settings,
        evaluate_generic_policy,
        shared_permission_paths,
    )
    limitation = ("Repository permission policy does not prove effective runtime "
                  "enforcement, identity, or sandbox containment.")
    candidates = shared_permission_paths(ctx)
    if not candidates:
        return failed(
            "Missing shared agent permissions config "
            "(.claude/settings.json or .agents/**/permissions*).",
            reason_code="security.agent_permissions.missing",
            limitations=[limitation])
    reports = []
    for path in candidates:
        text = ctx.static.read(path)
        if text is None:
            return unknown(
                "A shared permission file could not be read safely.",
                reason_code="security.agent_permissions.observation_indeterminate",
                limitations=[limitation])
        if path == ".claude/settings.json":
            data = parsers.loads_jsonc(text)
            reports.append(evaluate_claude_settings(path, data) if data is not None
                           else evaluate_claude_settings(path, None))
        elif path.endswith(".md"):
            reports.append(evaluate_generic_policy(
                path, _parse_permissions_markdown(text), text))
        else:
            data = parsers.loads_jsonc(text)
            if data is None:
                reports.append(evaluate_generic_policy(path, None, ""))
            else:
                reports.append(evaluate_generic_policy(path, data, ""))
    for state in ("dangerous_allow", "malformed", "unsupported_mode"):
        hits = [r for r in reports if r.state == state]
        if hits:
            categories = sorted({c for r in hits for c in r.categories})
            return failed(
                f"Shared permission policy is {state.replace('_', ' ')}"
                + (f": {', '.join(categories)}." if categories else "."),
                [ev("agent permissions", source=r.path, tier="T0") for r in hits],
                reason_code=f"security.agent_permissions.{state}",
                limitations=[limitation])
    for state in ("secret_denies_incomplete", "consequence_guards_incomplete"):
        hits = [r for r in reports if r.state == state]
        if hits:
            categories = sorted({c for r in hits for c in r.categories})
            return failed(
                f"Shared permission policy is missing coverage: {', '.join(categories)}.",
                [ev("agent permissions", source=r.path, tier="T0") for r in hits],
                reason_code=f"security.agent_permissions.{state}",
                limitations=[limitation])
    return passed(
        f"Shared agent permission policy is restrictive across {len(reports)} file(s).",
        [ev("agent permissions", source=r.path, tier="T0") for r in reports],
        reason_code="security.agent_permissions.safe",
        limitations=[limitation])


# --- Deepened platform/ownership/provenance controls (advisory) -------------------------


def branch_protection_depth(ctx):
    """Lossless T2 confirmation of the five branch-protection control families."""
    if not ctx.github.available:
        return skipped(
            "No GitHub API; branch-protection depth cannot be read.",
            reason_code="security.branch_protection_depth.github_unavailable",
            limitations=["The selected GitHub control was not verified."])
    obs = ctx.github.branch_protection_details()
    if obs.state == "unreadable":
        return unknown(
            "Branch protection could not be read; not verified.",
            reason_code="security.branch_protection_depth.observation_unreadable",
            limitations=["The selected GitHub control was not verified."])
    if obs.state == "absent":
        return failed(
            "Default branch is not protected.",
            reason_code="security.branch_protection_depth.not_protected",
            limitations=["The selected GitHub control was not verified beyond this "
                         "endpoint's documented 404 meaning."])
    record = obs.value
    missing = []
    if record.required_approving_review_count < 1:
        missing.append("≥1 approving review")
    if not record.require_code_owner_reviews:
        missing.append("code-owner review")
    if not record.status_contexts and not record.status_checks:
        missing.append("≥1 required status context/check")
    if record.allow_force_pushes:
        missing.append("force pushes enabled (must be disabled)")
    if record.allow_deletions:
        missing.append("branch deletions enabled (must be disabled)")
    if missing:
        return failed(
            "Branch protection is missing control(s): " + ", ".join(missing) + ".",
            [ev("branch protection details", tier="T2")],
            reason_code="security.branch_protection_depth.controls_incomplete",
            limitations=["The selected GitHub control was not verified beyond the "
                         "observed snapshot."])
    return passed(
        "Branch protection requires reviews, code-owner review, status checks, and "
        "disables force pushes and deletions.",
        [ev("branch protection details", tier="T2")],
        reason_code="security.branch_protection_depth.complete",
        limitations=["The selected GitHub control was not verified beyond the observed "
                     "snapshot."])


def agent_config_ownership(ctx):
    """Every recognized agent-control file must resolve to a definitive CODEOWNERS owner."""
    from ._agent_policy import (
        agent_control_paths,
        ownership_for_targets,
        parse_codeowners,
        select_codeowners,
    )
    selected = select_codeowners(ctx)
    if selected is None:
        return failed(
            "No CODEOWNERS file to establish agent-config ownership.",
            reason_code="security.agent_config_ownership.targets_unowned",
            limitations=["Recognized ownership syntax does not prove identity, access, or "
                         "required review."])
    text = ctx.static.read(selected)
    if text is None:
        return unknown(
            "The selected CODEOWNERS file could not be read safely.",
            reason_code="security.agent_config_ownership.discovery_indeterminate",
            limitations=["Files beyond documented bounds or safety rules are reported "
                         "unavailable rather than inspected."])
    rules = parse_codeowners(text)
    targets = sorted({selected, *agent_control_paths(ctx)})
    if len(targets) > 256:
        return unknown(
            "Agent-control target discovery exceeded the candidate cap.",
            reason_code="security.agent_config_ownership.discovery_indeterminate",
            limitations=["Files or candidate sets beyond documented bounds are reported "
                         "unavailable rather than inspected."])
    outcomes = ownership_for_targets(rules, targets)
    uncertain = sorted(t for t, o in outcomes.items() if o == "uncertain")
    unowned = sorted(t for t, o in outcomes.items() if o in ("unowned", "uncovered"))
    if uncertain:
        return unknown(
            f"{len(uncertain)} agent-control target(s) have uncertain ownership under "
            "the supported CODEOWNERS subset.",
            [ev("CODEOWNERS subset uncertainty", source=selected, tier="T0")],
            reason_code="security.agent_config_ownership.targets_uncertain",
            limitations=["Recognized ownership syntax does not prove identity, access, or "
                         "required review."])
    if unowned:
        return failed(
            f"{len(unowned)} agent-control target(s) have no definitive owner rule: "
            + ", ".join(unowned[:4]) + ".",
            [ev("CODEOWNERS coverage", source=selected, tier="T0")],
            reason_code="security.agent_config_ownership.targets_unowned",
            limitations=["Recognized ownership syntax does not prove identity, access, or "
                         "required review."])
    return passed(
        f"All {len(targets)} agent-control target(s) resolve to a definitive owner rule "
        "under the RA1 CODEOWNERS subset.",
        [ev("CODEOWNERS coverage", source=selected, tier="T0")],
        reason_code="security.agent_config_ownership.complete",
        limitations=["Recognized ownership syntax under the RA1 subset does not prove "
                     "identity, access, or required review."])


def supply_chain_provenance(ctx):
    """Scope-valid build provenance wiring when publication intent is present."""
    from ._workflow_policy import artifact_publication_intent, provenance_candidates
    intent = artifact_publication_intent(ctx)
    if intent == "absent":
        return skipped(
            "No explicit artifact-publication path detected.",
            reason_code="security.supply_chain_provenance.not_applicable",
            limitations=["Static absence of a publication path is not proof that nothing "
                         "is published."])
    if intent == "indeterminate":
        return unknown(
            "Artifact-publication applicability could not be determined safely.",
            reason_code="security.supply_chain_provenance.syntax_indeterminate",
            limitations=["The static parser could not establish effective workflow "
                         "semantics."])
    state, candidates = provenance_candidates(ctx)
    if state == "overflow":
        return unknown(
            "Provenance candidate discovery exceeded the 256-candidate cap.",
            reason_code="security.supply_chain_provenance.syntax_indeterminate",
            limitations=["Files or candidate sets beyond documented bounds are reported "
                         "unavailable rather than inspected."])
    if state == "indeterminate" or any(c.state == "indeterminate" for c in candidates):
        return unknown(
            "Provenance wiring could not be fully established from static workflow "
            "syntax.",
            reason_code="security.supply_chain_provenance.syntax_indeterminate",
            limitations=["The static parser could not establish effective workflow "
                         "semantics."])
    complete = [c for c in candidates if c.state == "complete"]
    if complete:
        winner = complete[0]
        locator = (f"{winner.workflow_path} job#{winner.job_ordinal}"
                   + (f" step#{winner.step_ordinal}" if winner.step_ordinal >= 0 else ""))
        return passed(
            f"Build provenance is wired via a recognized attestation path ({locator}).",
            [ev("provenance wiring", source=winner.workflow_path, tier="T0")],
            reason_code="security.supply_chain_provenance.complete",
            limitations=["Recognized workflow wiring does not prove successful runs or "
                         "released-artifact coverage; static shape does not prove YAML "
                         "acceptance, reachability, execution, distribution, "
                         "verification, or released-artifact coverage."])
    if candidates:
        winner = candidates[0]
        missing = ", ".join(winner.missing) or "incomplete wiring"
        return failed(
            f"Publication path exists but provenance wiring is incomplete ({missing}).",
            [ev("provenance candidate", source=winner.workflow_path, tier="T0")],
            reason_code="security.supply_chain_provenance.wiring_incomplete",
            limitations=["Recognized workflow wiring does not prove successful runs or "
                         "released-artifact coverage."])
    return failed(
        "An artifact-publication path exists with no recognized provenance wiring.",
        reason_code="security.supply_chain_provenance.wiring_incomplete",
        limitations=["Recognized workflow wiring does not prove successful runs or "
                     "released-artifact coverage."])
