---
colors:
  bg: "oklch(97% 0.01 283)"
  surface: "oklch(99% 0.006 283)"
  surface-sunken: "oklch(94% 0.015 283)"
  text: "oklch(24% 0.03 283)"
  text-muted: "oklch(48% 0.025 283)"
  border: "oklch(86% 0.02 283)"
  border-strong: "oklch(74% 0.025 283)"
  accent: "oklch(47% 0.20 351)"
  status-pass: "oklch(40% 0.11 197)"
  status-warn: "oklch(43% 0.11 86)"
  chart-grid: "oklch(90% 0.015 283)"
  chart-fill: "oklch(47% 0.20 351 / 0.14)"
  status-fail: "oklch(47% 0.20 351)"
  status-idle: "oklch(48% 0.025 283)"
  focus: "oklch(47% 0.20 351)"
  chart-track: "oklch(94% 0.015 283)"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif"
    fontSize: "1.1875rem"
    fontWeight: 600
    lineHeight: 1.4
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
  meta:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "tabular-nums"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.08em"
    fontFeature: "tabular-nums"
rounded:
  none: 0
  sm: "2px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "24px"
  "6": "32px"
  "7": "48px"
  "8": "64px"
components:
  gate-cleared:
    backgroundColor: "{colors.surface-sunken}"
    textColor: "{colors.status-pass}"
    rounded: "{rounded.sm}"
    padding: "{spacing.3}"
    typography: "{typography.label}"
  gate-blocked:
    backgroundColor: "{colors.surface-sunken}"
    textColor: "{colors.status-fail}"
    rounded: "{rounded.sm}"
    padding: "{spacing.3}"
    typography: "{typography.label}"
  row-criterion:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.none}"
    padding: "12px 0"
    typography: "{typography.title}"
  callout-warn:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-warn}"
    rounded: "{rounded.none}"
    padding: 0
    width: "72ch"
    typography: "{typography.body}"
  empty-state:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.none}"
    padding: 0
    typography: "{typography.body}"
---

# Design System: Ready Agent 1 — Readiness Report

<!-- The YAML frontmatter above and .impeccable/design.json are GENERATED from
     engine/readiness/theme.py by scripts/design_md.py. Edit the tokens there, then run
     `python3 scripts/design_md.py`. CI runs `--check`. The prose below is hand-written. -->

## 1. Overview

**Creative North Star: "The Play Screen."**

An arcade cabinet has two screens. *Attract mode* is the neon demo that runs when nobody has
inserted a coin: perspective grid, glow, scanlines, spectacle. The *play screen* is what you get
when you press start, and it is legible, high-contrast and readable at speed, because you have to
actually play it. `BRAND.md` already draws this line — "neon and hype live in the banner, headers,
and marketing. **Errors, reports, and instructions stay plain and useful.**" The HTML readiness
report is the play screen. It carries the cabinet's color DNA — violet-tinted neutrals, magenta /
cyan / amber status — pulled to playable contrast, with zero spectacle.

The physical scene the system is designed for: an engineer downloads a CI artifact after a
readiness gate blocked their merge, opens it from `~/Downloads` in a browser tab beside the failing
PR, and wants the blocking gate and the first fix without scrolling. The hour is unknown — it may be
a 2pm review or a 2am incident. That unknown hour is why the artifact **adapts** via
`prefers-color-scheme` instead of committing to one theme; both schemes are fully specified and
neither is a degraded fallback.

This system explicitly rejects the two category reflexes. It is not the first-order "developer tool
→ dark terminal, navy and blue": the surface is a violet-tinted adaptive neutral, not a terminal.
It is not the second-order "dev tool that isn't terminal-dark → GitHub-grey with blue links":
neutrals are hue-tinted rather than gray, status uses the cabinet's magenta / cyan / amber rather
than the default blue-green-red, and the artifact contains no links at all. Every future question
resolves against one test: **attract mode, or play screen?** If it is decoration, it is attract mode
and it does not ship here.

**Key Characteristics:**

- Single self-contained file — renders from `file://` with no server, no network, no sibling assets.
- Adaptive light/dark, both schemes first-class, every pairing at or above 4.5:1.
- Violet-tinted neutrals on one hue; no untinted gray anywhere.
- Status carried by symbol and word before color.
- Flat and square: no shadows, no gradients, no glow, no elevation scale.
- Document density, not dashboard density — hairline rules, generous prose measure, one column.

### Named Rules

**The Play-Screen Rule.** Reports carry the cabinet's hue, never its spectacle. No gradients, no
glow, no scanlines, no perspective grid, no display face, no decorative animation. Verbatim from
`BRAND.md`: errors, reports, and instructions stay plain and useful.

## 2. Colors

The palette is the arcade cabinet's, sampled at playing brightness: one violet hue carries every
neutral, and the three neon statuses are the `BRAND.md` neon values pulled down (light) or up (dark)
until they clear text contrast against the surface they sit on. Values are OKLCH because the
stylesheet is OKLCH-only; the hex in each entry is the `BRAND.md` source it derives from.

### Primary

- **Neon Magenta** (`#FF3CAC` → hue 351): the cabinet's primary neon and the wordmark color. In the
  report it is the failure signal and the focus ring — `--accent`, aliased by `--status-fail` and
  `--focus`. It appears on failing criterion badges and meta lines, on the blocked gate's numeral
  and state word, and nowhere decorative.

### Secondary

- **Neon Cyan** (`#2DE2E6` → hue 197): the pass signal, `--status-pass`. Passing criterion badges
  and meta lines, cleared gate numerals and state words.

### Tertiary

- **Sun Amber** (`#FFD36E` → hue 86): the caution signal, `--status-warn`. Criteria whose status is
  `unknown`, and the project-type warning callout.

### Neutral

Seven steps, all on Midnight's hue 283 (`#0B0A1E`). `--text` is inspired by Hologram (`#EAF6FF`) but
normalized from its native hue 240 to 283, because The Same-Hue Rule outranks palette fidelity for
neutrals.

- **Midnight Page** (`--bg`): the page behind the report column.
- **Midnight Sheet** (`--surface`): the report column itself — the reading surface.
- **Midnight Sunken** (`--surface-sunken`): recessed panels; the gate cards sit on it.
- **Hologram Ink** (`--text`): body text.
- **Hologram Ink Muted** (`--text-muted`): meta lines, labels, evidence, the idle gate numeral —
  also aliased as `--status-idle` for skipped and waived criteria.
- **Midnight Rule** (`--border`): hairline dividers between sections, rows, and table cells.
- **Midnight Rule Strong** (`--border-strong`): the 2px outline that isolates the blocked gate,
  and the skipped/waived segments of the distribution bar with their matching facet marks.

Two further neutrals serve the charts. `--chart-grid` (hue 283) draws the radar rings and spokes;
`--chart-fill` is `--accent` at 14% alpha (20% in dark), the only translucent value in the system.
There is deliberately **no `--chart-1..5` categorical ramp**: every series here is semantic (pass,
fail, warn, idle), so a categorical palette would be five tokens with no consumer.

### Named Rules

**The Same-Hue Rule.** Every neutral is tinted to hue **283** — Midnight `#0B0A1E`, which converts
to `oklch(15.9% 0.041 282.9)`. No untinted gray is permitted. Never `#000`, never `#fff`, never a
hue-free `oklch(L 0 H)`.

**The Two-Signal Rule.** Status is never carried by color alone. Every status renders a symbol
**and** a word — `✓ Pass`, `✗ Fail`, `1 cleared`, `2 blocked`. Color is the third signal, never the
first. The artifact must survive a grayscale print and a red-green colorblind reader unchanged.

## 3. Typography

**Display / Body Font:** the system sans stack (`-apple-system, BlinkMacSystemFont, "Segoe UI",
system-ui, sans-serif`).
**Label / Mono Font:** the system mono stack (`ui-monospace, SFMono-Regular, Consolas, monospace`).

**Character:** one family, no webfont, no download. The report borrows the reader's own OS voice so
it opens instantly from disk and never waits on a network the artifact is not allowed to touch. The
mono stack appears only where a string is a machine identifier the reader may copy: criterion ids,
file paths, evidence tiers and sources, gate counts.

### Hierarchy

- **Display** (600, `1.75rem`, 1.2, `-0.01em`): the report title. Once per document.
- **Headline** (600, `1.1875rem`, 1.4): section headings, the status level line, gate numerals.
- **Title** (600, `0.9375rem`): row titles (criterion, action, advisory) and gate names. Pillar
  subheadings use the same size in uppercase with `0.08em` tracking.
- **Body** (400, `1rem`, 1.55): rationale and prose, capped at a **72ch** measure so a long rationale
  never runs the full 1120px column.
- **Meta** (400, `0.8125rem`): the muted line under every title — status, scope, level, score — plus
  evidence rows and footer.
- **Label** (500, `0.75rem`, `0.08em`, uppercase): table column headers and gate state words.

Ratios: display→headline 1.47, headline→body 1.19, body→meta 1.23. Mono is not a texture: if a
string is not a machine identifier the reader might copy, it is set in the sans stack.

## 4. Elevation

**This system has no shadows.** There is no shadow token, no elevation scale, and no `box-shadow`
anywhere in the stylesheet. A report is a document, not a stack of cards.

Depth is entirely tonal plus hairlines. Three surface tones establish the only layering that exists:
`--bg` (page) sits behind `--surface` (the report column), and `--surface-sunken` recesses beneath
both for gate cards. Separation between sections, rows and table cells is a single `1px`
`--border` rule. The one place the system raises its voice is the blocked gate, which takes a `2px`
`--border-strong` outline — a heavier line, still not a shadow.

Corners are square by decision: `--radius-none` is the default and `--radius-sm` (`2px`) is the
largest radius in the system, used only on gate cards.

### Named Rules

**The No-Shadow Rule.** Depth comes from tone and hairlines. If a surface needs a shadow to be
legible, the tone is wrong — fix the tone.

## 5. Components

Twenty-two components. Every one is assembled in `engine/readiness/report.py` from the shared
component helpers (`_section`, `_row`, `_meta`, `_badge`, `_icon`, `_tip`, `_evidence`, `_callout`,
`_empty`, `_gate_track`, `_radar`, `_distribution`, `_education`, `_facet_menu`,
`_pillar_header`); no markup shape is spelled out twice.

**The three tiers.** Every criterion row is blocking, suggested or settled, and the tier is the
first thing the design communicates:

| Tier | Predicate | Treatment |
|---|---|---|
| **Blocking** | a gating criterion that failed, or a gating `unknown` that is not an agent judgment | filled status square, `--surface-sunken` box, `--border-strong` edge |
| **Suggested** | an advisory failure: worth doing, blocks nothing | plain row, outlined status square |
| **Settled** | pass, skipped, waived, an agent judgment, or an advisory `unknown` | plain row, muted square |

A gating `unknown` blocks because `score.py::_status_counts` scores it `0/1` exactly like a
failure. An advisory `unknown` does not: `score.summarize` filters to `r.gating`, so it never
reaches the level or the pass rate, and it is settled rather than flagged. An agent judgment never
enters the score at all, so it never wears the alarm.

Rows sort by tier first so the two treatments form contiguous blocks rather than alternating down
the page, and the pillar header's `n blocking` names exactly the rows beneath it.

**The next-step line is scoped per sentence, not per tier.** Remediation appears on any failure,
blocking or suggested, because an advisory failure blocks nothing yet is still worth doing and how
to do it is useful either way. The "counts as not passed" sentence appears only on a blocking
`unknown`, because on any other row it would claim a cost the score never charged. Enforced by
`TestActionLayer::test_each_sentence_appears_only_where_it_is_true`.

### Report Shell

- **Shape:** single centered column, `--content-max` (`1120px`), `--surface` background on the
  `--bg` page.
- **Padding:** `48px 24px 64px`, tightening to `32px 16px 48px` below `720px`.
- **Print:** the column drops its max-width, padding and background; closed disclosures are forced
  open so nothing is lost on paper.

### Header

- Title in Display, then one muted meta line joined with ` · `: engine version, redacted location,
  branch, short commit. Separated from the body by a hairline rule and `32px`.
- **Location:** a name, never a path. A relative scan root resolves to the directory's own name,
  because the basename of `.` is `.` and would print as a bare separator.

### Section

- `<section aria-labelledby="{slug}-heading">` with an `<h2 id="{slug}-heading">` in Headline.
- **Separation:** `32px` bottom margin, `24px` bottom padding, hairline bottom rule.
- Section order is the reading order of a blocked engineer: status → pillar coverage → actions →
  criteria → advisory improvements → applications → judgments → advisory → footer.

### Gate Track

- **Shape:** five equal columns (`repeat(5, minmax(0, 1fr))`, `8px` gap), collapsing to one column
  below `720px`. Each gate is a `12px`-padded card on `--surface-sunken` with a hairline border and
  a `2px` radius.
- **Content:** numeral, level name, `passed/total`, state word. Never a percentage — a percentage of
  zero criteria is the misleading `0/0 (100%)` this component exists to kill.
- **States:** `cleared` (cyan numeral and word) · `blocked` (magenta numeral and word, `2px`
  `--border-strong` outline) · `locked` (muted numeral) · `no criteria` (muted numeral, dashed
  border, no fill). Exactly one gate can be `blocked`: the first level not yet achieved.

### Status Line

- Headline-weight level line (`Level 4: Optimized`) followed by one muted meta line carrying the
  overall pass rate and gating count. The only place a percentage appears.

### Row (criterion / action / advisory variants)

- **Shape:** full-bleed list item, `12px` vertical padding, hairline top rule. Plain by default;
  a blocking row takes a `--surface-sunken` box with `16px` padding and a `--border-strong` edge.
  Two weights only, never three: a distinct third box shape read as two different components.
- **Rail:** a criterion is a two-column grid — a `1.35rem` badge column, then content. Title,
  tags, rationale, next step and evidence share one left edge, so every entry parses identically
  and nothing zigzags back to the page margin under the badge.
- **Head:** a baseline-aligned flex line — title (Title, 600), optional mono identifier, then the
  tag slots. Wraps at `4px 8px` gaps.
- **Tag slots, fixed order, register-differentiated.** `row-status` (Label, uppercase, the only
  colored token), `row-stake` (`Level 3 gate` or `advisory`, muted), `row-loop` (the AC/DC
  mapping, muted), `row-score` (`passed/evaluated`, muted mono). Slots separate by a `12px` gap
  and their typographic register, not interpuncts; absent slots collapse. `passed/evaluated`
  appears only when the fraction says something: a repository-scope pass is always `1/1`, and
  printing it on every green row is three tokens conveying nothing.
- **The `--status-color` token.** The row's `status-*` class sets one custom property; the badge
  stroke, the blocking badge fill, and the status word all consume it. One place maps status to
  color (the same idiom as the facet `--mark`), and only the status word is tinted — a fail-heavy
  report never becomes a wall of colored meta text.
- **Body:** optional rationale in Body at the `72ch` measure, then the optional next-step line,
  then the optional evidence disclosure.
- **Variants:** `criterion` carries the rail, badge, tag slots and `status-*` token class;
  `action` adds the mono criterion id with a joined meta line; `advisory-item` carries level and
  pillar only.

### Next Step

- One sentence under a failing row's rationale saying what to do, keyed off the criterion's
  remediation kind. Body size, `72ch`, `text-wrap: pretty`.
- **Accuracy is a hard constraint.** `fix/recipes.py` writes only `plan["auto"]`; `propose` and
  `github_setting` are printed for a human. So only the scaffold branch may mention `--apply`, and
  no branch may imply a file or a draft appears by itself. Enforced by
  `TestActionLayer::test_action_copy_matches_what_ra1_fix_actually_does`.
- **Silent when there is nothing true and concrete to say.** A criterion with no registered
  remediation gets no line at all: "Manual work, no scaffold covers this" repeated down the page
  is noise wearing the costume of guidance, and the rationale already names what is missing.
  Judgments and advisory `unknown`s are silent for the same reason, plus a sharper one: the
  sentence they would carry is not true of them.

### Pillar Radar

- **Shape:** one axis per pillar on a 240-unit box, radius 84. Four `--chart-grid` rings at 25 /
  50 / 75 / 100%, one spoke per axis, a `--chart-fill` polygon stroked 2px in `--accent`, and a
  vertex dot per pillar. Axes are numbered, not labelled: seven names will not fit at 240px, and
  the numbers key directly to the list beside it.
- **Data:** `score.pillars` only. That is the engine's gating-only, skipped/waived-excluded
  denominator, the same one behind the headline pass rate. Re-aggregating over `results` here
  would let advisory and skipped criteria move coverage while the score stayed still.
- **States:** fewer than three pillars renders the key alone, because two axes are a line rather
  than a shape. A zero-total pillar plots at the centre and reads `0/0`.
- **Accessibility:** `role="img"` with `aria-labelledby` pointing at an authored `<title>` and a
  `<desc>` carrying the full summary as text. The numbered key is the printable carrier.

### Pillar Key

- Four-column grid: index, pillar glyph, name, `passed/total` in tabular figures. Hairline rule
  between rows, no rule above the first. Sits beside the radar at wide widths, beneath it below
  `720px`.

### Pillar Section Header

- A pillar is a real division, so it gets a `2px` `--border-strong` rule and `32px` above it, not
  a micro-caps label floating over the next row. Every pillar takes the rule, including the first,
  which has to separate from the filter bar above it.
- **Content:** glyph and name in Title at full text colour, the state count immediately beside it
  (`12px` away, not flung to the far edge), and a one-line plain-English purpose beneath.
- **State:** `n blocking` in `--status-fail` when a gate is blocked, else `n suggested` muted, else
  `all clear` in `--status-pass`. Blocking and suggested are counted separately on purpose: one
  combined "to fix" number tells a reader four advisory nits are a blocked gate, and a count that
  overstates is a count they learn to ignore.
- **Purpose line:** nine authored sentences, one per pillar, answering "why would an agent care?".
  Nine is maintainable; 109 per-criterion essays would not be. An unrecognised pillar renders the
  header without a purpose line rather than inventing one.

### Status Distribution

- One 8px SVG bar spanning every criterion, segments proportional to pass / fail / unknown /
  skipped / waived, the last segment snapped to the right edge so rounding never leaves a gap.
  Zero-count statuses are dropped rather than drawn at zero width. `aria-hidden`: the facet marks
  directly beneath repeat each segment's colour beside its word and count.
- **Denominator:** the bar counts every criterion; the score above counts only applicable gating
  criteria. A sentence above the bar reconciles the two, because 19 failures beside a 100% pass
  rate reads as a bug otherwise.

### Facet Filter

- **Shape:** three native `<details>` dropdown menus — Status, AC/DC loop (only when mapped
  criteria exist), Pillar, in that order — in an `auto-fit` grid of `12rem`-minimum columns,
  stacking to one column below `720px`.
- **Mechanism:** a real `<input type="checkbox">` per option, nested inside its own menu's
  panel immediately ahead of its label, and generated `.report:has(...)` selectors that hide
  rows and show pillar sections. No script, no hoisted controls: modern `:has()` is what lets
  correctly nested, keyboard-operable checkboxes govern the rows below. The shared
  `name="criteria-filters"` gives supporting browsers one-open-menu behaviour for free.
- **Default:** `skipped` arrives unchecked. It is the noise floor, and hiding it is the single
  highest-value default on a 100+ criterion report. Zero selections is valid and simply
  reaches the no-match state.
- **States:** a pillar group hides once every status it contains is switched off, so no heading
  ever outlives its own rows. The `No criteria match these filters` message is **visible by
  default** and hidden by each surviving (status, pillar, loop) triple, which is what covers a
  cross-facet zero match: check only *Fail* and only *Build* and nothing matches even though a
  status is still checked. `@media print` neutralises the whole layer, so paper always carries
  every row.

### Facet Menu

- **Trigger:** a full-width `<summary>` (`2.75rem` minimum height, `--surface` fill, hairline
  `--border`, `2px` radius) carrying the group glyph, the title, a static `N options` count,
  and a chevron that rotates on open. The count is deliberately static: script-free CSS cannot
  keep a selected count from going stale, and the real checkbox states inside the open menu
  are the source of truth. The native disclosure marker is suppressed here only, so the
  chevron is never duplicated.
- **Panel:** absolutely positioned below the trigger (`22rem` maximum height, scrolling,
  `--border-strong` hairline, no shadow); below `720px` it returns to the flow so an open
  menu pushes content down instead of covering it. The panel holds a `<fieldset>` with a
  visually-hidden `<legend>`, then input-plus-label pairs in reading order — so a closed menu
  contributes nothing to the tab sequence and an open one walks its options in order.
- **Option rows:** a four-column grid — check square, option glyph, label, count. Status
  options tint square and glyph with their `--mark` status colour (the same binding the
  distribution bar uses); loop and pillar glyphs stay neutral. Checked fills the square and
  sinks the row to `--surface-sunken`; the focus ring lands on the label via the adjacent
  visually-hidden input, using the shared `--focus` tokens.

### Education Disclosure

- **Shape:** a closed `<details class="education">` with a focusable `<summary>`, its body
  capped at the `72ch` measure. Three instances: *How the levels work* after the gate track,
  *What the pillars measure* after the pillar key, and *How AC/DC loops map to this report*
  between the criteria denominator note and the distribution bar (the last only when at
  least one criterion carries an AC/DC loop mapping).
- **Definition lists:** term/definition rows on a two-column `minmax(10rem, 14rem) minmax(0,
  1fr)` grid, one column below `720px`, separated by full-width hairlines — never cards,
  never colored side stripes. The five level definitions are keyed by level number and take
  their names from `LEVEL_NAMES`; the nine pillar definitions reuse `_PILLAR_ICONS` and
  `_PILLAR_ELI5` rather than copying them. A level with no defined gating criteria says so
  in one appended sentence.
- **Source quotation:** the AC/DC disclosure closes with a `<blockquote>` holding Sonar's two
  loop-defining sentences, set off by top and bottom hairlines, and exactly one citation
  anchor in `--accent` with an underline and the standard focus outline. That anchor is the
  artifact's only external reference: authored, user-initiated navigation, never a render
  dependency (The Single-File Rule).
- **Print:** closed disclosures print expanded via the shared `@media print` rule, so the
  teaching content survives on paper.

### Tooltip

- Only where a token is genuinely opaque: the evidence tiers. The expansion lives in the DOM as a
  `<span role="tooltip">` referenced by `aria-describedby`, on `--surface` behind a
  `--border-strong` hairline, revealed on `:hover` and `:focus` and
  forced visible in print. `:focus`, not `:focus-visible`: revealing content is not an outline, and
  a tooltip that ignores programmatic focus is broken. Toggled by `display` rather than `opacity`,
  which keeps a hidden `max-content` box from widening its row and keeps the artifact at exactly
  one motion consumer. Never the sole carrier of meaning.

### Status Badge

- An inline SVG glyph on a 1.35rem square, 16px on a 24-unit box with 1.5 stroke and
  `currentColor`: check, cross, minus, question, ban. Drawn in Lucide's grammar and vendored into
  `report.py`, because the artifact may not fetch an icon library. `aria-hidden`, because the
  status word beside it is the accessible signal.
- **Three weights, matching the tiers.** A blocking row fills the square with the row's
  `--status-color` and knocks the glyph out in `--surface`; a suggested row leaves it outlined in
  that same token; a settled row is muted. The fill is reserved for blocking work so that it
  keeps meaning something.

### Evidence Disclosure

- Closed `<details>` labelled `Evidence (n)` in Meta. Open state is an ordered list indented `12px`
  behind a hairline left rule, each row leading with the mono tier (`T0` / `T1` / `T2`), which
  carries a tooltip because the codes are opaque on their own.
- **Focus:** `2px` `--focus` outline at `3px` offset. The summary color transition (`160ms`,
  `cubic-bezier(0.22, 1, 0.36, 1)`) is the only motion in the artifact.

### Data Table

- Full-width, collapsed borders, hairline bottom rule per cell, `8px 24px 8px 0` cell padding,
  `nowrap`. Header cells are Label. The table is wrapped in a horizontally scrollable container —
  the only element permitted to overflow — and carries a visually-hidden caption.

### Callout

- One-line tonal note at the `72ch` measure with `12px` top margin. `tone-warn` is amber. No fill,
  no icon chrome, and no colored side stripe.

### Empty State

- Italic Meta sentence in place of the absent content (`Score unavailable`, `No criteria results
  available`). Never a blank section: absence is always stated.

### Footer

- Hairline top rule, `16px` top padding, one muted meta line: generated timestamp, registry version,
  detector version.

### Visually-Hidden Caption Utility

- `.visually-hidden` clips content to a 1px box with `clip-path: inset(50%)` — present for screen
  readers, absent visually. Used for the applications-table caption.

### Named Rules

**The Single-File Rule.** The artifact makes no network request at render time, ever. No
`<script>`, `<link>`, `<img>`, `@import`, `url()`, remote font, or protocol-relative reference.
"Single-file" means exactly that: nothing is fetched to render. The one authored exception is
the Sonar citation anchor in the AC/DC education disclosure — a user-initiated navigation that
loads only on an explicit click, and the report renders identically whether or not it is ever
clicked. Enforced by `tests/test_report.py::TestHtmlSafety`.

## 6. Do's and Don'ts

### Do:

- **Do** derive every value from `engine/readiness/theme.py`. `_HTML_STYLE` is
  `theme.root_block() + theme.dark_block() + _STATIC_CSS`; a new design value means a new token, not
  a literal in `_STATIC_CSS`.
- **Do** keep every neutral on hue **283** (The Same-Hue Rule) and every color in OKLCH.
- **Do** pair every status color with a symbol and a word (The Two-Signal Rule).
- **Do** express depth with `--bg` < `--surface-sunken` < `--surface` plus `--hairline` rules, and
  reserve `--rule` (`2px`) with `--border-strong` for the single blocked gate (The No-Shadow Rule).
- **Do** route every value that came from the scanned repository through `_html()` or `_meta()`, and
  keep `_callout` bodies authored-markup-only. Repository and report values never reach a
  URL-bearing attribute: the artifact's one `href` is the authored Sonar citation constant.
- **Do** read coverage from `score.pillars`, never from a fresh aggregation over `results`: the
  engine already excludes non-gating and skipped/waived criteria, and that is the denominator the
  headline score uses.
- **Do** build interactivity from native controls plus `:has()`. A real `<input type="checkbox">`
  inside a grouped `<details>` menu is keyboard-operable and announced for free; a scripted one is
  not, and a script cannot enter this file at all.
- **Do** inline every glyph in Lucide's grammar (24-unit box, 1.5 stroke, `currentColor`) rather
  than adding an icon dependency.
- **Do** cap prose at `--prose-max` (`72ch`) and keep the artifact one column.
- **Do** state absence explicitly with the empty state rather than rendering a blank section.

### Don't:

- **Don't** let the arcade bit bury a clear error message — `BRAND.md`'s own "Don't". Reports and
  errors stay plain and useful.
- **Don't** reference *Ready Player One* by name, its characters, the OASIS, or its key art, logo or
  typography; and never use a trademarked tagline (`BRAND.md`'s "Don't" column, verbatim).
- **Don't** overclaim — the score is deterministic, not vibes (`BRAND.md`'s "Don't"). The advisory
  layer never changes a number.
- **Don't** ship attract-mode decoration into the report: no gradients, no glow, no scanlines, no
  perspective grid, no display face, no decorative animation (The Play-Screen Rule).
- **Don't** add a `<script>`, `<link>`, `<img>`, webfont, `@import` or `url()` (The Single-File
  Rule). The only external reference permitted is the authored Sonar citation anchor, and it is
  user-initiated navigation, not a render-time fetch.
- **Don't** use `border-left` greater than 1px as a colored side stripe.
- **Don't** use gradient text, glassmorphism, a hero-metric template, or a grid of identical cards.
- **Don't** add a shadow token or an elevation scale.
- **Don't** show a percentage of zero criteria: the gate track counts (`0/0`), it never rates.
- **Don't** put repository text in an attribute, or any URL in an attribute value. The single
  exception is chart geometry: server-computed coordinates derived from a ratio clamped to
  `[0, 1]`, which `TestHtmlSafety::test_chart_geometry_is_finite_numbers_only` holds to a
  finite numeric grammar. Dynamic prose travels as text (`<desc>`), never as `aria-label`.
