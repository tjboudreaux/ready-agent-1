"""Gate-closing branch coverage for readiness/gaps.py.

Complements test_gaps.py: verification-command candidate enumeration (npm scripts,
scripts/ paths, duplicate dedupe) and the default single-choice branch of
``_config_gaps``. New file by ownership convention.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from readiness import gaps as gaps_mod
from readiness.collectors import StaticCollector
from readiness.model import CriterionResult, Status

from tests._util import make_repo, rmtree


class TestVerifyCommandCandidates(unittest.TestCase):
    def test_npm_script_and_script_path_candidates(self):
        root = make_repo({
            "package.json": '{"scripts": {"check": "ruff check ."}}',
            "scripts/check.sh": "#!/bin/sh\nexit 0\n",
        })
        self.addCleanup(rmtree, root)
        candidates = gaps_mod._verify_command_candidates(StaticCollector(root))
        commands = [command for command, _cid in candidates]
        self.assertIn("npm run check", commands)
        self.assertIn("scripts/check.sh", commands)

    def test_candidate_cap_stops_at_sixteen(self):
        # 17 distinct candidates: the 17th takes the `len(out) < 16` False arc.
        targets = "check:\n\t.\nverify:\n\t.\nvalidate:\n\t.\n"
        root = make_repo({
            "Makefile": targets,
            "Justfile": targets,
            "Taskfile.yml": targets,
            "package.json": '{"scripts": {"check": ".", "verify": ".", "validate": "."}}',
            "tests/test_x.py": "def test_x():\n    pass\n",
            "scripts/check.sh": "#!/bin/sh\n",
            "scripts/check2.sh": "#!/bin/sh\n",
            "scripts/verify.sh": "#!/bin/sh\n",
            "scripts/verify2.sh": "#!/bin/sh\n",
        })
        self.addCleanup(rmtree, root)
        candidates = gaps_mod._verify_command_candidates(StaticCollector(root))
        self.assertEqual(len(candidates), 16)


class TestConfigGapsDefaultBranch(unittest.TestCase):
    def test_unmapped_spec_id_uses_single_choice_defaults(self):
        # A third spec id (neither verify_command nor ci_budget_minutes) takes the
        # `elif` False arc straight to the default single-choice Gap.
        spec = {
            "id": "config.other",
            "path": "other",
            "kind_of_value": "string",
            "statuses": (Status.FAIL,),
            "question": "q",
            "why": "w",
        }
        result = CriterionResult(id="build.third", title="t", pillar="P", level=2,
                                 scope="repository", gating=False, status=Status.FAIL,
                                 rationale="because")
        report = SimpleNamespace(results=[result])
        with mock.patch.dict(gaps_mod._CONFIG_GAPS, {"build.third": spec}):
            gaps = gaps_mod._config_gaps(report, {}, None)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].id, "config.other")
        self.assertEqual(gaps[0].input_kind, "single_choice")
        self.assertEqual(gaps[0].choices, [])
        self.assertEqual(gaps[0].evidence, ["because"])


if __name__ == "__main__":
    unittest.main()
