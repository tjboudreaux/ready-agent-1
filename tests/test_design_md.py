"""The design spec cannot drift from the tokens the stylesheet actually emits.

``scripts/design_md.py`` owns two generated artifacts — root ``DESIGN.md``'s YAML frontmatter and
``.impeccable/design.json``. These tests drive every branch of the generator and, at the end, run
its ``--check`` path against the committed files so a token edit without a regenerated spec fails
the suite.
"""
from __future__ import annotations

import contextlib
import io
import json
import runpy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import readiness  # noqa: F401 — ensures engine is importable
from readiness import theme

from tests._util import rmtree

REPO = Path(readiness.__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import design_md  # noqa: E402

MINIMAL = """---
stale: true
---

# Design System: Test

## 1. Overview

**Creative North Star: "The Play Screen."**

First philosophy paragraph that wraps
across two source lines.

Second philosophy paragraph.

**Key Characteristics:**

- One characteristic that wraps
  onto a second line.
- Another characteristic.

### Named Rules

**The Play-Screen Rule.** Hue, never spectacle.

## 2. Colors

**The Same-Hue Rule.** Everything on hue 283.

## 6. Do's and Don'ts

### Do:

- **Do** keep the tokens honest.

### Don't:

- **Don't** add a shadow.
"""


class TestTokenViews(unittest.TestCase):
    def test_colors_resolve_aliases_per_scheme(self):
        light, dark = design_md.colors(0), design_md.colors(1)
        self.assertEqual(light["accent"], theme.COLOR_TOKENS["accent"][0])
        self.assertEqual(dark["accent"], theme.COLOR_TOKENS["accent"][1])
        for alias, target in theme.COLOR_ALIASES.items():
            self.assertEqual(light[alias], light[target])
            self.assertEqual(dark[alias], dark[target])

    def test_scale_strips_the_prefix(self):
        self.assertEqual(design_md.scale("space-")["4"], theme.SCALE_TOKENS["space-4"])
        self.assertEqual(design_md.scale("radius-")["sm"], theme.SCALE_TOKENS["radius-sm"])

    def test_typography_roles_resolve_to_token_values(self):
        roles = design_md.typography()
        self.assertEqual(roles["display"]["fontSize"], theme.SCALE_TOKENS["size-display"])
        self.assertEqual(roles["label"]["letterSpacing"], theme.SCALE_TOKENS["track-label"])
        self.assertNotIn("letterSpacing", roles["body"])  # only props that are real

    def test_tonal_ramp_holds_hue_and_chroma(self):
        steps = design_md.ramp("oklch(47% 0.20 351)")
        self.assertEqual(len(steps), 8)
        self.assertEqual(steps[0], "oklch(15% 0.20 351)")
        for step in steps:
            self.assertTrue(step.endswith(" 0.20 351)"))


class TestFrontmatter(unittest.TestCase):
    def test_only_the_five_stitch_token_groups(self):
        top = [line.split(":")[0] for line in design_md.frontmatter().splitlines()
               if line and not line.startswith(" ")]
        self.assertEqual(top, ["colors", "typography", "rounded", "spacing", "components"])

    def test_numbers_stay_unquoted_and_lengths_are_quoted(self):
        yaml = design_md.frontmatter()
        self.assertIn("fontWeight: 600\n", yaml)
        self.assertIn("lineHeight: 1.2\n", yaml)
        self.assertIn('fontSize: "1.75rem"\n', yaml)
        self.assertIn('  "4": "16px"\n', yaml)  # numeric scale keys stay strings

    def test_every_color_key_is_a_real_token_or_alias(self):
        known = set(theme.COLOR_TOKENS) | set(theme.COLOR_ALIASES)
        emitted = json.loads(json.dumps(design_md.colors()))
        self.assertEqual(set(emitted), known)

    def test_component_refs_point_at_declared_primitives(self):
        colors, rounded = design_md.colors(), design_md.scale("radius-")
        spacing, typography = design_md.scale("space-"), design_md.typography()
        groups = {"colors": colors, "rounded": rounded, "spacing": spacing,
                  "typography": typography}
        for name, props in design_md.COMPONENTS.items():
            for prop, value in props.items():
                if value.startswith("{"):
                    group, key = value.strip("{}").split(".", 1)
                    self.assertIn(key, groups[group], f"{name}.{prop} -> {value}")


class TestNarrativeParsing(unittest.TestCase):
    def test_narrative_is_read_out_of_the_document(self):
        parsed = design_md.narrative(MINIMAL)
        self.assertEqual(parsed["northStar"], "The Play Screen")
        self.assertEqual(parsed["overview"],
                         "First philosophy paragraph that wraps across two source lines."
                         "\n\nSecond philosophy paragraph.")
        self.assertEqual(parsed["keyCharacteristics"],
                         ["One characteristic that wraps onto a second line.",
                          "Another characteristic."])
        self.assertEqual([(r["name"], r["section"]) for r in parsed["rules"]],
                         [("The Play-Screen Rule", "overview"),
                          ("The Same-Hue Rule", "colors")])
        self.assertEqual(parsed["rules"][1]["body"], "Everything on hue 283.")
        self.assertEqual(parsed["dos"], ["**Do** keep the tokens honest."])
        self.assertEqual(parsed["donts"], ["**Don't** add a shadow."])

    def test_missing_north_star_is_named(self):
        with self.assertRaises(ValueError) as cm:
            design_md.narrative("---\n---\n\n# Design System: Test\n")
        self.assertIn("Creative North Star", str(cm.exception))

    def test_missing_marker_is_named(self):
        document = MINIMAL.replace("### Don't:", "### Nope:")
        with self.assertRaises(ValueError) as cm:
            design_md.narrative(document)
        self.assertIn("### Don't:", str(cm.exception))

    def test_marker_without_a_list_is_named(self):
        document = MINIMAL.replace("- **Don't** add a shadow.\n", "")
        with self.assertRaises(ValueError) as cm:
            design_md.narrative(document)
        self.assertIn("no bullet list", str(cm.exception))

    def test_real_design_md_carries_the_five_named_rules(self):
        parsed = design_md.narrative((REPO / "DESIGN.md").read_text(encoding="utf-8"))
        self.assertEqual([r["name"] for r in parsed["rules"]],
                         ["The Play-Screen Rule", "The Same-Hue Rule", "The Two-Signal Rule",
                          "The No-Shadow Rule", "The Single-File Rule"])
        # Verbatim, not paraphrased: every rule body is a substring of the markdown.
        markdown = " ".join((REPO / "DESIGN.md").read_text(encoding="utf-8").split())
        for rule in parsed["rules"]:
            self.assertIn(rule["body"], markdown)
        for item in (*parsed["dos"], *parsed["donts"], *parsed["keyCharacteristics"]):
            self.assertIn(item, markdown)


class TestSplice(unittest.TestCase):
    def test_body_is_preserved_byte_for_byte(self):
        spliced = design_md.splice(MINIMAL, 'colors:\n  bg: "oklch(97% 0.01 283)"\n')
        self.assertTrue(spliced.startswith('---\ncolors:\n  bg: "oklch(97% 0.01 283)"\n---\n'))
        self.assertNotIn("stale: true", spliced)
        self.assertEqual(spliced.split("---\n", 2)[2], MINIMAL.split("---\n", 2)[2])

    def test_missing_opening_fence_is_named(self):
        with self.assertRaises(ValueError) as cm:
            design_md.splice("# No frontmatter\n", "colors:\n")
        self.assertIn("must open with", str(cm.exception))

    def test_unclosed_frontmatter_is_named(self):
        with self.assertRaises(ValueError) as cm:
            design_md.splice("---\ncolors:\n", "colors:\n")
        self.assertIn("never closed", str(cm.exception))


class TestGate(unittest.TestCase):
    def _mk(self, document=MINIMAL):
        tmp = Path(tempfile.mkdtemp(prefix="ar-design-"))
        self.addCleanup(rmtree, tmp)
        (tmp / design_md.DESIGN_MD).write_text(document, encoding="utf-8")
        return tmp

    def test_generate_writes_both_artifacts_then_reports_no_drift(self):
        tmp = self._mk()
        self.assertEqual(design_md.generate(tmp), [])
        self.assertEqual(design_md.generate(tmp, check=True), [])
        sidecar = json.loads((tmp / design_md.SIDECAR).read_text(encoding="utf-8"))
        self.assertEqual(sidecar["extensions"]["themeVersion"], theme.THEME_VERSION)
        self.assertEqual(sidecar["extensions"]["breakpoints"][0]["value"],
                         theme.NARROW_BREAKPOINT)
        self.assertEqual(len(sidecar["components"]), 8)

    def test_check_names_the_drifted_token(self):
        tmp = self._mk()
        design_md.generate(tmp)
        design = tmp / design_md.DESIGN_MD
        design.write_text(design.read_text(encoding="utf-8")
                          .replace(theme.COLOR_TOKENS["accent"][0], "oklch(47% 0.20 300)"),
                          encoding="utf-8")
        drift = design_md.generate(tmp, check=True)
        self.assertIn(f"{design_md.DESIGN_MD}:", drift)
        self.assertTrue(any("oklch(47% 0.20 300)" in line for line in drift))

    def test_check_reports_a_missing_sidecar(self):
        tmp = self._mk()
        design_md.generate(tmp)
        (tmp / design_md.SIDECAR).unlink()
        drift = design_md.generate(tmp, check=True)
        self.assertIn(f"{design_md.SIDECAR}:", drift)

    def test_missing_design_md_is_a_clear_failure_not_a_partial_write(self):
        tmp = Path(tempfile.mkdtemp(prefix="ar-design-"))
        self.addCleanup(rmtree, tmp)
        with self.assertRaises(ValueError) as cm:
            design_md.plan(tmp)
        self.assertIn("hand-written", str(cm.exception))


class TestMain(unittest.TestCase):
    def _redirect_root(self, root):
        old = design_md.ROOT
        design_md.ROOT = root
        self.addCleanup(setattr, design_md, "ROOT", old)

    def _tmp_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="ar-design-"))
        self.addCleanup(rmtree, tmp)
        shutil.copy(REPO / design_md.DESIGN_MD, tmp / design_md.DESIGN_MD)
        return tmp

    def test_write_then_check_then_drift(self):
        tmp = self._tmp_repo()
        self._redirect_root(tmp)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(design_md.main([]), 0)
            self.assertEqual(design_md.main(["--check"]), 0)
            (tmp / design_md.SIDECAR).unlink()
            self.assertEqual(design_md.main(["--check"]), 1)
        self.assertIn("DESIGN DRIFT", err.getvalue())

    def test_missing_design_md_exits_one_with_a_message(self):
        tmp = Path(tempfile.mkdtemp(prefix="ar-design-"))
        self.addCleanup(rmtree, tmp)
        self._redirect_root(tmp)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(design_md.main(["--check"]), 1)
        self.assertIn("not found", err.getvalue())

    def test_argv_defaults_to_sys_argv(self):
        tmp = self._tmp_repo()
        self._redirect_root(tmp)
        old_argv = sys.argv[:]
        try:
            sys.argv = ["design_md.py"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(design_md.main(), 0)
        finally:
            sys.argv = old_argv

    def test_script_entrypoint_checks_the_real_repo(self):
        old_argv = sys.argv[:]
        try:
            sys.argv = [str(REPO / "scripts" / "design_md.py"), "--check"]
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(REPO / "scripts" / "design_md.py"), run_name="__main__")
            self.assertEqual(cm.exception.code, 0,
                             "DESIGN.md drifted — run scripts/design_md.py and re-commit")
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
