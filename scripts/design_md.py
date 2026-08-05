#!/usr/bin/env python3
"""Generate the machine-readable half of the report design spec from the token module.

Two artifacts are owned here and nowhere else:

* the YAML frontmatter of root ``DESIGN.md`` (everything between the first two ``---`` lines);
  the markdown body below it is hand-written and preserved byte-for-byte.
* ``.impeccable/design.json`` in full — the sidecar carrying what the Stitch frontmatter schema
  cannot hold: tonal ramps, motion, breakpoints and drop-in component snippets.

Both are derived from ``engine/readiness/theme.py``, so the published spec cannot rot away from
the stylesheet the engine actually emits. ``--check`` is the CI gate.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from readiness import theme  # noqa: E402

DESIGN_MD = "DESIGN.md"
SIDECAR = ".impeccable/design.json"
FENCE = "---"

_NUMERIC = re.compile(r"-?\d+(?:\.\d+)?")
_OKLCH = re.compile(r"oklch\((?P<lightness>[\d.]+)% (?P<chroma>[\d.]+) (?P<hue>[\d.]+)\)")

# Dark to light, same hue and chroma. Rendered as a strip under each swatch.
RAMP_STEPS = (15, 26, 37, 48, 60, 71, 82, 93)

# CSS property -> the Stitch frontmatter key for it. The roles themselves live in
# theme.TYPE_ROLES, which is also what report.py generates its type layer from, so the
# published spec and the shipped stylesheet cannot disagree about a weight again.
STITCH_PROPS = {
    "font-family": "fontFamily",
    "font-size": "fontSize",
    "font-weight": "fontWeight",
    "line-height": "lineHeight",
    "letter-spacing": "letterSpacing",
    # tabular-nums is an OpenType feature (`tnum`); fontFeature is the schema's slot for it.
    "font-variant-numeric": "fontFeature",
}

# Frontmatter components. Only Stitch's eight permitted sub-props; anything richer (borders,
# focus rings, states) lives in the sidecar's drop-in snippets instead.
COMPONENTS = {
    "gate-cleared": {"backgroundColor": "{colors.surface-sunken}",
                     "textColor": "{colors.status-pass}", "rounded": "{rounded.sm}",
                     "padding": "{spacing.3}", "typography": "{typography.label}"},
    "gate-blocked": {"backgroundColor": "{colors.surface-sunken}",
                     "textColor": "{colors.status-fail}", "rounded": "{rounded.sm}",
                     "padding": "{spacing.3}", "typography": "{typography.label}"},
    "row-criterion": {"backgroundColor": "{colors.surface}", "textColor": "{colors.text}",
                      "rounded": "{rounded.none}", "padding": "12px 0",
                      "typography": "{typography.title}"},
    "callout-warn": {"backgroundColor": "{colors.surface}", "textColor": "{colors.status-warn}",
                     "rounded": "{rounded.none}", "padding": "0", "width": "72ch",
                     "typography": "{typography.body}"},
    "empty-state": {"backgroundColor": "{colors.surface}", "textColor": "{colors.text-muted}",
                    "rounded": "{rounded.none}", "padding": "0",
                    "typography": "{typography.body}"},
}

# token -> (Stitch role, display name, BRAND.md derivation)
COLOR_META = {
    "bg": ("neutral", "Midnight Page", "Midnight #0B0A1E"),
    "surface": ("neutral", "Midnight Sheet", "Midnight #0B0A1E"),
    "surface-sunken": ("neutral", "Midnight Sunken", "Midnight #0B0A1E"),
    "text": ("neutral", "Hologram Ink", "Hologram #EAF6FF, normalized to hue 283"),
    "text-muted": ("neutral", "Hologram Ink Muted", "Midnight #0B0A1E"),
    "border": ("neutral", "Midnight Rule", "Midnight #0B0A1E"),
    "border-strong": ("neutral", "Midnight Rule Strong", "Midnight #0B0A1E"),
    "accent": ("primary", "Neon Magenta", "Neon Magenta #FF3CAC"),
    "status-pass": ("secondary", "Neon Cyan", "Neon Cyan #2DE2E6"),
    "status-warn": ("tertiary", "Sun Amber", "Sun Amber #FFD36E"),
    "status-fail": ("primary", "Neon Magenta (fail)", "alias of --accent"),
    "status-idle": ("neutral", "Hologram Ink Muted (idle)", "alias of --text-muted"),
    "focus": ("primary", "Neon Magenta (focus)", "alias of --accent"),
}

TYPOGRAPHY_META = {
    "display": "The report title. Once per document.",
    "headline": "Section headings, the status level line, gate numerals.",
    "title": "Row titles and gate names; pillar subheadings in uppercase.",
    "meta": "The muted line under every title, evidence rows, and the footer.",
    "body": "Rationale and prose, capped at the 72ch measure.",
    "label": "Table column headers and gate state words.",
}

# Drop-in snippets for the live panel: self-contained, `ds-`-prefixed, states inline.
SIDECAR_COMPONENTS = [
    {
        "name": "Gate Track",
        "kind": "custom",
        "refersTo": "gate-cleared",
        "description": "Five readiness gates, cleared left to right. Counts, never percentages.",
        "html": ('<ol class="ds-gates">'
                 '<li class="ds-gate ds-gate-cleared"><span class="ds-gate-num">1</span>'
                 '<span class="ds-gate-name">Functional</span>'
                 '<span class="ds-gate-count">5/5</span>'
                 '<span class="ds-gate-state">cleared</span></li>'
                 '<li class="ds-gate ds-gate-blocked"><span class="ds-gate-num">2</span>'
                 '<span class="ds-gate-name">Structured</span>'
                 '<span class="ds-gate-count">3/6</span>'
                 '<span class="ds-gate-state">blocked</span></li>'
                 '<li class="ds-gate ds-gate-locked"><span class="ds-gate-num">3</span>'
                 '<span class="ds-gate-name">Governed</span>'
                 '<span class="ds-gate-count">0/4</span>'
                 '<span class="ds-gate-state">locked</span></li>'
                 '<li class="ds-gate ds-gate-locked"><span class="ds-gate-num">4</span>'
                 '<span class="ds-gate-name">Optimized</span>'
                 '<span class="ds-gate-count">0/3</span>'
                 '<span class="ds-gate-state">locked</span></li>'
                 '<li class="ds-gate ds-gate-empty"><span class="ds-gate-num">5</span>'
                 '<span class="ds-gate-name">Autonomous</span>'
                 '<span class="ds-gate-count">0/0</span>'
                 '<span class="ds-gate-state">no criteria</span></li>'
                 "</ol>"),
        "css": (".ds-gates { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); "
                "gap: var(--space-2); list-style: none; margin: 0; padding: 0; } "
                ".ds-gate { display: grid; gap: var(--space-1); min-width: 0; "
                "padding: var(--space-3); border: var(--hairline) solid var(--border); "
                "border-radius: var(--radius-sm); background: var(--surface-sunken); "
                "font-family: var(--font-sans); color: var(--text); } "
                ".ds-gate-num { font-size: var(--size-headline); "
                "font-weight: var(--weight-strong); line-height: var(--leading-tight); } "
                ".ds-gate-name { font-size: var(--size-title); } "
                ".ds-gate-count { font-family: var(--font-mono); font-size: var(--size-meta); "
                "color: var(--text-muted); } "
                ".ds-gate-state { font-size: var(--size-label); "
                "font-weight: var(--weight-medium); color: var(--text-muted); "
                "text-transform: uppercase; letter-spacing: var(--track-label); } "
                ".ds-gate-cleared .ds-gate-num, .ds-gate-cleared .ds-gate-state "
                "{ color: var(--status-pass); } "
                ".ds-gate-blocked { border: var(--rule) solid var(--border-strong); } "
                ".ds-gate-blocked .ds-gate-num, .ds-gate-blocked .ds-gate-state "
                "{ color: var(--status-fail); } "
                ".ds-gate-locked .ds-gate-num, .ds-gate-empty .ds-gate-num "
                "{ color: var(--status-idle); } "
                ".ds-gate-empty { border-style: dashed; background: none; }"),
    },
    {
        "name": "Criterion Row",
        "kind": "custom",
        "refersTo": "row-criterion",
        "description": "One criterion: badge, title, meta line, rationale. No card, no fill.",
        "html": ('<li class="ds-row ds-status-fail">'
                 '<p class="ds-row-head">'
                 '<span class="ds-badge" aria-hidden="true">\u2717</span>'
                 '<span class="ds-row-title">README present</span>'
                 '<span class="ds-row-meta">Fail \u00b7 gating \u00b7 L1 \u00b7 0/1</span></p>'
                 '<p class="ds-rationale">No README.md found at the repository root.</p>'
                 "</li>"),
        "css": (".ds-row { list-style: none; padding: var(--space-3) 0; "
                "border-top: var(--hairline) solid var(--border); "
                "font-family: var(--font-sans); color: var(--text); } "
                ".ds-row-head { margin: 0; display: flex; flex-wrap: wrap; "
                "gap: var(--space-1) var(--space-2); align-items: baseline; } "
                ".ds-row-title { font-size: var(--size-title); "
                "font-weight: var(--weight-strong); } "
                ".ds-row-meta { font-size: var(--size-meta); color: var(--text-muted); } "
                ".ds-rationale { margin: var(--space-1) 0 0; max-width: var(--prose-max); "
                "font-size: var(--size-body); line-height: var(--leading-normal); } "
                ".ds-status-fail .ds-badge, .ds-status-fail .ds-row-meta "
                "{ color: var(--status-fail); }"),
    },
    {
        "name": "Status Badge",
        "kind": "chip",
        "refersTo": "row-criterion",
        "description": "The status glyph. Always paired with the status word beside it.",
        "html": ('<span class="ds-badge ds-badge-pass" aria-hidden="true">\u2713</span>'
                 '<span class="ds-badge ds-badge-fail" aria-hidden="true">\u2717</span>'
                 '<span class="ds-badge ds-badge-warn" aria-hidden="true">?</span>'
                 '<span class="ds-badge ds-badge-idle" aria-hidden="true">\u2298</span>'),
        "css": (".ds-badge { font-variant-emoji: text; font-family: var(--font-sans); "
                "margin-right: var(--space-2); } "
                ".ds-badge-pass { color: var(--status-pass); } "
                ".ds-badge-fail { color: var(--status-fail); } "
                ".ds-badge-warn { color: var(--status-warn); } "
                ".ds-badge-idle { color: var(--status-idle); }"),
    },
    {
        "name": "Evidence Disclosure",
        "kind": "custom",
        "refersTo": "row-criterion",
        "description": "Closed by default; the only animated element in the artifact.",
        "html": ("<details class=\"ds-details\">"
                 '<summary class="ds-summary">Evidence (2)</summary>'
                 '<ol class="ds-evidence">'
                 '<li><span class="ds-tier">T0</span> README.md exists '
                 "<code>README.md</code></li>"
                 '<li><span class="ds-tier">T1</span> 42 commits in the last 90 days</li>'
                 "</ol></details>"),
        "css": (".ds-details { margin: var(--space-2) 0 0; font-family: var(--font-sans); } "
                ".ds-summary { cursor: pointer; width: fit-content; "
                "font-size: var(--size-meta); color: var(--text-muted); "
                "transition: color var(--duration) var(--ease); } "
                ".ds-summary:hover { color: var(--text); } "
                ".ds-summary:focus-visible { outline: var(--focus-width) solid var(--focus); "
                "outline-offset: var(--focus-offset); } "
                ".ds-evidence { list-style: none; margin: var(--space-2) 0 0; "
                "padding: var(--space-1) 0 var(--space-1) var(--space-3); "
                "border-left: var(--hairline) solid var(--border); } "
                ".ds-evidence > li { padding: var(--space-1) 0; font-size: var(--size-meta); } "
                ".ds-tier, .ds-evidence code { font-family: var(--font-mono); "
                "font-size: var(--size-meta); color: var(--text-muted); }"),
    },
    {
        "name": "Section Header",
        "kind": "custom",
        "refersTo": "row-criterion",
        "description": "Labelled section: heading, hairline separation, no chrome.",
        "html": ('<section class="ds-section" aria-labelledby="ds-demo-heading">'
                 '<h2 class="ds-section-title" id="ds-demo-heading">Readiness Status</h2>'
                 '<p class="ds-status-level">Level 4 \u2014 Optimized</p>'
                 '<p class="ds-section-meta">100% pass rate \u00b7 24/24 gating criteria</p>'
                 "</section>"),
        "css": (".ds-section { margin: 0 0 var(--space-6); padding-bottom: var(--space-5); "
                "border-bottom: var(--hairline) solid var(--border); "
                "font-family: var(--font-sans); color: var(--text); } "
                ".ds-section-title { font-size: var(--size-headline); "
                "font-weight: var(--weight-strong); line-height: var(--leading-snug); "
                "margin: 0 0 var(--space-3); } "
                ".ds-status-level { font-size: var(--size-headline); "
                "font-weight: var(--weight-strong); margin: 0; } "
                ".ds-section-meta { font-size: var(--size-meta); color: var(--text-muted); "
                "margin: var(--space-1) 0 0; }"),
    },
    {
        "name": "Callout",
        "kind": "custom",
        "refersTo": "callout-warn",
        "description": "A tonal note. No fill, no icon chrome, no colored side stripe.",
        "html": ('<p class="ds-callout ds-tone-warn">\u26a0\ufe0f Project type is '
                 "<strong>unknown</strong> (low detection confidence); type-dependent criteria "
                 "are reported as <code>unknown</code>, not silently skipped.</p>"),
        "css": (".ds-callout { max-width: var(--prose-max); margin-top: var(--space-3); "
                "font-family: var(--font-sans); font-size: var(--size-body); "
                "line-height: var(--leading-normal); } "
                ".ds-tone-warn { color: var(--status-warn); } "
                ".ds-callout code { font-family: var(--font-mono); "
                "font-size: var(--size-meta); }"),
    },
    {
        "name": "Empty State",
        "kind": "custom",
        "refersTo": "empty-state",
        "description": "Absence is stated, never rendered as a blank section.",
        "html": '<p class="ds-empty">No criteria results available</p>',
        "css": (".ds-empty { font-family: var(--font-sans); font-size: var(--size-meta); "
                "color: var(--text-muted); font-style: italic; margin: 0; }"),
    },
    {
        "name": "Data Table",
        "kind": "custom",
        "refersTo": "row-criterion",
        "description": "The applications table. The only element allowed to scroll sideways.",
        "html": ('<div class="ds-table-scroll"><table class="ds-table">'
                 '<thead><tr><th scope="col">Path</th><th scope="col">Deploy surface</th>'
                 '<th scope="col">Runtime</th></tr></thead>'
                 '<tbody><tr><th scope="row"><code>.</code></th><td>service</td>'
                 "<td>python3.11</td></tr></tbody></table></div>"),
        "css": (".ds-table-scroll { overflow-x: auto; } "
                ".ds-table { border-collapse: collapse; width: 100%; "
                "font-family: var(--font-sans); color: var(--text); } "
                ".ds-table th, .ds-table td { text-align: left; "
                "padding: var(--space-2) var(--space-5) var(--space-2) 0; "
                "border-bottom: var(--hairline) solid var(--border); white-space: nowrap; "
                "font-weight: var(--weight-normal); font-size: var(--size-body); } "
                ".ds-table thead th { font-size: var(--size-label); "
                "font-weight: var(--weight-medium); text-transform: uppercase; "
                "letter-spacing: var(--track-label); color: var(--text-muted); } "
                ".ds-table code { font-family: var(--font-mono); "
                "font-size: var(--size-meta); }"),
    },
]

# ---------------------------------------------------------------------------- narrative
# The sidecar's narrative is not a second copy of the prose — it is parsed out of DESIGN.md,
# so the two can never say different things.
_NORTH_STAR = re.compile(r'\*\*Creative North Star: "(?P<name>.+?)\.?"\*\*')
_RULE = re.compile(r"\*\*(?P<name>The .+? Rule)\.\*\* (?P<body>.+)")
_HEADING = re.compile(r"## \d+\. (?P<title>.+)")


def _paragraphs(lines):
    """[(section slug, joined paragraph)] — wrapped markdown lines rejoined with a space."""
    out, buffer, section = [], [], ""
    for line in [*lines, ""]:
        heading = _HEADING.fullmatch(line.strip())
        section = heading["title"].lower() if heading else section
        if line.strip():
            buffer.append(line.strip())
        elif buffer:
            out.append((section, " ".join(buffer)))
            buffer = []
    return out


def _bullets(lines, marker: str) -> list:
    """The contiguous `- ` list that follows `marker`, each item unwrapped to one string."""
    rest = _after(lines, marker)
    items = []
    for line in rest:
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.strip() and items:
            items[-1] += " " + line.strip()
        elif items:
            return items
    raise ValueError(f"{DESIGN_MD}: no bullet list follows {marker!r}")


def _after(lines, marker: str) -> list:
    for index, line in enumerate(lines):
        if line.strip() == marker:
            return lines[index + 1:]
    raise ValueError(f"{DESIGN_MD}: expected a {marker!r} line")


def _north_star(lines) -> str:
    for line in lines:
        found = _NORTH_STAR.search(line)
        if found:
            return found["name"]
    raise ValueError(f"{DESIGN_MD}: no '**Creative North Star: \"...\"**' line")


def _overview(paragraphs) -> str:
    """The Overview philosophy: the prose between the North Star and Key Characteristics."""
    body = [text for section, text in paragraphs
            if section == "overview" and not text.startswith("#")
            and not _NORTH_STAR.search(text)
            and not text.startswith("**Key Characteristics:**")
            and not text.startswith("- ") and not _RULE.fullmatch(text)]
    return "\n\n".join(body)


def narrative(document: str) -> dict:
    lines = _body(document)
    paragraphs = _paragraphs(lines)
    return {
        "northStar": _north_star(lines),
        "overview": _overview(paragraphs),
        "keyCharacteristics": _bullets(lines, "**Key Characteristics:**"),
        "rules": [{"name": found["name"], "section": section, "body": found["body"]}
                  for section, text in paragraphs
                  for found in [_RULE.fullmatch(text)] if found],
        "dos": _bullets(lines, "### Do:"),
        "donts": _bullets(lines, "### Don't:"),
    }


# ---------------------------------------------------------------------------- token views
def colors(scheme: int = 0) -> dict:
    """Every color name the stylesheet exposes, aliases resolved to their target's value.

    ``scheme`` indexes the (light, dark) pair, so the sidecar can record both halves of a
    token — including for aliases, which carry their target's dark value too.
    """
    resolved = {name: pair[scheme] for name, pair in theme.COLOR_TOKENS.items()}
    resolved.update({name: resolved[target] for name, target in theme.COLOR_ALIASES.items()})
    return resolved


def scale(prefix: str) -> dict:
    """The `prefix-*` scale tokens, keyed by the part after the prefix."""
    return {name[len(prefix):]: value for name, value in theme.SCALE_TOKENS.items()
            if name.startswith(prefix)}


def typography() -> dict:
    """The role table, translated from CSS property names to Stitch's frontmatter keys."""
    return {role: {STITCH_PROPS[prop]: theme.SCALE_TOKENS[token]
                   for prop, token in props.items()}
            for role, props in theme.TYPE_ROLES.items()}


def ramp(value: str) -> list:
    """An 8-step tonal ramp holding the token's hue and chroma, dark to light."""
    parsed = _OKLCH.fullmatch(value)
    return [f"oklch({step}% {parsed['chroma']} {parsed['hue']})" for step in RAMP_STEPS]


# ---------------------------------------------------------------------------- emission
def _scalar(value: str) -> str:
    return value if _NUMERIC.fullmatch(value) else json.dumps(value)


def _key(name: str) -> str:
    return json.dumps(name) if _NUMERIC.fullmatch(name) else name


def _yaml(mapping, indent: int = 0) -> str:
    pad = "  " * indent
    out = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            out.append(f"{pad}{_key(key)}:\n" + _yaml(value, indent + 1))
        else:
            out.append(f"{pad}{_key(key)}: {_scalar(value)}\n")
    return "".join(out)


def frontmatter() -> str:
    """The YAML between DESIGN.md's two `---` fences. Stitch's five token groups, nothing else."""
    return _yaml({
        "colors": colors(),
        "typography": typography(),
        "rounded": scale("radius-"),
        "spacing": scale("space-"),
        "components": COMPONENTS,
    })


def sidecar(document: str) -> str:
    light, dark = colors(0), colors(1)
    return json.dumps({
        "schemaVersion": 2,
        "title": "Design System: Ready Agent 1 — Readiness Report",
        "extensions": {
            "themeVersion": theme.THEME_VERSION,
            "colorMeta": {
                name: {
                    "role": role,
                    "displayName": display,
                    "source": source,
                    "canonical": light[name],
                    "dark": dark[name],
                    "tonalRamp": ramp(light[name]),
                }
                for name, (role, display, source) in COLOR_META.items()
            },
            "typographyMeta": {
                role: {"displayName": role.capitalize(), "purpose": purpose}
                for role, purpose in TYPOGRAPHY_META.items()
            },
            "motion": [
                {"name": "duration", "value": theme.SCALE_TOKENS["duration"],
                 "purpose": "The summary color transition — the artifact's only motion."},
                {"name": "ease", "value": theme.SCALE_TOKENS["ease"],
                 "purpose": "Ease-out-quint, paired with duration."},
            ],
            "breakpoints": [
                {"name": "narrow", "value": theme.NARROW_BREAKPOINT,
                 "purpose": "Gate track and report padding collapse below this width."},
            ],
        },
        "components": SIDECAR_COMPONENTS,
        "narrative": narrative(document),
    }, indent=2, ensure_ascii=False) + "\n"


def splice(document: str, block: str) -> str:
    """Replace the frontmatter of `document`, preserving the hand-written body byte-for-byte."""
    lines = document.split("\n")
    closing = _closing_fence(lines)
    return "\n".join([FENCE, block.rstrip("\n"), *lines[closing:]])


def _closing_fence(lines) -> int:
    """Index of the frontmatter's closing `---`, or a ValueError naming what is wrong."""
    if not lines or lines[0] != FENCE:
        raise ValueError(f"{DESIGN_MD} must open with a '{FENCE}' frontmatter fence")
    for index, line in enumerate(lines[1:], 1):
        if line == FENCE:
            return index
    raise ValueError(f"{DESIGN_MD} frontmatter is never closed by a second '{FENCE}'")


def _body(document: str) -> list:
    """The hand-written markdown lines, with the generated frontmatter stripped off."""
    lines = document.split("\n")
    return lines[_closing_fence(lines) + 1:]


# ---------------------------------------------------------------------------- gate
def plan(root: Path) -> list:
    """[(relative path, desired full content)] for both generated artifacts."""
    design = root / DESIGN_MD
    if not design.exists():
        raise ValueError(f"{DESIGN_MD} not found at {root}; the markdown body is hand-written "
                         "and cannot be generated")
    document = design.read_text(encoding="utf-8")
    return [
        (DESIGN_MD, splice(document, frontmatter())),
        (SIDECAR, sidecar(document)),
    ]


def _diff(rel: str, expected: str, actual: str) -> list:
    if actual == expected:
        return []
    return [f"{rel}:", *difflib.unified_diff(
        actual.splitlines(), expected.splitlines(),
        fromfile=f"{rel} (committed)", tofile=f"{rel} (generated)", lineterm="")]


def generate(root, check: bool = False) -> list:
    """Write both artifacts (check=False) or return the drift between them (check=True)."""
    root = Path(root)
    drift = []
    for rel, content in plan(root):
        dst = root / rel
        if check:
            actual = dst.read_text(encoding="utf-8") if dst.exists() else ""
            drift.extend(_diff(rel, content, actual))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding="utf-8")
    return drift


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    try:
        drift = generate(ROOT, check=check)
    except ValueError as exc:
        sys.stderr.write(f"design_md: {exc}\n")
        return 1
    if not check:
        print(f"generated {DESIGN_MD} frontmatter + {SIDECAR}")
        return 0
    if drift:
        sys.stderr.write("DESIGN DRIFT (run scripts/design_md.py to sync):\n"
                         + "\n".join(drift) + "\n")
        return 1
    print(f"{DESIGN_MD} and {SIDECAR} are in sync with theme.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
