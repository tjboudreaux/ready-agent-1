"""Design tokens for the HTML readiness artifact — the single source of truth for every
color, type, space and border value the report emits.

The report is the brand's *play screen*, not its attract mode: it carries the cabinet's hue
(violet-tinted neutrals, magenta / cyan / amber status) pulled to legible contrast, with no
gradients, glow or shadow. See ``DESIGN.md`` for the full spec, which is generated from the
tables below by ``scripts/design_md.py`` and drift-checked in CI.

Pure stdlib and import-free by design: this module is vendored verbatim into both shipped
skills and must stay loadable from a bare ``python3``.
"""
from __future__ import annotations

THEME_VERSION = "1.0.0"

# Media queries cannot read custom properties, so the narrow bound stays a literal in
# report.py's `@media` rule. It is recorded here for DESIGN.md and the sidecar only.
NARROW_BREAKPOINT = "720px"

# name -> (light, dark). Hues are the measured OKLCH conversions of the BRAND.md palette:
# Midnight #0B0A1E -> 283, Neon Magenta #FF3CAC -> 351, Neon Cyan #2DE2E6 -> 197,
# Sun Amber #FFD36E -> 86. Every neutral is tinted to hue 283 (The Same-Hue Rule).
COLOR_TOKENS: dict[str, tuple[str, str]] = {
    "bg": ("oklch(97% 0.01 283)", "oklch(16% 0.025 283)"),
    "surface": ("oklch(99% 0.006 283)", "oklch(21% 0.032 283)"),
    "surface-sunken": ("oklch(94% 0.015 283)", "oklch(25% 0.035 283)"),
    "text": ("oklch(24% 0.03 283)", "oklch(93% 0.012 283)"),
    "text-muted": ("oklch(48% 0.025 283)", "oklch(72% 0.025 283)"),
    "border": ("oklch(86% 0.02 283)", "oklch(34% 0.03 283)"),
    "border-strong": ("oklch(74% 0.025 283)", "oklch(46% 0.035 283)"),
    "accent": ("oklch(47% 0.20 351)", "oklch(80% 0.17 351)"),
    "status-pass": ("oklch(40% 0.11 197)", "oklch(80% 0.12 197)"),
    "status-warn": ("oklch(43% 0.11 86)", "oklch(84% 0.11 86)"),
    "chart-grid": ("oklch(90% 0.015 283)", "oklch(30% 0.03 283)"),
    "chart-fill": ("oklch(47% 0.20 351 / 0.14)", "oklch(80% 0.17 351 / 0.20)"),
}

# Emitted as var() references so there is exactly one value per concept: renaming the
# accent can never silently repaint failures with a different color than the wordmark.
COLOR_ALIASES: dict[str, str] = {
    "status-fail": "accent",
    "status-idle": "text-muted",
    "focus": "accent",
    "chart-track": "surface-sunken",
}

# Scheme-independent scales. Sizes are `--size-*` so they cannot collide with `--text`.
SCALE_TOKENS: dict[str, str] = {
    "font-sans": '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
    "font-mono": "ui-monospace, SFMono-Regular, Consolas, monospace",
    "size-display": "1.75rem",
    "size-headline": "1.1875rem",
    "size-body": "1rem",
    "size-title": "0.9375rem",
    "size-meta": "0.8125rem",
    "size-label": "0.75rem",
    "weight-strong": "600",
    "weight-medium": "500",
    "weight-normal": "400",
    "leading-tight": "1.2",
    "leading-snug": "1.4",
    "leading-normal": "1.55",
    "track-tight": "-0.01em",
    "track-label": "0.08em",
    "space-1": "4px",
    "space-2": "8px",
    "space-3": "12px",
    "space-4": "16px",
    "space-5": "24px",
    "space-6": "32px",
    "space-7": "48px",
    "space-8": "64px",
    "hairline": "1px",
    "rule": "2px",
    "radius-none": "0",
    "radius-sm": "2px",
    "content-max": "1120px",
    "prose-max": "72ch",
    "focus-width": "2px",
    "focus-offset": "3px",
    "numeric": "tabular-nums",
    "icon-size": "16px",
    "icon-stroke": "1.5",
    "radar-size": "240px",
    "duration": "160ms",
    "ease": "cubic-bezier(0.22, 1, 0.36, 1)",
}

# The six type roles, each a full set of declarations keyed by CSS property. report.py
# generates its type layer from this table and scripts/design_md.py publishes the same
# table as DESIGN.md's `typography` group, so a role cannot be documented in one weight
# and rendered in another.
TYPE_ROLES: dict[str, dict[str, str]] = {
    "display": {"font-family": "font-sans", "font-size": "size-display",
                "font-weight": "weight-strong", "line-height": "leading-tight",
                "letter-spacing": "track-tight"},
    "headline": {"font-family": "font-sans", "font-size": "size-headline",
                 "font-weight": "weight-strong", "line-height": "leading-snug"},
    "title": {"font-family": "font-sans", "font-size": "size-title",
              "font-weight": "weight-strong", "line-height": "leading-snug"},
    "body": {"font-family": "font-sans", "font-size": "size-body",
             "font-weight": "weight-normal", "line-height": "leading-normal"},
    "meta": {"font-family": "font-sans", "font-size": "size-meta",
             "font-weight": "weight-normal", "line-height": "leading-normal",
             "font-variant-numeric": "numeric"},
    "label": {"font-family": "font-sans", "font-size": "size-label",
              "font-weight": "weight-medium", "line-height": "leading-snug",
              "letter-spacing": "track-label", "font-variant-numeric": "numeric"},
}

# Properties a role owns outright. Nothing else in the stylesheet may set these for a
# selector the role map claims — `tests/test_report.py` fails the build if it does.
ROLE_OWNED = ("font-size", "font-weight", "line-height")


def _declarations(pairs, indent: str) -> str:
    """Custom-property declarations, one per line. Shared so the two blocks cannot diverge."""
    return "".join(f"{indent}--{name}: {value};\n" for name, value in pairs)


def root_block() -> str:
    """The `:root` block: light color values, alias references, and every scale token."""
    return (
        ":root {\n"
        "  color-scheme: light dark;\n"
        + _declarations(((name, light) for name, (light, _dark) in COLOR_TOKENS.items()), "  ")
        + _declarations(((name, f"var(--{target})") for name, target in COLOR_ALIASES.items()),
                        "  ")
        + _declarations(SCALE_TOKENS.items(), "  ")
        + "}\n"
    )


def dark_block() -> str:
    """The dark-scheme override: color values only — `var()` resolves aliases automatically."""
    return (
        "@media (prefers-color-scheme: dark) {\n"
        "  :root {\n"
        + _declarations(((name, dark) for name, (_light, dark) in COLOR_TOKENS.items()), "    ")
        + "  }\n"
        "}\n"
    )


def type_block(assignments) -> str:
    """The type layer: one rule per role, from `{role: selector}`, in role-table order."""
    return "".join(
        f"{assignments[role]} {{\n"
        + "".join(f"  {prop}: var(--{token});\n" for prop, token in props.items())
        + "}\n"
        for role, props in TYPE_ROLES.items()
    )
