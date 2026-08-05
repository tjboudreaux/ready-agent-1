"""Report renderers. JSON is canonical; Markdown is the human report; GitHub Checks /
JUnit / SARIF are CI surfaces. SARIF carries ONLY criteria with real source locations
(per the review — repo-level claims like "backlog health" don't belong in code scanning).
"""
from __future__ import annotations

import html
import json
import math
import os
from xml.etree import ElementTree as ET

from . import theme
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


def render(report, fmt: str | None) -> str:
    canonical = normalize_format(fmt)
    if canonical == "json":
        return json.dumps(report.to_dict(), indent=2)
    if canonical == "markdown":
        return render_markdown(report)
    if canonical == "html":
        return render_html(report)
    if canonical == "github":
        return render_github(report)
    if canonical == "junit":
        return render_junit(report)
    return render_sarif(report)


def _location(d) -> str:
    """Redacted scan location for the human subtitle — never the raw absolute path.

    A relative scan root (`ra1 report` with no --project) has no useful basename: "." would
    print as a bare separator in the meta line. Resolve it to the directory's own name,
    which is still just a name and never a path.
    """
    repo = d.repository or {}
    if repo.get("identity_kind") == "origin" and repo.get("owner"):
        return f"{repo['owner']}/{repo.get('name', '')}"
    return repo.get("name") or os.path.basename(os.path.abspath(d.project_path))


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
            lines.append(
                f"- **L{lv.level} {lv.name}**: {lv.passed}/{lv.total} "
                f"({round(lv.ratio*100)}%) — {mark}")
        lines.append("")

    lines.append("## Criteria Results")
    for pillar in _pillars_in_order(d.results):
        lines.append("")
        lines.append(f"### {pillar}")
        for r in [x for x in d.results if x.pillar == pillar]:
            sym = _SYMBOL.get(r.status.value, "?")
            gate_label = "gating" if r.gating else "**advisory**"
            acdc = f", {_acdc_label(r)}" if _acdc_label(r) else ""
            lines.append(
                f"- {sym} **{r.title}** ({gate_label}, L{r.level}{acdc}, {_display_score(r)}): "
                f"{r.rationale}")

    recs = _recommendations(d.results, score.level if score else 0)
    if recs:
        lines.append("")
        lines.append("## Action Items")
        lines.append("")
        lines.append(
            f"_Top {len(recs)} highest-impact gating next steps "
            f"(clear the next level first)._")
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
                acdc = f", {_acdc_label(r)}" if _acdc_label(r) else ""
                lines.append(f"- {r.title} (L{r.level}, {r.pillar}{acdc}) — {r.rationale}")

    judgments = [r for r in d.results if r.id.startswith("judgment.")]
    if judgments:
        assess = [r for r in judgments if r.status == Status.UNKNOWN]
        ignored = [r for r in judgments if r.status == Status.WAIVED]
        lines.append("")
        lines.append("## Agent Judgments (advisory, never scored)")
        if assess:
            lines.append("")
            lines.append("To assess: " + ", ".join(r.title for r in assess) + ".")
        if ignored:
            lines.append("")
            lines.append(
                f"Ignored judgments ({len(ignored)}): "
                + ", ".join(r.title for r in ignored)
                + " — silenced via .agents/readiness/config.json `judgments`.")

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


_ACDC_LOOPS = {"inner": "inner loop", "outer": "outer loop", "both": "both loops"}


def _acdc_label(r):
    """'inner loop · verify' when the registry maps the criterion into the AC/DC model.

    Returns "" for unmapped criteria so callers can drop it into any meta join unchanged.
    """
    if not r.acdc_stage:
        return ""
    return f"{_ACDC_LOOPS.get(r.acdc_loop, r.acdc_loop)} · {r.acdc_stage}"


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
    "title": "h3, .gate-name, .row-title",
    "body": "body, tbody th, tbody td",
    "meta": (".meta, .row-meta, .gate-count, .row-id, code, .tier, .detail, .empty,\n"
             ".note, .report-footer, summary, .evidence > li, .facet, .pillar-why"),
    "label": "thead th, .gate-state, .facet-legend, .pillar-state",
}

_STATIC_CSS = """
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  overflow-wrap: break-word;
}
code, .tier, .row-id, .gate-count {
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
.row-head {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  align-items: baseline;
}
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
.status-fail.needs-action .badge { background: var(--status-fail); }
.status-unknown.needs-action .badge { background: var(--status-warn); }
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
/* Legend above its row, not beside it: the pillar group has nine chips and a narrow
   column forced them into a ragged two-line wrap. */
.facets { margin-bottom: var(--space-5); }
.facet-group + .facet-group { margin-top: var(--space-3); }
/* Status is the filter a reader reaches for, so its legend sits at full text colour and
   the pillar legend stays muted. Same chip geometry, different rank. */
.facet-legend {
  margin: 0 0 var(--space-1);
  color: var(--text);
  text-transform: uppercase;
}
.facet-plain .facet-legend { color: var(--text-muted); }
.facet-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
}
/* Status is the primary filter and keeps its chip box. Pillar is secondary: nine boxes
   was the noise in the screenshot, so those are text plus a mark. */
.facet-plain .facet { border-color: transparent; padding-left: 0; padding-right: 0; }
.facet-plain .facet-list { gap: var(--space-1) var(--space-4); }
.facet {
  --mark: currentColor;
  --check-fill: transparent;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border: var(--hairline) solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  cursor: pointer;
}
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
.status-pass .badge, .status-pass .row-meta { color: var(--status-pass); }
.status-fail .badge, .status-fail .row-meta { color: var(--status-fail); }
.status-unknown .badge, .status-unknown .row-meta { color: var(--status-warn); }
.status-skipped .badge, .status-waived .badge { color: var(--status-idle); }
/* Knocked out of the fill. Higher specificity than the status colour rules above, which
   would otherwise paint the glyph the same colour as the square it sits in. */
.status-fail.needs-action .badge,
.status-unknown.needs-action .badge { color: var(--surface); }
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
}
@media print {
  body { background: none; }
  .report { max-width: none; padding: 0; background: none; }
  details:not([open]) > *:not(summary) { display: block; }
  details::details-content { content-visibility: visible; display: block; }
  .facets, .facet-input { display: none; }
  .criteria-body .criterion, .criteria-body .pillar { display: block !important; }
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


def _row(out, *, cls, title, meta, badge="", ident="", rationale="", extra=()) -> None:
    out += [
        f'<li class="row {cls}">',
        '<p class="row-head">',
        badge,
        f'<span class="row-title">{_html(title)}</span>',
        ident,
        f'<span class="row-meta">{meta}</span>',
        "</p>",
    ]
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


def render_html(report) -> str:
    d = report
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
    _html_criteria(out, d)
    _html_advisory_improvements(out, d)
    _html_applications(out, d)
    _html_judgments(out, d)
    _html_advisory(out, d)
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
        _gate_track(out, s.levels)
    else:
        _empty(out, "Score unavailable")
    out.append("</section>")


def _gate_track(out, levels) -> None:
    """Five gates, cleared left to right. Counts only — a percentage of nothing is a lie."""
    if not levels:
        _empty(out, "No level data available")
        return
    out.append('<ol class="gates">')
    blocked_marked = False
    for lv in levels:
        if not lv.total:
            cls, state = "gate-empty", "no criteria"
        elif lv.achieved:
            cls, state = "gate-cleared", "cleared"
        elif blocked_marked:
            cls, state = "gate-locked", "locked"
        else:
            cls, state, blocked_marked = "gate-blocked", "blocked", True
        out += [
            f'<li class="gate {cls}">',
            f'<span class="gate-num">{_html(lv.level)}</span>',
            f'<span class="gate-name">{_html(lv.name)}</span>',
            f'<span class="gate-count">{_html(lv.passed)}/{_html(lv.total)}</span>',
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
    out += ["</ol>", "</div>", "</section>"]


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
    recs = _recommendations(d.results, d.score.level) if d.score else []
    if not recs:
        return
    _section(out, "actions", "Action Items")
    out += [f'<p class="note">Top {_html(len(recs))} highest-impact gating next steps '
            "(clear the next level first).</p>",
            '<ol class="actions">']
    for rec in recs:
        effort = _EFFORT.get(rec.get("fix_kind", ""), _EFFORT[""])
        _row(out, cls="action", title=rec["title"], ident=_ident(rec["id"]),
             meta=_meta([f"L{rec['level']}", rec["pillar"], effort]),
             rationale=rec["rationale"])
    out += ["</ol>", "</section>"]


def _facet_model(d):
    """The closed facet set: (status facets, pillar facets, per-pillar status ids).

    Every id is derived from an enum value or an ordinal, never from repository text, so
    the generated CSS and the `for=` attributes can only ever contain authored constants.
    """
    pillars = _pillars_in_order(d.results)
    statuses = [s for s in _DIST_ORDER if any(r.status.value == s for r in d.results)]
    status_facets = [(f"f-s-{s}", s.capitalize(),
                      sum(1 for r in d.results if r.status.value == s),
                      s != "skipped")
                     for s in statuses]
    pillar_facets = [(f"f-p{i}", name, sum(1 for r in d.results if r.pillar == name), True)
                     for i, name in enumerate(pillars, 1)]
    # A pillar group is empty once every status it actually contains is switched off, so
    # each group carries the ids that keep it alive. Without this the heading survives its
    # own rows and the reader gets a section with nothing under it.
    owners = [(f"p{i}", [f"f-s-{s}" for s in _pillar_statuses(d.results, name)])
              for i, name in enumerate(pillars, 1)]
    return status_facets, pillar_facets, owners


def _pillar_statuses(results, pillar):
    return [s for s in _DIST_ORDER if any(r.pillar == pillar and r.status.value == s
                                          for r in results)]


def _filter_css(model) -> str:
    """Filtering with no script: each checkbox precedes the rows it governs, so a plain

    sibling combinator does the work and no `:has()` support is required.

    The empty state is inverted deliberately. Showing it only when every status is off
    misses the cross-facet hole — check Fail alone and Build alone and both groups vanish
    while a status is still checked. So it defaults to visible and each surviving
    (status, pillar) pair hides it: visible content and the message are mutually exclusive
    by construction rather than by enumerating the ways a filter can empty the list.
    """
    status_facets, pillar_facets, owners = model
    if not status_facets:
        return ""
    rules = [f"#{fid}:not(:checked) ~ .criteria-body .criterion.status-{fid[4:]}"
             " { display: none; }" for fid, *_ in status_facets]
    rules += [f"#{fid}:not(:checked) ~ .criteria-body .{fid[2:]}"
              " { display: none; }" for fid, *_ in pillar_facets]
    rules += [" ~ ".join(f"#{sid}:not(:checked)" for sid in sids)
              + f" ~ .criteria-body .{cls}" " { display: none; }"
              for cls, sids in owners if sids]
    rules += [f"#{sid}:checked ~ #f-{cls}:checked ~ .criteria-body .criteria-empty"
              " { display: none; }" for cls, sids in owners for sid in sids]
    # Pillar chips default to checked, so the checked rule is what a reader actually sees.
    # Split it by rank: status keeps the box, pillar stays text plus a mark. Sharing one
    # rule silently re-boxed all nine pillar chips in the default state.
    selector = lambda facets, state: ",\n".join(  # noqa: E731 - local formatting helper
        f'#{fid}:{state} ~ .facets label[for="{fid}"]' for fid, *_ in facets)
    rules.append(selector(status_facets, "checked")
                 + " {\n  --check-fill: var(--mark);\n  color: var(--text);\n"
                 "  border-color: var(--border-strong);\n"
                 "  background: var(--surface-sunken);\n}")
    rules.append(selector(pillar_facets, "checked")
                 + " {\n  --check-fill: var(--mark);\n  color: var(--text);\n"
                 "  border-color: transparent;\n  background: none;\n}")
    rules.append(selector(status_facets + pillar_facets, "focus-visible")
                 + " {\n  outline: var(--focus-width) solid var(--focus);\n"
                 "  outline-offset: var(--focus-offset);\n}")
    return "\n" + "\n".join(rules) + "\n"


def _facets(out, model) -> None:
    status_facets, pillar_facets, _owners = model
    for fid, _label, _count, checked in status_facets + pillar_facets:
        out.append(f'<input class="facet-input visually-hidden" type="checkbox" id="{fid}"'
                   + (" checked" if checked else "") + ">")
    out.append('<div class="facets">')
    groups = (("Status", "", status_facets), ("Pillar", " facet-plain", pillar_facets))
    for legend, variant, facets in groups:
        out += [f'<div class="facet-group{variant}">',
                f'<p class="facet-legend">{legend}</p>', '<ul class="facet-list">']
        for fid, label, count, _checked in facets:
            # `f-s-pass` -> `facet-pass`; pillar ids carry no status tint.
            tint = f" facet-{fid[4:]}" if fid.startswith("f-s-") else ""
            out.append(f'<li><label class="facet{tint}" for="{fid}">'
                       + f'<span class="facet-name">{_html(label)}</span>'
                       + f'<span class="facet-count">{_html(count)}</span></label></li>')
        out += ["</ul>", "</div>"]
    out.append("</div>")


def _html_criteria(out, d) -> None:
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
            "applicable gating criteria.</p>",
            _distribution([(s, sum(1 for r in d.results if r.status.value == s))
                           for s in _DIST_ORDER])]
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
            _html_criterion(out, r, index)
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
    return r.id.startswith("judgment.")


def _blocking(r) -> bool:
    """Does ignoring this row cost a level?

    Only gating criteria move the score, and score.py counts UNKNOWN as 0/1 exactly like a
    failure. An advisory failure is worth doing but blocks nothing, so shouting about it in
    the same voice as a blocked gate teaches the reader to distrust the loud voice.
    """
    return r.gating and (r.status == Status.FAIL
                         or (r.status == Status.UNKNOWN and not _is_judgment(r)))


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
    if r.status == Status.FAIL:
        return _ACTIONS.get(r.fix_kind or "", "")
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


def _html_criterion(out, r, index) -> None:
    status = r.status.value
    action = _action(r)
    tier = " needs-action" if _blocking(r) else (" suggested" if _suggested(r) else "")
    extra = [_evidence(r.evidence, f"tip-{index}")]
    if action:
        extra.insert(0, f'<p class="next-step">{action}</p>')
    # N/M earns its place when it is not tautological: a repository-scope pass is always
    # 1/1, and printing it on every green row is three tokens saying nothing.
    partial = r.passed_apps != r.evaluated_apps
    score = _display_score(r) if (partial or r.evaluated_apps > 1) else ""
    _row(out, cls=f"criterion status-{status}{tier}", badge=_badge(status), title=r.title,
         meta=_meta([status.capitalize(), _stakes(r), _acdc_label(r), score]),
         rationale=r.rationale, extra=tuple(extra))


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
                   + " — silenced via <code>.agents/readiness/config.json</code> "
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


def _html_footer(out, d) -> None:
    parts = []
    if d.generated_at:
        parts.append(f"generated {d.generated_at}")
    parts += [f"registry {d.registry_version}", f"detector {d.detector_version}"]
    out += ['<footer class="report-footer">',
            f"<p>{_meta(parts)}</p>",
            "</footer>"]


# ---------------------------------------------------------------------------- github
def _gha_escape_property(value) -> str:
    return (str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            .replace(":", "%3A").replace(",", "%2C"))


def render_github(report) -> str:
    out = []
    for r in report.results:
        if r.gating and r.status == Status.FAIL:
            src = _first_source(r)
            title = _gha_escape_property(f"Readiness: {r.title}")
            props = [f"title={title}"]
            if src:
                props.append(f"file={_gha_escape_property(src)}")
            out.append(f"::warning {','.join(props)}::{r.rationale}")
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
