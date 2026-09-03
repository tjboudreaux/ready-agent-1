"""Tests for the T0 static collector: globbing, manifests, and dependency parsing."""
import os
import unittest

from readiness import safe_io
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

    def test_requirements_txt_is_a_python_manifest(self):
        c = self._c({"requirements.txt": "flask>=3\n"})
        self.assertEqual(c.manifests()["requirements.txt"][0], "python")
        self.assertIn("python", c.languages())

    def test_requirement_lines_yield_distribution_names(self):
        """Pins, ranges, extras, markers, and inline comments all reduce to the name."""
        c = self._c({"requirements.txt": (
            "# core deps\n"
            "Django==5.0  # pinned for LTS\n"
            "psycopg[binary]>=3\n"
            'flask[async]>=3.0,<4 ; python_version >= "3.11"\n'
            "requests~=2.31\n"
            "\n"
        )})
        self.assertEqual(c.declared_deps(), {"django", "psycopg", "flask", "requests"})

    def test_requirement_lines_that_name_no_distribution_are_skipped(self):
        """`-r`, `-e`, flags and bare URLs must not become junk dependency names."""
        c = self._c({"requirements.txt": (
            "-r base.txt\n"
            "-e .\n"
            "--index-url https://example.com/simple\n"
            "https://example.com/pkg-1.0-py3-none-any.whl\n"
            "uvicorn @ https://example.com/uvicorn.whl\n"
            "pytest==8.0\n"
        )})
        self.assertEqual(c.declared_deps(), {"uvicorn", "pytest"})

    def test_dev_requirements_files_count_as_declared(self):
        """A repo can declare its only linter or test runner in a dev requirements file."""
        c = self._c({"requirements.txt": "flask>=3\n",
                     "requirements-dev.txt": "pytest==8.0\nruff==0.6.0\n",
                     "requirements/extra.txt": "mypy==1.11\n"})
        deps = c.declared_deps()
        for name in ("flask", "pytest", "ruff", "mypy"):
            self.assertIn(name, deps)
        self.assertEqual(c.has_dep(["pytest"]), "pytest")

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
        c = self._c({"Gemfile": "source 'https://rubygems.org'\ngem 'rails', '~> 7'\ngem "
                                "\"sinatra\"\n"})
        deps = c.declared_deps()
        self.assertIn("rails", deps)
        self.assertIn("sinatra", deps)

    def test_has_dep_and_tool_config(self):
        c = self._c({"pyproject.toml": '[project]\nname="x"\ndependencies=["pytest"]\n[tool.mypy]\n'
                                       'strict=true\n'})
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

    def test_malformed_manifests_raise_repository_input_error(self):
        """A malformed JSON/TOML/INI manifest is repository-indeterminate, never skipped."""
        for fname, content in (
            ("package.json", "{bad"),
            ("pyproject.toml", "[project\n"),
            ("setup.cfg", "[metadata"),
        ):
            c = self._c({fname: content})
            with self.assertRaises(safe_io.RepositoryInputError):
                c.manifests()

    def test_unsafe_reads_raise_from_legacy_helpers(self):
        """A symlinked file/manifest is unsafe: legacy read/manifests raise, never follow."""
        root = make_repo({"outside.txt": "x", "outside.json": "{}"})
        self.addCleanup(rmtree, root)
        os.symlink("/etc/hosts", root / "link.txt")
        os.symlink(str(root / "outside.json"), root / "package.json")
        c = StaticCollector(root)
        with self.assertRaises(safe_io.RepositoryInputError):
            c.read("link.txt")
        with self.assertRaises(safe_io.RepositoryInputError):
            c.manifests()

    def test_within_rejects_traversal_and_absolute_paths(self):
        """Sub-collectors open fd-relative beneath the root: no '..' or absolute escape."""
        c = self._c({"pkg/package.json": "{}"})
        for bad in ("..", "/etc", "pkg/../.."):
            with self.assertRaises(safe_io.RepositoryInputError):
                c.within(bad)


if __name__ == "__main__":
    unittest.main()
