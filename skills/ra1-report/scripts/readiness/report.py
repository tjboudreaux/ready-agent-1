"""Report renderers. JSON is canonical; Markdown is the human report; GitHub Checks /
JUnit / SARIF are CI surfaces. SARIF carries ONLY criteria with real source locations
(per the review — repo-level claims like "backlog health" don't belong in code scanning).
"""
from __future__ import annotations

import html
import json
import math
from xml.etree import ElementTree as ET

from . import theme
from .model import LEVEL_NAMES, Status

_SYMBOL = {
    "pass": "✓", "fail": "✗", "skipped": "–",
    "unknown": "?", "waived": "⊘",
}
_EFFORT = {
    "scaffold": "Quick wins (auto-scaffold via ra1-fix)",
    "github_setting": "GitHub settings (apply via gh, confirm first)",
    "propose": "Needs authoring (draft via ra1-fix, review before use)",
    "": "Manual remediation",
}

# Inline glyph set. Lucide's grammar (24-unit box, round caps, currentColor) drawn by hand,
# because the artifact may not fetch an icon library. `dot` is the fallback for any pillar
# a future registry adds.
_ICON_PATHS = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "minus": '<path d="M5 12h14"/>',
    "question": ('<circle cx="12" cy="12" r="9"/>'
                 '<path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.4-1 1-1 1.7v.5"/>'
                 '<path d="M12 17.6v.01"/>'),
    "ban": '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
    "book": '<path d="M4 19.5V5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2Z"/><path d="M8 3v14"/>',
    "wrench": ('<path d="M15.5 3a5.5 5.5 0 0 0-5.1 7.6L3 18l3 3 7.4-7.4A5.5 5.5 0 0 0 21 8.5'
               'c0-.6-.1-1.2-.3-1.7l-3 3-2.5-2.5 3-3A5.5 5.5 0 0 0 15.5 3Z"/>'),
    "shield": '<path d="M12 3 5 6v5.5c0 4.2 2.9 7.6 7 8.5 4.1-.9 7-4.3 7-8.5V6l-7-3Z"/>',
    "beaker": ('<path d="M9 3h6"/><path d="M10 3v6.5L5.4 17A2 2 0 0 0 7.1 20h9.8a2 2 0 0 0'
               ' 1.7-3L14 9.5V3"/><path d="M7 15h10"/>'),
    "type": '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
    "list": ('<path d="M10 6h11M10 12h11M10 18h11"/><path d="m3 6 1.5 1.5L7 5"/>'
             '<path d="m3 12 1.5 1.5L7 11"/><path d="m3 18 1.5 1.5L7 17"/>'),
    "laptop": ('<path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9"/>'
               '<path d="M2.5 16h19l-1 3H3.5l-1-3Z"/>'),
    "activity": '<path d="M3 12h4l2.5-7 4 14L16 12h5"/>',
    "target": ('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>'
               '<circle cx="12" cy="12" r="1.4"/>'),
    "repeat": ('<path d="m17 2 4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/>'
               '<path d="m7 22-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>'),
    "layers": ('<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/>'
               '<path d="m3 17 9 5 9-5"/>'),
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "dot": '<circle cx="12" cy="12" r="9"/>',
}
_STATUS_ICONS = {"pass": "check", "fail": "x", "skipped": "minus",
                 "unknown": "question", "waived": "ban"}
_PILLAR_ICONS = {
    "Documentation": "book",
    "Build System": "wrench",
    "Security & Governance": "shield",
    "Testing": "beaker",
    "Style & Validation": "type",
    "Task Discovery": "list",
    "Dev Environment": "laptop",
    "Debugging & Observability": "activity",
    "Product & Experimentation": "target",
}
# One plain-English line per pillar, answering "why would an agent care?". Nine sentences
# is authorable and maintainable; 109 per-criterion essays would not be.
_PILLAR_ELI5 = {
    "Documentation": "What an agent reads before it touches your code.",
    "Build System": "Whether an agent can install, build and run this repo unattended.",
    "Security & Governance": "The guardrails that stop an agent doing damage.",
    "Testing": "How an agent proves a change works before it opens a pull request.",
    "Style & Validation": "The rules an agent can run itself instead of guessing.",
    "Task Discovery": "How an agent finds work worth doing, and what finished means.",
    "Dev Environment": "How fast a fresh machine, human or agent, becomes productive.",
    "Debugging & Observability": "What an agent can see when something breaks.",
    "Product & Experimentation": "Whether an agent can tell if a shipped change worked.",
}

# ---- education copy. Authored product content: it teaches the reader how to read the
# report, so it lives here with the renderer, never in registry data.
# One line per level, keyed by the canonical level number. The names come from
# model.LEVEL_NAMES at render time so this table can never drift from the engine's.
_LEVEL_EDUCATION = {
    1: "Minimum runnable foundation: setup guidance, pinned dependencies, source-control "
       "tools, ignore rules, and unit tests.",
    2: "Repeatable project guidance: agent instructions, environment templates, linting, "
       "formatting, CI, security policy, and contribution templates.",
    3: "Standardized delivery controls: typing, integration tests, hooks, protected CI, "
       "ownership, secrets, dependency updates, and a reproducible dev environment.",
    4: "Deeper quality and delivery automation: strict typing, automated security review, "
       "API documentation, releases, and a healthy labeled backlog.",
    5: "Bounded agent-led work can progress through dependable guardrails, feedback, and "
       "human governance without ad hoc intervention.",
}
_LEVEL_INTRO = ("Each level is cumulative. A gate clears when at least 80% of its applicable "
                "gating criteria pass and every lower level has cleared. Advisory, skipped, "
                "and waived criteria do not move the level; an unknown gating result counts "
                "as not passed.")
_PILLAR_INTRO = ("Pillars organize checks by the kind of support an agent needs. Coverage "
                 "uses applicable gating criteria only; advisory, skipped, and waived "
                 "criteria do not change the chart.")
_ACDC_INTRO = ("Sonar’s Agent Centric Development Cycle surrounds generated code with "
               "Guide → Verify → Solve. Ready Agent 1 uses AC/DC stage and loop as advisory "
               "metadata; it never changes the Level 1–5 score.")
_ACDC_LOOPS_EDUCATION = [
    ("Inner", "Fast feedback during the agent’s reasoning process: local guidance, "
              "a single verify command, and post-edit checks."),
    ("Outer", "Broader verification after the agent considers the task complete: CI, "
              "coverage and changed-code quality gates, and branch protection."),
    ("Both", "Guidance that must remain available during local work and final verification."),
]
# The one external reference in the artifact: an authored citation, clicked deliberately,
# never fetched at render time. Quoted from Sonar's March 2, 2026 AC/DC overview.
_SONAR_ACDC_URL = ("https://www.sonarsource.com/blog/"
                   "the-future-is-ac-dc-the-agent-centric-development-cycle/")

# What to actually do about a failure, per remediation kind. Wording is checked against
# fix/recipes.py: only `plan["auto"]` is ever written, `propose` and `github_setting` are
# printed for a human, so nothing here may promise that a file appears by itself.
_ACTIONS = {
    "scaffold": "<code>ra1 fix</code> plans it; <code>--apply</code> writes safe config "
                "scaffolds.",
    "propose": "<code>ra1 fix</code> lists this one; you write it.",
    "github_setting": "<code>ra1 fix</code> prints the setting; you apply it.",
    # No entry for "": a criterion with no registered remediation gets no action line at
    # all. "Manual work, no scaffold covers this" repeated down the page is noise.
}
# UNKNOWN is not "unscored": score.py::_status_counts scores it 0/1, exactly like a
# failure, so a gating UNKNOWN blocks a level. Agent judgments say so in their own
# rationale and get no action line, which is why only one sentence is needed here.
_ASSESS_UNKNOWN = "No verdict, so it counts as not passed. Read the evidence, then fix or waive."
_TIER_TIPS = {
    "T0": "Static evidence: read straight off the files in the repository.",
    "T1": "Local evidence: derived from git history on this machine.",
    "T2": "Remote evidence: fetched from the GitHub API.",
}

# User-facing order; `ra1 formats` and `report --help` both derive from this tuple.
REPORT_FORMATS = ("json", "markdown", "html", "github", "junit", "sarif")
_FORMAT_ALIASES = {"md": "markdown", "checks": "github", "annotations": "github"}
# Artifact extension per accepted token. Aliases keep their historical filenames so that
# `--format checks` still writes report.txt and `--format annotations` report.annotations.
_FORMAT_EXTENSIONS = {
    "json": "json", "markdown": "md", "md": "md", "html": "html",
    "github": "txt", "checks": "txt", "annotations": "annotations",
    "junit": "xml", "sarif": "sarif",
}


def normalize_format(fmt: str | None) -> str:
    """Canonical format name for a user token. Raises ValueError on anything unsupported.

    Empty/None means the default (json). Matching is case-insensitive and aliases resolve
    to their canonical renderer. The message quotes the token with ``repr`` so control
    characters cannot forge a stderr line.
    """
    token = (fmt or "").strip()
    if not token:
        return "json"
    canonical = _FORMAT_ALIASES.get(token.lower(), token.lower())
    if canonical not in REPORT_FORMATS:
        raise ValueError(f"unsupported report format {token!r}; "
                         f"supported formats: {', '.join(REPORT_FORMATS)}")
    return canonical


def format_extension(fmt: str | None) -> str:
    """Artifact extension for a user token, validated through :func:`normalize_format`."""
    normalize_format(fmt)
    return _FORMAT_EXTENSIONS[(fmt or "").strip().lower() or "json"]


def render(report, fmt: str | None, *, detail: str = "actionable") -> str:
    if detail not in ("actionable", "all"):
        raise ValueError(f"unsupported detail mode: {detail!r}")
    canonical = normalize_format(fmt)
    if canonical == "json":
        return json.dumps(report.to_dict(), indent=2)
    if canonical == "markdown":
        return render_markdown(report, detail=detail)
    if canonical == "html":
        return render_html(report, detail=detail)
    if canonical == "github":
        return render_github(report)
    if canonical == "junit":
        return render_junit(report)
    return render_sarif(report)


def _location(d) -> str:
    """The display location for the human subtitle — only canonical repository fields.

    A repository without a display name renders a fixed label; the renderer's own process
    state (cwd, project path) never enters the artifact.
    """
    repo = d.repository or {}
    if repo.get("identity_kind") == "origin" and repo.get("owner"):
        return f"{repo['owner']}/{repo.get('name', '')}"
    return repo.get("name") or "local repository"


# ---------------------------------------------------------------------------- markdown
def render_markdown(report, *, detail: str = "actionable") -> str:
    """The actionable-detail Markdown report, rendered only from the canonical dict."""
    d = report.to_dict() if hasattr(report, "to_dict") else report
    score = d.get("score") or {}
    results = d.get("results") or []
    lines = ["# Agent Readiness Report", ""]
    if score:
        pct = round((score.get("pass_rate") or 0) * 100)
        lines.append(f"**Level {score.get('level', 0)} — {score.get('level_name', 'None')}**"
                     f"  \u00b7  {score.get('gating_passed', 0)}/{score.get('gating_total', 0)}"
                     f" gating criteria  \u00b7  {pct}%")
        lines.append("")
        max_level = score.get("max_available_level", 0)
        if max_level:
            ceiling = _LEVEL_NAMES.get(max_level, "")
            lines.append(f"_Current deterministic ceiling: L{max_level} {ceiling}._")
    lines.append("")
    lines.append(f"_{d.get('engine_version', '')} \u00b7 {_location_dict(d)}"
                 + (f" \u00b7 commit {d['commit'][:8]}" if d.get("commit") else "") + "_")
    lines.append("")

    if not d.get("github_available"):
        lines.append("> **T2 GitHub checks were not requested** (offline default): remote "
                     "controls such as branch protection are skipped/excluded and were not "
                     "verified. Re-run with `--github` to assess them.")
        lines.append("")
    else:
        partial = [r for r in results
                   if r["id"] in ("security.branch_protection",
                                  "security.branch_protection_depth")
                   and r["status"] == "unknown"]
        if partial:
            lines.append("> **Partial T2 evidence:** branch-protection controls could not "
                         "be read and are **not verified** (this never means \u201cnot "
                         "protected\u201d).")
            lines.append("")

    detection = d.get("detection")
    if detection:
        lines.append("## Applications Discovered")
        for i, app in enumerate(detection.get("apps") or [], 1):
            langs = ", ".join(app.get("languages") or []) or "n/a"
            lines.append(f"{i}. `{app.get('path', '.')}` — {app.get('deploy_surface', '')}; "
                         f"languages: {langs}")
        if detection.get("project_type") == "unknown":
            lines.append("")
            lines.append("> \u26a0\ufe0f Project type is **unknown** (low detection "
                         "confidence); type-dependent criteria are reported as `unknown`, "
                         "not silently skipped.")
        lines.append("")

    if score.get("levels"):
        lines.append("## Levels")
        for lv in score["levels"]:
            if not lv.get("defined"):
                lines.append(f"- **L{lv['level']} {lv['name']}**: not currently scored — "
                             "no gating criteria defined")
            elif lv.get("total", 0) == 0:
                lines.append(
                    f"- **L{lv['level']} {lv['name']}**: not achieved — all "
                    f"{lv.get('defined_total', 0)} defined criteria were skipped/waived")
            else:
                mark = "achieved" if lv.get("achieved") else "not yet"
                lines.append(f"- **L{lv['level']} {lv['name']}**: {lv['passed']}/{lv['total']} "
                             f"({round(lv.get('ratio', 0) * 100)}%) — {mark}")
        lines.append("")

    coverage = score.get("evidence_coverage")
    if coverage:
        counts = coverage.get("status_counts", {})
        lines.append("## Evidence coverage")
        lines.append("")
        lines.append(
            f"- Results by status: {counts.get('pass', 0)} pass \u00b7 {counts.get('fail', 0)} "
            f"fail \u00b7 {counts.get('unknown', 0)} unknown \u00b7 "
            f"{counts.get('skipped', 0)} skipped \u00b7 {counts.get('waived', 0)} waived")
        lines.append(
            f"- Results with evidence: {coverage.get('results_with_evidence', 0)}/"
            f"{len(results)}; evidence items: {coverage.get('evidence_items', 0)} "
            + "(" + ", ".join(f"{tier}: {n}" for tier, n in
                              (coverage.get("evidence_items_by_tier") or {}).items()) + ")")
        lines.append(
            f"- Decision traces: {coverage.get('results_with_decision_trace', 0)}/"
            f"{len(results)} results; rule-step coverage: "
            f"{coverage.get('results_with_rule_step', 0)}; with limitations: "
            f"{coverage.get('results_with_limitations', 0)}")
        referenced = coverage.get("evidence_items_referenced", 0)
        unreferenced = coverage.get("evidence_items_unreferenced", 0)
        defect = "" if unreferenced == 0 and \
            coverage.get("results_with_decision_trace", 0) == len(results) \
            else " \u2014 **contract defect**"
        lines.append(f"- Evidence referenced by traces: {referenced}; unreferenced: "
                     f"{unreferenced}{defect}")
        lines.append("")

    lines.append("## Criteria Results")
    expanded = {r["id"] for r in results
                if detail == "all" or r["status"] in ("fail", "unknown")}
    for pillar in _pillars_in_order(results):
        lines.append("")
        lines.append(f"### {pillar}")
        eli5 = _PILLAR_ELI5.get(pillar)
        if eli5:
            lines.append(f"_{eli5}_")
        lines.append("")
        for r in [x for x in results if x["pillar"] == pillar]:
            lines.extend(_markdown_result_row(r, expand=r["id"] in expanded))

    next_actions = score.get("next_gate_actions") or []
    if next_actions:
        lines.append("")
        lines.append("## Clear the next gate")
        lines.append("")
        lines.append("_Every gating blocker at the first unachieved defined Level, in "
                     "deterministic order._")
        for action in next_actions:
            effort = _EFFORT.get(action.get("fix_kind", ""), _EFFORT[""])
            lines.append(f"- **{action['title']}** ({action['id']}, L{action['level']}, "
                         f"{action['pillar']}) — {effort} — {action['rationale']}")

    if d.get("gaps"):
        lines.append("")
        lines.extend(_gap_lines(d["gaps"]))

    advisory_actions = _advisory_items(results)
    if advisory_actions:
        lines.append("")
        lines.append("## Advisory Improvements")
        for group, items in advisory_actions:
            lines.append("")
            lines.append(f"**{group}**")
            for r in items:
                acdc = f", {_acdc_label(r)}" if _acdc_label(r) else ""
                lines.append(f"- {r['title']} (L{r['level']}, {r['pillar']}{acdc}) — "
                             f"{r['rationale']}")

    judgments = [r for r in results if r["id"].startswith("judgment.")]
    if judgments:
        assess = [r for r in judgments if r["status"] == "unknown"]
        ignored = [r for r in judgments if r["status"] == "waived"]
        lines.append("")
        lines.append("## Agent Judgments (advisory, never scored)")
        if assess:
            lines.append("")
            lines.append("To assess: " + ", ".join(r["title"] for r in assess) + ".")
        if ignored:
            lines.append("")
            lines.append(
                f"Ignored judgments ({len(ignored)}): "
                + ", ".join(r["title"] for r in ignored)
                + " — silenced via .ra1/config.json `judgments`.")

    if d.get("advisory"):
        lines.append("")
        lines.append("## Advisory (non-gating, agent-authored)")
        for note in d["advisory"]:
            lines.append(f"- {note}")
    else:
        lines.append("")
        lines.append("_Advisory commentary, soft-criteria judgement, and Δ-vs-last-run are "
                     "added by the ra1-report skill; the score above is deterministic._")

    lines.extend(_markdown_provenance(d))
    lines.extend(_markdown_boundary(d))

    lines.append("")
    lines.append("---")
    lines.append("_The engine owns the score, evidence traces, assessment provenance, "
                 "history/delta, and the verified remediation rescan. Agent commentary is "
                 "advisory and never changes the score._")
    return "\n".join(lines) + "\n"


def _markdown_result_row(r, *, expand: bool) -> list:
    sym = _SYMBOL.get(r["status"], "?")
    gate_label = "gating" if r["gating"] else "**advisory**"
    acdc = f", {_acdc_label(r)}" if _acdc_label(r) else ""
    trace = r.get("decision_trace") or {}
    code = trace.get("reason_code", "")
    rows = [
        f"- {sym} **{r['title']}** ({gate_label}, L{r['level']}{acdc}, "
        f"{_display_score(r)}): {r['rationale']}" + (f"  `{code}`" if code else "")
    ]
    if not expand or not trace:
        return rows
    rows.append("")
    rows.append("  **Why this result**")
    evidence = r.get("evidence") or []
    for step in trace.get("steps", []):
        if step["kind"] == "rule":
            rows.append(f"  1. **Rule:** {step['message']} (`{trace.get('rule_ref', '')}`)")
        elif step["kind"] == "observation":
            rows.append(f"  2. **Observed:** {step['message']}")
            for ref in step.get("evidence_refs", []):
                if 0 <= ref < len(evidence):
                    item = evidence[ref]
                    src = f" — `{item['source']}`" if item.get("source") else ""
                    rows.append(f"     - [{item['tier']}] {item['summary']}{src}")
        elif step["kind"] == "evaluation":
            rows.append(f"  3. **Evaluation:** {step['message']} (`{step['code']}`)")
        elif step["kind"] == "conclusion":
            rows.append(f"  4. **Conclusion:** {step['message']}")
    for limitation in trace.get("limitations", []):
        rows.append(f"  - ⚠ {limitation}")
    action = _action({"id": r["id"], "fix_kind": r["fix_kind"], "title": r["title"],
                      "status": r["status"], "gating": r["gating"]})
    if action:
        rows.append(f"  - → {action}")
    rows.append("")
    return rows


def _markdown_provenance(d) -> list:
    provenance = d.get("assessment_provenance")
    if not provenance:
        return []
    inv = provenance["invocation"]
    subject = provenance["subject"]
    lines = ["", "## Assessment provenance", ""]
    lines.append("_Engine-recorded unsigned metadata — **not authenticated provenance or "
                 "an attestation**; it cannot establish report integrity._")
    lines.append("")
    builder = provenance["builder"]
    lines.append(f"- **Builder:** {builder['id']} {builder['engine_version']} "
                 f"({builder['platform']})")
    lines.append(f"- **Subject:** {subject['repository_identity_kind'] or 'unknown'}"
                 + (f" at commit `{subject['commit'][:12]}`" if subject.get("commit") else "")
                 + (f" on branch `{subject['branch']}`" if subject.get("branch") else ""))
    git = inv["git"]
    lines.append(f"- **Git:** profile `{git['resource_profile']}`, metadata "
                 f"`{git['metadata_profile']}`, collection complete: "
                 f"{_yes(git['collection_complete'])}")
    gh = inv["github"]
    lines.append(f"- **GitHub (T2):** requested: {_yes(gh['requested'])}, host proxy "
                 f"requested: {_yes(gh['host_proxy'])}, available: {_yes(gh['available'])}, "
                 f"collection complete: {_yes(gh['collection_complete'])}")
    ex = inv["execution"]
    lines.append(f"- **Execution (T3):** requested: {_yes(ex['requested'])}, timeout "
                 f"{ex['timeout_seconds']}s, completed: {_yes(ex['completed'])}, "
                 f"successful: {_yes(ex['successful'])}")
    lines.append(f"- **Waivers:** source {inv['waivers']['source']}; static collection "
                 f"complete: {_yes(inv['static']['collection_complete'])}")
    lines.append(f"- **Generated at:** {provenance['generated_at']}")
    lines.append("")
    lines.append("_Scope limits: linked-worktree support covers primary checkouts and "
                 "standard reciprocal current-user linked worktrees only; on macOS "
                 "(darwin) automatic Git has CPU/core/wall/output/command/snapshot caps "
                 "but **no hard memory cap** (deferred)._")
    return lines


def _markdown_boundary(d) -> list:
    boundary = d.get("assessment_boundary")
    if not boundary:
        return []
    lines = ["", "## Assessment boundary", ""]
    lines.append("### Evidence layers")
    for layer in boundary["evidence_layers"]:
        lines.append("")
        lines.append(f"- **{layer['id']}** ({'/'.join(layer['tiers'])}): "
                     f"{layer['assesses']}.")
        for item in layer["does_not_prove"]:
            lines.append(f"  - does not prove: {item}")
    lines.append("")
    lines.append("### Not assessed by this repository scan")
    for item in boundary["not_assessed"]:
        lines.append(f"- {item['label']}")
    lines.append("")
    lines.append("### Known limitations")
    for item in boundary["known_limitations"]:
        lines.append(f"- **{item['id']}:** {item['detail']}")
    return lines


def _yes(value) -> str:
    return "yes" if value else "no"


_LEVEL_NAMES = {1: "Functional", 2: "Documented", 3: "Standardized", 4: "Optimized",
                5: "Autonomous"}


def _location_dict(d) -> str:
    repo = d.get("repository") or {}
    if repo.get("identity_kind") == "origin" and repo.get("owner"):
        return f"{repo['owner']}/{repo.get('name', '')}"
    return repo.get("name") or "local repository"


_WAIVERS_FILE = ".ra1/waivers.json"

_GAP_KINDS = {
    "detection": "the scan could not classify this",
    "config": "a value only your team can decide",
    "capability": "a data source the scan could not reach",
}


def _gap_lines(gaps) -> list:
    """The unanswered-questions section, shared by the report and `ra1 gaps`.

    Accepts Gap objects (engine) or the non-executable public projection (imported data).
    Advisory framing is deliberate and load-bearing: answering a gap supplies an input the
    engine re-evaluates, so the section never presents an answer as credit.
    """

    def field(g, key, default=""):
        if isinstance(g, dict):
            return g.get(key, default)
        return getattr(g, {"gap_id": "id", "blocked_ids": "blocks"}.get(key, key), default)

    blocked = sum(field(g, "blocked_gating", 0) for g in gaps)
    lines = [f"## Unanswered Questions ({len(gaps)})", ""]
    lines.append(
        f"_{len(gaps)} input(s) the scan could not determine for itself"
        + (f", holding back {blocked} gating criteria" if blocked else "")
        + ". Answering one lets the engine judge the affected criteria; it never marks "
          "them passing. Run the `ra1-interview` skill to work through them._")
    for g in gaps:
        levels = field(g, "levels", [])
        blocked_ids = field(g, "blocked_ids", [])
        blocked_gating = field(g, "blocked_gating", 0)
        lines.append("")
        stake = (f"{blocked_gating} gating"
                 + (f" at L{'/L'.join(str(x) for x in levels)}" if levels else "")
                 if blocked_gating else f"{len(blocked_ids)} advisory")
        lines.append(f"### {field(g, 'question')}")
        lines.append("")
        lines.append(f"- **Gap:** `{field(g, 'gap_id')}` — "
                     f"{_GAP_KINDS.get(field(g, 'kind'), field(g, 'kind'))} ({stake})")
        lines.append(f"- **Why it matters:** {field(g, 'why')}")
        if field(g, "recordable"):
            lines.append("- **Recordable:** yes — the interview records one typed answer "
                         "and the engine re-scores from it.")
        else:
            lines.append("- **Recordable:** no — this needs action outside the interview "
                         "(see the question).")
        evidence = field(g, "evidence", [])
        if evidence:
            lines.append("- **What the scan saw:** " + "; ".join(evidence))
    return lines


def _pillars_in_order(results):
    seen = []
    for r in results:
        pillar = r["pillar"] if isinstance(r, dict) else r.pillar
        if pillar not in seen:
            seen.append(pillar)
    return seen


def _display_score(r):
    """N/M shown next to each criterion: passed vs evaluated apps (repository scope is 1 unit)."""
    passed = r.get("passed_apps", 0) if isinstance(r, dict) else r.passed_apps
    evaluated = r.get("evaluated_apps", 0) if isinstance(r, dict) else r.evaluated_apps
    return f"{passed}/{evaluated}"


_ACDC_LOOPS = {"inner": "inner loop", "outer": "outer loop", "both": "both loops"}


def _acdc_label(r):
    """'inner loop · verify' when the registry maps the criterion into the AC/DC model.

    Returns "" for unmapped criteria so callers can drop it into any meta join unchanged.
    """
    stage = r["acdc_stage"] if isinstance(r, dict) else r.acdc_stage
    loop = r["acdc_loop"] if isinstance(r, dict) else r.acdc_loop
    if not stage:
        return ""
    return f"{_ACDC_LOOPS.get(loop, loop)} · {stage}"


def _group_by_effort(items):
    groups = {}
    for r in items:
        kind = r["fix_kind"] if isinstance(r, dict) else r.fix_kind
        groups.setdefault(_EFFORT.get(kind, _EFFORT[""]), []).append(r)
    ordered = []
    for label in _EFFORT.values():
        if label in groups:
            ordered.append((label, sorted(
                groups[label],
                key=lambda r: r["level"] if isinstance(r, dict) else r.level)))
    return ordered


def _advisory_items(results):
    return _group_by_effort([
        r for r in results
        if not (r["gating"] if isinstance(r, dict) else r.gating)
        and (r["status"] if isinstance(r, dict) else r.status) in
        ("fail", Status.FAIL)
    ])


# ------------------------------------------------------------------------------ html
# Single-file, offline, script-free artifact: an engineer opens it from a CI download or
# straight off disk. Everything data-derived goes through _html(); tag names, class names
# and the stylesheet are fixed literals so repository content can never reach markup.
_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"

# Selector per type role. This is the ONLY place these selectors receive font-size,
# font-weight or line-height; theme.type_block generates the declarations from the role
# table, and tests/test_report.py fails if any other rule sets those for these selectors.
_ROLE_SELECTORS = {
    "display": "h1",
    "headline": "h2, .status-level, .gate-num",
    "title": "h3, .gate-name, .row-title, .facet-trigger, .education-term",
    "body": "body, tbody th, tbody td",
    "meta": (".meta, .row-meta, .gate-count, .row-id, code, .tier, .detail, .empty,\n"
             ".note, .report-footer, summary, .evidence > li, .facet, .pillar-why,\n"
             ".facet-options, .row-tags"),
    "label": "thead th, .gate-state, .pillar-state, .row-status",
}

_STATIC_CSS = """
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  overflow-wrap: break-word;
}
code, .tier, .row-id, .gate-count, .row-score {
  font-family: var(--font-mono);
  overflow-wrap: anywhere;
}
.report {
  max-width: var(--content-max);
  margin: 0 auto;
  padding: var(--space-7) var(--space-5) var(--space-8);
  background: var(--surface);
}
h1 {
  margin: 0 0 var(--space-2);
  letter-spacing: var(--track-tight);
}
h2 { margin: 0 0 var(--space-3); }
h3 {
  margin: var(--space-5) 0 var(--space-1);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: var(--track-label);
}
p { margin: 0 0 var(--space-2); }
p:last-child { margin-bottom: 0; }
.meta, .row-meta, .gate-count, .report-footer, .note, .tier, .detail, .empty, summary {
  color: var(--text-muted);
}
.rationale, .note, .disclosure, .judgment-assess, .judgment-ignored, .callout {
  max-width: var(--prose-max);
  text-wrap: pretty;
}
.rationale { margin: var(--space-1) 0 0; }
.empty { font-style: italic; }
.report-header {
  border-bottom: var(--hairline) solid var(--border);
  padding-bottom: var(--space-5);
  margin-bottom: var(--space-6);
}
.report > section {
  margin: 0 0 var(--space-6);
  padding-bottom: var(--space-5);
  border-bottom: var(--hairline) solid var(--border);
}
.pillar { margin: 0; padding: 0; border: 0; }
.status-level { margin: 0; }
.gates, .criteria, .actions, .advisory-items, .advisory-notes, .evidence {
  list-style: none; margin: 0; padding: 0;
}
.gates {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--space-2);
  margin-top: var(--space-4);
}
.gate {
  display: grid;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-3);
  border: var(--hairline) solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
}
.gate > * { min-width: 0; }
.gate-state {
  color: var(--text-muted);
  text-transform: uppercase;
}
.gate-cleared .gate-num, .gate-cleared .gate-state { color: var(--status-pass); }
.gate-blocked { border: var(--rule) solid var(--border-strong); }
.gate-blocked .gate-num, .gate-blocked .gate-state { color: var(--status-fail); }
.gate-locked .gate-num, .gate-empty .gate-num { color: var(--status-idle); }
.gate-empty { border-style: dashed; background: none; }
.row, .advisory-notes > li {
  padding: var(--space-3) 0; border-top: var(--hairline) solid var(--border);
}
/* The criterion rail: badge column, then content. Title, tags, rationale, next step and
   evidence share one left edge, so every entry parses the same way at a glance. */
.criterion {
  display: grid;
  grid-template-columns: 1.35rem minmax(0, 1fr);
  column-gap: var(--space-2);
}
.criterion > .badge { grid-column: 1; grid-row: 1; }
.criterion > :not(.badge) { grid-column: 2; }
.row-head {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  align-items: baseline;
}
/* Fixed tag slots, differentiated by typographic register rather than interpuncts:
   colored small-caps status, muted stake and loop, muted mono score. */
.row-tags {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: baseline;
  column-gap: var(--space-3);
  row-gap: var(--space-1);
  color: var(--text-muted);
}
.row-status { color: var(--status-color); text-transform: uppercase; }
/* Three tiers: a filled square blocks a gate, an outlined one is flagged, a muted one is
   settled. The fill is reserved for blocking work so that it keeps meaning something. */
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.35rem;
  height: 1.35rem;
  flex: none;
  border-radius: var(--radius-sm);
}
.needs-action > .badge { background: var(--status-color); }
.icon {
  width: var(--icon-size);
  height: var(--icon-size);
  stroke-width: var(--icon-stroke);
  flex: none;
}
.coverage {
  display: grid;
  grid-template-columns: var(--radar-size) minmax(0, 34rem);
  gap: var(--space-5);
  align-items: center;
}
.radar { width: 100%; max-width: var(--radar-size); height: auto; }
.radar-ring, .radar-spoke { fill: none; stroke: var(--chart-grid); stroke-width: 1; }
.radar-area {
  fill: var(--chart-fill);
  stroke: var(--accent);
  stroke-width: var(--rule);
  stroke-linejoin: round;
}
.radar-dot { fill: var(--accent); }
.radar-index { fill: var(--text-muted); font-size: 11px; }
.pillar-key { list-style: none; margin: 0; padding: 0; }
.pillar-key-item {
  display: grid;
  grid-template-columns: 1.25rem var(--icon-size) minmax(0, 1fr) auto;
  gap: var(--space-2);
  align-items: center;
  padding: var(--space-1) 0;
  border-top: var(--hairline) solid var(--border);
}
.pillar-key-item:first-child { border-top: 0; }
.pillar-index, .pillar-count { color: var(--text-muted); }
.pillar-glyph { color: var(--text-muted); display: inline-flex; }
.dist { width: 100%; height: 8px; display: block; margin-bottom: var(--space-4); }
.dist-seg.status-pass { fill: var(--status-pass); }
.dist-seg.status-fail { fill: var(--status-fail); }
.dist-seg.status-unknown { fill: var(--status-warn); }
.dist-seg.status-skipped, .dist-seg.status-waived { fill: var(--border-strong); }
/* Three native dropdown menus — Status, AC/DC loop, Pillar — in one grid row. The shared
   `name` lets supporting browsers keep at most one menu open, with no script. */
.facets {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-2);
  position: relative;
  margin-bottom: var(--space-5);
}
.facet-menu { position: relative; margin: 0; }
.facet-trigger {
  width: 100%;
  min-height: 2.75rem;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--surface);
  border: var(--hairline) solid var(--border);
  border-radius: var(--radius-sm);
  list-style: none;
}
/* The custom chevron replaces the native disclosure marker on the trigger only; every
   other summary in the artifact keeps its marker. */
.facet-trigger::marker { content: ""; }
.facet-trigger::-webkit-details-marker { display: none; }
.facet-trigger .icon { color: var(--text-muted); }
.facet-title { color: var(--text); }
.facet-options { margin-left: auto; color: var(--text-muted); }
.facet-chevron { display: inline-flex; color: var(--text-muted); }
.facet-menu[open] .facet-chevron { transform: rotate(180deg); }
.facet-panel {
  position: absolute;
  top: calc(100% + var(--space-1));
  left: 0;
  z-index: 2;
  width: 100%;
  min-width: 0;
  max-height: 22rem;
  overflow: auto;
  margin: 0;
  padding: var(--space-2);
  background: var(--surface);
  border: var(--hairline) solid var(--border-strong);
  border-radius: var(--radius-sm);
}
.facet-fields { margin: 0; padding: 0; border: 0; min-width: 0; }
/* Option row: check square, option glyph, label, count. The real checkbox sits
   immediately before its label, so the adjacent-sibling rules below own its states. */
.facet {
  --mark: currentColor;
  --check-fill: transparent;
  display: grid;
  grid-template-columns: 0.7em var(--icon-size) minmax(0, 1fr) auto;
  gap: var(--space-2);
  align-items: center;
  padding: var(--space-2);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  cursor: pointer;
}
.facet .icon { color: var(--text-muted); }
/* A box that fills, not a tick: a checkmark beside the word "Fail" reads as a pass. The
   mark carries its own status colour, which is what binds these words to the segments in
   the bar above them. Colour is the third signal here, never the first. */
.facet::before {
  content: "";
  width: 0.7em;
  height: 0.7em;
  flex: none;
  border: var(--hairline) solid var(--mark);
  border-radius: var(--radius-sm);
  background: var(--check-fill);
}
.facet-pass { --mark: var(--status-pass); }
.facet-fail { --mark: var(--status-fail); }
.facet-unknown { --mark: var(--status-warn); }
.facet-skipped, .facet-waived { --mark: var(--border-strong); }
/* Status options tint their glyph with the mark; loop and pillar glyphs stay muted. */
.facet-pass .icon, .facet-fail .icon, .facet-unknown .icon,
.facet-skipped .icon, .facet-waived .icon { color: var(--mark); }
.facet-input:checked + .facet {
  --check-fill: var(--mark);
  color: var(--text);
  background: var(--surface-sunken);
}
.facet-input:focus-visible + .facet {
  outline: var(--focus-width) solid var(--focus);
  outline-offset: var(--focus-offset);
}
/* Visible by default; the generated pair rules hide it whenever any row survives. */
.criteria-empty { display: block; }
.tip { position: relative; text-decoration: underline dotted; cursor: help; }
.tip-body {
  display: none;
  position: absolute;
  left: 0;
  top: calc(100% + var(--space-1));
  z-index: 1;
  width: max-content;
  max-width: 22rem;
  padding: var(--space-2);
  border: var(--hairline) solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
}
.tip:hover .tip-body, .tip:focus .tip-body { display: block; }
.tip:focus-visible {
  outline: var(--focus-width) solid var(--focus);
  outline-offset: var(--focus-offset);
}
h3 .icon { color: var(--text-muted); vertical-align: -0.2em; margin-right: var(--space-2); }
.badge { font-variant-emoji: text; }
/* The row's one component token: the status-* class sets it once, and the badge stroke,
   the blocking fill, and the status word all consume it. Same idiom as the facet --mark. */
.criterion { --status-color: var(--status-idle); }
.criterion.status-pass { --status-color: var(--status-pass); }
.criterion.status-fail { --status-color: var(--status-fail); }
.criterion.status-unknown { --status-color: var(--status-warn); }
.criterion > .badge { color: var(--status-color); }
/* Knocked out of the fill: later in the sheet than the stroke rule, so the glyph is never
   painted the same colour as the square it sits in. */
.needs-action > .badge { color: var(--surface); }
/* Quiet rows, loud problems. Rhythm comes from which rows are filled, not from giving
   every row a card: 109 identical cards would be the same flat wall in a heavier costume. */
.needs-action {
  padding: var(--space-4);
  margin-top: var(--space-2);
  border: var(--hairline) solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
}
.status-fail.needs-action { border-color: var(--border-strong); }
/* Deliberately no box on `.suggested`. Two weights only: boxed and filled means a gate is
   blocked, plain means everything else. A third box shape read as two components. */
.next-step {
  margin: var(--space-2) 0 0;
  max-width: var(--prose-max);
  text-wrap: pretty;
}
.next-step code { color: var(--text); }
/* Every pillar gets the rule, including the first: it has to separate from the filter bar
   above it, and a section boundary must outweigh the hairlines between rows inside it. */
.pillar {
  margin-top: var(--space-6);
  padding-top: var(--space-5);
  border-top: var(--rule) solid var(--border-strong);
}
/* The count sits immediately after the name, not flung to the far edge: at 1120px a
   right-aligned count is a third of a metre from the thing it counts. */
.pillar-head {
  display: grid;
  grid-template-columns: auto auto;
  justify-content: start;
  align-items: baseline;
  gap: 0 var(--space-3);
  margin-bottom: var(--space-3);
}
.pillar-head h3 { margin: 0; color: var(--text); }
.pillar-why { grid-column: 1 / -1; margin: var(--space-1) 0 0; color: var(--text-muted); }
.pillar-state {
  grid-column: 2;
  grid-row: 1;
  margin: 0;
  text-transform: uppercase;
}
.pillar-state.tone-open { color: var(--status-fail); }
.pillar-state.tone-suggest { color: var(--text-muted); }
.pillar-state.tone-clear { color: var(--status-pass); }
.callout { margin-top: var(--space-3); }
.tone-warn { color: var(--status-warn); }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td {
  text-align: left;
  padding: var(--space-2) var(--space-5) var(--space-2) 0;
  border-bottom: var(--hairline) solid var(--border);
  white-space: nowrap;
}
thead th {
  text-transform: uppercase;
  color: var(--text-muted);
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}
/* Teaching disclosures: how the ladder, the pillars, and the AC/DC mapping work. Quiet
   definition lists — full-width hairlines between rows, no cards, no side stripes. */
.education { margin-top: var(--space-3); }
.education-body { padding-top: var(--space-3); max-width: var(--prose-max); }
.education-list { margin: var(--space-2) 0 0; padding: 0; }
.education-row {
  display: grid;
  grid-template-columns: minmax(10rem, 14rem) minmax(0, 1fr);
  gap: var(--space-1) var(--space-4);
  padding: var(--space-2) 0;
  border-top: var(--hairline) solid var(--border);
}
.education-def { margin: 0; }
.education-quote {
  margin: var(--space-4) 0 0;
  padding: var(--space-3) 0;
  border-top: var(--hairline) solid var(--border);
  border-bottom: var(--hairline) solid var(--border);
}
.education-quote a {
  color: var(--accent);
  text-decoration: underline;
}
.education-quote a:focus-visible {
  outline: var(--focus-width) solid var(--focus);
  outline-offset: var(--focus-offset);
}
details { margin: var(--space-2) 0 0; }
summary {
  cursor: pointer;
  width: fit-content;
  transition: color var(--duration) var(--ease);
}
summary:focus-visible {
  outline: var(--focus-width) solid var(--focus);
  outline-offset: var(--focus-offset);
}
.evidence {
  margin: var(--space-2) 0 0;
  padding: var(--space-1) 0 var(--space-1) var(--space-3);
  border-left: var(--hairline) solid var(--border);
}
.evidence > li { padding: var(--space-1) 0; min-width: 0; }
.report-footer { border-top: var(--hairline) solid var(--border); padding-top: var(--space-4); }
@media (max-width: 720px) {
  .report { padding: var(--space-6) var(--space-4) var(--space-7); }
  .gates { grid-template-columns: minmax(0, 1fr); }
  .coverage { grid-template-columns: minmax(0, 1fr); justify-items: center; }
  .pillar-key { width: 100%; }
  .facets { grid-template-columns: minmax(0, 1fr); }
  /* Menus stack and an open panel re-enters the flow instead of floating over content. */
  .facet-panel { position: static; max-height: none; margin-top: var(--space-1); }
  .education-row { grid-template-columns: minmax(0, 1fr); }
}
@media print {
  body { background: none; }
  .report { max-width: none; padding: 0; background: none; }
  details:not([open]) > *:not(summary) { display: block; }
  details::details-content { content-visibility: visible; display: block; }
  .facets, .facet-input { display: none; }
  .criteria-body .criterion { display: grid !important; }
  .criteria-body .pillar { display: block !important; }
  .criteria-empty { display: none !important; }
  .tip-body { display: inline; position: static; border: 0; padding: 0; }
}
"""

_HTML_STYLE = ("\n" + theme.root_block() + theme.dark_block()
               + theme.type_block(_ROLE_SELECTORS) + _STATIC_CSS)


def _html(value) -> str:
    """The only path from report data into the document. Escapes quotes too."""
    return html.escape(str(value), quote=True)


# ---- components. Every section is assembled from these; no markup shape is spelled out
# twice. `slug`, `title`, `cls`, `tone` and `message` are authored constants — only the
# values routed through _html() and _meta() may come from the scanned repository.
def _section(out, slug, title) -> None:
    out += [f'<section aria-labelledby="{slug}-heading">',
            f'<h2 id="{slug}-heading">{title}</h2>']


def _meta(parts) -> str:
    """The one meta join. Escapes unconditionally, so it can never carry markup."""
    return " · ".join(_html(p) for p in parts if p)


def _empty(out, message) -> None:
    out.append(f'<p class="empty">{message}</p>')


def _callout(out, tone, body) -> None:
    """`body` is authored markup (it carries <strong>/<code>) and never report data."""
    out.append(f'<p class="callout tone-{tone}">{body}</p>')


def _education(out, slug, summary, content) -> None:
    """A collapsed teaching disclosure. `content` is authored markup only — report data
    never reaches it, so nothing here is escaped at emit time."""
    out += [f'<details class="education" id="{slug}-education">',
            f"<summary>{summary}</summary>",
            '<div class="education-body">',
            *content,
            "</div>",
            "</details>"]


def _icon(name) -> str:
    """An inline glyph from the vendored set. Never a fetch, never a font."""
    return ('<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
            'focusable="false">' + _ICON_PATHS.get(name, _ICON_PATHS["dot"]) + "</svg>")


def _badge(status) -> str:
    """The status glyph. aria-hidden: the status word beside it is the accessible signal."""
    return ('<span class="badge" aria-hidden="true">'
            + _icon(_STATUS_ICONS.get(status, "question")) + "</span>")


def _tip(text, tip_id, description) -> str:
    """A term plus its expansion. The expansion is in the DOM, not only in a hover state."""
    return (f'<span class="tip" tabindex="0" aria-describedby="{tip_id}">{_html(text)}'
            f'<span class="tip-body" id="{tip_id}" role="tooltip">{_html(description)}</span>'
            "</span>")


def _row(out, *, cls, title, meta="", badge="", ident="", rationale="", extra=(), tags=()) -> None:
    """One list entry. Criterion rows pass `badge` (the rail column) and `tags` (fixed
    slots: status, stake, loop, score); action and advisory rows pass a joined `meta`."""
    out.append(f'<li class="row {cls}">')
    if badge:
        out.append(badge)
    out += ['<p class="row-head">', f'<span class="row-title">{_html(title)}</span>']
    if ident:
        out.append(ident)
    if tags:
        out.append('<span class="row-tags">' + "".join(tags) + "</span>")
    else:
        out.append(f'<span class="row-meta">{meta}</span>')
    out.append("</p>")
    if rationale:
        out.append(f'<p class="rationale">{_html(rationale)}</p>')
    out += [chunk for chunk in extra if chunk]
    out.append("</li>")


def _ident(value) -> str:
    return f'<span class="row-id">{_html(value)}</span>'


def _evidence(items, tip_prefix) -> str:
    """The evidence disclosure, or "" when there is none to disclose."""
    if not items:
        return ""
    rows = []
    for index, e in enumerate(items, 1):
        tier = _TIER_TIPS.get(e.tier, "")
        cell = (_tip(e.tier, f"{tip_prefix}-{index}", tier) if tier
                else f"<span>{_html(e.tier)}</span>")
        row = [f'<li><span class="tier">{cell}</span> {_html(e.summary)}']
        if e.source:
            row.append(f" <code>{_html(e.source)}</code>")
        if e.detail:
            row.append(f' <span class="detail">{_html(e.detail)}</span>')
        rows.append("".join(row) + "</li>")
    return "\n".join(["<details>", f"<summary>Evidence ({_html(len(items))})</summary>",
                      '<ol class="evidence">', *rows, "</ol>", "</details>"])


# ---- charts. Server-computed SVG geometry: the artifact carries no script, and a `style`
# attribute is forbidden, so proportions travel as SVG presentation attributes.
_RADAR_BOX = 240
_RADAR_C = 120
_RADAR_R = 84
_RADAR_RINGS = (0.25, 0.5, 0.75, 1.0)
# Urgency order, not alphabetical and not "good news first". The bar, the facet chips and
# every generated selector chain share it, so the reader meets failures before passes.
_DIST_ORDER = ("fail", "unknown", "pass", "skipped", "waived")


def _radar_point(index, count, ratio, radius=_RADAR_R):
    angle = -math.pi / 2 + (2 * math.pi * index / count)
    return (round(_RADAR_C + radius * ratio * math.cos(angle), 1),
            round(_RADAR_C + radius * ratio * math.sin(angle), 1))


def _radar_points(ratios, radius=_RADAR_R) -> str:
    return " ".join(f"{x},{y}" for x, y in
                    (_radar_point(i, len(ratios), r, radius) for i, r in enumerate(ratios)))


def _radar(pillars, summary) -> str:
    """Coverage shape across pillars. Fewer than three axes is a line, not a shape: skip it.

    Attribute discipline: repository TEXT never reaches an attribute here. The dynamic
    summary travels as <desc> text, the referenced ids are authored constants, and the only
    report-derived attribute values are coordinates computed from a ratio clamped to
    [0, 1] — a finite numeric grammar that TestHtmlSafety enforces.
    """
    count = len(pillars)
    if count < 3:
        return ""
    ratios = [min(1.0, max(0.0, v["passed"] / v["total"])) if v["total"] else 0.0
              for v in pillars.values()]
    parts = [f'<svg class="radar" viewBox="0 0 {_RADAR_BOX} {_RADAR_BOX}" role="img" '
             'aria-labelledby="radar-title radar-desc">',
             '<title id="radar-title">Pillar coverage</title>',
             f'<desc id="radar-desc">{_html(summary)}</desc>']
    parts += [f'<polygon class="radar-ring" points="{_radar_points([1.0] * count, _RADAR_R * s)}"/>'
              for s in _RADAR_RINGS]
    for index in range(count):
        x, y = _radar_point(index, count, 1.0)
        parts.append(f'<line class="radar-spoke" x1="{_RADAR_C}" y1="{_RADAR_C}" '
                     f'x2="{x}" y2="{y}"/>')
    parts.append(f'<polygon class="radar-area" points="{_radar_points(ratios)}"/>')
    for index, ratio in enumerate(ratios):
        x, y = _radar_point(index, count, ratio)
        parts.append(f'<circle class="radar-dot" cx="{x}" cy="{y}" r="2.5"/>')
    for index in range(count):
        x, y = _radar_point(index, count, 1.0, _RADAR_R + 18)
        parts.append(f'<text class="radar-index" x="{x}" y="{y}" text-anchor="middle" '
                     f'dominant-baseline="central">{index + 1}</text>')
    return "".join(parts) + "</svg>"


def _distribution(counts) -> str:
    """One bar over every criterion. Decorative: the facet labels below carry the numbers."""
    present = [(status, n) for status, n in counts if n]
    total = sum(n for _, n in present)
    if not total:
        return ""
    parts, x = [], 0.0
    for position, (status, n) in enumerate(present, 1):
        width = round(100 - x, 2) if position == len(present) else round(100 * n / total, 2)
        parts.append(f'<rect class="dist-seg status-{status}" x="{round(x, 2)}" y="0" '
                     f'width="{width}" height="8"/>')
        x += width
    return ('<svg class="dist" viewBox="0 0 100 8" preserveAspectRatio="none" '
            'aria-hidden="true">' + "".join(parts) + "</svg>")


def _canonical_view(data):
    """Rebuild the typed report view strictly from the canonical schema-v3 dict.

    Renderers consume only this projection — never raw model internals. Malformed payloads
    fail here (imported dicts must already be validated), matching the no-repair contract.
    """
    from types import SimpleNamespace

    from .model import (
        App,
        CriterionResult,
        DecisionStep,
        DecisionTrace,
        Detection,
        Evidence,
        LevelScore,
        Report,
        ScoreSummary,
        Status,
    )
    results = []
    for r in data.get("results") or []:
        evidence = [Evidence(summary=e["summary"], tier=e["tier"], source=e["source"],
                             detail=e["detail"]) for e in r.get("evidence") or []]
        trace = None
        t = r.get("decision_trace")
        if t:
            trace = DecisionTrace(
                version=t["version"], reason_code=t["reason_code"], rule_ref=t["rule_ref"],
                steps=[DecisionStep(kind=s["kind"], code=s["code"], message=s["message"],
                                    evidence_refs=list(s["evidence_refs"]))
                       for s in t["steps"]],
                limitations=list(t["limitations"]))
        results.append(CriterionResult(
            id=r["id"], title=r["title"], pillar=r["pillar"], level=r["level"],
            scope=r["scope"], gating=r["gating"], status=Status(r["status"]),
            rationale=r["rationale"], evidence=evidence, app_path=r["app_path"],
            fixable=r["fixable"], fix_kind=r["fix_kind"], acdc_stage=r["acdc_stage"],
            acdc_loop=r["acdc_loop"], passed_apps=r["passed_apps"],
            evaluated_apps=r["evaluated_apps"], decision_trace=trace))
    score = None
    s = data.get("score")
    if s:
        score = ScoreSummary(
            level=s["level"], level_name=s["level_name"], pass_rate=s["pass_rate"],
            gating_passed=s["gating_passed"], gating_total=s["gating_total"],
            levels=[LevelScore(level=lv["level"], name=lv["name"], passed=lv["passed"],
                               total=lv["total"], achieved=lv["achieved"],
                               defined=lv["defined"], defined_total=lv["defined_total"])
                    for lv in s["levels"]],
            pillars=s["pillars"], recommendations=s["recommendations"],
            max_available_level=s["max_available_level"],
            next_gate_actions=s["next_gate_actions"],
            evidence_coverage=s["evidence_coverage"])
    detection = None
    det = data.get("detection")
    if det:
        detection = Detection(
            project_type=det["project_type"], confidence=det["confidence"],
            signals=list(det["signals"]), languages=list(det["languages"]),
            apps=[App(path=a["path"], languages=a["languages"], runtime=a["runtime"],
                      deploy_surface=a["deploy_surface"], prod_facing=a["prod_facing"],
                      test_cmd=a["test_cmd"], type_confidence=a["type_confidence"],
                      type_candidates=a["type_candidates"], surfaces=a["surfaces"])
                  for a in det["apps"]],
            is_monorepo=det["is_monorepo"], opt_in=det["opt_in"],
            candidates=det["candidates"], surfaces=det["surfaces"],
            repository_indeterminate=det["repository_indeterminate"],
            indeterminate_reason=det["indeterminate_reason"])
    view = Report(
        project_path="", schema_version=data["schema_version"],
        engine_version=data["engine_version"], registry_version=data["registry_version"],
        detector_version=data["detector_version"], commit=data["commit"],
        branch=data["branch"], github_available=data["github_available"],
        generated_at=data["generated_at"], repository=data.get("repository"),
        detection=detection, results=results, score=score,
        advisory=list(data.get("advisory") or []),
        gaps=[SimpleNamespace(**g) for g in (data.get("gaps") or [])],
        assessment_provenance=data.get("assessment_provenance"))
    # The boundary is a canonical constant; the view carries it verbatim.
    view.assessment_boundary = data.get("assessment_boundary")
    return view


def render_html(report, *, detail: str = "actionable") -> str:
    d = _canonical_view(report.to_dict() if hasattr(report, "to_dict") else report)
    out = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">',
        "<title>Agent Readiness Report</title>",
        f"<style>{_HTML_STYLE}{_filter_css(_facet_model(d))}</style>",
        "</head>",
        "<body>",
        '<main class="report">',
    ]
    _html_header(out, d)
    _html_status(out, d)
    _html_pillars(out, d)
    _html_actions(out, d)
    _html_criteria(out, d, detail=detail)
    _html_advisory_improvements(out, d)
    _html_applications(out, d)
    _html_judgments(out, d)
    _html_advisory(out, d)
    _html_provenance(out, d)
    _html_boundary(out, d)
    _html_footer(out, d)
    out += ["</main>", "</body>", "</html>"]
    return "\n".join(out) + "\n"


def _html_header(out, d) -> None:
    meta = [d.engine_version, _location(d)]
    if d.branch:
        meta.append(f"branch {d.branch}")
    if d.commit:
        meta.append(f"commit {d.commit[:8]}")
    out += [
        '<header class="report-header">',
        "<h1>Agent Readiness Report</h1>",
        f'<p class="meta">{_meta(meta)}</p>',
        "</header>",
    ]


def _html_status(out, d) -> None:
    """The blocked engineer's first question, answered first: which gate, and how far."""
    _section(out, "status", "Readiness Status")
    if d.score:
        s = d.score
        out += [
            f'<p class="status-level">Level {_html(s.level)}: {_html(s.level_name)}</p>',
            '<p class="meta">'
            + _meta([f"{round(s.pass_rate * 100)}% pass rate",
                     f"{s.gating_passed}/{s.gating_total} gating criteria"])
            + "</p>",
        ]
        if s.max_available_level:
            out.append(f'<p class="meta">Current deterministic ceiling: '
                       f'L{_html(s.max_available_level)} '
                       f'{_html(_LEVEL_NAMES.get(s.max_available_level, ""))}.</p>')
        _gate_track(out, s.levels)
        _html_level_education(out, s.levels)
        coverage = s.evidence_coverage or {}
        counts = coverage.get("status_counts", {})
        defects = coverage.get("evidence_items_unreferenced", 0)
        out.append('<p class="meta">Evidence coverage: '
                   + _meta([f"{counts.get('pass', 0)} pass",
                            f"{counts.get('fail', 0)} fail",
                            f"{counts.get('unknown', 0)} unknown",
                            f"{counts.get('skipped', 0)} skipped",
                            f"{counts.get('waived', 0)} waived",
                            f"{coverage.get('results_with_decision_trace', 0)}/"
                            f"{len(d.results)} traced",
                            f"{defects} unreferenced evidence"])
                   + "</p>")
    else:
        _empty(out, "Score unavailable")
    if not d.github_available:
        _callout(out, "note",
                 "<strong>T2 GitHub checks were not requested</strong> (offline default): "
                 "remote controls such as branch protection are skipped/excluded and were "
                 "not verified. Re-run with <code>--github</code> to assess them.")
    else:
        partial = [r for r in d.results
                   if r.id in ("security.branch_protection",
                               "security.branch_protection_depth")
                   and r.status == Status.UNKNOWN]
        if partial:
            _callout(out, "warn",
                     "<strong>Partial T2 evidence:</strong> branch-protection controls "
                     "could not be read and are <strong>not verified</strong> (never "
                     "&ldquo;not protected&rdquo;).")
    # Advisory pointer, not a finding: the artifact is read away from a terminal, so it has
    # to say that some results are stuck on an input rather than on the repository.
    if d.gaps:
        blocked = sum(g.blocked_gating for g in d.gaps)
        _callout(out, "warn",
                 f"<strong>{_html(len(d.gaps))} unanswered question(s)</strong> the scan could "
                 "not determine for itself"
                 + (f", holding back {_html(blocked)} gating criteria" if blocked else "")
                 + ". Run <code>ra1 gaps</code> to list them, or the "
                   "<code>ra1-interview</code> skill to answer them. Answers supply inputs the "
                   "engine re-evaluates; they never mark a criterion passing.")
    out.append("</section>")


def _html_level_education(out, levels) -> None:
    """How the ladder works: the same five canonical levels for every report.

    Names come from LEVEL_NAMES, not from the LevelScore rows, so a partial or synthetic
    score cannot make the renderer invent alternate level names. A level with no defined
    gating criteria says so plainly instead of implying a gate that cannot be cleared.
    """
    by_level = {lv.level: lv for lv in levels}
    content = [f"<p>{_LEVEL_INTRO}</p>", '<dl class="education-list">']
    for level in range(1, 6):
        description = _LEVEL_EDUCATION[level]
        state = by_level.get(level)
        if state is None or not state.total:
            description += " No gating criteria are defined for this level yet."
        content += [
            '<div class="education-row">',
            f'<dt class="education-term">{_html(level)} {LEVEL_NAMES[level]}</dt>',
            f'<dd class="education-def">{description}</dd>',
            "</div>",
        ]
    content.append("</dl>")
    _education(out, "levels", "How the levels work", content)


def _gate_track(out, levels) -> None:
    """Five gates, cleared left to right. Counts only — a percentage of nothing is a lie."""
    if not levels:
        _empty(out, "No level data available")
        return
    out.append('<ol class="gates">')
    blocked_marked = False
    for lv in levels:
        if not lv.defined:
            cls, state = "gate-empty", "not scored"
        elif not lv.total:
            cls, state = "gate-empty", f"all {lv.defined_total} excluded"
        elif lv.achieved:
            cls, state = "gate-cleared", "cleared"
        elif blocked_marked:
            cls, state = "gate-locked", "locked"
        else:
            cls, state, blocked_marked = "gate-blocked", "blocked", True
        count = (f"{_html(lv.passed)}/{_html(lv.total)}" if lv.defined and lv.total
                 else "—")
        out += [
            f'<li class="gate {cls}">',
            f'<span class="gate-num">{_html(lv.level)}</span>',
            f'<span class="gate-name">{_html(lv.name)}</span>',
            f'<span class="gate-count">{count}</span>',
            f'<span class="gate-state">{state}</span>',
            "</li>",
        ]
    out.append("</ol>")


def _html_pillars(out, d) -> None:
    """Where the repo is structurally weak. Reads score.pillars, which is the engine's own

    gating-only, skipped/waived-excluded denominator: re-aggregating over d.results here
    would let advisory and skipped criteria distort coverage while the headline stays put.
    """
    pillars = d.score.pillars if d.score else {}
    if not pillars:
        return
    _section(out, "pillars", "Pillar Coverage")
    summary = ", ".join(f"{name} {v['passed']} of {v['total']}" for name, v in pillars.items())
    out += ['<div class="coverage">', _radar(pillars, summary), '<ol class="pillar-key">']
    for index, (name, value) in enumerate(pillars.items(), 1):
        passed, total = value["passed"], value["total"]
        out += [
            '<li class="pillar-key-item">',
            f'<span class="pillar-index">{index}</span>',
            f'<span class="pillar-glyph">{_icon(_PILLAR_ICONS.get(name, "dot"))}</span>',
            f'<span class="pillar-name">{_html(name)}</span>',
            f'<span class="pillar-count">{_html(passed)}/{_html(total)}</span>',
            "</li>",
        ]
    out += ["</ol>", "</div>"]
    _html_pillar_education(out)
    out.append("</section>")


def _html_pillar_education(out) -> None:
    """What each pillar measures, built from the same _PILLAR_ICONS/_PILLAR_ELI5 mappings
    the key and section headers read — never a second copy of the nine descriptions."""
    content = [f"<p>{_PILLAR_INTRO}</p>", '<dl class="education-list">']
    for name, why in _PILLAR_ELI5.items():
        content += [
            '<div class="education-row">',
            f'<dt class="education-term">{_icon(_PILLAR_ICONS.get(name, "dot"))}'
            f"{_html(name)}</dt>",
            f'<dd class="education-def">{why}</dd>',
            "</div>",
        ]
    content.append("</dl>")
    _education(out, "pillars", "What the pillars measure", content)


def _html_applications(out, d) -> None:
    if not d.detection:
        return
    _section(out, "applications", "Applications Discovered")
    if d.detection.apps:
        out += [
            '<div class="table-scroll">',
            "<table>",
            '<caption class="visually-hidden">Applications discovered in this repository</caption>',
            '<thead><tr><th scope="col">Path</th><th scope="col">Deploy surface</th>'
            '<th scope="col">Languages</th><th scope="col">Runtime</th></tr></thead>',
            "<tbody>",
        ]
        for app in d.detection.apps:
            langs = ", ".join(app.languages) or "n/a"
            out.append(f'<tr><th scope="row"><code>{_html(app.path)}</code></th>'
                       f"<td>{_html(app.deploy_surface)}</td>"
                       f"<td>{_html(langs)}</td>"
                       f"<td>{_html(app.runtime)}</td></tr>")
        out += ["</tbody>", "</table>", "</div>"]
    else:
        _empty(out, "No applications discovered")
    if d.detection.project_type == "unknown":
        _callout(out, "warn",
                 "⚠️ Project type is <strong>unknown</strong> (low detection confidence); "
                 "type-dependent criteria are reported as <code>unknown</code>, not silently "
                 "skipped.")
    out.append("</section>")


def _html_actions(out, d) -> None:
    actions = d.score.next_gate_actions if d.score else []
    if not actions:
        return
    _section(out, "actions", "Clear the Next Gate")
    out += ['<p class="note">Every gating blocker at the first unachieved defined Level, '
            "in deterministic order.</p>",
            '<ol class="actions">']
    for action in actions:
        effort = _EFFORT.get(action.get("fix_kind", ""), _EFFORT[""])
        _row(out, cls="action", title=action["title"], ident=_ident(action["id"]),
             meta=_meta([f"L{action['level']}", action["pillar"], effort]),
             rationale=action["rationale"])
    out += ["</ol>", "</section>"]


_LOOP_ORDER = ("inner", "outer", "both")
_LOOP_LABELS = {"inner": "Inner", "outer": "Outer", "both": "Both"}
_LOOP_ICONS = {"inner": "activity", "outer": "target", "both": "repeat"}


def _facet_model(d):
    """The closed facet set: (status, loop, pillar facets, per-pillar live pairs).

    Each facet is (id, label, count, checked, icon). Every id is derived from an enum
    value or an ordinal, never from repository text, so the generated CSS and the `for=`
    attributes can only ever contain authored constants.
    """
    pillars = _pillars_in_order(d.results)
    statuses = [s for s in _DIST_ORDER if any(r.status.value == s for r in d.results)]
    status_facets = [(f"f-s-{s}", s.capitalize(),
                      sum(1 for r in d.results if r.status.value == s),
                      s != "skipped", _STATUS_ICONS[s])
                     for s in statuses]
    loops = [loop for loop in _LOOP_ORDER
             if any(r.acdc_loop == loop for r in d.results)]
    loop_facets = [(f"f-l-{loop}", _LOOP_LABELS[loop],
                    sum(1 for r in d.results if r.acdc_loop == loop), True,
                    _LOOP_ICONS[loop])
                   for loop in loops]
    pillar_facets = [(f"f-p{i}", name, sum(1 for r in d.results if r.pillar == name),
                      True, _PILLAR_ICONS.get(name, "dot"))
                     for i, name in enumerate(pillars, 1)]
    # A pillar group lives exactly as long as one of the (status, loop) combinations it
    # actually contains has every chip on. Tracking pairs — not just statuses — is what
    # stops a heading from surviving its rows once the loop axis can hide them too.
    owners = [(f"p{i}", _pillar_pairs(d.results, name))
              for i, name in enumerate(pillars, 1)]
    return status_facets, loop_facets, pillar_facets, owners


def _pillar_pairs(results, pillar):
    """The (status-facet, loop-facet|None) combinations a pillar actually contains."""
    pairs = []
    for s in _DIST_ORDER:
        for loop in ("",) + _LOOP_ORDER:
            if any(r.pillar == pillar and r.status.value == s and r.acdc_loop == loop
                   for r in results):
                pairs.append((f"f-s-{s}", f"f-l-{loop}" if loop else None))
    return pairs


def _filter_css(model) -> str:
    """Filtering with no script: modern `:has()` lets correctly nested checkboxes (inside
    their own menu, keyboard-operable in reading order) govern the rows below them.

    Rows are hidden: a row dies when its status or its loop bucket is switched off.
    Pillar sections are *shown*, not hidden: with three facet axes, "this pillar is
    empty" is a disjunction of (status ∧ loop ∧ pillar) conjunctions, which cannot be
    negated directly. Inverting the default makes emptiness the absence of a live pair
    rather than a rule of its own, so an empty heading can never survive its rows.

    The empty state follows the same inversion. It defaults to visible and each live
    pair hides it under exactly the condition that shows a pillar, so visible content
    and the message are mutually exclusive by construction.
    """
    status_facets, loop_facets, pillar_facets, owners = model
    if not status_facets:
        return ""
    rules = [f".report:has(#{fid}:not(:checked)) .criteria-body .criterion.status-{fid[4:]}"
             " { display: none; }" for fid, *_ in status_facets]
    rules += [f".report:has(#{fid}:not(:checked)) .criteria-body .criterion.loop-{fid[4:]}"
              " { display: none; }" for fid, *_ in loop_facets]
    rules.append(".criteria-body .pillar { display: none; }")
    for cls, pairs in owners:
        for sid, lid in pairs:
            chain = f".report:has(#{sid}:checked)"
            if lid:
                chain += f":has(#{lid}:checked)"
            chain += f":has(#f-{cls}:checked)"
            rules.append(f"{chain} .criteria-body .{cls} {{ display: block; }}")
            rules.append(f"{chain} .criteria-body .criteria-empty {{ display: none; }}")
    return "\n" + "\n".join(rules) + "\n"


def _facets(out, model) -> None:
    status_facets, loop_facets, pillar_facets, _owners = model
    out.append('<div class="facets">')
    _facet_menu(out, "status", "Status", "activity", status_facets)
    if loop_facets:
        _facet_menu(out, "loop", "AC/DC loop", "repeat", loop_facets)
    _facet_menu(out, "pillar", "Pillar", "layers", pillar_facets)
    out.append("</div>")


def _facet_menu(out, slug, title, icon, facets) -> None:
    """One native dropdown multi-select. Closed initially; the checkboxes live inside the
    panel, so a closed menu contributes nothing to the tab sequence and opening it exposes
    the options in reading order. The trigger carries a static option count, never a
    selected count: script-free CSS cannot keep such a number from going stale.
    """
    out += [f'<details class="facet-menu" id="{slug}-facets" name="criteria-filters">',
            '<summary class="facet-trigger">',
            _icon(icon),
            f'<span class="facet-title">{title}</span>',
            f'<span class="facet-options">{len(facets)} options</span>',
            f'<span class="facet-chevron">{_icon("chevron-down")}</span>',
            "</summary>",
            '<div class="facet-panel">',
            '<fieldset class="facet-fields">',
            f'<legend class="visually-hidden">{title} filter options</legend>']
    for fid, label, count, checked, option_icon in facets:
        # `f-s-pass` -> `facet-pass`; pillar and loop ids carry no status tint. The input
        # immediately precedes its label so `.facet-input:checked + .facet` owns the state.
        tint = f" facet-{fid[4:]}" if fid.startswith("f-s-") else ""
        out.append(f'<input class="facet-input visually-hidden" type="checkbox" id="{fid}"'
                   + (" checked" if checked else "") + ">"
                   + f'<label class="facet{tint}" for="{fid}">'
                   + _icon(option_icon)
                   + f'<span class="facet-name">{_html(label)}</span>'
                   + f'<span class="facet-count">{_html(count)}</span></label>')
    out += ["</fieldset>", "</div>", "</details>"]


def _html_acdc_education(out) -> None:
    """How Sonar's AC/DC loops map onto this report, with the two sentences that define
    the loops quoted and cited. The anchor is the artifact's one external reference:
    authored, deliberate, and never a render-time dependency.
    """
    rows = []
    for term, definition in _ACDC_LOOPS_EDUCATION:
        rows += [
            '<div class="education-row">',
            f'<dt class="education-term">{term}</dt>',
            f'<dd class="education-def">{definition}</dd>',
            "</div>",
        ]
    content = [
        f"<p>{_ACDC_INTRO}</p>",
        '<dl class="education-list">',
        *rows,
        "</dl>",
        '<blockquote class="education-quote">',
        "<p>“The inner loop: Guide-Verify-Solve happens in each agentic reasoning loop, "
        "ensuring that the agent stays on track as it methodically works to achieve the "
        "plans.”</p>",
        "<p>“The outer loop: Guide-Verify-Solve happens once the agent has ‘finished’ its "
        "work.”</p>",
        f'<p><a href="{_SONAR_ACDC_URL}" target="_blank" rel="noopener noreferrer">'
        "Sonar, “The future is AC/DC: the Agent Centric Development Cycle”</a></p>",
        "</blockquote>",
    ]
    _education(out, "acdc", "How AC/DC loops map to this report", content)


def _html_criteria(out, d, *, detail: str = "actionable") -> None:
    _section(out, "criteria", "Criteria Results")
    pillars = _pillars_in_order(d.results)
    if not pillars:
        _empty(out, "No criteria results available")
        out.append("</section>")
        return
    model = _facet_model(d)
    gating = sum(1 for r in d.results if r.gating and r.status not in
                 (Status.SKIPPED, Status.WAIVED))
    # Without this line the bar reads as a contradiction: 19 failures beside a 100% pass
    # rate. The score counts only applicable gating criteria; this section counts them all.
    out += [f'<p class="note">All {_html(len(d.results))} criteria evaluated, including '
            f"advisory and skipped. The score above rates only the {_html(gating)} "
            "applicable gating criteria.</p>"]
    if model[1]:
        _html_acdc_education(out)
    out.append(_distribution([(s, sum(1 for r in d.results if r.status.value == s))
                              for s in _DIST_ORDER]))
    _facets(out, model)
    out += ['<div class="criteria-body">',
            '<p class="empty criteria-empty">No criteria match these filters</p>']
    index = 0
    for i, pillar in enumerate(pillars, 1):
        # Urgency first, gates before advisories, lowest level first: the top of each
        # pillar is the row to fix next, not whatever the registry happened to list first.
        rows = sorted((x for x in d.results if x.pillar == pillar), key=_sort_key)
        _pillar_header(out, i, pillar, rows)
        out.append('<ol class="criteria">')
        for r in rows:
            index += 1
            _html_criterion(out, r, index, detail=detail)
        out += ["</ol>", "</section>"]
    out += ["</div>", "</section>"]


def _pillar_header(out, index, pillar, rows) -> None:
    """Pillar divider: what the pillar is for, and how much of it is actually in your way.

    Blocking and suggested are reported separately because collapsing them into one "to
    fix" number tells a reader four advisory nits are a blocked gate.
    """
    blocking = sum(1 for r in rows if _blocking(r))
    suggested = sum(1 for r in rows if _suggested(r))
    if blocking:
        tone, state = "open", f"{blocking} blocking"
    elif suggested:
        tone, state = "suggest", f"{suggested} suggested"
    else:
        tone, state = "clear", "all clear"
    out += [
        f'<section class="pillar p{index}" aria-labelledby="pillar-{index}-heading">',
        '<div class="pillar-head">',
        f'<h3 id="pillar-{index}-heading">'
        + _icon(_PILLAR_ICONS.get(pillar, "dot"))
        + f"{_html(pillar)}</h3>",
        f'<p class="pillar-state tone-{tone}">{_html(state)}</p>',
        f'<p class="pillar-why">{_html(_PILLAR_ELI5.get(pillar, ""))}</p>',
        "</div>",
    ]


def _is_judgment(r) -> bool:
    """Agent-graded criteria: genuinely outside the score, so genuinely not your problem."""
    rid = r["id"] if isinstance(r, dict) else r.id
    return rid.startswith("judgment.")


def _blocking(r) -> bool:
    """Does ignoring this row cost a level?

    Only gating criteria move the score, and score.py counts UNKNOWN as 0/1 exactly like a
    failure. An advisory failure is worth doing but blocks nothing, so shouting about it in
    the same voice as a blocked gate teaches the reader to distrust the loud voice.
    """
    gating = r["gating"] if isinstance(r, dict) else r.gating
    status = r["status"] if isinstance(r, dict) else r.status
    return gating and (status in ("fail", Status.FAIL)
                       or (status in ("unknown", Status.UNKNOWN) and not _is_judgment(r)))


def _suggested(r) -> bool:
    """Worth fixing, blocks nothing: an advisory failure."""
    return not r.gating and r.status == Status.FAIL


def _action(r) -> str:
    """The concrete next step, or "" when there is nothing true and concrete to add.

    Each sentence is scoped to where it is actually true, which is narrower than "any row
    that is not settled":

    * Remediation belongs on any failure. An advisory failure blocks nothing but is still
      worth doing, and how to do it is useful on a suggested row as much as a blocking one.
    * "Counts as not passed" belongs only on a BLOCKING unknown. `score.summarize` filters
      to `r.gating`, so an advisory unknown never reaches the level or the pass rate, and
      telling its reader it cost them something is simply false.
    * Judgments and unregistered fix kinds stay silent: the rationale already says it.
    """
    status = r["status"] if isinstance(r, dict) else r.status
    kind = r["fix_kind"] if isinstance(r, dict) else r.fix_kind
    if status in ("fail", Status.FAIL):
        return _ACTIONS.get(kind or "", "")
    if _blocking(r):  # a gating, non-judgment unknown: the only unknown that costs a level
        return _ASSESS_UNKNOWN
    return ""


def _stakes(r) -> str:
    return f"Level {r.level} gate" if r.gating else "advisory"


def _sort_key(r):
    """Tier first, then urgency, then the lowest level.

    Tier leads because tier is what the eye sees: blocking rows are boxed and filled, the
    rest are plain. Sorting by status first interleaved them (fail-gate, fail-advisory,
    unknown-gate, unknown-advisory), so the page alternated between two treatments and
    read as a styling bug rather than a hierarchy. Now the boxes are one contiguous block
    and the pillar's "n blocking" count names exactly the rows beneath it.
    """
    tier = 0 if _blocking(r) else (1 if _suggested(r) else 2)
    return (tier, _DIST_ORDER.index(r.status.value), r.level)


def _trace_disclosure(r, index, *, open_: bool = False) -> str:
    """The accessible "Why this result" disclosure: the deterministic decision trace in
    order, with evidence references resolved to the escaped evidence cards."""
    trace = r.decision_trace
    if trace is None or not trace.steps:
        return ""
    rows = []
    for _position, step in enumerate(trace.steps, 1):
        if step.kind == "rule":
            rows.append(f"<li><strong>Rule</strong> <code>{_html(trace.rule_ref)}</code>: "
                        f"{_html(step.message)}</li>")
        elif step.kind == "observation":
            rows.append(f"<li><strong>Observed</strong> {_html(step.message)}"
                        "<ol class=\"trace-evidence\">"
                        + "".join(
                            f"<li>[{_html(r.evidence[ref].tier)}] "
                            f"{_html(r.evidence[ref].summary)}"
                            + (f" <code>{_html(r.evidence[ref].source)}</code>"
                               if r.evidence[ref].source else "")
                            + "</li>"
                            for ref in step.evidence_refs if 0 <= ref < len(r.evidence))
                        + "</ol></li>")
        elif step.kind == "evaluation":
            rows.append(f"<li><strong>Evaluation</strong> <code>{_html(step.code)}</code>: "
                        f"{_html(step.message)}</li>")
        elif step.kind == "conclusion":
            rows.append(f"<li><strong>Conclusion</strong> {_html(step.message)}</li>")
    limitations = "".join(f'<li class="trace-limitation">⚠ {_html(item)}</li>'
                          for item in trace.limitations)
    if limitations:
        rows.append(limitations)
    open_attr = " open" if open_ else ""
    return (f'<details class="trace"{open_attr}><summary>Why this result '
            f"<code>{_html(trace.reason_code)}</code></summary>"
            f'<ol class="trace-steps">{"".join(rows)}</ol></details>')


def _html_criterion(out, r, index, detail: str = "actionable") -> None:
    status = r.status.value
    action = _action(r)
    tier = " needs-action" if _blocking(r) else (" suggested" if _suggested(r) else "")
    extra = [_evidence(r.evidence, f"tip-{index}")]
    if action:
        extra.insert(0, f'<p class="next-step">{action}</p>')
    trace_html = _trace_disclosure(
        r, index, open_=(detail == "all" or status in ("fail", "unknown")))
    if trace_html:
        extra.append(trace_html)
    # N/M earns its place when it is not tautological: a repository-scope pass is always
    # 1/1, and printing it on every green row is three tokens saying nothing.
    partial = r.passed_apps != r.evaluated_apps
    score = _display_score(r) if (partial or r.evaluated_apps > 1) else ""
    # Membership test, not truthiness: only registry-validated loop values may reach a
    # class name, keeping the generated CSS closed to authored constants.
    loop = f" loop-{r.acdc_loop}" if r.acdc_loop in _LOOP_ORDER else ""
    # Fixed slots in fixed order; absent slots collapse. Only the status word carries the
    # row's --status-color, so a fail-heavy report is not a wall of tinted meta text.
    tags = [f'<span class="row-status">{_html(status.capitalize())}</span>',
            f'<span class="row-stake">{_html(_stakes(r))}</span>']
    acdc = _acdc_label(r)
    if acdc:
        tags.append(f'<span class="row-loop">{_html(acdc)}</span>')
    if score:
        tags.append(f'<span class="row-score">{_html(score)}</span>')
    _row(out, cls=f"criterion status-{status}{loop}{tier}", badge=_badge(status), title=r.title,
         tags=tuple(tags), rationale=r.rationale, extra=tuple(extra))


def _html_advisory_improvements(out, d) -> None:
    groups = _advisory_items(d.results)
    if not groups:
        return
    _section(out, "advisory-improvements", "Advisory Improvements")
    for group, items in groups:
        out += [f"<h3>{_html(group)}</h3>", '<ul class="advisory-items">']
        for r in items:
            _row(out, cls="advisory-item", title=r.title,
                 meta=_meta([f"L{r.level}", r.pillar, _acdc_label(r)]), rationale=r.rationale)
        out.append("</ul>")
    out.append("</section>")


def _html_judgments(out, d) -> None:
    judgments = [r for r in d.results if r.id.startswith("judgment.")]
    if not judgments:
        return
    _section(out, "judgments", "Agent Judgments (advisory, never scored)")
    assess = [r for r in judgments if r.status == Status.UNKNOWN]
    ignored = [r for r in judgments if r.status == Status.WAIVED]
    if assess:
        out.append('<p class="judgment-assess">To assess: '
                   + _html(", ".join(r.title for r in assess)) + ".</p>")
    if ignored:
        out.append(f'<p class="judgment-ignored">Ignored judgments ({_html(len(ignored))}): '
                   + _html(", ".join(r.title for r in ignored))
                   + " — silenced via <code>.ra1/config.json</code> "
                     "<code>judgments</code>.</p>")
    out.append("</section>")


def _html_advisory(out, d) -> None:
    if not d.advisory:
        out.append('<p class="disclosure">Advisory commentary, soft-criteria judgement, and '
                   "Δ-vs-last-run are added by the ra1-report skill; the score above is "
                   "deterministic.</p>")
        return
    _section(out, "advisory", "Advisory (non-gating, agent-authored)")
    out.append('<ul class="advisory-notes">')
    for note in d.advisory:
        out.append(f"<li>{_html(note)}</li>")
    out += ["</ul>", "</section>"]



def _html_provenance(out, d) -> None:
    provenance = d.assessment_provenance
    if not provenance:
        return
    _section(out, "provenance", "Assessment Provenance")
    inv = provenance["invocation"]
    subject = provenance["subject"]
    builder = provenance["builder"]
    out.append('<p class="note">Engine-recorded unsigned metadata — <strong>not '
               "authenticated provenance or an attestation</strong>; it cannot establish "
               "report integrity.</p>")
    out.append('<dl class="education-list">')
    rows = [
        ("builder", f"{builder['id']} {builder['engine_version']} ({builder['platform']})"),
        ("subject", (subject.get("repository_identity_kind") or "unknown")
         + (f" at commit {subject['commit'][:12]}" if subject.get("commit") else "")
         + (f" on branch {subject['branch']}" if subject.get("branch") else "")),
        ("git", f"profile {inv['git']['resource_profile']}; metadata "
                f"{inv['git']['metadata_profile']}; collection complete: "
                f"{_yes(inv['git']['collection_complete'])}"),
        ("github (T2)", f"requested: {_yes(inv['github']['requested'])}; host proxy "
                        f"requested: {_yes(inv['github']['host_proxy'])}; available: "
                        f"{_yes(inv['github']['available'])}; collection complete: "
                        f"{_yes(inv['github']['collection_complete'])}"),
        ("execution (T3)", f"requested: {_yes(inv['execution']['requested'])}; timeout "
                           f"{inv['execution']['timeout_seconds']}s; completed: "
                           f"{_yes(inv['execution']['completed'])}; successful: "
                           f"{_yes(inv['execution']['successful'])}"),
        ("waivers / static", f"waivers source {inv['waivers']['source']}; static "
                             f"collection complete: "
                             f"{_yes(inv['static']['collection_complete'])}"),
        ("generated at", provenance["generated_at"]),
    ]
    for term, definition in rows:
        out.append('<div class="education-row">'
                   f'<dt class="education-term">{_html(term)}</dt>'
                   f'<dd class="education-def">{_html(definition)}</dd></div>')
    out.append("</dl>")
    out.append('<p class="note">Scope limits: linked-worktree support covers primary '
               "checkouts and standard reciprocal current-user linked worktrees only; on "
               "macOS (darwin) automatic Git has CPU/core/wall/output/command/snapshot "
               "caps but <strong>no hard memory cap</strong> (deferred).</p>")
    out.append("</section>")


def _html_boundary(out, d) -> None:
    boundary = getattr(d, "assessment_boundary", None)
    if not boundary:
        return
    _section(out, "boundary", "Assessment Boundary")
    out.append("<h3>Evidence layers</h3>")
    out.append('<dl class="education-list">')
    for layer in boundary["evidence_layers"]:
        out.append('<div class="education-row">'
                   f'<dt class="education-term">{_html(layer["id"])} '
                   f'({"/".join(layer["tiers"])})</dt>'
                   f'<dd class="education-def">{_html(layer["assesses"])}</dd></div>')
    out.append("</dl>")
    out.append("<h3>Not assessed by this repository scan</h3>")
    out.append("<ul>")
    for item in boundary["not_assessed"]:
        out.append(f"<li>{_html(item['label'])}</li>")
    out.append("</ul>")
    out.append("<h3>Known limitations</h3>")
    out.append('<dl class="education-list">')
    for item in boundary["known_limitations"]:
        out.append('<div class="education-row">'
                   f'<dt class="education-term">{_html(item["id"])}</dt>'
                   f'<dd class="education-def">{_html(item["detail"])}</dd></div>')
    out.append("</dl>")
    out.append("</section>")


def _html_footer(out, d) -> None:
    parts = []
    if d.generated_at:
        parts.append(f"generated {d.generated_at}")
    parts += [f"registry {d.registry_version}", f"detector {d.detector_version}"]
    out += ['<footer class="report-footer">',
            f"<p>{_meta(parts)}</p>",
            "</footer>"]


# ---------------------------------------------------------------------------- github
def _gha_escape_data(value) -> str:
    """Escape %, CR, LF — applied exactly once to message data."""
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _gha_escape_property(value) -> str:
    """Data escaping plus ``:``/``,`` — only for title/file properties, never message bodies."""
    return _gha_escape_data(value).replace(":", "%3A").replace(",", "%2C")


def render_github(report) -> str:
    """One escaped workflow command per result, plus ceiling/boundary/provenance notices.

    Narrow gate adapter, not a complete assessment report: it never duplicates the full
    trace/boundary payload.
    """
    d = report.to_dict() if hasattr(report, "to_dict") else report
    out = []
    for r in d.get("results") or []:
        if r["gating"] and r["status"] == "fail":
            trace = r.get("decision_trace") or {}
            code = trace.get("reason_code", "")
            src = _first_source(r)
            title = _gha_escape_property(f"Readiness: {r['title']}")
            props = [f"title={title}"]
            if src:
                props.append(f"file={_gha_escape_property(src)}")
            body = r["rationale"] + (f" [{code}]" if code else "")
            out.append(f"::warning {','.join(props)}::{_gha_escape_data(body)}")
    score = d.get("score")
    if score:
        out.append(f"::notice::Agent Readiness Level {score['level']} "
                   f"({_gha_escape_data(str(score['gating_passed']))}/"
                   f"{_gha_escape_data(str(score['gating_total']))} gating criteria) — "
                   f"current deterministic ceiling L{score.get('max_available_level', 0)}")
    provenance = d.get("assessment_provenance")
    if provenance:
        inv = provenance["invocation"]
        builder = provenance["builder"]
        subject = provenance["subject"]
        commit = subject.get("commit") or "unknown"
        out.append(
            "::notice::Assessment provenance (unsigned/unverified — not an attestation): "
            f"{_gha_escape_data(builder['id'])} "
            f"{_gha_escape_data(builder['engine_version'])} "
            f"on {_gha_escape_data(builder['platform'])}; subject commit "
            f"{_gha_escape_data(commit[:12])}; T2 requested="
            f"{_gha_escape_data(str(inv['github']['requested']))}, T3 requested="
            f"{_gha_escape_data(str(inv['execution']['requested']))}")
    return "\n".join(out) + ("\n" if out else "")


def _first_source(r):
    """A validated repository-relative source for the file property, or "" (never sentinel)."""
    for e in r.get("evidence") or []:
        src = e.get("source") or ""
        if src and "/" in src and not src.startswith(("repos/", "/")) \
                and ".." not in src.split("/") and "\\" not in src \
                and not src.startswith("[redacted"):
            return src
    return ""


# ---------------------------------------------------------------------------- junit
def render_junit(report) -> str:
    """Narrow gating adapter over the canonical dict — not a complete assessment report."""
    d = report.to_dict() if hasattr(report, "to_dict") else report
    results = [r for r in d.get("results") or [] if r["gating"]]
    failures = sum(1 for r in results if r["status"] == "fail")
    skipped = sum(1 for r in results if r["status"] in ("skipped", "waived", "unknown"))
    suites = ET.Element("testsuites", name="agent-readiness",
                        tests=str(len(results)), failures=str(failures), skipped=str(skipped))
    by_pillar = {}
    for r in results:
        by_pillar.setdefault(r["pillar"], []).append(r)
    for pillar, items in by_pillar.items():
        suite = ET.SubElement(suites, "testsuite", name=pillar, tests=str(len(items)),
                              failures=str(sum(1 for r in items if r["status"] == "fail")))
        for r in items:
            case = ET.SubElement(suite, "testcase", classname=f"{pillar}",
                                 name=f"{r['id']} {r['title']}")
            if r["status"] == "fail":
                ET.SubElement(case, "failure", message=r["rationale"]).text = r["rationale"]
            elif r["status"] in ("skipped", "waived", "unknown"):
                ET.SubElement(case, "skipped", message=r["status"])
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suites, encoding="unicode")


# ---------------------------------------------------------------------------- sarif
def render_sarif(report) -> str:
    """Narrow gating adapter over the canonical dict — not a complete assessment report."""
    d = report.to_dict() if hasattr(report, "to_dict") else report
    rules, results = [], []
    seen_rules = set()
    for r in d.get("results") or []:
        if not (r["gating"] and r["status"] == "fail"):
            continue
        src = _first_source(r)
        if not src:
            continue  # SARIF only for criteria with a real source location
        if r["id"] not in seen_rules:
            rules.append({"id": r["id"], "name": r["title"],
                          "shortDescription": {"text": r["title"]},
                          "properties": {"pillar": r["pillar"], "level": r["level"]}})
            seen_rules.add(r["id"])
        results.append({
            "ruleId": r["id"], "level": "warning",
            "message": {"text": r["rationale"]},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": src}}}],
        })
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "agent-readiness",
                                "version": d["engine_version"],
                                "informationUri": "https://github.com/tjboudreaux/agent-readiness",
                                "rules": rules}},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2)


# ---------------------------------------------------------------------------- history
def render_history_list(payload) -> str:
    repo = payload.get("repository") or {}
    lines = ["# Readiness History", "",
             f"_{repo.get('identity_kind', '?')}: {repo.get('name', '')}_", "",
             "| id | timestamp | level | pass_rate | gating | registry |",
             "|---|---|---|---|---|---|"]
    for e in payload.get("entries", []):
        lines.append(
            f"| {e.get('id', '')} | {e.get('timestamp', '')} | {e.get('level', '')} | "
            f"{e.get('pass_rate', '')} | "
            f"{e.get('gating_passed', '')}/{e.get('gating_total', '')} | "
            f"{e.get('registry_version', '')} |")
    if not payload.get("entries"):
        lines.append("| _(none)_ | | | | | |")
    return "\n".join(lines) + "\n"


def render_history_diff(payload) -> str:
    lines = ["# Readiness Delta", "", f"_{payload.get('from')} → {payload.get('to')}_", ""]
    if not payload.get("comparable", False):
        lines.append(f"Not comparable: {payload.get('reason', '')}")
        return "\n".join(lines) + "\n"
    lvl = (payload.get("score_delta") or {}).get("level", {})
    lines.append(f"- Level: {lvl.get('from')} → {lvl.get('to')}")
    if payload.get("detector_changed"):
        lines.append("- ⚠️ detector version changed: application N/M deltas are suppressed.")
    for label, key in (("Newly passing", "newly_passing"), ("Newly failing", "newly_failing"),
                       ("Newly unknown", "newly_unknown")):
        items = payload.get(key) or []
        if items:
            lines.append(f"- {label}: {', '.join(items)}")
    return "\n".join(lines) + "\n"
