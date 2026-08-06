"""Gap derivation, and the integrity rules that keep an answer from becoming a verdict."""
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from readiness import cli
from readiness import gaps as gaps_mod
from readiness import report as report_mod
from readiness.collectors import StaticCollector
from readiness.detect import classify_candidates, load_readiness_config
from readiness.model import (
    App,
    CriterionResult,
    Detection,
    Gap,
    LevelScore,
    Report,
    ScoreSummary,
    Status,
)
from readiness.run import analyze
from readiness.score import NOT_OPTED_IN_LOOP, load_registry

from tests._util import make_repo, rmtree

REPO = Path(__file__).resolve().parents[1]


def _rep(results=None, detection=None, score=None, github_available=False, **kw):
    return Report(project_path="/p", schema_version="2", engine_version="0.10.0",
                  registry_version="0.7.0", detector_version="0.5.0",
                  results=results or [], detection=detection, score=score,
                  github_available=github_available, **kw)


def _crit(cid, **kw):
    base = dict(id=cid, title=cid, pillar="P", level=2, scope="repository", gating=True,
                status=Status.UNKNOWN)
    base.update(kw)
    return CriterionResult(**base)


class TestConfigMappingIsCurrent(unittest.TestCase):
    """The authored criterion->config table must not rot as checks are renamed."""

    def test_every_mapped_criterion_exists_in_the_registry(self):
        ids = {c["id"] for c in load_registry()}
        for cid in gaps_mod._CONFIG_GAPS:
            self.assertIn(cid, ids, f"{cid} is mapped in gaps.py but not in the registry")

    def test_every_mapped_gap_declares_a_config_path_and_question(self):
        for cid, spec in gaps_mod._CONFIG_GAPS.items():
            for field in ("id", "path", "kind_of_value", "statuses", "question", "why"):
                self.assertIn(field, spec, f"{cid} mapping is missing {field}")


class TestDetectionGaps(unittest.TestCase):
    def test_unknown_type_asks_for_a_pin_and_names_the_stuck_criteria(self):
        narrow = next(c["id"] for c in load_registry()
                      if (c.get("applies_when") or {}).get("project_types", ["*"]) != ["*"])
        detection = Detection(project_type="unknown", confidence=0.3,
                              signals=["no recognizable manifest"],
                              apps=[App(path=".")])
        report = _rep(results=[_crit(narrow, level=3)], detection=detection)
        found = {g.id: g for g in gaps_mod.derive_gaps(report, {})}
        self.assertIn("detect.project_type", found)
        gap = found["detect.project_type"]
        self.assertEqual(gap.kind, "detection")
        self.assertEqual(gap.blocks, [narrow])
        self.assertEqual(gap.blocked_gating, 1)
        self.assertEqual(gap.levels, [3])
        self.assertEqual(gap.answer["path"], "detect.project_type")
        self.assertIn("service", gap.options)
        self.assertIn("no recognizable manifest", gap.evidence)

    def test_an_existing_pin_silences_the_detection_gap(self):
        detection = Detection(project_type="unknown", confidence=0.3, apps=[App(path=".")])
        report = _rep(detection=detection)
        config = {"detect": {"project_type": "service"}}
        self.assertNotIn("detect.project_type",
                         {g.id for g in gaps_mod.derive_gaps(report, config)})

    def test_competing_strong_signals_surface_as_a_contested_gap(self):
        detection = Detection(
            project_type="service", confidence=0.9, apps=[App(path=".")],
            candidates=[{"type": "service", "confidence": 0.9, "signal": "django"},
                        {"type": "frontend", "confidence": 0.9, "signal": "next"}])
        found = {g.id: g for g in gaps_mod.derive_gaps(_rep(detection=detection), {})}
        gap = found["detect.project_type.contested"]
        self.assertIn("service", gap.question)
        self.assertIn("frontend", gap.question)
        self.assertEqual(gap.options, ["service", "frontend"])
        self.assertEqual(gap.evidence, ["django", "next"])

    def test_a_single_confident_classification_asks_nothing(self):
        detection = Detection(
            project_type="service", confidence=0.9, apps=[App(path=".")],
            candidates=[{"type": "service", "confidence": 0.9, "signal": "django"}])
        self.assertEqual(gaps_mod.derive_gaps(_rep(detection=detection), {}), [])

    def test_three_candidates_read_as_a_sentence(self):
        detection = Detection(
            project_type="service", confidence=0.9, apps=[App(path=".")],
            candidates=[{"type": "service", "confidence": 0.9, "signal": "flask"},
                        {"type": "frontend", "confidence": 0.9, "signal": "next"},
                        {"type": "library", "confidence": 0.6, "signal": "packaged"}])
        found = {g.id: g for g in gaps_mod.derive_gaps(_rep(detection=detection), {})}
        gap = found["detect.project_type.contested"]
        self.assertIn("service, frontend, and library", gap.question)
        self.assertIn("frontend and library", gap.why)

    def test_a_requirements_only_service_is_never_asked_to_classify_itself(self):
        """The inference fix, stated as an outcome: better detection means fewer questions.

        A Flask app declaring deps only in requirements.txt used to scan as `unknown` and
        earn a `detect.project_type` gap — the scan asking a developer to supply something it
        could read for itself.
        """
        root = make_repo({"README.md": "# svc", "requirements.txt": "flask>=3\ngunicorn==21\n"})
        self.addCleanup(rmtree, root)
        report = analyze(root, {"no_github": True})
        self.assertEqual(report.detection.project_type, "service")
        self.assertNotIn("detect.project_type", {g.id for g in report.gaps})
        self.assertNotIn("detect.project_type.contested", {g.id for g in report.gaps})

    def test_declaring_several_surfaces_applies_every_surfaces_criteria(self):
        """The full-stack answer, end to end: declare two surfaces, get both criteria sets.

        Without this the contested question was unanswerable — picking `service` silently
        skipped frontend-only criteria and picking `frontend` skipped the service-only ones,
        which is the exact defect the question exists to surface.
        """
        files = {"README.md": "# app",
                 "requirements.txt": "flask>=3\n",
                 "package.json": '{"name":"web","dependencies":{"next":"14"}}'}
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        service_only = "docs.api_schema_docs"        # service/api, never frontend
        frontend_only = "build.dependency_weight_budget"  # frontend only

        before = analyze(root, {"no_github": True})
        self.assertIn("detect.project_type.contested", {g.id for g in before.gaps})
        rows = {r.id: r for r in before.results}
        # Inferred as a service: the frontend-only criterion is skipped as inapplicable.
        self.assertEqual(rows[frontend_only].status, Status.SKIPPED)

        cfg = Path(root) / ".agents" / "readiness"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(
            json.dumps({"detect": {"surfaces": ["service", "frontend"]}}), encoding="utf-8")

        after = analyze(root, {"no_github": True})
        self.assertNotIn("detect.project_type.contested", {g.id for g in after.gaps})
        self.assertEqual(after.detection.apps[0].surfaces, ["service", "frontend"])
        rows = {r.id: r for r in after.results}
        for cid in (service_only, frontend_only):
            self.assertNotEqual(rows[cid].status, Status.SKIPPED,
                                f"{cid} should apply once both surfaces are declared")

    def test_surface_order_changes_display_but_never_a_result(self):
        """First-entry display semantics must not leak into applicability.

        Repository-scope criteria were matched against `project_type`, which is the first
        declared surface, so `["frontend", "service"]` skipped seven repository-scope
        service-only criteria that `["service", "frontend"]` evaluated — the same declaration
        hiding findings based on the order it was written in.

        Two total assertions, guarding different things. The (id, app_path, status) comparison
        is what catches that regression: every affected criterion today is advisory, so the
        level never moved and a score-only check would have passed straight through it. The
        score comparison guards the stronger claim for the day a repository-scope
        surface-specific *gating* criterion is registered, of which there are currently none.
        """
        files = {"README.md": "# app",
                 "requirements.txt": "flask>=3\n",
                 "package.json": '{"name":"web","dependencies":{"next":"14"}}'}
        # Repository scope (service, not frontend) and application scope (frontend only):
        # these must be *evaluated*, not merely equal, or both orders could skip them alike.
        probes = ("security.dast", "observability.runbooks", "docs.architecture_doc",
                  "build.dependency_weight_budget")
        results, scores = {}, {}
        for order in (["service", "frontend"], ["frontend", "service"]):
            root = make_repo(files)
            self.addCleanup(rmtree, root)
            cfg = Path(root) / ".agents" / "readiness"
            cfg.mkdir(parents=True, exist_ok=True)
            (cfg / "config.json").write_text(json.dumps({"detect": {"surfaces": order}}),
                                             encoding="utf-8")
            report = analyze(root, {"no_github": True})
            key = tuple(order)
            results[key] = sorted((r.id, r.app_path, r.status.value) for r in report.results)
            scores[key] = report.score.to_dict()
            rows = {r.id: r for r in report.results}
            # Only the displayed type follows declaration order.
            self.assertEqual(report.detection.project_type, order[0])
            self.assertEqual(report.detection.match_surfaces(), order)
            for cid in probes:
                self.assertNotEqual(rows[cid].status, Status.SKIPPED,
                                    f"{cid} skipped with surfaces={order}")
        forward, reversed_ = ("service", "frontend"), ("frontend", "service")
        # Every registered criterion is accounted for, so "equal" cannot mean "both empty".
        self.assertEqual(len(results[forward]), len(load_registry()))
        self.assertEqual(results[forward], results[reversed_])   # catches the regression
        self.assertEqual(scores[forward], scores[reversed_])     # guards a future gating one

    def test_an_undeclared_repository_matches_on_its_inferred_type(self):
        """The fallback: no declaration means applicability is exactly as before."""
        root = make_repo({"README.md": "# x", "requirements.txt": "flask>=3\n"})
        self.addCleanup(rmtree, root)
        report = analyze(root, {"no_github": True})
        self.assertEqual(report.detection.surfaces, [])
        self.assertEqual(report.detection.match_surfaces(), ["service"])

    def test_an_invalid_surfaces_pin_is_ignored_and_disclosed(self):
        root = make_repo({"README.md": "# x", "requirements.txt": "flask>=3\n"})
        self.addCleanup(rmtree, root)
        cfg = Path(root) / ".agents" / "readiness"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(
            json.dumps({"detect": {"surfaces": ["nonsense"]}}), encoding="utf-8")
        report = analyze(root, {"no_github": True})
        self.assertEqual(report.detection.apps[0].surfaces, [])
        self.assertTrue(any("ignored invalid surfaces pin" in s
                            for s in report.detection.signals))

    def test_monorepo_asks_per_app_only_for_unevidenced_apps(self):
        apps = [App(path="apps/web", type_confidence=0.9,
                    type_candidates=[{"type": "frontend", "confidence": 0.9, "signal": "next"}]),
                App(path="apps/mystery", type_confidence=0.3,
                    type_candidates=[{"type": "unknown", "confidence": 0.3,
                                      "signal": "ambiguous"}])]
        detection = Detection(project_type="monorepo-root", confidence=0.9, is_monorepo=True,
                              apps=apps)
        results = [_crit("x.y", app_path="apps/mystery", level=2)]
        found = {g.id for g in gaps_mod.derive_gaps(_rep(results=results, detection=detection), {})}
        self.assertIn("detect.apps.apps/mystery", found)
        self.assertNotIn("detect.apps.apps/web", found)

    def test_a_pinned_monorepo_app_is_not_asked_about(self):
        apps = [App(path="apps/mystery", type_confidence=0.3, type_candidates=[])]
        detection = Detection(project_type="monorepo-root", confidence=0.9, is_monorepo=True,
                              apps=apps)
        config = {"detect": {"apps": {"apps/mystery": "service"}}}
        self.assertEqual(gaps_mod.derive_gaps(_rep(detection=detection), config), [])

    def test_no_detection_yields_no_gaps(self):
        self.assertEqual(gaps_mod.derive_gaps(_rep(), {}), [])
        self.assertEqual(gaps_mod.derive_gaps(None), [])


class TestConfigAndOptInGaps(unittest.TestCase):
    def test_missing_config_value_becomes_a_gap_naming_its_path(self):
        report = _rep(results=[_crit("build.ci_duration_budget", status=Status.UNKNOWN,
                                     gating=False, rationale="No ci_budget_minutes")])
        gap = {g.id: g for g in gaps_mod.derive_gaps(report, {})}["config.ci_budget_minutes"]
        self.assertEqual(gap.kind, "config")
        self.assertEqual(gap.answer["path"], "ci_budget_minutes")
        self.assertEqual(gap.blocks, ["build.ci_duration_budget"])
        self.assertEqual(gap.blocked_gating, 0)

    def test_a_configured_value_silences_its_gap(self):
        report = _rep(results=[_crit("build.ci_duration_budget", status=Status.UNKNOWN)])
        self.assertNotIn("config.ci_budget_minutes",
                         {g.id for g in gaps_mod.derive_gaps(report, {"ci_budget_minutes": 15})})

    def test_a_nested_configured_value_silences_its_gap(self):
        report = _rep(results=[_crit("build.check_command", status=Status.FAIL)])
        config = {"acdc": {"verify_command": "make check"}}
        self.assertNotIn("config.acdc.verify_command",
                         {g.id for g in gaps_mod.derive_gaps(report, config)})

    def test_a_passing_criterion_is_never_asked_about(self):
        report = _rep(results=[_crit("build.check_command", status=Status.PASS)])
        self.assertEqual(gaps_mod.derive_gaps(report, {}), [])

    def test_loop_opt_in_gap_counts_the_skipped_criteria(self):
        results = [_crit(f"loop.{i}", status=Status.SKIPPED, rationale=NOT_OPTED_IN_LOOP,
                         gating=False) for i in range(3)]
        found = {g.id: g for g in gaps_mod.derive_gaps(_rep(results=results), {})}
        gap = found["config.loop_ready"]
        self.assertEqual(len(gap.blocks), 3)
        self.assertEqual(gap.options, [True, False])
        self.assertEqual(gap.answer["path"], "loop_ready")

    def test_an_explicit_false_opt_in_is_an_answer_not_a_pending_question(self):
        """A developer who said "no loop" must not be asked again on every run."""
        results = [_crit("loop.x", status=Status.SKIPPED, rationale=NOT_OPTED_IN_LOOP,
                         gating=False)]
        report = _rep(results=results)
        self.assertIn("config.loop_ready", {g.id for g in gaps_mod.derive_gaps(report, {})})
        for answered in ({"loop_ready": False}, {"loop_ready": True}):
            self.assertNotIn("config.loop_ready",
                             {g.id for g in gaps_mod.derive_gaps(report, answered)},
                             f"still asking after {answered}")


class TestCapabilityGaps(unittest.TestCase):
    def test_unreachable_github_becomes_one_waivable_gap(self):
        results = [_crit("security.branch_protection", status=Status.SKIPPED, level=3,
                         rationale="No GitHub API; cannot read branch protection."),
                   _crit("taskdisc.backlog_health", status=Status.SKIPPED, level=4,
                         rationale="no GitHub API")]
        found = {g.id: g for g in gaps_mod.derive_gaps(_rep(results=results), {})}
        gap = found["capability.github"]
        self.assertEqual(gap.kind, "capability")
        self.assertTrue(gap.waivable)
        self.assertEqual(sorted(gap.blocks),
                         ["security.branch_protection", "taskdisc.backlog_health"])
        self.assertEqual(gap.levels, [3, 4])
        self.assertIn("gh auth login", gap.answer["action"])

    def test_available_github_asks_nothing(self):
        results = [_crit("x.y", status=Status.SKIPPED, rationale="No GitHub API")]
        self.assertEqual(gaps_mod.derive_gaps(_rep(results=results, github_available=True), {}), [])

    def test_a_plain_failure_is_never_waivable(self):
        """The scanner looked and found nothing: that is a finding, not a fact to declare."""
        results = [_crit("style.linter_config", status=Status.FAIL, rationale="No linter config.")]
        self.assertEqual(gaps_mod.derive_gaps(_rep(results=results), {}), [])


class TestOrdering(unittest.TestCase):
    def test_the_gate_being_cleared_comes_first(self):
        score = ScoreSummary(level=2, level_name="Documented", pass_rate=0.5, gating_passed=1,
                             gating_total=2,
                             levels=[LevelScore(level=2, name="Documented", passed=1, total=2,
                                                achieved=True)])
        results = [
            # Heavier by raw gating weight, but at a level the reader is not clearing yet.
            _crit("security.branch_protection", status=Status.SKIPPED, level=4,
                  rationale="No GitHub API"),
            _crit("taskdisc.backlog_health", status=Status.SKIPPED, level=4,
                  rationale="No GitHub API"),
            _crit("build.check_command", status=Status.FAIL, level=3, gating=True),
        ]
        report = _rep(results=results, score=score)
        order = [g.id for g in gaps_mod.derive_gaps(report, {})]
        self.assertEqual(order[0], "config.acdc.verify_command")

    def test_ordering_is_stable_across_runs(self):
        results = [_crit("build.check_command", status=Status.FAIL),
                   _crit("x.y", status=Status.SKIPPED, rationale="No GitHub API")]
        report = _rep(results=results)
        first = [g.id for g in gaps_mod.derive_gaps(report, {})]
        self.assertEqual(first, [g.id for g in gaps_mod.derive_gaps(report, {})])


class TestGapsAreAdvisory(unittest.TestCase):
    """A gap explains a result. It can never be an input to one."""

    def test_deriving_gaps_does_not_mutate_the_report_or_its_score(self):
        results = [_crit("build.check_command", status=Status.FAIL)]
        score = ScoreSummary(level=1, level_name="Functional", pass_rate=1.0, gating_passed=1,
                             gating_total=1)
        report = _rep(results=results, score=score)
        before = json.dumps(report.to_dict(), sort_keys=True)
        gaps_mod.derive_gaps(report, {})
        report.gaps = []
        self.assertEqual(json.dumps(report.to_dict(), sort_keys=True), before)

    def test_gap_questions_carry_no_repository_text(self):
        hostile = '<script>alert(1)</script>'
        detection = Detection(project_type="unknown", confidence=0.3, signals=[hostile],
                              apps=[App(path=".")])
        for gap in gaps_mod.derive_gaps(_rep(detection=detection), {}):
            self.assertNotIn(hostile, gap.question)
            self.assertNotIn(hostile, gap.why)


class TestClassificationCandidates(unittest.TestCase):
    """The ranked list is additive: its head must be the decision the scorer already used."""

    def test_head_of_the_ranked_list_is_the_classification(self):
        cases = [
            {"pyproject.toml": '[project]\nname="x"\nversion="1"\n'},
            {"package.json": '{"name":"x","bin":{"x":"c.js"}}'},
            {"package.json": '{"name":"x","dependencies":{"next":"14"}}'},
            {"requirements.txt": "django\n"},
            {"main.tf": 'resource "null_resource" "x" {}'},
            {"requirements.txt": "apache-airflow\n"},
            {"README.md": "# nothing"},
        ]
        for files in cases:
            root = make_repo(files)
            self.addCleanup(rmtree, root)
            static = StaticCollector(root)
            from readiness.detect import _classify
            surface, conf, signals = _classify(static)
            candidates = classify_candidates(static)
            self.assertEqual(surface, candidates[0]["type"], files)
            self.assertEqual(conf, candidates[0]["confidence"], files)
            self.assertEqual(signals, [candidates[0]["signal"]], files)

    def test_a_contested_directory_keeps_the_runner_up(self):
        root = make_repo({"package.json": '{"name":"x","main":"i.js","dependencies":'
                                         '{"next":"14"}}'})
        self.addCleanup(rmtree, root)
        candidates = classify_candidates(StaticCollector(root))
        self.assertEqual(candidates[0]["type"], "frontend")
        self.assertIn("library", [c["type"] for c in candidates])


class TestEndToEnd(unittest.TestCase):
    def test_a_real_scan_carries_serializable_gaps(self):
        root = make_repo({"README.md": "# x", "pyproject.toml": '[project]\nname="x"\n'})
        self.addCleanup(rmtree, root)
        report = analyze(root, {"no_github": True})
        payload = json.loads(json.dumps(report.to_dict()))
        self.assertIn("gaps", payload)
        for gap in payload["gaps"]:
            self.assertTrue(gap["question"])
            self.assertIn(gap["kind"], {"detection", "config", "capability"})

    def test_pinning_a_type_removes_the_gap_and_re_evaluates(self):
        files = {"README.md": "# x", "Makefile": "check:\n\techo ok\n"}
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        before = analyze(root, {"no_github": True})
        self.assertIn("detect.project_type", {g.id for g in before.gaps})

        cfg = Path(root) / ".agents" / "readiness"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(json.dumps({"detect": {"project_type": "service"}}),
                                         encoding="utf-8")
        after = analyze(root, {"no_github": True})
        self.assertNotIn("detect.project_type", {g.id for g in after.gaps})
        self.assertEqual(after.detection.project_type, "service")

    def test_the_documented_waiver_json_actually_waives(self):
        """The exact shape `reference/recording-answers.md` tells the skill to write.

        The array-of-objects form is not cosmetic: the engine iterates the file and reads
        `id` off each entry, so an object keyed by criterion id raises rather than waiving.
        """
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        target = "docs.readme"
        waiver = [{"id": target,
                   "reason": "Documented elsewhere. — asked by ra1-interview, "
                             "answered by @dev, 2026-08-06",
                   "expires": "2027-02-01"}]
        cfg = Path(root) / ".agents" / "readiness"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "waivers.json").write_text(json.dumps(waiver), encoding="utf-8")

        report = analyze(root, {"no_github": True})
        row = next(r for r in report.results if r.id == target)
        self.assertEqual(row.status, Status.WAIVED)
        self.assertIn("Documented elsewhere", row.rationale)
        # A waived criterion leaves the gate denominator; it is never counted as passing.
        self.assertEqual(row.evaluated_apps, 0)

    def test_config_written_at_the_documented_paths_is_read_back(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        cfg = Path(root) / ".agents" / "readiness"
        cfg.mkdir(parents=True, exist_ok=True)
        documented = {"detect": {"project_type": "cli", "apps": {"apps/web": "frontend"}},
                      "loop_ready": True, "ci_budget_minutes": 15,
                      "acdc": {"verify_command": "make check"}}
        (cfg / "config.json").write_text(json.dumps(documented), encoding="utf-8")
        loaded = load_readiness_config(root)
        self.assertEqual(loaded, documented)
        report = analyze(root, {"no_github": True})
        self.assertEqual(report.detection.project_type, "cli")
        self.assertTrue(report.detection.opt_in["loop_ready"])
        self.assertNotIn("config.loop_ready", {g.id for g in report.gaps})


class TestGapRendering(unittest.TestCase):
    """`_gap_lines` is shared by the markdown report and `ra1 gaps`, so it is tested once."""

    def _gap(self, **kw):
        base = dict(id="x.y", kind="config", question="Q?", why="because")
        base.update(kw)
        return Gap(**base)

    def test_a_config_gap_names_the_file_and_path(self):
        gap = self._gap(answer={"file": "cfg.json", "path": "a.b"}, options=["one", "two"],
                        evidence=["saw this"], blocks=["c.1"], blocked_gating=1, levels=[3])
        text = "\n".join(report_mod._gap_lines([gap]))
        self.assertIn("### Q?", text)
        self.assertIn("`cfg.json` → `a.b`", text)
        self.assertIn("`one`, `two`", text)
        self.assertIn("saw this", text)
        self.assertIn("1 gating at L3", text)

    def test_a_capability_gap_names_the_action_and_the_waiver_path(self):
        gap = self._gap(kind="capability", answer={"action": "authenticate gh"}, waivable=True,
                        blocks=["c.1", "c.2"])
        text = "\n".join(report_mod._gap_lines([gap]))
        self.assertIn("**Resolved by:** authenticate gh", text)
        self.assertIn("2 advisory", text)
        self.assertIn("waivers.json", text)
        self.assertIn("never counted as passing", text)

    def test_a_gap_with_no_answer_target_or_evidence_still_renders(self):
        text = "\n".join(report_mod._gap_lines([self._gap()]))
        self.assertIn("### Q?", text)
        self.assertNotIn("Recorded at", text)
        self.assertNotIn("Resolved by", text)
        self.assertNotIn("What the scan saw", text)


class TestGapsCli(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_markdown_is_the_default_and_lists_the_questions(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        code, out = self._run(["gaps", "--project", str(root), "--no-github"])
        self.assertEqual(code, 0)
        self.assertIn("## Unanswered Questions", out)
        self.assertIn("What kind of project is this repository", out)

    def test_json_format_emits_the_gap_records(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        code, out = self._run(["gaps", "--project", str(root), "--format", "json",
                               "--no-github"])
        self.assertEqual(code, 0)
        self.assertTrue(any(g["kind"] == "detection" for g in json.loads(out)))

    def test_a_gapless_scan_says_so_and_still_exits_zero(self):
        """An unanswered question is a worklist item, never a failing build.

        `--no-github` always produces the capability gap, so the gapless branch is exercised
        against a report that genuinely carries none rather than a repo that cannot exist
        offline.
        """
        gapless = _rep(github_available=True)
        with mock.patch.object(cli, "analyze", return_value=gapless):
            code, out = self._run(["gaps", "--project", ".", "--no-github"])
        self.assertEqual(code, 0)
        self.assertIn("No unanswered questions", out)

    def test_gaps_never_fail_the_command(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        code, out = self._run(["gaps", "--project", str(root), "--no-github"])
        self.assertEqual(code, 0)
        self.assertIn("Unanswered Questions", out)


class TestInstalledSkillCommands(unittest.TestCase):
    """The commands in SKILL.md must run from a vendored skill exactly as written.

    Nothing else covers the seam between the documented invocation and the shipped CLI: a
    renamed command or an unvendored module would ship a skill whose first step fails.
    """

    def _skill_cli(self, skill):
        return REPO / "skills" / skill / "scripts" / "readiness" / "cli.py"

    def _run(self, skill, args, cwd):
        return subprocess.run([sys.executable, str(self._skill_cli(skill)), *args],
                              cwd=cwd, capture_output=True, text=True, timeout=180)

    def test_interview_skill_documents_commands_that_exist(self):
        text = (REPO / "skills" / "ra1-interview" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("cli.py\" gaps", text)
        self.assertIn("cli.py\" report", text)

    def test_gaps_command_runs_from_the_vendored_skill(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        done = self._run("ra1-interview", ["gaps", "--project", str(root), "--format", "json",
                                           "--no-github"], cwd=root)
        self.assertEqual(done.returncode, 0, done.stderr)
        payload = json.loads(done.stdout)
        self.assertTrue(any(g["id"] == "detect.project_type" for g in payload), payload)

    def test_report_command_runs_from_the_vendored_skill(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        out = Path(root) / ".agents" / "readiness"
        done = self._run("ra1-interview",
                         ["report", "--project", str(root), "--format", "json",
                          "--out", str(out), "--no-github"], cwd=root)
        self.assertEqual(done.returncode in (0, 1), True, done.stderr)  # 1 = below min level
        payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertIn("gaps", payload)


if __name__ == "__main__":
    unittest.main()
