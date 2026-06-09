import re
import unittest
from pathlib import Path

import readiness

REPO = Path(readiness.__file__).resolve().parents[2]
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def parse_frontmatter(text: str) -> dict:
    assert text.startswith("---"), "SKILL.md must start with YAML frontmatter"
    end = text.index("\n---", 3)
    out = {}
    for line in text[3:end].strip().splitlines():
        if line and not line.startswith((" ", "\t")) and ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


class TestSkillManifests(unittest.TestCase):
    def test_skills_are_agentskills_compliant_and_self_contained(self):
        for name in ("readiness-report", "readiness-fix"):
            d = REPO / "skills" / name
            with self.subTest(skill=name):
                self.assertTrue((d / "SKILL.md").exists())  # gh skill discovers skills/*/SKILL.md
                fm = parse_frontmatter((d / "SKILL.md").read_text())
                self.assertEqual(fm.get("name"), name, "name must equal directory name")
                self.assertTrue(NAME_RE.match(fm["name"]))
                self.assertTrue(fm.get("description"))
                self.assertLessEqual(len(fm["description"]), 1024)
                self.assertIn("license", fm)
                self.assertIn("allowed-tools", fm)
                # self-contained: vendored engine + templates present for single-skill installs
                self.assertTrue((d / "scripts" / "readiness" / "cli.py").exists())
                self.assertTrue((d / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
