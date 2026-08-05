"""The design-token contract: the rules the tables must obey, not the bytes they emit.

``theme.py`` is the single source of truth for the HTML artifact's design values and the
input to ``DESIGN.md``. These tests encode the brand rules that make it safe — every neutral
tinted to one hue, no alias without a target, no color format the report cannot render, and
nothing in the stylesheet that would make the artifact fetch a byte.
"""
from __future__ import annotations

import unittest

from readiness import theme

NEUTRALS = ("bg", "surface", "surface-sunken", "text", "text-muted", "border", "border-strong")


class TestTokenTables(unittest.TestCase):
    def test_every_color_is_a_light_dark_pair_in_oklch(self):
        for name, value in theme.COLOR_TOKENS.items():
            self.assertIsInstance(value, tuple, name)
            self.assertEqual(len(value), 2, name)
            for scheme, css in zip(("light", "dark"), value, strict=True):
                self.assertTrue(css.startswith("oklch("), f"{name} {scheme}: {css}")
                self.assertTrue(css.endswith(")"), f"{name} {scheme}: {css}")

    def test_same_hue_rule_holds_for_every_neutral(self):
        # The Same-Hue Rule: no untinted gray. Every neutral sits on Midnight's hue 283.
        for name in NEUTRALS:
            for css in theme.COLOR_TOKENS[name]:
                self.assertTrue(css.endswith(" 283)"), f"{name} left hue 283: {css}")

    def test_aliases_point_at_real_colors_and_never_shadow_a_token(self):
        names = set(theme.COLOR_TOKENS) | set(theme.SCALE_TOKENS)
        for name, target in theme.COLOR_ALIASES.items():
            self.assertIn(target, theme.COLOR_TOKENS, f"{name} aliases unknown {target}")
            self.assertNotIn(name, names, f"alias {name} collides with a real token")

    def test_scale_values_are_non_empty_strings(self):
        for name, value in theme.SCALE_TOKENS.items():
            self.assertIsInstance(value, str, name)
            self.assertTrue(value.strip(), name)

    def test_narrow_breakpoint_is_a_css_length(self):
        # Recorded for DESIGN.md; the media query itself must keep the literal.
        self.assertTrue(theme.NARROW_BREAKPOINT.endswith("px"))


class TestEmittedBlocks(unittest.TestCase):
    def test_root_declares_every_name_exactly_once(self):
        css = theme.root_block()
        self.assertTrue(css.startswith(":root {\n"))
        self.assertTrue(css.endswith("}\n"))
        self.assertIn("color-scheme: light dark;", css)
        for name in (*theme.COLOR_TOKENS, *theme.COLOR_ALIASES, *theme.SCALE_TOKENS):
            self.assertEqual(css.count(f"--{name}:"), 1, f"--{name} declared {css.count(name)}x")

    def test_root_emits_light_values_and_alias_references(self):
        css = theme.root_block()
        self.assertIn("--bg: oklch(97% 0.01 283);", css)
        for name, target in theme.COLOR_ALIASES.items():
            self.assertIn(f"--{name}: var(--{target});", css)

    def test_dark_block_overrides_colors_and_nothing_else(self):
        css = theme.dark_block()
        self.assertTrue(css.startswith("@media (prefers-color-scheme: dark) {\n  :root {\n"))
        self.assertTrue(css.endswith("  }\n}\n"))
        declared = {line.split(":")[0].strip().removeprefix("--")
                    for line in css.splitlines() if line.strip().startswith("--")}
        self.assertEqual(declared, set(theme.COLOR_TOKENS))
        for name, (_light, dark) in theme.COLOR_TOKENS.items():
            self.assertIn(f"    --{name}: {dark};", css)

    def test_neither_block_uses_a_legacy_or_fetching_color_format(self):
        css = theme.root_block() + theme.dark_block()
        for banned in ("#", "rgb(", "hsl(", "url(", "@import"):
            self.assertNotIn(banned, css, f"{banned} reached the token blocks")


if __name__ == "__main__":
    unittest.main()
