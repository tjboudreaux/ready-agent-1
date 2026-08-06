"""T4 judgment-criteria suppression — ESLint-style ignore for agent-graded criteria.

Judgments (naming consistency, code modularization, doc quality, PII handling, …) are graded
qualitatively by the agent layer and **never affect the deterministic score**. This module lets a
repo silence a judgment the way you disable an ESLint rule, via ``.agents/readiness/config.json``::

    {
      "judgments": {
        "*": "advisory",
        "naming_consistency": "off",
        "pii_handling": {"severity": "off", "reason": "static site, no PII"}
      },
      "judgment_overrides": [
        {"paths": ["legacy/**", "vendor/**"], "judgments": {"naming_consistency": "off"}}
      ]
    }

Severity is ``off | advisory`` only. ``error`` is rejected — a judgment can never gate — and is
downgraded to ``advisory`` so a misconfiguration can never turn a judgment into score-affecting
credit. Silencing a judgment yields ``WAIVED`` (disclosed in the report); it never adds a pass.
"""
from __future__ import annotations

import fnmatch

_VALID = {"off", "advisory"}


def _norm(value):
    """Normalize a config entry to ``(severity, reason)``. Unknown/``error`` -> ``advisory``."""
    if isinstance(value, dict):
        sev = value.get("severity")
        reason = value.get("reason", "")
    else:
        sev, reason = value, ""
    if sev not in _VALID:
        sev = "advisory"
    return sev, reason


def _short_id(criterion_id):
    return criterion_id.split(".", 1)[1] if criterion_id.startswith("judgment.") else criterion_id


def decide(config, criterion_id, path=None):
    """Return ``(severity, reason)`` for a judgment criterion under the readiness ``config``.

    Precedence: a matching ``judgment_overrides`` entry (by glob on ``path``) beats the top-level
    ``judgments`` map, which beats the ``"*"`` default, which beats the built-in ``advisory``."""
    judgments = config.get("judgments")
    if not isinstance(judgments, dict):
        return "advisory", ""
    key = _short_id(criterion_id)
    sev, reason = _norm(judgments.get(key, judgments.get("*")))
    overrides = config.get("judgment_overrides")
    if path and isinstance(overrides, list):
        for ov in overrides:
            if not isinstance(ov, dict):
                continue
            rules = ov.get("judgments") or {}
            if key in rules and any(fnmatch.fnmatch(path, p) for p in (ov.get("paths") or [])):
                sev, reason = _norm(rules[key])
    return sev, reason
