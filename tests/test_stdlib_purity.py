"""Enforce the engine's zero-dependency invariant.

``AGENTS.md`` states "Pure stdlib only in ``engine/`` (no third-party imports)". That claim is the
project's entire supply-chain story -- a scanner that ships into other people's repos and pulls no
transitive dependencies. Until now it was enforced only by reviewer memory, so this test turns it
into a gate: every absolute import reachable from ``engine/readiness`` must resolve to the standard
library or back into ``readiness`` itself.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

import readiness

ENGINE_ROOT = Path(readiness.__file__).resolve().parent

# Local first-party roots. Anything else must be in sys.stdlib_module_names.
LOCAL_ROOTS = {"readiness"}


def _engine_sources():
    return sorted(p for p in ENGINE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_roots(tree: ast.AST):
    """Yield (root_module, lineno) for every absolute import in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative (in-package) import -- always first-party.
            if node.level == 0 and node.module:
                yield node.module.split(".")[0], node.lineno


class TestStdlibPurity(unittest.TestCase):
    def test_engine_sources_discovered(self):
        # Guard against the walk silently matching nothing and vacuously passing.
        sources = _engine_sources()
        self.assertGreater(len(sources), 20, "engine source walk found suspiciously few files")
        self.assertIn(ENGINE_ROOT / "cli.py", sources)

    def test_engine_imports_only_stdlib_or_first_party(self):
        allowed = set(sys.stdlib_module_names) | LOCAL_ROOTS
        offenders = []
        for path in _engine_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for root, lineno in _imported_roots(tree):
                if root not in allowed:
                    rel = path.relative_to(ENGINE_ROOT.parent)
                    offenders.append(f"{rel}:{lineno} imports third-party {root!r}")
        self.assertEqual(
            offenders, [],
            "engine/ must import only the standard library:\n  " + "\n  ".join(offenders),
        )

    def test_detects_a_planted_third_party_import(self):
        # The check above only has value if it would actually fail; prove the detector works.
        tree = ast.parse("import os\nimport requests\nfrom .x import y\nfrom yaml import safe_"
                         "load\n")
        roots = {root for root, _ in _imported_roots(tree)}
        self.assertEqual(roots, {"os", "requests", "yaml"})  # relative import excluded
        allowed = set(sys.stdlib_module_names) | LOCAL_ROOTS
        self.assertEqual({r for r in roots if r not in allowed}, {"requests", "yaml"})


if __name__ == "__main__":
    unittest.main()
