"""Serialization contract for the core data model.

`Status` was `class Status(str, Enum)` and is now `StrEnum` (ruff UP042). That is only safe
because every serialization path goes through `.value` explicitly -- `model.to_dict`,
`report.render_markdown`, `report.render_junit` and the `--fail-on` comparison in `cli`. The
switch does change `str(Status.PASS)` from `"Status.PASS"` to `"pass"`, so these tests pin the
behaviour the rest of the engine relies on, and would fail if someone reverted to a plain
`Enum` (whose `str()` would leak `Status.PASS` into rendered output).
"""
from __future__ import annotations

import json
import unittest

from readiness.model import CriterionResult, Evidence, Status, Verdict


class TestStatusContract(unittest.TestCase):
    def test_values_are_the_wire_format(self):
        self.assertEqual(
            {s.name: s.value for s in Status},
            {"PASS": "pass", "FAIL": "fail", "SKIPPED": "skipped",
             "UNKNOWN": "unknown", "WAIVED": "waived"},
        )

    def test_str_is_the_bare_value_not_the_enum_repr(self):
        # A plain Enum would give "Status.PASS" here and leak that into reports.
        for s in Status:
            self.assertEqual(str(s), s.value)
            self.assertNotIn("Status.", str(s))

    def test_compares_equal_to_plain_strings(self):
        # cli.py's --fail-on path compares r.status.value against "fail"; scorer code compares
        # members directly. Both must keep working.
        self.assertEqual(Status.FAIL, "fail")
        self.assertEqual(Status.FAIL.value, "fail")
        self.assertIn(Status.PASS, {"pass", "fail"})

    def test_json_serializes_to_the_bare_value(self):
        self.assertEqual(json.loads(json.dumps(Status.WAIVED)), "waived")


class TestToDict(unittest.TestCase):
    def test_criterion_result_emits_a_plain_status_string(self):
        r = CriterionResult(id="x.y", title="T", pillar="P", level=1, scope="repository",
                            gating=True, status=Status.PASS, rationale="why")
        d = r.to_dict()
        self.assertEqual(d["status"], "pass")
        self.assertIsInstance(d["status"], str)
        # No Python type names may leak into the JSON payload.
        self.assertNotIn("Status", json.dumps(d))

    def test_evidence_round_trips_every_field(self):
        e = Evidence(summary="s", tier="T1", source="git", detail="d")
        self.assertEqual(e.to_dict(),
                         {"summary": "s", "tier": "T1", "source": "git", "detail": "d"})

    def test_verdict_carries_status_and_evidence(self):
        v = Verdict(Status.FAIL, "nope", [Evidence(summary="s")])
        self.assertEqual(v.status, Status.FAIL)
        self.assertEqual(len(v.evidence), 1)


if __name__ == "__main__":
    unittest.main()
