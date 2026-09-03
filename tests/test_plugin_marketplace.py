"""`.claude-plugin/marketplace.json`: the catalog Claude Code installs ready-agent-1 from.

The install path advertised in the README is ``/plugin marketplace add tjboudreaux/ready-agent-1``
then ``/plugin install ready-agent-1@ready-agent-1``. Each assertion here defends one thing that
command depends on: the catalog parses, its marketplace name is not one Anthropic reserves, the
single plugin entry names the plugin manifest at the repository root, and the entry's source is
either the repository root itself or this GitHub repository. Kept in sync with
https://code.claude.com/docs/en/plugin-marketplaces (reserved-name list as of Claude Code 2.1.258).
"""
import json
import re
import unittest
from pathlib import Path

import readiness

REPO = Path(readiness.__file__).resolve().parents[2]
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
PLUGIN = REPO / ".claude-plugin" / "plugin.json"
GITHUB_REPO = "tjboudreaux/ready-agent-1"

# Names Anthropic reserves for official marketplaces; a third-party catalog using one of them
# stops loading. Impersonating variants are also blocked, so the pattern check covers those.
RESERVED_MARKETPLACE_NAMES = frozenset({
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official",
    "claude-plugins-community", "claude-community", "anthropic-marketplace",
    "anthropic-plugins", "agent-skills", "anthropic-agent-skills", "knowledge-work-plugins",
    "life-sciences", "claude-for-legal", "claude-for-financial-services",
    "financial-services-plugins", "first-party-plugins", "healthcare",
})
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class TestMarketplaceCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        cls.plugin = json.loads(PLUGIN.read_text(encoding="utf-8"))

    def test_marketplace_name_is_kebab_case_and_not_reserved(self):
        name = self.catalog["name"]
        self.assertRegex(name, _KEBAB_RE)
        self.assertNotIn(name, RESERVED_MARKETPLACE_NAMES)
        self.assertNotIn("anthropic", name)
        self.assertNotIn("official", name)

    def test_owner_names_a_maintainer(self):
        self.assertTrue(self.catalog["owner"]["name"].strip())

    def test_single_entry_names_the_root_plugin(self):
        entries = self.catalog["plugins"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], self.plugin["name"])
        self.assertEqual(entries[0]["description"], self.plugin["description"])

    def test_entry_leaves_version_to_plugin_json(self):
        # plugin.json is the single version authority checked by check_release_versions.py;
        # a second copy here would drift.
        self.assertNotIn("version", self.catalog["plugins"][0])

    def test_source_is_repo_root_or_this_github_repo(self):
        source = self.catalog["plugins"][0]["source"]
        if isinstance(source, str):
            self.assertEqual(source, "./")
        else:
            self.assertEqual(source.get("source"), "github")
            self.assertEqual(source.get("repo"), GITHUB_REPO)

    def test_plugin_root_is_a_valid_plugin(self):
        # The `./` source resolves to the marketplace root, so plugin.json must be there and
        # the skills the plugin ships must exist at the auto-discovered location.
        self.assertTrue(PLUGIN.is_file())
        for skill in ("ra1-report", "ra1-fix", "ra1-interview"):
            self.assertTrue((REPO / "skills" / skill / "SKILL.md").is_file(), skill)


if __name__ == "__main__":
    unittest.main()
