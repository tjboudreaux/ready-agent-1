"""Gate-closing coverage for model.py defensive guards.

Every guard here is unreachable through public behavior (the canonical paths always
produce the safe value), so inputs are patched exactly the way test_model2.py patches
module internals. New file by ownership convention.
"""
from __future__ import annotations

import unittest
from unittest import mock

from readiness import model
from readiness.model import (
    App,
    CriterionResult,
    PublicReportValidationError,
    Report,
    Status,
)


def _crit(**kw):
    base = dict(id="a.b", title="a.b", pillar="P", level=1, scope="repository",
                gating=True, status=Status.PASS, rationale="ok")
    base.update(kw)
    return CriterionResult(**base)


def _report(results):
    return Report(project_path="/p", schema_version="3", engine_version="0.11.0",
                  registry_version="0.8.0", detector_version="0.6.0", results=results)


class TestToDictReraise(unittest.TestCase):
    def test_authored_validation_error_reraises_unchanged(self):
        # A PublicReportValidationError raised inside finalization must propagate as-is,
        # never be wrapped by the generic "result finalization failed" path.
        report = _report([_crit()])
        with mock.patch.object(model, "finalize_public_result",
                               side_effect=PublicReportValidationError("authored")):
            with self.assertRaises(PublicReportValidationError) as caught:
                report.to_dict()
        self.assertEqual(str(caught.exception), "authored")


class TestDotAppPathGuards(unittest.TestCase):
    """``_public_source(".")`` canonically returns ".", so the ``not app_path`` fallback
    is defensive; patch the source boundary to exercise it."""

    def _patch_source(self):
        real = model._public_source
        return mock.patch.object(
            model, "_public_source",
            lambda value, tier: "" if value == "." else real(value, tier))

    def test_finalize_public_result_dot_app_path(self):
        with self._patch_source():
            out = model.finalize_public_result(_crit(app_path="."))
        self.assertEqual(out.app_path, ".")

    def test_public_app_dot_path(self):
        with self._patch_source():
            out = model._public_app(App(path="."))
        self.assertEqual(out["path"], ".")


class TestSchema3ExactTopKeys(unittest.TestCase):
    def test_inexact_schema3_top_keys(self):
        # ``required`` and ``allowed`` are the same global for schema 3, so a proper
        # subset can only reach the exactness error when the global admits a superset.
        class WeirdSet(frozenset):
            def __le__(self, other):
                return True

            def __ge__(self, other):
                return True

        weird = WeirdSet(set(model._SCHEMA3_TOP_KEYS) | {"extra_key"})
        report = {key: None for key in model._SCHEMA3_TOP_KEYS}
        report["schema_version"] = "3"
        with mock.patch.object(model, "_SCHEMA3_TOP_KEYS", weird):
            errors = model.validate_imported_report(report, "3")
        self.assertEqual(errors, ["schema3 top-level keys must be exact"])


class TestSchema2BoundaryKeys(unittest.TestCase):
    def _schema2_report(self):
        return {
            "schema_version": "2",
            "engine_version": "0.10.0",
            "registry_version": "0.7.0",
            "detector_version": "0.6.0",
            "commit": "",
            "branch": "",
            "github_available": False,
            "generated_at": "2026-06-20T00:00:00+00:00",
            "repository": None,
            "detection": None,
            "score": {
                "level": 0, "level_name": "Ad hoc", "pass_rate": 0.0,
                "gating_passed": 0, "gating_total": 0,
                "levels": [
                    {"level": i, "name": f"L{i}", "passed": 0, "total": 0,
                     "ratio": 0.0, "achieved": False}
                    for i in range(1, 6)
                ],
                "pillars": {}, "recommendations": [],
            },
            "results": [],
            "advisory": [],
        }

    def test_schema2_with_boundary_key_is_rejected(self):
        # ``assessment_boundary`` is not in the schema-2 allowlist, so the schema-2
        # boundary/provenance error is reachable only when the allowlist admits it.
        report = self._schema2_report()
        report["assessment_boundary"] = {}
        allowed = model._SCHEMA2_TOP_KEYS | {"assessment_boundary"}
        with mock.patch.object(model, "_SCHEMA2_TOP_KEYS", allowed):
            errors = model.validate_imported_report(report, "2")
        self.assertIn("schema2 carries no boundary/provenance", errors)


if __name__ == "__main__":
    unittest.main()
