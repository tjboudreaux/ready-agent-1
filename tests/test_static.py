"""Tests for the T0 static collector: globbing, manifests, and dependency parsing."""
import unittest

from readiness.collectors.static import StaticCollector
from tests._util import make_repo, rmtree


class TestStatic(unittest.TestCase):
    def _c(self, files):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        return StaticCollector(root)

    def test_glob_string_pattern_and_ignore_dirs(self):
        c = self._c({"a.py": "x", "node_modules/dep/b.py": "y"})
        hits = c.glob("**/*.py")  # string is normalized to a single-element list
        self.assertIn("a.py", hits)
        self.assertNotIn("node_modules/dep/b.py", hits)

    def test_exists_any_and_read(self):
        c = self._c({"README.md": "hello"})
        self.assertEqual(c.exists_any(["README.md", "missing"]), "README.md")
        self.assertIsNone(c.exists_any(["missing"]))
        self.assertEqual(c.read("README.md"), "hello")

    def test_manifests_setup_cfg_ini(self):
        c = self._c({"setup.cfg": "[metadata]\nname = x\n"})
        m = c.manifests()
        self.assertEqual(m["setup.cfg"][0], "python")

    def test_manifests_text_parsed_for_gomod(self):
        c = self._c({"go.mod": "module x\n\ngo 1.21\n"})
        m = c.manifests()
        self.assertEqual(m["go.mod"][0], "go")
        self.assertIsInstance(m["go.mod"][1], str)

    def test_declared_deps_pyproject_all_sections(self):
        c = self._c({"pyproject.toml": (
            '[project]\nname="x"\ndependencies=["requests>=2"]\n'
            '[project.optional-dependencies]\ndev=["pytest"]\n'
            '[tool.poetry.dependencies]\nflask="^3"\n'
            '[tool.poetry.dev-dependencies]\nblack="^24"\n'
            '[tool.ruff]\nline-length=100\n'
        )})
        deps = c.declared_deps()
        for name in ("requests", "pytest", "flask", "black", "tool:ruff", "tool:poetry"):
            self.assertIn(name, deps)

    def test_declared_deps_cargo(self):
        c = self._c({"Cargo.toml":
                     '[package]\nname="x"\n[dependencies]\nactix-web="4"\n[dev-dependencies]\ncriterion="0.5"\n'})
        deps = c.declared_deps()
        self.assertIn("actix-web", deps)
        self.assertIn("criterion", deps)

    def test_declared_deps_go_mod_require_block(self):
        c = self._c({"go.mod":
                     "module x\n\ngo 1.21\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n"
                     "\tsolo v0.1.0\n)\n"})  # solo has no slash -> exercises the len<2 branch
        deps = c.declared_deps()
        self.assertIn("gin-gonic/gin", deps)
        self.assertIn("github.com/gin-gonic/gin", deps)
        self.assertIn("solo", deps)
        c = self._c({"Gemfile": "source 'https://rubygems.org'\ngem 'rails', '~> 7'\ngem \"sinatra\"\n"})
        deps = c.declared_deps()
        self.assertIn("rails", deps)
        self.assertIn("sinatra", deps)

    def test_has_dep_and_tool_config(self):
        c = self._c({"pyproject.toml": '[project]\nname="x"\ndependencies=["pytest"]\n[tool.mypy]\nstrict=true\n'})
        self.assertEqual(c.has_dep("pytest"), "pytest")
        self.assertIsNone(c.has_dep("nonexistent"))
        self.assertTrue(c.has_tool_config("mypy"))
        self.assertFalse(c.has_tool_config("ruff"))

    def test_languages_sorted(self):
        c = self._c({"package.json": "{}", "go.mod": "module x\n"})
        self.assertEqual(c.languages(), ["go", "npm"])

    def test_lockfiles(self):
        c = self._c({"package-lock.json": "{}"})
        self.assertIn("package-lock.json", c.lockfiles())

    def test_gitignore_patterns_strips_comments(self):
        c = self._c({".gitignore": "# comment\n.env\n\nnode_modules/\n"})
        pats = c.gitignore_patterns()
        self.assertIn(".env", pats)
        self.assertNotIn("# comment", pats)

    def test_within(self):
        c = self._c({"pkg/package.json": "{}"})
        self.assertIn("package.json", c.within("pkg").manifests())
        self.assertIs(c.within("."), c)


if __name__ == "__main__":
    unittest.main()
