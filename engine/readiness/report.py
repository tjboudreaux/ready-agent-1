"""Report renderers. JSON is canonical; Markdown is the human report; GitHub Checks /
JUnit / SARIF are CI surfaces. SARIF carries ONLY criteria with real source locations
(per the review — repo-level claims like "backlog health" don't belong in code scanning).
"""
from __future__ import annotations

import json
from xml.etree import ElementTree as ET

from .model import Status
from .score import _recommendations

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


def render(report, fmt: str) -> str:
    fmt = (fmt or "json").lower()
    if fmt in ("md", "markdown"):
        return render_markdown(report)
    if fmt in ("github", "checks", "annotations"):
        return render_github(report)
    if fmt == "junit":
        return render_junit(report)
    if fmt == "sarif":
        return render_sarif(report)
    return json.dumps(report.to_dict(), indent=2)

def _location(d) -> str:
    """Redacted scan location for the human subtitle — never the raw absolute path."""
    repo = d.repository or {}
    if repo.get("identity_kind") == "origin" and repo.get("owner"):
        return f"{repo['owner']}/{repo.get('name', '')}"
    return repo.get("name") or d.project_path.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------- markdown
def render_markdown(report) -> str:
    d = report
    lines = ["# Agent Readiness Report", ""]
    score = d.score
    if score:
        pct = round(score.pass_rate * 100)
        lines.append(f"**Level {score.level} — {score.level_name}**  ·  "
                     f"{score.gating_passed}/{score.gating_total} gating criteria  ·  {pct}%")
    lines.append("")
    lines.append(f"_{d.engine_version} · {_location(d)}"
                 + (f" · commit {d.commit[:8]}" if d.commit else "") + "_")
    lines.append("")

    if d.detection:
        lines.append("## Applications Discovered")
        for i, app in enumerate(d.detection.apps, 1):
            langs = ", ".join(app.languages) or "n/a"
            lines.append(f"{i}. `{app.path}` — {app.deploy_surface}; languages: {langs}")
        if d.detection.project_type == "unknown":
            lines.append("")
            lines.append("> ⚠️ Project type is **unknown** (low detection confidence); "
                         "type-dependent criteria are reported as `unknown`, not silently skipped.")
        lines.append("")

    if score and score.levels:
        lines.append("## Levels")
        for lv in score.levels:
            mark = "achieved" if lv.achieved else "not yet"
            lines.append(f"- **L{lv.level} {lv.name}**: {lv.passed}/{lv.total} ({round(lv.ratio*100)}%) — {mark}")
        lines.append("")

    lines.append("## Criteria Results")
    for pillar in _pillars_in_order(d.results):
        lines.append("")
        lines.append(f"### {pillar}")
        for r in [x for x in d.results if x.pillar == pillar]:
            sym = _SYMBOL.get(r.status.value, "?")
            gate_label = "gating" if r.gating else "**advisory**"
            lines.append(f"- {sym} **{r.title}** ({gate_label}, L{r.level}, {_display_score(r)}): {r.rationale}")

    recs = _recommendations(d.results, score.level if score else 0)
    if recs:
        lines.append("")
        lines.append("## Action Items")
        lines.append("")
        lines.append(f"_Top {len(recs)} highest-impact gating next steps (clear the next level first)._")
        for rec in recs:
            effort = _EFFORT.get(rec.get("fix_kind", ""), _EFFORT[""])
            lines.append(f"- **{rec['title']}** ({rec['id']}, L{rec['level']}, {rec['pillar']}) "
                         f"— {effort} — {rec['rationale']}")

    advisory_actions = _advisory_items(d.results)
    if advisory_actions:
        lines.append("")
        lines.append("## Advisory Improvements")
        for group, items in advisory_actions:
            lines.append("")
            lines.append(f"**{group}**")
            for r in items:
                lines.append(f"- {r.title} (L{r.level}, {r.pillar}) — {r.rationale}")

    if d.advisory:
        lines.append("")
        lines.append("## Advisory (non-gating, agent-authored)")
        for note in d.advisory:
            lines.append(f"- {note}")
    else:
        lines.append("")
        lines.append("_Advisory commentary, soft-criteria judgement, and Δ-vs-last-run are added "
                     "by the ra1-report skill; the score above is deterministic._")

    return "\n".join(lines) + "\n"


def _pillars_in_order(results):
    seen = []
    for r in results:
        if r.pillar not in seen:
            seen.append(r.pillar)
    return seen


def _display_score(r):
    """N/M shown next to each criterion: passed vs evaluated apps (repository scope is 1 unit)."""
    return f"{r.passed_apps}/{r.evaluated_apps}"


def _group_by_effort(items):
    groups = {}
    for r in items:
        groups.setdefault(_EFFORT.get(r.fix_kind, _EFFORT[""]), []).append(r)
    ordered = []
    for label in _EFFORT.values():
        if label in groups:
            ordered.append((label, sorted(groups[label], key=lambda r: r.level)))
    return ordered


def _advisory_items(results):
    return _group_by_effort([r for r in results if not r.gating and r.status == Status.FAIL])


# ---------------------------------------------------------------------------- github
def render_github(report) -> str:
    out = []
    for r in report.results:
        if r.gating and r.status == Status.FAIL:
            src = _first_source(r)
            prefix = f"::warning title=Readiness: {r.title}"
            if src:
                prefix += f" file={src}"
            out.append(f"{prefix}::{r.rationale}")
    if report.score:
        out.append(f"::notice::Agent Readiness Level {report.score.level} "
                   f"({report.score.gating_passed}/{report.score.gating_total} gating criteria)")
    return "\n".join(out) + ("\n" if out else "")


def _first_source(r):
    for e in r.evidence:
        if e.source and "/" in e.source and not e.source.startswith("repos/"):
            return e.source
    return ""


# ---------------------------------------------------------------------------- junit
def render_junit(report) -> str:
    results = [r for r in report.results if r.gating]
    failures = sum(1 for r in results if r.status == Status.FAIL)
    skipped = sum(1 for r in results if r.status in (Status.SKIPPED, Status.WAIVED, Status.UNKNOWN))
    suites = ET.Element("testsuites", name="agent-readiness",
                        tests=str(len(results)), failures=str(failures), skipped=str(skipped))
    by_pillar = {}
    for r in results:
        by_pillar.setdefault(r.pillar, []).append(r)
    for pillar, items in by_pillar.items():
        suite = ET.SubElement(suites, "testsuite", name=pillar, tests=str(len(items)),
                              failures=str(sum(1 for r in items if r.status == Status.FAIL)))
        for r in items:
            case = ET.SubElement(suite, "testcase", classname=f"{pillar}", name=f"{r.id} {r.title}")
            if r.status == Status.FAIL:
                ET.SubElement(case, "failure", message=r.rationale).text = r.rationale
            elif r.status in (Status.SKIPPED, Status.WAIVED, Status.UNKNOWN):
                ET.SubElement(case, "skipped", message=r.status.value)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suites, encoding="unicode")


# ---------------------------------------------------------------------------- sarif
def render_sarif(report) -> str:
    rules, results = [], []
    seen_rules = set()
    for r in report.results:
        if not (r.gating and r.status == Status.FAIL):
            continue
        src = _first_source(r)
        if not src:
            continue  # SARIF only for criteria with a real source location
        if r.id not in seen_rules:
            rules.append({"id": r.id, "name": r.title,
                          "shortDescription": {"text": r.title},
                          "properties": {"pillar": r.pillar, "level": r.level}})
            seen_rules.add(r.id)
        results.append({
            "ruleId": r.id, "level": "warning",
            "message": {"text": r.rationale},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": src}}}],
        })
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "agent-readiness", "version": report.engine_version,
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
        lines.append(f"| {e.get('id', '')} | {e.get('timestamp', '')} | {e.get('level', '')} | "
                     f"{e.get('pass_rate', '')} | {e.get('gating_passed', '')}/{e.get('gating_total', '')} | "
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
