"""Branch-focused tests for readiness.model validators, sanitizers, and finalizers.

Complements test_model.py: every malformed-input branch of validate_imported_report
(schema 2/3), validate_decision_trace, validate_legacy_fix_report_v1, the public-text
and source sanitizers, repository/result finalizers, and Report.to_dict failure modes.
"""
import copy
import unittest

from readiness import model
from readiness.run import analyze

from ._util import make_repo, rmtree


# --------------------------------------------------------------------------- helpers
def _evidence_dict():
    return {"summary": "s", "tier": "T0", "source": "src/a.py", "detail": "d"}


def _trace_dict(status="fail", n_evidence=0, reason_code="check.fail"):
    steps = [{"kind": "rule", "code": "rule.applied", "message": "Rule.",
              "evidence_refs": []}]
    if n_evidence:
        steps.append({"kind": "observation", "code": "evidence.observed",
                      "message": "Observed.", "evidence_refs": list(range(n_evidence))})
    steps.append({"kind": "evaluation", "code": reason_code, "message": "Because.",
                  "evidence_refs": []})
    steps.append({"kind": "conclusion", "code": f"conclusion.{status}",
                  "message": f"Result: {status}.", "evidence_refs": []})
    return {"version": "1", "reason_code": reason_code, "rule_ref": "mod.func",
            "steps": steps, "limitations": []}


def _traced_result(status="fail", n_evidence=0):
    return {"status": status,
            "evidence": [_evidence_dict() for _ in range(n_evidence)],
            "decision_trace": _trace_dict(status, n_evidence)}


def _schema2_result(cid="a.b", status="pass", **over):
    result = {
        "id": cid, "title": cid, "pillar": "Style & Validation", "level": 1,
        "scope": "repo", "gating": True, "status": status, "rationale": "",
        "evidence": [], "app_path": "", "fixable": False, "fix_kind": "",
        "passed_apps": 1, "evaluated_apps": 1,
    }
    result.update(over)
    return result


def _schema2_report(**over):
    report = {
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
    report.update(over)
    return report


def _valid_gap():
    return {"gap_id": "g", "kind": "config", "question": "q", "why": "w",
            "recordable": True, "input_kind": "single_choice", "blocked_ids": [],
            "blocked_gating": 0, "levels": [], "evidence": []}


def _valid_app():
    return {"path": "src", "languages": ["python"], "runtime": "library",
            "deploy_surface": "library", "prod_facing": False, "test_cmd": "",
            "ci_jobs": [], "type_confidence": 0.5, "type_candidates": [],
            "surfaces": []}


def _valid_candidate():
    return {"type": "library", "confidence": 0.5, "signal": "pyproject"}


class Schema3Fixture(unittest.TestCase):
    """One real schema-3 report dict shared by every mutation test in a subclass."""

    @classmethod
    def setUpClass(cls):
        cls._root = make_repo({"README.md": "# fixture\n"})
        cls._report = analyze(cls._root).to_dict()
        assert not model.validate_imported_report(cls._report, "3")

    @classmethod
    def tearDownClass(cls):
        rmtree(cls._root)

    def fresh(self):
        return copy.deepcopy(self._report)

    def assert_invalid(self, report, fragment, schema="3"):
        errors = model.validate_imported_report(report, schema)
        self.assertTrue(any(fragment in e for e in errors),
                        f"expected {fragment!r} in {errors}")


# --------------------------------------------------------------------------- text hygiene
class TestSanitizePublicText(unittest.TestCase):
    def test_non_string_coerced(self):
        self.assertEqual(model._sanitize_public_text(123), "123")

    def test_safe_text_byte_identical(self):
        text = "a normal rationale with *markdown* and ünicode"
        self.assertEqual(model._sanitize_public_text(text), text)

    def test_controls_normalized(self):
        self.assertEqual(model._sanitize_public_text("a\x00b\x1fc‮d"), "a b c d")

    def test_oversize_boundary(self):
        self.assertEqual(model._sanitize_public_text("a" * 512), "a" * 512)
        self.assertEqual(model._sanitize_public_text("a" * 513),
                         "[redacted oversized public text]")

    def test_oversize_multibyte_boundary(self):
        self.assertEqual(model._sanitize_public_text("é" * 256), "é" * 256)  # 512 bytes
        self.assertEqual(model._sanitize_public_text("é" * 257),
                         "[redacted oversized public text]")  # 514 bytes

    def test_sensitive_categories(self):
        cases = {
            "pem block": "-----BEGIN PRIVATE KEY-----\nabc",
            "credential": "Authorization: Bearer abcdef123",
            "github token": "ghp_" + "a" * 12,
            "aws key": "AKIA" + "0" * 16,
            "jwt": "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 5,
            "secret assignment": "password=hunter2",
            "credential url": "https://user:pass@example.com/x",
            "absolute path": "/home/user/file.txt",
        }
        for category, text in cases.items():
            with self.subTest(category=category):
                self.assertEqual(model._sanitize_public_text(text),
                                 f"[redacted {category}]")

    def test_oversize_wins_over_sensitive(self):
        text = ("password=x " + "a" * 600)
        self.assertEqual(model._sanitize_public_text(text),
                         "[redacted secret assignment]")

    def test_idempotent(self):
        once = model._sanitize_public_text("token=abc")
        self.assertEqual(model._sanitize_public_text(once), once)


class TestPublicSource(unittest.TestCase):
    def test_non_string_and_empty(self):
        self.assertEqual(model._public_source(None, "T0"), "")
        self.assertEqual(model._public_source("", "T0"), "")

    def test_root_marker_canonical(self):
        self.assertEqual(model._public_source(".", "T0"), ".")

    def test_valid_relative(self):
        self.assertEqual(model._public_source("docs/guide.md", "T0"), "docs/guide.md")

    def test_sensitive_source_sentinel(self):
        self.assertEqual(model._public_source("password=x", "T0"),
                         "[redacted repository source]")

    def test_t2_endpoint_id_kept(self):
        ep = "repos/o/r/branches/main/protection"
        self.assertEqual(model._public_source(ep, "T2"), ep)

    def test_t2_endpoint_rejections(self):
        for bad in ("has space", "/absolute", "a/../b", "e" * 256):
            with self.subTest(bad=bad):
                self.assertEqual(model._public_source(bad, "T2"),
                                 "[redacted repository source]")

    def test_absolute_and_hostile_relative_rejected(self):
        for bad in ("/x/y", "a\\b", "~/x", "C:\\x", "a//b", "a/./b", "a/../b",
                    "f" * 256):
            with self.subTest(bad=bad):
                self.assertEqual(model._public_source(bad, "T0"),
                                 "[redacted repository source]")


class TestPublicCommit(unittest.TestCase):
    def test_valid_hex_kept(self):
        self.assertEqual(model._public_commit("a" * 40), "a" * 40)

    def test_rejections(self):
        for bad in ("", "abc", "g" * 40, None, 42):
            with self.subTest(bad=bad):
                self.assertEqual(model._public_commit(bad), "")


# --------------------------------------------------------------------------- finalizers
class TestFinalizeTrace(unittest.TestCase):
    def _trace(self, **kw):
        base = model.DecisionTrace(
            version="1", reason_code="check.fail", rule_ref="mod.func",
            steps=[model.DecisionStep("rule", "rule.applied", "R.", []),
                   model.DecisionStep("evaluation", "check.fail", "E.", []),
                   model.DecisionStep("conclusion", "conclusion.fail",
                                      "Result: fail.", [])],
            limitations=[])
        for key, value in kw.items():
            setattr(base, key, value)
        return base

    def test_none_and_wrong_type(self):
        self.assertIsNone(model._finalize_trace(None))
        self.assertIsNone(model._finalize_trace({"not": "a trace"}))

    def test_machine_field_anomalies_drop_trace(self):
        self.assertIsNone(model._finalize_trace(self._trace(reason_code="chéck.fail")))
        self.assertIsNone(model._finalize_trace(self._trace(rule_ref="m" * 256)))
        self.assertIsNone(model._finalize_trace(self._trace(version="é")))

    def test_step_anomalies_drop_trace(self):
        bad_kind = self._trace()
        bad_kind.steps[0].kind = "rulé"
        self.assertIsNone(model._finalize_trace(bad_kind))
        bad_code = self._trace()
        bad_code.steps[0].code = "c" * 129
        self.assertIsNone(model._finalize_trace(bad_code))
        bad_ref_bool = self._trace()
        bad_ref_bool.steps[0].evidence_refs = [True]
        self.assertIsNone(model._finalize_trace(bad_ref_bool))
        bad_ref_negative = self._trace()
        bad_ref_negative.steps[0].evidence_refs = [-1]
        self.assertIsNone(model._finalize_trace(bad_ref_negative))

    def test_limitations_deduped_and_empties_dropped(self):
        trace = self._trace(limitations=["a", "a", "b", ""])
        out = model._finalize_trace(trace)
        self.assertEqual(out.limitations, ["a", "b"])

    def test_step_message_sanitized(self):
        trace = self._trace()
        trace.steps[0].message = "token=abc"
        out = model._finalize_trace(trace)
        self.assertEqual(out.steps[0].message, "[redacted secret assignment]")

    def test_valid_trace_round_trip(self):
        out = model._finalize_trace(self._trace(limitations=["lim"]))
        self.assertEqual(out.reason_code, "check.fail")
        self.assertEqual(out.limitations, ["lim"])
        self.assertEqual(len(out.steps), 3)


class TestFinalizePublicResult(unittest.TestCase):
    def _result(self, **kw):
        base = dict(
            id="x.y", title="t", pillar="p", level=1, scope="repository",
            gating=False, status=model.Status.FAIL, rationale="ok",
            evidence=[], app_path=".", fixable=False, fix_kind="",
            passed_apps=0, evaluated_apps=1, decision_trace=None,
        )
        base.update(kw)
        return model.CriterionResult(**base)

    def test_oversize_rationale_sentinel(self):
        out = model.finalize_public_result(self._result(rationale="a" * 513))
        self.assertEqual(out.rationale, "[redacted oversized public text]")

    def test_sensitive_rationale_category(self):
        out = model.finalize_public_result(self._result(rationale="password=x"))
        self.assertEqual(out.rationale, "[redacted secret assignment]")

    def test_app_path_root_marker_kept(self):
        out = model.finalize_public_result(self._result(app_path="."))
        self.assertEqual(out.app_path, ".")

    def test_evidence_sources_confined(self):
        ev = [model.Evidence(summary="s", tier="T0", source="/etc/passwd"),
              model.Evidence(summary="s2", tier="T0", source="docs/a.md"),
              model.Evidence(summary="s3", tier="T2",
                             source="repos/o/r/branches/b/protection")]
        out = model.finalize_public_result(self._result(evidence=ev))
        self.assertEqual(out.evidence[0].source, "[redacted repository source]")
        self.assertEqual(out.evidence[1].source, "docs/a.md")
        self.assertEqual(out.evidence[2].source, "repos/o/r/branches/b/protection")

    def test_trace_finalized_with_result(self):
        trace = model.DecisionTrace(
            version="1", reason_code="check.fail", rule_ref="m.f",
            steps=[model.DecisionStep("rule", "rule.applied", "R.", []),
                   model.DecisionStep("evaluation", "check.fail", "E.", []),
                   model.DecisionStep("conclusion", "conclusion.fail",
                                      "Result: fail.", [])],
            limitations=["dup", "dup"])
        out = model.finalize_public_result(self._result(decision_trace=trace))
        self.assertEqual(out.decision_trace.limitations, ["dup"])

    def test_idempotent(self):
        ev = [model.Evidence(summary="s", tier="T0", source="docs/a.md")]
        once = model.finalize_public_result(
            self._result(rationale="plain", evidence=ev))
        twice = model.finalize_public_result(once)
        self.assertEqual(once.to_dict(), twice.to_dict())


class TestFinalizePublicRepository(unittest.TestCase):
    def test_none_and_non_dict(self):
        self.assertIsNone(model.finalize_public_repository(None))
        self.assertIsNone(model.finalize_public_repository("x"))

    def test_bad_kind_or_hash(self):
        self.assertIsNone(model.finalize_public_repository(
            {"identity_kind": "weird", "identity_hash": "a" * 16}))
        self.assertIsNone(model.finalize_public_repository(
            {"identity_kind": "origin", "identity_hash": "zz" * 8}))
        self.assertIsNone(model.finalize_public_repository(
            {"identity_kind": "origin", "identity_hash": "abc"}))
        self.assertIsNone(model.finalize_public_repository(
            {"identity_kind": "origin"}))

    def test_origin_display_set_kept(self):
        repo = {"identity_kind": "origin", "identity_hash": "a" * 16,
                "host": "github.com", "owner": "Owner-1", "name": "repo_2"}
        self.assertEqual(model.finalize_public_repository(repo), repo)

    def test_origin_display_omitted_wholesale(self):
        base = {"identity_kind": "origin", "identity_hash": "a" * 16,
                "host": "github.com", "owner": "o", "name": "r"}
        variants = {
            "bad host": {"host": "bad host"},
            "missing owner": {"owner": None},
            "empty name": {"name": ""},
            "oversize name": {"name": "n" * 256},
            "slash name": {"name": "o/r"},
            "sensitive owner": {"owner": "ghp_" + "a" * 12},
            "non-string host": {"host": 1},
        }
        for label, patch in variants.items():
            with self.subTest(label=label):
                repo = {**base, **patch}
                self.assertEqual(model.finalize_public_repository(repo),
                                 {"identity_kind": "origin",
                                  "identity_hash": "a" * 16})

    def test_local_path_name_kept(self):
        repo = {"identity_kind": "local_path", "identity_hash": "b" * 16,
                "name": "my-repo"}
        self.assertEqual(model.finalize_public_repository(repo), repo)

    def test_local_path_bad_name_omitted(self):
        repo = {"identity_kind": "local_path", "identity_hash": "b" * 16,
                "name": "my repo"}
        self.assertEqual(model.finalize_public_repository(repo),
                         {"identity_kind": "local_path", "identity_hash": "b" * 16})

    def test_idempotent(self):
        repo = {"identity_kind": "origin", "identity_hash": "a" * 16,
                "host": "github.com", "owner": "o", "name": "r"}
        once = model.finalize_public_repository(repo)
        self.assertEqual(model.finalize_public_repository(once), once)


class TestPublicAppAndProvenance(unittest.TestCase):
    def test_public_app_projections(self):
        app = model.App(path="src", test_cmd="npm test", runtime="mystery",
                        prod_facing="maybe",
                        type_candidates=[{"type": "alien", "confidence": 0.9,
                                          "signal": "password=x"}])
        out = model._public_app(app)
        self.assertEqual(out["test_cmd"], "npm_test")
        self.assertEqual(out["runtime"], "unknown")
        self.assertEqual(out["prod_facing"], "unknown")
        self.assertEqual(out["type_candidates"][0]["type"], "unknown")
        self.assertEqual(out["type_candidates"][0]["signal"],
                         "[redacted secret assignment]")

    def test_public_detection_none(self):
        self.assertIsNone(model._public_detection(None))

    def test_public_provenance_none_and_deepcopy(self):
        self.assertIsNone(model._public_provenance(None))
        prov = {"trust": "unsigned_unverified", "nested": {"a": 1}}
        out = model._public_provenance(prov)
        out["nested"]["a"] = 2
        self.assertEqual(prov["nested"]["a"], 1)

    def test_public_provenance_bad_trust_raises(self):
        with self.assertRaises(model.PublicReportValidationError):
            model._public_provenance({"trust": "signed"})


class TestAssessmentBoundary(unittest.TestCase):
    def test_exact_shape(self):
        boundary = model.assessment_boundary()
        self.assertEqual(set(boundary.keys()),
                         {"evidence_layers", "not_assessed", "known_limitations"})
        self.assertEqual([layer["id"] for layer in boundary["evidence_layers"]],
                         ["repository", "github", "execution", "judgment"])
        self.assertEqual(len(boundary["not_assessed"]), 9)
        self.assertEqual(len(boundary["known_limitations"]), 16)

    def test_fresh_deep_structure_each_call(self):
        first = model.assessment_boundary()
        first["evidence_layers"][0]["tiers"].append("T9")
        first["injected"] = True
        second = model.assessment_boundary()
        self.assertNotIn("injected", second)
        self.assertEqual(second["evidence_layers"][0]["tiers"], ["T0", "T1"])


# --------------------------------------------------------------------------- decision traces
class TestValidateDecisionTrace(unittest.TestCase):
    def assert_error(self, result, fragment):
        errors = model.validate_decision_trace(result)
        self.assertTrue(any(fragment in e for e in errors),
                        f"expected {fragment!r} in {errors}")

    def test_valid_traces(self):
        self.assertEqual(model.validate_decision_trace(_traced_result("fail")), [])
        self.assertEqual(model.validate_decision_trace(_traced_result("pass", 2)), [])

    def test_null_and_non_object(self):
        self.assertEqual(model.validate_decision_trace({"status": "fail"}),
                         ["decision_trace is null"])
        self.assertEqual(model.validate_decision_trace("not-a-dict"),
                         ["decision_trace is null"])
        self.assertEqual(
            model.validate_decision_trace({"status": "fail", "decision_trace": []}),
            ["decision_trace is not an object"])

    def test_forbidden_keys_rejected_anywhere(self):
        result = _traced_result()
        result["decision_trace"]["chain_of_thought"] = "secret"
        self.assert_error(result, "forbidden key 'chain_of_thought'")
        result = _traced_result()
        result["decision_trace"]["steps"][0]["thinking"] = "hmm"
        self.assert_error(result, "forbidden key 'thinking'")
        result = _traced_result()
        result["decision_trace"]["steps"][0]["nested"] = [{"Analysis": 1}]
        self.assert_error(result, "forbidden key")

    def test_top_level_shape(self):
        result = _traced_result()
        del result["decision_trace"]["limitations"]
        self.assert_error(result, "decision_trace keys are not exact")
        result = _traced_result()
        result["decision_trace"]["version"] = "2"
        self.assert_error(result, "decision_trace.version must be '1'")
        result = _traced_result()
        result["decision_trace"]["reason_code"] = ""
        self.assert_error(result, "reason_code must be non-empty")
        result = _traced_result()
        result["decision_trace"]["reason_code"] = 7
        self.assert_error(result, "reason_code must be non-empty")
        result = _traced_result()
        result["decision_trace"]["rule_ref"] = ""
        self.assert_error(result, "rule_ref must be non-empty")

    def test_limitations_shape(self):
        result = _traced_result()
        result["decision_trace"]["limitations"] = "nope"
        self.assert_error(result, "limitations must be a list")
        for bad in (["a", "a"], [""], [1]):
            result = _traced_result()
            result["decision_trace"]["limitations"] = bad
            self.assert_error(result, "limitations must be unique non-empty strings")

    def test_steps_not_a_list(self):
        result = _traced_result()
        result["decision_trace"]["steps"] = "nope"
        self.assert_error(result, "steps must be a list")

    def test_step_count(self):
        result = _traced_result()
        result["decision_trace"]["steps"] = result["decision_trace"]["steps"][:2]
        self.assert_error(result, "must have exactly 3 step(s)")
        result = _traced_result(n_evidence=1)
        result["decision_trace"]["steps"].pop(1)  # drop the observation step
        self.assert_error(result, "must have exactly 4 step(s)")

    def test_step_object_and_keys(self):
        result = _traced_result()
        result["decision_trace"]["steps"][0] = "x"
        self.assert_error(result, "step 0 is not an object")
        result = _traced_result()
        result["decision_trace"]["steps"][0]["extra"] = 1
        self.assert_error(result, "step 0 keys are not exact")

    def test_kind_validation(self):
        result = _traced_result()
        result["decision_trace"]["steps"][0]["kind"] = "weird"
        self.assert_error(result, "unknown kind 'weird'")
        self.assert_error(result, "step 0 kind must be 'rule'")
        result = _traced_result()
        steps = result["decision_trace"]["steps"]
        steps[1]["kind"] = "conclusion"  # swapped order
        self.assert_error(result, "kind must be 'evaluation'")

    def test_message_and_refs_types(self):
        result = _traced_result()
        result["decision_trace"]["steps"][0]["message"] = 1
        self.assert_error(result, "step 0 message must be a string")
        result = _traced_result()
        result["decision_trace"]["steps"][0]["evidence_refs"] = "x"
        self.assert_error(result, "step 0 evidence_refs must be a list")

    def test_ref_ranges(self):
        result = _traced_result(n_evidence=1)
        result["decision_trace"]["steps"][1]["evidence_refs"] = [1]
        self.assert_error(result, "invalid/out-of-range evidence refs")
        result = _traced_result(n_evidence=1)
        result["decision_trace"]["steps"][1]["evidence_refs"] = [True]
        self.assert_error(result, "invalid/out-of-range evidence refs")

    def test_step_codes(self):
        result = _traced_result()
        result["decision_trace"]["steps"][0]["code"] = "rule.other"
        self.assert_error(result, "rule step code must be 'rule.applied'")
        result = _traced_result(n_evidence=1)
        result["decision_trace"]["steps"][1]["code"] = "evidence.other"
        self.assert_error(result, "observation step code must be 'evidence.observed'")
        result = _traced_result()
        result["decision_trace"]["steps"][1]["code"] = "check.other"
        self.assert_error(result, "evaluation step code must equal reason_code")
        result = _traced_result(status="fail")
        result["decision_trace"]["steps"][2]["code"] = "conclusion.pass"
        self.assert_error(result, "conclusion step code must be 'conclusion.fail'")

    def test_non_observation_step_must_not_reference_evidence(self):
        result = _traced_result(n_evidence=1)
        result["decision_trace"]["steps"][0]["evidence_refs"] = [0]
        self.assert_error(result, "must not reference evidence")

    def test_conclusion_message_matches_status(self):
        result = _traced_result(status="fail")
        result["decision_trace"]["steps"][2]["message"] = "Result: pass."
        self.assert_error(result, "conclusion message must match the result status")

    def test_observation_references_every_index_once(self):
        result = _traced_result(n_evidence=2)
        result["decision_trace"]["steps"][1]["evidence_refs"] = [0, 0]
        self.assert_error(result, "every evidence index exactly once")
        result = _traced_result(n_evidence=2)
        result["decision_trace"]["steps"][1]["evidence_refs"] = [0]
        self.assert_error(result, "every evidence index exactly once")


class TestValidateRequiredReasonCodes(unittest.TestCase):
    def _result(self, cid, code):
        return {"id": cid, "decision_trace": {"reason_code": code}}

    def test_unmapped_and_non_dict_skipped(self):
        self.assertEqual(model.validate_required_reason_codes(
            [self._result("other.criterion", "anything"), "not-a-dict"]), [])

    def test_valid_direct_code(self):
        self.assertEqual(model.validate_required_reason_codes(
            [self._result("style.linter_config", "style.linter_config.missing")]), [])

    def test_structural_codes_skipped(self):
        for code in ("applicability.no_applications", "waiver.active",
                     "judgment.suppressed", "prerequisite.unmet", "aggregate.fail",
                     "input.repository_indeterminate"):
            with self.subTest(code=code):
                self.assertEqual(model.validate_required_reason_codes(
                    [self._result("style.linter_config", code)]), [])

    def test_malformed_direct_code(self):
        errors = model.validate_required_reason_codes(
            [self._result("style.linter_config", "check.fail")])
        self.assertTrue(any("missing or malformed" in e for e in errors))
        errors = model.validate_required_reason_codes(
            [{"id": "style.linter_config"}])  # no trace at all
        self.assertTrue(any("missing or malformed" in e for e in errors))

    def test_unallowlisted_suffix(self):
        errors = model.validate_required_reason_codes(
            [self._result("style.linter_config", "style.linter_config.bogus")])
        self.assertTrue(any("not allowlisted" in e for e in errors))


# --------------------------------------------------------------------------- schema 2 import
class TestValidateImportedReportSchema2(unittest.TestCase):
    def assert_invalid(self, report, fragment, schema="2"):
        errors = model.validate_imported_report(report, schema)
        self.assertTrue(any(fragment in e for e in errors),
                        f"expected {fragment!r} in {errors}")

    def test_valid_minimal(self):
        self.assertEqual(model.validate_imported_report(_schema2_report(), "2"), [])

    def test_valid_with_optional_keys(self):
        report = _schema2_report(gaps=[])
        report["results"] = [_schema2_result(acdc_stage="verify", acdc_loop="both")]
        report["score"]["gating_total"] = 1
        report["score"]["gating_passed"] = 1
        self.assertEqual(model.validate_imported_report(report, "2"), [])

    def test_root_and_schema_literal(self):
        self.assertEqual(model.validate_imported_report([], "2"),
                         ["report root is not an object"])
        self.assertEqual(model.validate_imported_report(_schema2_report(), "3"),
                         ["schema literal mismatch"])
        self.assertEqual(model.validate_imported_report(_schema2_report(), "9"),
                         ["schema literal mismatch"])

    def test_top_level_keys(self):
        report = _schema2_report(extra=1)
        self.assert_invalid(report, "top-level keys not allowed")
        report = _schema2_report()
        del report["results"]
        self.assert_invalid(report, "top-level keys not allowed")

    def test_top_level_types(self):
        self.assert_invalid(_schema2_report(engine_version=1),
                            "engine_version must be a string")
        self.assert_invalid(_schema2_report(github_available="yes"),
                            "github_available must be a bool")
        self.assert_invalid(_schema2_report(commit=None),
                            "commit/branch/generated_at must be strings")
        self.assert_invalid(_schema2_report(results={}),
                            "results must be a list")
        self.assert_invalid(_schema2_report(advisory="x"),
                            "advisory must be a list")

    def test_unknown_version_tuple(self):
        self.assert_invalid(_schema2_report(detector_version="0.4.0"),
                            "unknown schema/version combination")

    def test_detection_shape(self):
        self.assert_invalid(_schema2_report(detection=[]),
                            "detection must be an object or null")
        report = _schema2_report(detection={"any": "dict is fine"})
        self.assertEqual(model.validate_imported_report(report, "2"), [])

    def test_result_shape(self):
        report = _schema2_report(results=["x"])
        self.assert_invalid(report, "results[0]: result not an object")
        report = _schema2_report(results=[_schema2_result()])
        del report["results"][0]["title"]
        self.assert_invalid(report, "results[0]: result keys not allowed")

    def test_result_fields(self):
        self.assert_invalid(_schema2_report(results=[_schema2_result(id="")]),
                            "result id invalid")
        self.assert_invalid(
            _schema2_report(results=[_schema2_result(status="great")]),
            "unknown status")
        for bad_level in (0, 6, "1", True):
            with self.subTest(bad_level=bad_level):
                self.assert_invalid(
                    _schema2_report(results=[_schema2_result(level=bad_level)]),
                    "level out of range")
        self.assert_invalid(
            _schema2_report(results=[_schema2_result(gating="yes")]),
            "field types invalid")
        self.assert_invalid(
            _schema2_report(results=[_schema2_result(passed_apps=True)]),
            "app counts invalid")

    def test_result_evidence(self):
        report = _schema2_report(results=[_schema2_result(evidence="x")])
        self.assert_invalid(report, "evidence not a list")
        report = _schema2_report(results=[_schema2_result(evidence=["x"])])
        self.assert_invalid(report, "evidence item not an object")
        report = _schema2_report(results=[_schema2_result(evidence=[{"a": 1}])])
        self.assert_invalid(report, "evidence keys not exact")
        report = _schema2_report(
            results=[_schema2_result(evidence=[
                {"summary": 1, "tier": "T0", "source": "s", "detail": "d"}])])
        self.assert_invalid(report, "evidence fields must be strings")
        report = _schema2_report(
            results=[_schema2_result(evidence=[
                {"summary": "s", "tier": "T9", "source": "s", "detail": "d"}])])
        self.assert_invalid(report, "unknown evidence tier")

    def test_schema2_carries_no_trace_or_boundary(self):
        report = _schema2_report(
            results=[_schema2_result(decision_trace=None)])
        self.assert_invalid(report, "schema2 carries no decision trace")
        report = _schema2_report(assessment_boundary={})
        self.assert_invalid(report, "top-level keys not allowed")

    def test_score_shape(self):
        self.assert_invalid(_schema2_report(score="x"), "score not an object")
        report = _schema2_report()
        del report["score"]["pillars"]
        self.assert_invalid(report, "score keys not exact")
        report = _schema2_report()
        report["score"]["level"] = "1"
        self.assert_invalid(report, "score.level must be an integer")
        report = _schema2_report()
        report["score"]["level_name"] = 1
        self.assert_invalid(report, "score scalar types invalid")
        report = _schema2_report()
        report["score"]["pass_rate"] = True
        self.assert_invalid(report, "score scalar types invalid")
        report = _schema2_report()
        report["score"]["levels"] = report["score"]["levels"][:4]
        self.assert_invalid(report, "score.levels must have five entries")
        report = _schema2_report()
        del report["score"]["levels"][0]["ratio"]
        self.assert_invalid(report, "score.levels entry keys not exact")
        report = _schema2_report()
        report["score"]["levels"][0]["achieved"] = "yes"
        self.assert_invalid(report, "score.levels entry types invalid")
        report = _schema2_report()
        report["score"]["pillars"] = []
        self.assert_invalid(report, "score pillars/recommendations invalid")

    def test_score_gating_invariant(self):
        report = _schema2_report(results=[_schema2_result(status="pass")])
        # score still claims 0/0 while results contain one passing gating criterion
        self.assert_invalid(report, "gating counts inconsistent")


# --------------------------------------------------------------------------- schema 3 import
class TestValidateImportedReportSchema3(Schema3Fixture):
    def test_fixture_is_valid(self):
        self.assertEqual(model.validate_imported_report(self.fresh(), "3"), [])

    def test_top_keys_exact(self):
        report = self.fresh()
        del report["gaps"]
        # schema3 requires every top-level key; a missing one is rejected up front
        self.assert_invalid(report, "top-level keys not allowed")

    def test_result_keys_exact(self):
        report = self.fresh()
        del report["results"][0]["fix_kind"]
        self.assert_invalid(report, "results[0]: keys not exact schema3")
        report = self.fresh()
        report["results"][0] = "x"
        self.assert_invalid(report, "results[0]: keys not exact schema3")

    def test_result_trace_errors_prefixed(self):
        report = self.fresh()
        report["results"][0]["decision_trace"]["version"] = "2"
        self.assert_invalid(report,
                            "results[0]: decision_trace.version must be '1'")

    def test_score_schema3_fields(self):
        report = self.fresh()
        del report["score"]["evidence_coverage"]["status_counts"]
        self.assert_invalid(report, "evidence_coverage keys not exact")
        report = self.fresh()
        report["score"]["evidence_coverage"]["status_counts"] = {"pass": 1}
        self.assert_invalid(report, "status_counts keys not exact")
        report = self.fresh()
        report["score"]["evidence_coverage"]["status_counts"]["pass"] = -1
        self.assert_invalid(report, "status_counts values invalid")
        report = self.fresh()
        report["score"]["evidence_coverage"]["status_counts"]["pass"] += 1
        self.assert_invalid(report, "status_counts must partition results")
        report = self.fresh()
        report["score"]["max_available_level"] = "4"
        self.assert_invalid(report, "score schema3 fields invalid")
        report = self.fresh()
        report["score"]["next_gate_actions"] = {}
        self.assert_invalid(report, "score schema3 fields invalid")
        report = self.fresh()
        report["score"]["levels"][0]["defined"] = "yes"
        self.assert_invalid(report, "score.levels defined fields invalid")

    def test_detection_shape(self):
        report = self.fresh()
        report["detection"] = None
        self.assertEqual(model.validate_imported_report(report, "3"), [])
        report = self.fresh()
        report["detection"] = {}
        self.assert_invalid(report, "detection keys not exact")

    def test_detection_fields(self):
        cases = [
            ({"project_type": "nope"}, "detection.project_type invalid"),
            ({"confidence": True}, "detection.confidence invalid"),
            ({"signals": [1]}, "detection.signals invalid"),
            ({"languages": [1]}, "detection.languages invalid"),
            ({"is_monorepo": 1}, "detection.is_monorepo invalid"),
            ({"opt_in": {}}, "detection.opt_in invalid"),
            ({"repository_indeterminate": 1},
             "detection indeterminate fields invalid"),
            ({"surfaces": ["nope"]}, "detection.surfaces invalid"),
            ({"candidates": "x"}, "detection.candidates invalid"),
            ({"apps": "x"}, "detection.apps invalid"),
        ]
        for patch, fragment in cases:
            with self.subTest(fragment=fragment):
                report = self.fresh()
                report["detection"].update(patch)
                self.assert_invalid(report, fragment)

    def test_detection_candidates(self):
        report = self.fresh()
        report["detection"]["candidates"] = [{}]
        self.assert_invalid(report, "detection.candidates[0]: candidate keys not exact")
        for patch, fragment in (
                ({"type": "alien"}, "candidate type invalid"),
                ({"signal": 1}, "candidate signal invalid"),
                ({"confidence": True}, "candidate confidence invalid")):
            with self.subTest(fragment=fragment):
                report = self.fresh()
                candidate = _valid_candidate()
                candidate.update(patch)
                report["detection"]["candidates"] = [candidate]
                self.assert_invalid(report, fragment)

    def test_detection_apps(self):
        report = self.fresh()
        report["detection"]["apps"] = [{}]
        self.assert_invalid(report, "detection.apps[0]: app keys not exact")
        cases = [
            ({"path": ""}, "app.path invalid"),
            ({"languages": [1]}, "app.languages invalid"),
            ({"runtime": "alien"}, "app surface enums invalid"),
            ({"deploy_surface": "alien"}, "app surface enums invalid"),
            ({"prod_facing": "maybe"}, "app.prod_facing invalid"),
            ({"test_cmd": "make test"}, "app.test_cmd invalid"),
            ({"ci_jobs": ["x"]}, "app.ci_jobs must be empty"),
            ({"surfaces": ["alien"]}, "app.surfaces invalid"),
            ({"type_candidates": "x"}, "app.type_candidates invalid"),
            ({"type_candidates": [{}]}, "candidate keys not exact"),
        ]
        for patch, fragment in cases:
            with self.subTest(fragment=fragment):
                report = self.fresh()
                app = _valid_app()
                app.update(patch)
                report["detection"]["apps"] = [app]
                self.assert_invalid(report, fragment)
        report = self.fresh()
        report["detection"]["apps"] = [_valid_app()]
        self.assertEqual(model.validate_imported_report(report, "3"), [])

    def test_advisory_and_gaps(self):
        report = self.fresh()
        report["advisory"] = ["a valid advisory note", "another"]
        self.assertEqual(model.validate_imported_report(report, "3"), [])
        report = self.fresh()
        report["advisory"] = [1]
        self.assert_invalid(report, "advisory[0] must be a string")
        report = self.fresh()
        report["gaps"] = None
        self.assertEqual(model.validate_imported_report(report, "3"), [])
        report = self.fresh()
        report["gaps"] = {}
        self.assert_invalid(report, "gaps must be a list")
        report = self.fresh()
        report["gaps"] = [{}]
        self.assert_invalid(report, "gaps[0]: keys not exact")

    def test_gap_fields(self):
        cases = [
            ({"gap_id": ""}, "gap_id invalid"),
            ({"kind": "alien"}, "kind invalid"),
            ({"question": 1}, "question/why invalid"),
            ({"recordable": "yes"}, "recordable/input_kind invalid"),
            ({"input_kind": "alien"}, "recordable/input_kind invalid"),
            ({"blocked_ids": [1]}, "blocked_ids invalid"),
            ({"blocked_gating": -1}, "blocked_gating invalid"),
            ({"levels": [0]}, "levels invalid"),
            ({"levels": [6]}, "levels invalid"),
            ({"evidence": [1]}, "evidence invalid"),
        ]
        for patch, fragment in cases:
            with self.subTest(fragment=fragment):
                report = self.fresh()
                gap = _valid_gap()
                gap.update(patch)
                report["gaps"] = [gap]
                self.assert_invalid(report, fragment)
        report = self.fresh()
        report["gaps"] = [_valid_gap()]
        self.assertEqual(model.validate_imported_report(report, "3"), [])

    def test_boundary_must_match_canonical(self):
        report = self.fresh()
        report["assessment_boundary"] = {}
        self.assert_invalid(report,
                            "assessment_boundary does not match the canonical object")

    def test_provenance_shape(self):
        report = self.fresh()
        report["assessment_provenance"] = None
        self.assert_invalid(report, "assessment_provenance not an object")
        report = self.fresh()
        report["assessment_provenance"]["trust"] = "signed"
        self.assert_invalid(report,
                            "assessment_provenance.trust must be unsigned_unverified")
        report = self.fresh()
        del report["assessment_provenance"]["materials"]
        self.assert_invalid(report, "assessment_provenance keys not exact")
        report = self.fresh()
        report["assessment_provenance"]["predicate_type"] = "other"
        self.assert_invalid(report, "predicate_type invalid")
        report = self.fresh()
        report["assessment_provenance"]["builder"] = {}
        self.assert_invalid(report, "builder keys not exact")
        report = self.fresh()
        report["assessment_provenance"]["builder"]["platform"] = "windows"
        self.assert_invalid(report, "builder id/platform invalid")
        report = self.fresh()
        report["assessment_provenance"]["builder"]["id"] = "other"
        self.assert_invalid(report, "builder id/platform invalid")
        report = self.fresh()
        report["assessment_provenance"]["subject"] = {}
        self.assert_invalid(report, "subject keys not exact")
        report = self.fresh()
        del report["assessment_provenance"]["invocation"]["waivers"]
        self.assert_invalid(report, "invocation keys not exact")
        report = self.fresh()
        report["assessment_provenance"]["invocation"]["inputs"]["profile"] = "other"
        self.assert_invalid(report, "invocation.inputs.profile invalid")
        report = self.fresh()
        report["assessment_provenance"]["invocation"]["execution"] = "x"
        self.assert_invalid(report, "invocation.execution not an object")
        report = self.fresh()
        execution = report["assessment_provenance"]["invocation"]["execution"]
        execution.update({"requested": False, "completed": False, "successful": True})
        self.assert_invalid(report, "execution.successful requires completed")
        report = self.fresh()
        execution = report["assessment_provenance"]["invocation"]["execution"]
        execution.update({"requested": False, "completed": True, "successful": False})
        self.assert_invalid(report, "execution.completed requires requested")
        report = self.fresh()
        report["assessment_provenance"]["generated_at"] = 1
        self.assert_invalid(report, "generated_at must be a string")

    def test_repository_must_be_canonical(self):
        report = self.fresh()
        report["repository"] = {"identity_kind": "local_path",
                                "identity_hash": "a" * 16, "name": "bad name"}
        self.assert_invalid(report,
                            "repository identity is not in canonical public form")


# --------------------------------------------------------------------------- legacy schema 1
class TestValidateLegacyFixReportV1(unittest.TestCase):
    def _report(self, **over):
        report = {
            "schema_version": "1", "engine_version": "0.2.0",
            "registry_version": "0.2.0", "detector_version": "0.2.0",
            "project_path": "/untrusted", "commit": "", "branch": "",
            "github_available": False, "detection": None,
            "score": {"level": 0}, "results": [], "advisory": [],
        }
        report.update(over)
        return report

    def test_valid(self):
        self.assertEqual(model.validate_legacy_fix_report_v1(self._report()), [])
        report = self._report(results=[{"status": "pass"}, {"status": "fail"}])
        self.assertEqual(model.validate_legacy_fix_report_v1(report), [])

    def test_root_and_keys(self):
        self.assertEqual(model.validate_legacy_fix_report_v1([]),
                         ["report root is not an object"])
        self.assertEqual(model.validate_legacy_fix_report_v1({}),
                         ["schema1 top-level keys not exact"])

    def test_schema_literal(self):
        self.assertEqual(
            model.validate_legacy_fix_report_v1(self._report(schema_version="2")),
            ["schema1 literal mismatch"])

    def test_score_shape(self):
        self.assertEqual(model.validate_legacy_fix_report_v1(self._report(score="x")),
                         ["schema1 score shape invalid"])
        report = self._report(score={"level": 0, "recommendations": []})
        self.assertEqual(model.validate_legacy_fix_report_v1(report),
                         ["schema1 score shape invalid"])

    def test_results(self):
        self.assertEqual(
            model.validate_legacy_fix_report_v1(self._report(results=["x"])),
            ["schema1 results[0] not an object"])
        report = self._report(results=[{"status": "pass", "passed_apps": 1}])
        self.assertEqual(model.validate_legacy_fix_report_v1(report),
                         ["schema1 results[0] carries post-v0.2 fields"])
        report = self._report(results=[{"status": "great"}])
        self.assertEqual(model.validate_legacy_fix_report_v1(report),
                         ["schema1 results[0] unknown status"])


# --------------------------------------------------------------------------- Report.to_dict
class TestReportToDict(unittest.TestCase):
    def _report(self, results):
        return model.Report(project_path="/process-local",
                            schema_version="3", engine_version="0.11.0",
                            registry_version="0.8.0", detector_version="0.6.0",
                            results=results)

    def _result(self, trace=None, rationale="ok"):
        return model.CriterionResult(
            id="x.y", title="t", pillar="p", level=1, scope="repository",
            gating=False, status=model.Status.PASS, rationale=rationale,
            decision_trace=trace)

    def _valid_trace(self):
        return model.DecisionTrace(
            version="1", reason_code="check.pass", rule_ref="m.f",
            steps=[model.DecisionStep("rule", "rule.applied", "R.", []),
                   model.DecisionStep("evaluation", "check.pass", "E.", []),
                   model.DecisionStep("conclusion", "conclusion.pass",
                                      "Result: pass.", [])])

    def test_minimal_report_projects(self):
        report = self._report([self._result(trace=self._valid_trace())])
        data = report.to_dict()
        self.assertIsNone(data["score"])
        self.assertIsNone(data["assessment_provenance"])
        self.assertNotIn("project_path", data)
        self.assertEqual(data["results"][0]["decision_trace"]["reason_code"],
                         "check.pass")

    def test_invalid_trace_fails_closed(self):
        bad_trace = model.DecisionTrace(
            version="1", reason_code="check.pass", rule_ref="m.f",
            steps=[model.DecisionStep("rule", "rule.applied", "R.", [])])
        with self.assertRaises(model.PublicReportValidationError):
            self._report([self._result(trace=bad_trace)]).to_dict()

    def test_finalization_failure_never_leaks(self):
        class Exploding:
            def __str__(self):
                raise RuntimeError("sensitive internals")

        with self.assertRaises(model.PublicReportValidationError) as ctx:
            self._report([self._result(rationale=Exploding())]).to_dict()
        self.assertNotIn("sensitive", str(ctx.exception))

    def test_invalid_provenance_fails_closed(self):
        report = self._report([self._result(trace=self._valid_trace())])
        report.assessment_provenance = {"trust": "signed"}
        with self.assertRaises(model.PublicReportValidationError):
            report.to_dict()

    def test_advisory_text_sanitized(self):
        report = self._report([self._result(trace=self._valid_trace())])
        report.advisory = ["note\x00with controls"]
        data = report.to_dict()
        self.assertEqual(data["advisory"], ["note with controls"])


if __name__ == "__main__":
    unittest.main()
