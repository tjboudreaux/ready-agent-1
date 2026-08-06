# Product

## Register

product

## Users

Engineers whose merge was blocked by a readiness gate, or who are preparing a repository for AI
agents. Primary scene: an engineer downloads the CI artifact after `ra1` blocked their PR, opens
`report.html` from `~/Downloads` in a tab beside the failing PR, and wants the blocking gate and
the first fix without scrolling. The hour is unknown (2pm review or 2am incident), which is why the
artifact adapts to `prefers-color-scheme` instead of committing to one theme. Secondary users:
agents themselves, reading the JSON/markdown surfaces.

## Product Purpose

Ready Agent 1 (`ra1`) scans a repository and assigns a deterministic readiness Level (1–5), five
cumulative gates, citing evidence for every check, and hands the user safe remediation. The HTML
report is the human reading surface for that score. Success: the reader identifies the blocking
gate, the rows that block it, and the concrete next step in under a minute, and trusts every number
because the score is reproducible and the evidence is cited.

## Brand Personality

Arcade hype meets a good co-op teammate: encouraging, fast, a little 1986. But the brand's own rule
splits the registers: neon and hype live in banners and marketing; **errors, reports, and
instructions stay plain and useful**. The report is "The Play Screen": legible, high-contrast,
readable at speed, carrying the cabinet's color DNA (violet-tinted neutrals; magenta / cyan / amber
status) with zero spectacle.

## Anti-references

- Attract-mode decoration in the report: gradients, glow, scanlines, perspective grids, display
  faces, decorative animation (The Play-Screen Rule).
- First-order category reflex: "developer tool → dark terminal, navy and blue."
- Second-order reflex: "dev tool that isn't terminal-dark → GitHub-grey with blue links."
- Ready Player One references, trademarked taglines, or key-art lifts.
- Overclaiming: the score is deterministic, not vibes; the advisory layer never changes a number.
- Dashboard clichés: hero metrics, identical card grids, colored side stripes, shadows.

## Design Principles

- **Attract mode or play screen?** If it is decoration, it does not ship in the report.
- **Answer the blocked engineer first.** Section order and row hierarchy follow urgency: which
  gate, how far, what to fix next.
- **Two signals before color.** Every status is carried by a symbol and a word; color is the third
  signal, never the first.
- **Quiet rows, loud problems.** Fills and boxes are reserved for blocking work so they keep
  meaning something; 109 identical cards would be a flat wall in a heavier costume.
- **Counts, never vibes.** A percentage of zero criteria is a lie; N/M appears only when the
  fraction says something.

## Accessibility & Inclusion

- Every color pairing at or above 4.5:1 in both schemes; light and dark are both first-class.
- Status readable without color (symbol + word); color-blind safe by construction.
- Keyboard-operable native controls only (details/summary, real checkboxes); visible focus rings
  via shared focus tokens; screen-reader landmarks (`aria-labelledby` sections, visually-hidden
  captions and legends).
- Single motion consumer (a summary color transition); no animation that conveys state alone.
- Print is a supported medium: disclosures expand, filters neutralize, nothing is lost on paper.
