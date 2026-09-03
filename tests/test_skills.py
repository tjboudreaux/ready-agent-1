import re
import sys
import tomllib
import unittest
from pathlib import Path

import readiness

REPO = Path(readiness.__file__).resolve().parents[2]
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
# Discovered, never hardcoded: a new skill directory is covered by these contracts the
# moment it ships, and cannot be forgotten by a list that was updated somewhere else.
SKILLS = sorted(p.parent.name for p in (REPO / "skills").glob("*/SKILL.md"))


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
        self.assertTrue(SKILLS, "no skills discovered under skills/*/SKILL.md")
        for name in SKILLS:
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
                self.assertEqual(fm["allowed-tools"], "Bash",
                                 "skills grant Bash only per the fixed CLI grammar")
                # self-contained: vendored engine + templates present for single-skill installs
                self.assertTrue((d / "scripts" / "readiness" / "cli.py").exists())
                self.assertTrue((d / "manifest.json").exists())

    def test_project_version_matches_engine_version(self):
        project = tomllib.loads((REPO / "pyproject.toml").read_text())
        self.assertEqual(project["project"]["version"], readiness.ENGINE_VERSION)

    def test_skill_metadata_versions_match_engine_version(self):
        for name in SKILLS:
            text = (REPO / "skills" / name / "SKILL.md").read_text()
            match = re.search(r"(?m)^  version:\s*(\S+)\s*$", text)
            self.assertIsNotNone(match)
            self.assertEqual(match.group(1), readiness.ENGINE_VERSION)

    def test_shipped_skill_contracts_match_eval_contracts(self):
        """The shipped SKILL.md command grammars must satisfy the eval contracts: the names
        and required payload keys cannot drift between the skills and evals/contracts.py."""
        import sys as _sys
        _sys.path.insert(0, str(REPO))
        from evals import contracts
        from evals.scenarios import all_scenarios
        self.assertEqual(sorted(contracts.SKILL_CONTRACTS), ["ra1-fix", "ra1-interview",
                                                             "ra1-report"])
        for skill in SKILLS:
            self.assertIn(skill, contracts.SKILL_CONTRACTS)
        for scenario in all_scenarios():
            self.assertIn(contracts.scenario_skill(scenario), contracts.SKILL_CONTRACTS)
        report_text = (REPO / "skills" / "ra1-report" / "SKILL.md").read_text()
        self.assertIn("## Evidence explanations", report_text)
        self.assertIn("next_gate_actions", report_text)
        # the report skill must demand the full key set, not the legacy six-field subset
        for key in ("max_available_level", "next_gate_actions", "evidence_coverage"):
            self.assertIn(key, report_text)
        fix_text = (REPO / "skills" / "ra1-fix" / "SKILL.md").read_text()
        self.assertIn("fix_contract", fix_text)
        self.assertIn("confirmed_ids", fix_text)
        interview_text = (REPO / "skills" / "ra1-interview" / "SKILL.md").read_text()
        self.assertIn("answer_contract", interview_text)
        self.assertIn("--apply", interview_text)

    def test_every_shipped_skill_is_registered_for_vendoring(self):
        """A skill that ships without being vendored installs with no engine at all."""
        sys.path.insert(0, str(REPO / "scripts"))
        import vendor
        self.assertEqual(sorted(vendor.SKILLS), SKILLS)


if __name__ == "__main__":
    unittest.main()
