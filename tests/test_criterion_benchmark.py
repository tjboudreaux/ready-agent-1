"""Criterion-graduation benchmark: label validation, eligibility thresholds, CLI exits."""
import io
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from evals import criterion_benchmark as cb

LABELS_PATH = Path(cb.__file__).resolve().parent / "criterion_labels.json"

ECOS = ["python", "node", "go", "rust", "java"]


def _case(cid, i, eco="python", expected="pass", *, adversarial=False, severity="low",
          fixture="fx-a"):
    return {"id": f"{cid}-{i}", "criterion_id": cid, "fixture": fixture,
            "ecosystem": eco, "expected": expected, "adversarial": adversarial,
            "severity": severity}


def _corpus(cid, *, passes=40, fails=40, unknowns=10, skipped=10, ecosystems=ECOS):
    """A metric-eligible corpus by default: 100 cases, >=30 pass, >=30 blocking, 5 ecosystems."""
    cases = []
    i = 0
    for expected, count in (("pass", passes), ("fail", fails),
                            ("unknown", unknowns), ("skipped", skipped)):
        for _ in range(count):
            cases.append(_case(cid, i, ecosystems[i % len(ecosystems)], expected))
            i += 1
    return cases


def _perfect(case, *, fixture_root):
    return case["expected"]


def _stub(predictions):
    """A deterministic evaluate_case: {case id: predicted status}; default = expected."""
    def run(case, *, fixture_root):
        return predictions.get(case["id"], case["expected"])
    return run


class TestValidateLabels(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ra1-labels-")
        self.root = Path(self._tmp.name)
        (self.root / "fx-a").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def validate(self, data):
        return cb.validate_labels(data, fixture_root=self.root)

    def test_valid_corpus(self):
        data = {"schema_version": "1", "cases": [_case("docs.readme", 0)]}
        self.assertEqual(self.validate(data), [])

    def test_committed_labels_file_validates(self):
        data = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cb.validate_labels(data), [])

    def test_root_shape(self):
        self.assertIn("label file root must be {schema_version, cases}",
                      self.validate({"schema_version": "1"}))
        self.assertIn("label file root must be {schema_version, cases}",
                      self.validate({"schema_version": "1", "cases": [], "extra": 1}))
        self.assertIn("label file root must be {schema_version, cases}", self.validate([]))

    def test_schema_version(self):
        self.assertEqual(self.validate({"schema_version": "2", "cases": []}),
                         ["schema_version must be '1'"])

    def test_cases_must_be_a_list(self):
        self.assertEqual(self.validate({"schema_version": "1", "cases": {}}),
                         ["cases must be a list"])

    def test_case_keys_exact(self):
        case = _case("docs.readme", 0)
        del case["severity"]
        errors = self.validate({"schema_version": "1", "cases": [case]})
        self.assertTrue(any("keys not exact" in e for e in errors))
        case = {**_case("docs.readme", 0), "reviewer": "x"}
        errors = self.validate({"schema_version": "1", "cases": [case]})
        self.assertTrue(any("keys not exact" in e for e in errors))

    def test_id_invalid_and_duplicate(self):
        case = {**_case("docs.readme", 0), "id": ""}
        self.assertIn("cases[0]: id invalid",
                      self.validate({"schema_version": "1", "cases": [case]}))
        dup = [_case("docs.readme", 0), _case("docs.readme", 0)]
        errors = self.validate({"schema_version": "1", "cases": dup})
        self.assertIn("cases[1]: duplicate id 'docs.readme-0'", errors)

    def test_criterion_id_invalid(self):
        case = {**_case("docs.readme", 0), "criterion_id": "readme"}
        self.assertIn("cases[0]: criterion_id invalid",
                      self.validate({"schema_version": "1", "cases": [case]}))

    def test_fixture_path_safety(self):
        for bad in ("/abs/fx", "a\\b", "../escape", "a/../../b"):
            case = {**_case("docs.readme", 0), "fixture": bad}
            with self.subTest(fixture=bad):
                self.assertIn("cases[0]: fixture must be a repository-relative safe path",
                              self.validate({"schema_version": "1", "cases": [case]}))

    def test_fixture_root_must_exist(self):
        case = {**_case("docs.readme", 0), "fixture": "fx-missing"}
        self.assertIn("cases[0]: fixture root does not exist: fx-missing",
                      self.validate({"schema_version": "1", "cases": [case]}))

    def test_enums(self):
        for key, value, fragment in (
                ("ecosystem", "cobol", "unknown ecosystem 'cobol'"),
                ("expected", "passed", "unknown expected status 'passed'"),
                ("severity", "blocker", "unknown severity 'blocker'")):
            case = {**_case("docs.readme", 0), key: value}
            with self.subTest(key=key):
                self.assertIn(f"cases[0]: {fragment}",
                              self.validate({"schema_version": "1", "cases": [case]}))
        for expected in ("pass", "fail", "unknown", "skipped"):
            case = {**_case("docs.readme", 0), "expected": expected}
            with self.subTest(expected=expected):
                self.assertEqual(self.validate({"schema_version": "1", "cases": [case]}), [])

    def test_adversarial_must_be_a_real_bool(self):
        for bad in (1, "true", None):
            case = {**_case("docs.readme", 0), "adversarial": bad}
            with self.subTest(value=bad):
                self.assertIn("cases[0]: adversarial must be a bool",
                              self.validate({"schema_version": "1", "cases": [case]}))

    def test_errors_accumulate_across_cases(self):
        cases = [{**_case("docs.readme", 0), "ecosystem": "cobol"},
                 {**_case("docs.readme", 1), "severity": "blocker"}]
        errors = self.validate({"schema_version": "1", "cases": cases})
        self.assertEqual(len(errors), 2)


class TestEvaluateCandidate(unittest.TestCase):
    def test_eligible_corpus(self):
        artifact = cb.evaluate_candidate("docs.readme", _corpus("docs.readme"),
                                         evaluate_case=_perfect)
        self.assertTrue(artifact["eligible"])
        self.assertEqual(artifact["reasons"], [])
        self.assertEqual(artifact["review_status"], "unverified_external")
        self.assertEqual(artifact["offending_cases"], [])
        metrics = artifact["metrics"]
        self.assertEqual(metrics["cases"], 100)
        self.assertEqual(metrics["expected_pass"], 40)
        self.assertEqual(metrics["expected_blocking"], 50)
        self.assertEqual(metrics["ecosystems"], sorted(ECOS))
        self.assertEqual(metrics["pass_precision"], 1.0)
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["confusion"]["pass"]["pass"], 40)
        self.assertEqual(metrics["confusion"]["fail"]["fail"], 40)
        for eco in ECOS:
            self.assertEqual(metrics["per_ecosystem"][eco],
                             {"total": 20, "correct": 20, "accuracy": 1.0})

    def test_deterministic_artifact(self):
        cases = _corpus("docs.readme")
        first = cb.evaluate_candidate("docs.readme", cases, evaluate_case=_perfect)
        second = cb.evaluate_candidate("docs.readme", cases, evaluate_case=_perfect)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_confusion_matrix_covers_every_status(self):
        artifact = cb.evaluate_candidate("docs.readme", _corpus("docs.readme"),
                                         evaluate_case=_perfect)
        self.assertEqual(set(artifact["metrics"]["confusion"]),
                         {"pass", "fail", "unknown", "skipped"})
        for row in artifact["metrics"]["confusion"].values():
            self.assertEqual(set(row), {"pass", "fail", "unknown", "skipped"})

    def test_min_cases_boundary(self):
        cases = _corpus("docs.readme", passes=39, fails=40, unknowns=10, skipped=10)
        artifact = cb.evaluate_candidate("docs.readme", cases, evaluate_case=_perfect)
        self.assertFalse(artifact["eligible"])
        self.assertIn("cases 99 < 100", artifact["reasons"])

    def test_min_expected_pass_boundary(self):
        cases = _corpus("docs.readme", passes=29, fails=40, unknowns=31, skipped=0)
        artifact = cb.evaluate_candidate("docs.readme", cases, evaluate_case=_perfect)
        self.assertFalse(artifact["eligible"])
        self.assertIn("expected pass 29 < 30", artifact["reasons"])

    def test_min_expected_blocking_boundary(self):
        cases = _corpus("docs.readme", passes=71, fails=29, unknowns=0, skipped=0)
        artifact = cb.evaluate_candidate("docs.readme", cases, evaluate_case=_perfect)
        self.assertFalse(artifact["eligible"])
        self.assertIn("expected blocking 29 < 30", artifact["reasons"])

    def test_min_ecosystems_boundary(self):
        cases = _corpus("docs.readme", ecosystems=ECOS[:4])
        artifact = cb.evaluate_candidate("docs.readme", cases, evaluate_case=_perfect)
        self.assertFalse(artifact["eligible"])
        self.assertIn("ecosystems 4 < 5", artifact["reasons"])

    def test_precision_boundary(self):
        # 99 true passes out of 100 predicted passes is exactly 0.99 — eligible.
        cases = _corpus("docs.readme", passes=99, fails=31, unknowns=1, skipped=0)
        artifact = cb.evaluate_candidate(
            "docs.readme", cases,
            evaluate_case=_stub({"docs.readme-99": "pass"}))  # first fail case predicted pass
        self.assertEqual(artifact["metrics"]["pass_precision"], 0.99)
        self.assertEqual(artifact["offending_cases"], [])  # low severity, not adversarial
        self.assertTrue(artifact["eligible"])
        # 98/100 = 0.98 — below the floor.
        cases = _corpus("docs.readme", passes=98, fails=32, unknowns=0, skipped=0)
        artifact = cb.evaluate_candidate(
            "docs.readme", cases,
            evaluate_case=_stub({"docs.readme-98": "pass", "docs.readme-99": "pass"}))
        self.assertFalse(artifact["eligible"])
        self.assertEqual(artifact["reasons"], ["pass precision 0.98 < 0.99"])

    def test_accuracy_boundary(self):
        # 95/100 exact-status correct is exactly 0.95 — eligible.
        flips = {f"docs.readme-{i}": "unknown" for i in range(30, 35)}  # fail -> unknown
        artifact = cb.evaluate_candidate("docs.readme", _corpus(
            "docs.readme", passes=30, fails=30, unknowns=40, skipped=0),
            evaluate_case=_stub(flips))
        self.assertEqual(artifact["metrics"]["accuracy"], 0.95)
        self.assertEqual(artifact["metrics"]["pass_precision"], 1.0)
        self.assertTrue(artifact["eligible"])
        # 94/100 — below the floor.
        flips = {f"docs.readme-{i}": "unknown" for i in range(30, 36)}
        artifact = cb.evaluate_candidate("docs.readme", _corpus(
            "docs.readme", passes=30, fails=30, unknowns=40, skipped=0),
            evaluate_case=_stub(flips))
        self.assertFalse(artifact["eligible"])
        self.assertIn("accuracy 0.94 < 0.95", artifact["reasons"])

    def test_adversarial_false_pass_ineligible(self):
        cases = _corpus("docs.readme")
        cases[40] = _case("docs.readme", 40, "node", "fail", adversarial=True)
        artifact = cb.evaluate_candidate(
            "docs.readme", cases, evaluate_case=_stub({"docs.readme-40": "pass"}))
        self.assertFalse(artifact["eligible"])
        self.assertIn("1 offending case(s)", artifact["reasons"])
        self.assertEqual(artifact["offending_cases"], [
            {"id": "docs.readme-40", "reason": "false pass on adversarial case (expected fail)"}])

    def test_high_and_critical_false_pass_ineligible(self):
        cases = _corpus("docs.readme")
        cases[40] = _case("docs.readme", 40, "node", "fail", severity="high")
        cases[41] = _case("docs.readme", 41, "go", "fail", severity="critical")
        artifact = cb.evaluate_candidate(
            "docs.readme", cases,
            evaluate_case=_stub({"docs.readme-40": "pass", "docs.readme-41": "pass"}))
        self.assertFalse(artifact["eligible"])
        self.assertIn("2 offending case(s)", artifact["reasons"])
        self.assertEqual(artifact["offending_cases"], [
            {"id": "docs.readme-40", "reason": "false pass on high case (expected fail)"},
            {"id": "docs.readme-41", "reason": "false pass on critical case (expected fail)"}])

    def test_evaluation_error_is_offending(self):
        artifact = cb.evaluate_candidate(
            "docs.readme", _corpus("docs.readme"),
            evaluate_case=_stub({"docs.readme-0": None}))
        self.assertFalse(artifact["eligible"])
        self.assertEqual(artifact["offending_cases"],
                         [{"id": "docs.readme-0", "reason": "evaluation error"}])
        # The error case is excluded from the confusion denominators but still counted as a case.
        self.assertEqual(artifact["metrics"]["cases"], 100)
        total = sum(sum(row.values()) for row in artifact["metrics"]["confusion"].values())
        self.assertEqual(total, 99)

    def test_zero_cases_ineligible(self):
        artifact = cb.evaluate_candidate("x.absent", _corpus("docs.readme"),
                                         evaluate_case=_perfect)
        self.assertFalse(artifact["eligible"])
        self.assertEqual(artifact["reasons"], [
            "cases 0 < 100", "expected pass 0 < 30", "expected blocking 0 < 30",
            "ecosystems 0 < 5", "pass precision 0.0 < 0.99", "accuracy 0.0 < 0.95"])
        self.assertEqual(artifact["review_status"], "unverified_external")


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ra1-bench-main-")
        self.fixture_root = Path(self._tmp.name) / "corpus"
        (self.fixture_root / "fx-a").mkdir(parents=True)
        self.labels = Path(self._tmp.name) / "labels.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_labels(self, cases):
        self.labels.write_text(json.dumps({"schema_version": "1", "cases": cases}),
                               encoding="utf-8")
        return self.labels

    def _run(self, argv, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cb.main(argv, **kw)
        return rc, out.getvalue()

    def test_validate_committed_corpus_exits_0(self):
        rc, out = self._run(["--validate", str(LABELS_PATH)])
        self.assertEqual(rc, 0)
        artifact = json.loads(out)
        self.assertEqual(artifact["mode"], "validate")
        self.assertTrue(artifact["valid"])
        self.assertEqual(artifact["errors"], [])
        self.assertEqual(artifact["review_status"], "unverified_external")

    def test_validate_invalid_corpus_exits_2(self):
        labels = self._write_labels([_case("docs.readme", 0, fixture="no-such-fixture-dir")])
        rc, out = self._run(["--validate", str(labels)])
        self.assertEqual(rc, 2)
        artifact = json.loads(out)
        self.assertFalse(artifact["valid"])
        self.assertTrue(any("fixture root does not exist" in e for e in artifact["errors"]))
        self.assertEqual(artifact["review_status"], "unverified_external")

    def test_validate_unreadable_file_raises_systemexit(self):
        with self.assertRaises(SystemExit):
            cb.main(["--validate", "/nonexistent/labels.json"])

    def test_candidate_eligible_exits_0(self):
        # Full real pipeline: injected labels + fixture root, deterministic evaluate_case.
        labels = self._write_labels(_corpus("docs.readme"))
        rc, out = self._run(["--candidate", "docs.readme"], labels_path=labels,
                            fixture_root=self.fixture_root, evaluate_case=_perfect)
        self.assertEqual(rc, 0)
        artifact = json.loads(out)
        self.assertEqual(artifact["criterion_id"], "docs.readme")
        self.assertTrue(artifact["eligible"])
        self.assertEqual(artifact["reasons"], [])
        self.assertEqual(artifact["metrics"]["cases"], 100)
        self.assertEqual(artifact["metrics"]["pass_precision"], 1.0)
        self.assertEqual(artifact["review_status"], "unverified_external")

    def test_candidate_ineligible_exits_1(self):
        # Structurally valid corpus that is below the quantitative policy (99 cases).
        labels = self._write_labels(_corpus("docs.readme", passes=39, fails=40,
                                            unknowns=10, skipped=10))
        rc, out = self._run(["--candidate", "docs.readme"], labels_path=labels,
                            fixture_root=self.fixture_root, evaluate_case=_perfect)
        self.assertEqual(rc, 1)
        artifact = json.loads(out)
        self.assertFalse(artifact["eligible"])
        self.assertIn("cases 99 < 100", artifact["reasons"])

    def test_candidate_committed_corpus_ineligible_exits_1(self):
        # The default path (no injection): the committed corpus is below policy.
        rc, out = self._run(["--candidate", "docs.readme"])
        self.assertEqual(rc, 1)
        artifact = json.loads(out)
        self.assertEqual(artifact["criterion_id"], "docs.readme")
        self.assertFalse(artifact["eligible"])
        self.assertEqual(artifact["review_status"], "unverified_external")

    def test_candidate_invalid_corpus_exits_2(self):
        # Real validate_labels failure on an injected corpus with a missing fixture root.
        labels = self._write_labels([_case("docs.readme", 0, fixture="no-such-dir")])
        rc, out = self._run(["--candidate", "docs.readme"], labels_path=labels,
                            fixture_root=self.fixture_root, evaluate_case=_perfect)
        self.assertEqual(rc, 2)
        artifact = json.loads(out)
        self.assertEqual(artifact["mode"], "candidate")
        self.assertFalse(artifact["valid"])
        self.assertTrue(any("fixture root does not exist" in e for e in artifact["errors"]))
        self.assertEqual(artifact["review_status"], "unverified_external")

    def test_usage_errors_exit_2(self):
        for argv in ([], ["--bogus"], ["--validate"], ["--validate", "a", "b"]):
            with self.subTest(argv=argv):
                self.assertEqual(cb.main(argv), 2)


class TestValidateLabelsCaseShape(unittest.TestCase):
    def test_case_not_an_object(self):
        errors = cb.validate_labels({"schema_version": "1", "cases": ["nope"]})
        self.assertIn("cases[0]: not an object", errors)


class TestEvaluateCaseReal(unittest.TestCase):
    """The real _evaluate_case pipeline: registry lookup, fixture scan, raw check."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ra1-bench-eval-")
        self.fixture_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _case(self, cid, fixture="fx-a"):
        return {"id": "c-1", "criterion_id": cid, "fixture": fixture,
                "ecosystem": "python", "expected": "pass", "adversarial": False,
                "severity": "low"}

    def test_unknown_criterion_returns_none(self):
        self.assertIsNone(cb._evaluate_case(self._case("nope.nope"),
                                            fixture_root=self.fixture_root))

    def test_real_check_runs_against_fixture(self):
        fx = self.fixture_root / "fx-a"
        fx.mkdir()
        body = "# Title\n\n" + ("Substantive readme content. " * 20) + "\n## Usage\n"
        (fx / "README.md").write_text(body, encoding="utf-8")
        self.assertEqual(cb._evaluate_case(self._case("docs.readme"),
                                           fixture_root=self.fixture_root), "pass")

    def test_raising_check_returns_none(self):
        # A malformed manifest makes the raw style.linter_config check raise; the
        # benchmark converts that into an "evaluation error" (None), never a crash.
        fx = self.fixture_root / "fx-a"
        fx.mkdir()
        (fx / "pyproject.toml").write_text("not [valid toml", encoding="utf-8")
        self.assertIsNone(cb._evaluate_case(self._case("style.linter_config"),
                                            fixture_root=self.fixture_root))

    def test_git_backed_check_uses_no_git_runner(self):
        # build.vcs_cli consults the Git collector; the benchmark's runner answers
        # "no git" (exit 128) so the check reports the repo as not version controlled.
        fx = self.fixture_root / "fx-a"
        fx.mkdir()
        self.assertEqual(cb._evaluate_case(self._case("build.vcs_cli"),
                                           fixture_root=self.fixture_root), "fail")

    def test_engine_path_inserted_when_missing(self):
        engine_path = str(cb._ROOT / "engine")
        saved = sys.path[:]
        sys.path = [p for p in sys.path if p != engine_path]
        try:
            self.assertIsNone(cb._evaluate_case(self._case("nope.nope"),
                                                fixture_root=self.fixture_root))
            self.assertIn(engine_path, sys.path)
        finally:
            sys.path = saved


class TestModuleImport(unittest.TestCase):
    def test_import_inserts_engine_path_when_missing(self):
        engine_path = str(cb._ROOT / "engine")
        saved = sys.path[:]
        sys.path = [p for p in sys.path if p != engine_path]
        try:
            runpy.run_path(str(cb.__file__))
            self.assertIn(engine_path, sys.path)
        finally:
            sys.path = saved


if __name__ == "__main__":
    unittest.main()
