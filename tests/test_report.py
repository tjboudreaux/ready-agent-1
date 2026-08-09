import io
import json
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from html.parser import HTMLParser
from unittest import mock
from xml.etree import ElementTree as ET

from readiness import cli, theme
from readiness import report as report_mod
from readiness.model import (
    REQUIRED_REASON_CODES,
    App,
    CriterionResult,
    DecisionStep,
    DecisionTrace,
    Detection,
    Evidence,
    LevelScore,
    Report,
    ScoreSummary,
    Status,
    validate_decision_trace,
)
from readiness.run import analyze

from tests._util import make_repo, rmtree

BARE = {"README.md": "# x"}
GOODISH = {
    "README.md": "# Project\n\n## Setup\n\n```\nrun\n```\n" + ("text " * 80),
    ".gitignore": ".env\n__pycache__/\n",
    "pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n[tool.ruff]\nx=1\n',
    "tests/test_x.py": "def test_x():\n    assert True\n",
}


def _report(files):
    root = make_repo(files)
    return root, analyze(root)  # offline (T2 not requested) is the default


def _trace(result, reason_code=None):
    """Attach a schema-v3-valid DecisionTrace to a directly constructed CriterionResult.

    Mirrors the engine's four-stage chain (rule → observation → evaluation → conclusion);
    required-code criteria get an allowlisted typed suffix, everything else the generic
    ``check.<status>`` code untouched checks keep.
    """
    code = reason_code
    if code is None:
        if result.id in REQUIRED_REASON_CODES:
            suffixes = REQUIRED_REASON_CODES[result.id]
            code = f"{result.id}.{suffixes[0] if result.status is Status.PASS else suffixes[1]}"
        else:
            code = f"check.{result.status.value}"
    rule_ref = "checks._test.check"
    steps = [DecisionStep(kind="rule", code="rule.applied",
                          message=f"Criterion {result.id} ({result.title}) is evaluated by "
                                  f"{rule_ref}.")]
    if result.evidence:
        steps.append(DecisionStep(
            kind="observation", code="evidence.observed",
            message=f"Observed {len(result.evidence)} cited evidence item(s).",
            evidence_refs=list(range(len(result.evidence)))))
    steps.append(DecisionStep(kind="evaluation", code=code,
                              message=result.rationale or "evaluated"))
    steps.append(DecisionStep(kind="conclusion", code=f"conclusion.{result.status.value}",
                              message=f"Result: {result.status.value}."))
    result.decision_trace = DecisionTrace(reason_code=code, rule_ref=rule_ref, steps=steps)
    return result


def _traced(results):
    """Attach valid traces to hand-built results, self-checked against the model validator."""
    for result in results:
        _trace(result)
        assert validate_decision_trace(result.to_dict()) == [], result.id
    return results


class TestMarkdown(unittest.TestCase):
    def test_sections_present(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        md = report_mod.render_markdown(rep)
        self.assertIn("# Agent Readiness Report", md)
        self.assertIn("Level 0", md)
        self.assertIn("## Criteria Results", md)
        self.assertIn("## Clear the next gate", md)
        self.assertIn("Quick wins", md)  # gitignore scaffold

    def test_unknown_type_warning(self):
        root, rep = _report({"Makefile": "all:\n\techo hi\n"})
        self.addCleanup(rmtree, root)
        md = report_mod.render_markdown(rep)
        self.assertIn("unknown", md.lower())


    def test_non_gating_failures_render_as_advisory_improvements(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        blocking = CriterionResult(id="docs.readme", title="README", pillar="Documentation",
                                   level=1, scope="repository", gating=True,
                                   status=Status.FAIL, rationale="missing",
                                   passed_apps=0, evaluated_apps=1)
        rep.results = _traced([
            blocking,
            CriterionResult(id=
                "loop.loop_runs_dir", title="Loop Run Log README", pillar="Documentation", level=2,
                            scope=
                                "repository", gating=False, status=Status.FAIL, rationale="missing "
                                    "loop log",
                            fix_kind="scaffold", passed_apps=0, evaluated_apps=1),
        ])
        # The next-gate list is the complete set of blockers at the first unachieved
        # defined Level — an advisory failure never appears in it.
        rep.score = ScoreSummary(
            level=0, level_name="None", pass_rate=0.0, gating_passed=0, gating_total=1,
            levels=[LevelScore(level=1, name="Functional", passed=0, total=1,
                               achieved=False, defined=True, defined_total=1)],
            next_gate_actions=[{"id": blocking.id, "title": blocking.title,
                                "pillar": blocking.pillar, "level": blocking.level,
                                "status": "fail", "fix_kind": "",
                                "rationale": blocking.rationale}])
        md = report_mod.render_markdown(rep)
        self.assertIn("**Loop Run Log README** (**advisory**, L2, 0/1): missing loop log", md)
        self.assertIn("## Advisory Improvements", md)
        self.assertIn("- Loop Run Log README (L2, Documentation) — missing loop log", md)
        gate_section = md.split("## Advisory Improvements")[0].split(
            "## Clear the next gate", 1)[1]
        self.assertNotIn("Loop Run Log README", gate_section)
        self.assertIn("README", gate_section)

class TestAcdcLabels(unittest.TestCase):
    def _mapped(self, **kw):
        base = dict(id="build.check_command", title="Single Verify Command",
                    pillar="Build System", level=2, scope="repository", gating=False,
                    status=Status.FAIL, rationale="no single verify entrypoint",
                    acdc_stage="verify", acdc_loop="inner",
                    passed_apps=0, evaluated_apps=1)
        base.update(kw)
        return _trace(CriterionResult(**base))

    def test_to_dict_carries_acdc_fields(self):
        d = self._mapped().to_dict()
        self.assertEqual(d["acdc_stage"], "verify")
        self.assertEqual(d["acdc_loop"], "inner")

    def test_unmapped_result_serializes_empty_acdc(self):
        r = CriterionResult(id="docs.readme", title="README", pillar="Documentation", level=1,
                            scope="repository", gating=True, status=Status.PASS)
        self.assertEqual((r.to_dict()["acdc_stage"], r.to_dict()["acdc_loop"]), ("", ""))

    def test_markdown_labels_mapped_rows(self):
        rep = Report(project_path=".", schema_version="2", engine_version="0.7.0",
                     registry_version="0.7.0", detector_version="0.5.0")
        rep.results = [self._mapped()]
        md = report_mod.render_markdown(rep)
        self.assertIn("(**advisory**, L2, inner loop · verify, 0/1)", md)
        self.assertIn("- Single Verify Command (L2, Build System, inner loop · verify) — "
                      "no single verify entrypoint", md)

    def test_markdown_omits_label_for_unmapped_rows(self):
        rep = Report(project_path=".", schema_version="2", engine_version="0.7.0",
                     registry_version="0.7.0", detector_version="0.5.0")
        rep.results = [self._mapped(acdc_stage="", acdc_loop="")]
        md = report_mod.render_markdown(rep)
        self.assertIn("(**advisory**, L2, 0/1)", md)
        self.assertNotIn("loop · verify", md)

    def test_html_labels_mapped_rows(self):
        rep = Report(project_path=".", schema_version="2", engine_version="0.7.0",
                     registry_version="0.7.0", detector_version="0.5.0")
        rep.results = [self._mapped()]
        text = _parse(report_mod.render_html(rep)).body_text
        self.assertIn("inner loop · verify", text)

    def test_html_omits_label_for_unmapped_rows(self):
        rep = Report(project_path=".", schema_version="2", engine_version="0.7.0",
                     registry_version="0.7.0", detector_version="0.5.0")
        rep.results = [self._mapped(acdc_stage="", acdc_loop="")]
        text = _parse(report_mod.render_html(rep)).body_text
        self.assertNotIn("loop · verify", text)


class TestGithub(unittest.TestCase):
    def test_annotations(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        gh = report_mod.render_github(rep)
        self.assertIn("::warning", gh)
        self.assertIn("::notice::Agent Readiness Level", gh)

    def test_located_annotation_uses_comma_separated_escaped_properties(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = _traced([CriterionResult(id="style.large_file_guard", title="Large File",
                                       pillar="Style", level=1, scope="repository",
                                       gating=True, status=Status.FAIL,
                                       rationale="src/big,file.py is huge",
                                       evidence=[Evidence(summary="large",
                                                          source="src/big,file.py")])])
        gh = report_mod.render_github(rep)
        self.assertIn(
            "::warning title=Readiness%3A Large File,file=src/big%2Cfile.py::src/big,file.py is "
            "huge",
            gh,
        )



    def test_non_gating_failures_omit_annotations(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = _traced([CriterionResult(id="loop.loop_runs_dir", title="Loop Run Log README",
                                       pillar="Documentation", level=2, scope="repository",
                                       gating=False, status=Status.FAIL, rationale="missing")])
        gh = report_mod.render_github(rep)
        self.assertNotIn("::warning", gh)

class TestJunit(unittest.TestCase):
    def test_valid_xml(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        xml = report_mod.render_junit(rep)
        tree = ET.fromstring(xml)
        self.assertEqual(tree.tag, "testsuites")
        self.assertGreater(int(tree.get("tests")), 0)
        self.assertGreaterEqual(len(tree.findall(".//failure")), 1)


class TestSarif(unittest.TestCase):
    def test_valid_and_scoped(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        doc = json.loads(report_mod.render_sarif(rep))
        self.assertEqual(doc["version"], "2.1.0")
        self.assertEqual(doc["runs"][0]["tool"]["driver"]["name"], "agent-readiness")
        self.assertIsInstance(doc["runs"][0]["results"], list)  # repo-level fails excluded

    def test_located_criterion_emitted(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.1.0",
                     registry_version="0.1.0", detector_version="0.1.0")
        rep.results = _traced([CriterionResult(
            id="style.large_file", title="Large File", pillar="Style & Validation", level=1,
            scope="repository", gating=True, status=Status.FAIL, rationale="src/big.py is huge",
            evidence=[Evidence(summary="huge", source="src/big.py")],
        )])
        doc = json.loads(report_mod.render_sarif(rep))
        res = doc["runs"][0]["results"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["ruleId"], "style.large_file")
        self.assertEqual(
                         res[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
                         "src/big.py")

    def test_non_gating_failures_omitted_from_sarif(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = _traced([CriterionResult(
            id="loop.loop_runs_dir", title="Loop Run Log README", pillar="Documentation", level=2,
            scope="repository", gating=False, status=Status.FAIL, rationale="missing",
            evidence=[Evidence(summary="loop", source="loop-runs/README.md")],
        )])
        doc = json.loads(report_mod.render_sarif(rep))
        self.assertEqual(doc["runs"][0]["results"], [])


class TestRenderDispatch(unittest.TestCase):
    def test_json_is_canonical_default(self):
        root, rep = _report(GOODISH)
        self.addCleanup(rmtree, root)
        canonical = json.dumps(rep.to_dict(), indent=2)
        self.assertEqual(report_mod.render(rep, "json"), canonical)
        self.assertEqual(report_mod.render(rep, ""), canonical)
        self.assertEqual(report_mod.render(rep, None), canonical)

    def test_whitespace_and_case_dispatch(self):
        root, rep = _report(GOODISH)
        self.addCleanup(rmtree, root)
        self.assertEqual(report_mod.render(rep, "  MarkDown  "), report_mod.render_markdown(rep))
        self.assertEqual(report_mod.render(rep, "HTML"), report_mod.render_html(rep))

    def test_alias_dispatch(self):
        root, rep = _report(GOODISH)
        self.addCleanup(rmtree, root)
        self.assertEqual(report_mod.render(rep, "md"), report_mod.render_markdown(rep))
        self.assertEqual(report_mod.render(rep, "checks"), report_mod.render_github(rep))
        self.assertEqual(report_mod.render(rep, "annotations"), report_mod.render_github(rep))

    def test_every_canonical_format_has_a_distinct_renderer(self):
        root, rep = _report(GOODISH)
        self.addCleanup(rmtree, root)
        expected = {
            "json": json.dumps(rep.to_dict(), indent=2),
            "markdown": report_mod.render_markdown(rep),
            "html": report_mod.render_html(rep),
            "github": report_mod.render_github(rep),
            "junit": report_mod.render_junit(rep),
            "sarif": report_mod.render_sarif(rep),
        }
        self.assertEqual(set(expected), set(report_mod.REPORT_FORMATS))
        for fmt, text in expected.items():
            self.assertEqual(report_mod.render(rep, fmt), text, fmt)

    def test_render_rejects_unknown_format(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        with self.assertRaisesRegex(ValueError, "unsupported report format 'htlm'"):
            report_mod.render(rep, "htlm")

    def test_render_rejects_unknown_detail_mode(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        with self.assertRaisesRegex(ValueError, "unsupported detail mode"):
            report_mod.render(rep, "markdown", detail="everything")


class TestFormatNormalization(unittest.TestCase):
    def test_empty_and_none_default_to_json(self):
        self.assertEqual(report_mod.normalize_format(None), "json")
        self.assertEqual(report_mod.normalize_format(""), "json")
        self.assertEqual(report_mod.normalize_format("   "), "json")
        self.assertEqual(report_mod.format_extension(None), "json")
        self.assertEqual(report_mod.format_extension(""), "json")

    def test_canonical_and_alias_normalization(self):
        cases = {
            "json": "json", "markdown": "markdown", "html": "html", "github": "github",
            "junit": "junit", "sarif": "sarif",
            "md": "markdown", "checks": "github", "annotations": "github",
            " MarkDown ": "markdown", "SARIF": "sarif", "MD": "markdown",
        }
        for token, canonical in cases.items():
            self.assertEqual(report_mod.normalize_format(token), canonical, token)

    def test_extensions_keep_alias_filenames_stable(self):
        cases = {
            "json": "json", "markdown": "md", "html": "html", "github": "txt",
            "junit": "xml", "sarif": "sarif",
            "md": "md", "checks": "txt", "annotations": "annotations",
            " HTML ": "html",
        }
        for token, ext in cases.items():
            self.assertEqual(report_mod.format_extension(token), ext, token)

    def test_unknown_format_message_lists_canonical_formats(self):
        with self.assertRaisesRegex(
            ValueError,
            r"^unsupported report format 'htlm'; supported formats: "
            r"json, markdown, html, github, junit, sarif$",
        ):
            report_mod.normalize_format("htlm")
        with self.assertRaisesRegex(ValueError, "unsupported report format 'nope'"):
            report_mod.format_extension("nope")

    def test_control_characters_cannot_forge_output_lines(self):
        with self.assertRaises(ValueError) as ctx:
            report_mod.normalize_format("bad\n::warning")
        message = str(ctx.exception)
        self.assertIn("\\n", message)          # the two printable characters, not a newline
        self.assertNotIn("\n", message)


class TestRecommendationsAndDisplay(unittest.TestCase):
    def test_json_has_recommendations_and_counts(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        d = rep.to_dict()
        recs = d["score"]["recommendations"]
        self.assertGreater(len(recs), 0)
        self.assertLessEqual(len(recs), 3)
        self.assertIn("id", recs[0])
        self.assertIn("passed_apps", d["results"][0])
        self.assertIn("evaluated_apps", d["results"][0])

    def test_markdown_renders_nm_and_action_items(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        md = report_mod.render_markdown(rep)
        self.assertIn("## Criteria Results", md)
        self.assertIn("/1):", md)  # repository-scope criteria render N/1
        self.assertIn("## Clear the next gate", md)
        self.assertIn("first unachieved defined Level", md)


class TestRenderCoverage(unittest.TestCase):
    def _rep(self, results, advisory=None, score=None):
        rep = Report(project_path=".", schema_version="2", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = _traced(results)
        if advisory:
            rep.advisory = advisory
        rep.score = score
        return rep

    def test_no_action_items_when_all_pass(self):
        rep = self._rep([CriterionResult(id="docs.readme", title="README", pillar="Docs", level=1,
                         scope="repository", gating=True, status=Status.PASS,
                         passed_apps=1, evaluated_apps=1)])
        self.assertNotIn("## Clear the next gate", report_mod.render_markdown(rep))

    def test_agent_advisory_rendered(self):
        md = report_mod.render_markdown(self._rep([], advisory=["Consider tightening X."]))
        self.assertIn("## Advisory (non-gating, agent-authored)", md)
        self.assertIn("Consider tightening X.", md)

    def test_github_annotation_with_source_skips_non_file_evidence(self):
        r = CriterionResult(id="docs.api_schema_docs", title="API Schema", pillar="Docs", level=3,
                            scope="repository"
                                  , gating=True, status=Status.FAIL, rationale="missing",
                            evidence=[Evidence(summary="api", source="repos/o/r"),
                                      Evidence(summary="schema", source="src/openapi.yaml")])
        gh = report_mod.render_github(self._rep([r]))
        self.assertIn("file=src/openapi.yaml", gh)

    def test_sarif_dedups_rule_for_repeated_id(self):
        ev = [Evidence(summary="x", source="src/a.py")]
        results = [
            CriterionResult(id="x.y", title="X", pillar="P", level=2, scope="application",
                            gating=
                                True, status=Status.FAIL, rationale="r", evidence=ev, app_path="a"),
            CriterionResult(id="x.y", title="X", pillar="P", level=2, scope="application",
                            gating=
                                True, status=Status.FAIL, rationale="r", evidence=ev, app_path="b"),
        ]
        doc = json.loads(report_mod.render_sarif(self._rep(results)))
        rule_ids = [ru["id"] for ru in doc["runs"][0]["tool"]["driver"]["rules"]]
        self.assertEqual(rule_ids.count("x.y"), 1)
        self.assertEqual(len(doc["runs"][0]["results"]), 2)

    def test_judgment_disclosure_both(self):
        results = [
            CriterionResult(id=
                "judgment.naming_consistency", title="Naming Consistency", pillar="Style",
                            level=2, scope="repository", gating=False, status=Status.UNKNOWN),
            CriterionResult(id="judgment.pii_handling", title="PII Handling", pillar="Security",
                            level=3, scope="repository", gating=False, status=Status.WAIVED,
                            rationale="ignored by judgments config"),
        ]
        md = report_mod.render_markdown(self._rep(results))
        self.assertIn("## Agent Judgments", md)
        self.assertIn("To assess: Naming Consistency", md)
        self.assertIn("Ignored judgments (1): PII Handling", md)

    def test_judgment_disclosure_assess_only(self):
        results = [CriterionResult(id="judgment.x", title="X", pillar="P", level=2,
                                   scope="repository", gating=False, status=Status.UNKNOWN)]
        md = report_mod.render_markdown(self._rep(results))
        self.assertIn("To assess: X", md)
        self.assertNotIn("Ignored judgments", md)

    def test_judgment_disclosure_ignored_only(self):
        results = [CriterionResult(id="judgment.x", title="X", pillar="P", level=2,
                                   scope="repository", gating=False, status=Status.WAIVED,
                                   rationale="ignored by judgments config")]
        md = report_mod.render_markdown(self._rep(results))
        self.assertIn("Ignored judgments (1): X", md)
        self.assertNotIn("To assess", md)


class _Doc(HTMLParser):
    """Parses the artifact so assertions inspect the DOM, not a raw string search."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []      # [(tag, {attr: value})] in document order
        self.decl = ""
        self._text = []
        self._in_style = False

    def handle_decl(self, decl):
        self.decl = decl

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, {name: (value or "") for name, value in attrs}))
        if tag in ("style", "script"):
            self._in_style = True

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._in_style = False

    def handle_data(self, data):
        if not self._in_style:
            self._text.append(data)

    @property
    def tags(self):
        return [tag for tag, _ in self.elements]

    @property
    def attrs(self):
        return [(tag, name, value) for tag, a in self.elements for name, value in a.items()]

    @property
    def ids(self):
        return {a["id"] for _, a in self.elements if "id" in a}

    @property
    def body_text(self):
        """All rendered text with whitespace runs collapsed; stylesheet content excluded."""
        return " ".join(" ".join(self._text).split())

    def find(self, tag):
        return [a for t, a in self.elements if t == tag]


def _parse(markup: str) -> _Doc:
    doc = _Doc()
    doc.feed(markup)
    doc.close()
    return doc


class TestHtmlReport(unittest.TestCase):
    def _rep(self, **kw):
        kw.setdefault("project_path", "/abs/secret/proj")
        kw.setdefault("schema_version", "2")
        kw.setdefault("engine_version", "0.6.0")
        kw.setdefault("registry_version", "0.6.0")
        kw.setdefault("detector_version", "0.5.0")
        if "results" in kw:
            kw["results"] = _traced(kw["results"])
        return Report(**kw)

    def test_document_shell(self):
        out = report_mod.render_html(self._rep())
        self.assertTrue(out.startswith("<!doctype html>\n"))
        self.assertTrue(out.endswith("</html>\n"))
        doc = _parse(out)
        self.assertEqual(doc.decl.lower(), "doctype html")
        self.assertEqual(doc.tags[:2], ["html", "head"])
        self.assertEqual(doc.find("html"), [{"lang": "en"}])
        self.assertEqual(doc.tags.count("main"), 1)
        self.assertIn("Agent Readiness Report", doc.body_text)

    def test_header_and_footer_metadata(self):
        rep = self._rep(branch="main", commit="0123456789abcdef",
                        generated_at="2026-08-04T00:00:00+00:00",
                        repository={"identity_kind": "origin", "host": "github.com",
                                    "owner": "acme", "name": "widget",
                                    "identity_hash": "aaaabbbbccccdddd"})
        text = _parse(report_mod.render_html(rep)).body_text
        self.assertIn("0.6.0 · acme/widget · branch main · commit 01234567", text)
        self.assertIn("generated 2026-08-04T00:00:00+00:00 · registry 0.6.0 · detector 0.5.0", text)
        self.assertNotIn("0123456789abcdef", text)

    def test_footer_omits_empty_timestamp_and_optional_metadata(self):
        out = report_mod.render_html(self._rep())
        text = _parse(out).body_text
        self.assertIn("registry 0.6.0 · detector 0.5.0", text)
        # No repository identity: a fixed label, never the process-local absolute path.
        self.assertIn("0.6.0 · local repository", text)
        # The header/footer chrome omits absent metadata (the boundary/limitations prose
        # elsewhere in the document legitimately uses words like "commit").
        chrome = (out.split("<header", 1)[1].split("</header>", 1)[0]
                  + out.split("<footer", 1)[1].split("</footer>", 1)[0])
        self.assertNotIn("generated", chrome)
        self.assertNotIn("branch", chrome)
        self.assertNotIn("commit", chrome)

    def test_score_present_renders_status_gate_track_and_actions(self):
        score = ScoreSummary(
            level=1, level_name="Functional", pass_rate=0.5, gating_passed=4, gating_total=8,
            levels=[LevelScore(level=1, name="Functional", passed=3, total=4, achieved=True,
                               defined=True, defined_total=4),
                    LevelScore(level=2, name="Structured", passed=1, total=4, achieved=False,
                               defined=True, defined_total=4)],
            next_gate_actions=[{"id": "docs.readme", "title": "README", "pillar": "Docs",
                                "level": 2, "status": "fail", "fix_kind": "scaffold",
                                "rationale": "No README found"}])
        results = [CriterionResult(id="docs.readme", title="README", pillar="Docs", level=2,
                                   scope="repository", gating=True, status=Status.FAIL,
                                   rationale="No README found", fix_kind="scaffold",
                                   passed_apps=0, evaluated_apps=1)]
        doc = _parse(report_mod.render_html(self._rep(score=score, results=results)))
        text = doc.body_text
        self.assertIn("Readiness Status", text)
        self.assertIn("Level 1: Functional", text)
        self.assertIn("50% pass rate · 4/8 gating criteria", text)
        self.assertIn("Functional 3/4 cleared", text)
        self.assertIn("Structured 1/4 blocked", text)
        self.assertIn("Clear the Next Gate", text)
        self.assertIn("Every gating blocker at the first unachieved defined Level", text)
        self.assertIn("docs.readme", text)
        self.assertIn("L2 · Docs · Quick wins (auto-scaffold via ra1-fix)", text)
        self.assertIn("No README found", text)

    def test_gate_track_marks_only_the_first_remaining_gate_as_blocked(self):
        score = ScoreSummary(
            level=1, level_name="Functional", pass_rate=0.5, gating_passed=2, gating_total=4,
            levels=[LevelScore(level=1, name="Functional", passed=2, total=2, achieved=True,
                               defined=True, defined_total=2),
                    LevelScore(level=2, name="Structured", passed=0, total=1, achieved=False,
                               defined=True, defined_total=1),
                    LevelScore(level=3, name="Governed", passed=0, total=1, achieved=False,
                               defined=True, defined_total=1),
                    LevelScore(level=4, name="Optimized", passed=0, total=0, achieved=False,
                               defined=True, defined_total=2),
                    LevelScore(level=5, name="Autonomous", passed=0, total=0, achieved=False,
                               defined=False, defined_total=0)])
        doc = _parse(report_mod.render_html(self._rep(score=score)))
        gates = [a["class"] for _, a in doc.elements
                 if a.get("class", "").startswith("gate gate-")]
        self.assertEqual(gates, ["gate gate-cleared", "gate gate-blocked", "gate gate-locked",
                                 "gate gate-empty", "gate gate-empty"])
        text = doc.body_text
        for word in ("cleared", "blocked", "locked", "not scored"):
            self.assertIn(word, text)
        # The track counts, it never rates: a defined-but-all-excluded level says how many
        # were excluded, and an undefined level says it is not scored — never "0/0 (100%)".
        self.assertIn("Optimized — all 2 excluded", text)
        self.assertIn("Autonomous — not scored", text)
        self.assertNotIn("100%", text)

    def test_score_absent_omits_gate_track_and_action_items(self):
        results = [CriterionResult(id="docs.readme", title="README", pillar="Docs", level=1,
                                   scope="repository", gating=True, status=Status.FAIL,
                                   rationale="missing")]
        doc = _parse(report_mod.render_html(self._rep(results=results)))
        self.assertIn("Score unavailable", doc.body_text)
        self.assertIn("status-heading", doc.ids)
        self.assertNotIn("gates", {a.get("class") for _, a in doc.elements})
        self.assertNotIn("actions-heading", doc.ids)

    def test_score_without_levels_or_recommendations(self):
        score = ScoreSummary(level=0, level_name="Unscored", pass_rate=0.0,
                             gating_passed=0, gating_total=0, levels=[])
        doc = _parse(report_mod.render_html(self._rep(score=score)))
        text = doc.body_text
        self.assertIn("Level 0: Unscored", text)
        self.assertIn("No level data available", text)
        self.assertIn("status-heading", doc.ids)
        self.assertNotIn("actions-heading", doc.ids)

    def test_applications_table(self):
        detection = Detection(project_type="service", apps=[
            App(path=".", languages=["python", "go"], runtime="cli",
                deploy_surface="service"),
            App(path="web", languages=[], runtime="unknown", deploy_surface="frontend")])
        out = report_mod.render_html(self._rep(detection=detection))
        doc = _parse(out)
        self.assertEqual(doc.tags.count("table"), 1)
        self.assertEqual(doc.find("caption"), [{"class": "visually-hidden"}])
        text = doc.body_text
        # The canonical projection sorts languages and confines runtime to known enums.
        self.assertIn("go, python", text)
        self.assertIn("n/a", text)  # app with no languages
        self.assertIn("cli", text)
        # The root app keeps its canonical "." marker — it is never redacted.
        self.assertIn("<code>.</code>", out)
        self.assertIn("<code>web</code>", out)
        self.assertNotIn("[redacted repository source]", out)
        self.assertNotIn("Project type is", text)

    def test_no_applications_and_unknown_project_type_warning(self):
        doc = _parse(report_mod.render_html(self._rep(detection=Detection())))
        self.assertIn("No applications discovered", doc.body_text)
        self.assertIn("Project type is unknown (low detection confidence)", doc.body_text)
        self.assertNotIn("table", doc.tags)

    def test_detection_absent_omits_applications_section(self):
        doc = _parse(report_mod.render_html(self._rep()))
        self.assertNotIn("applications-heading", doc.ids)

    def test_empty_criteria(self):
        doc = _parse(report_mod.render_html(self._rep()))
        self.assertIn("criteria-heading", doc.ids)
        self.assertIn("No criteria results available", doc.body_text)
        self.assertNotIn("pillar-1-heading", doc.ids)

    def test_every_status_renders_symbol_label_and_class(self):
        results = [CriterionResult(id=f"p.{s.value}", title=f"Criterion {s.value}", pillar="P",
                                   level=1, scope="repository", gating=(s is Status.PASS),
                                   status=s, rationale=f"why {s.value}",
                                   passed_apps=1, evaluated_apps=1)
                   for s in Status]
        doc = _parse(report_mod.render_html(self._rep(results=results)))
        classes = {a["class"] for _, a in doc.elements
                   if a.get("class", "").startswith("row criterion status-")}
        text = doc.body_text
        icons = [a.get("class") for t, a in doc.elements if t == "svg"]
        joined = " | ".join(sorted(classes))
        for s in Status:
            self.assertIn(f"row criterion status-{s.value}", joined)
        # The status word is its own slot beside the badge: two signals before color.
        statuses = [a for t, a in doc.elements if a.get("class") == "row-status"]
        self.assertEqual(len(statuses), len(list(Status)))
        self.assertLessEqual(set(icons), {"icon", "radar", "dist"})
        self.assertGreaterEqual(icons.count("icon"), len(list(Status)))  # one badge per row
        # Every fail/unknown here is advisory, so none blocks: the advisory failure is
        # `suggested` and the advisory unknown is a judgment-free flag with no tier at all.
        self.assertEqual({c for c in classes if "needs-action" in c}, set())
        self.assertEqual({c.split(" status-")[1].split()[0]
                          for c in classes if "suggested" in c}, {"fail"})
        self.assertIn("Pass Level 1 gate", text)
        # An unregistered fix kind emits no action line at all.
        self.assertIn("Fail advisory why fail", text)
        self.assertNotIn("Manual work", text)

    def test_criteria_grouped_by_pillar_in_first_seen_order(self):
        results = [
            CriterionResult(id="d.1", title="Alfa", pillar="Docs", level=1, scope="repository",
                            gating=True, status=Status.PASS),
            CriterionResult(id="s.1", title="Bravo", pillar="Security", level=1,
                            scope="repository", gating=True, status=Status.PASS),
            CriterionResult(id="d.2", title="Charlie", pillar="Docs", level=2,
                            scope="repository", gating=True, status=Status.PASS),
        ]
        doc = _parse(report_mod.render_html(self._rep(results=results)))
        self.assertEqual(len([a for _, a in doc.elements
                              if a.get("class", "").startswith("pillar p")]), 2)
        self.assertIn("pillar-1-heading", doc.ids)
        self.assertIn("pillar-2-heading", doc.ids)
        text = doc.body_text
        # Docs first (first seen), and its level-2 criterion groups back under it, not Security.
        self.assertLess(text.index("Alfa"), text.index("Charlie"))
        self.assertLess(text.index("Charlie"), text.index("Bravo"))

    def test_evidence_details_shows_optional_source_and_detail(self):
        evidence = [Evidence(summary="bare fact", tier="T0"),
                    Evidence(summary="cited file", tier="T1", source="src/a.py"),
                    Evidence(summary="api call", tier="T2", source="repos/o/r", detail="404")]
        r = CriterionResult(id="x.y", title="X", pillar="P", level=1, scope="repository",
                            gating=True, status=Status.FAIL, rationale="r", evidence=evidence)
        doc = _parse(report_mod.render_html(self._rep(results=[r])))
        # The evidence disclosure is the only class-less <details>: facet menus and
        # education disclosures carry their own classes.
        disclosures = [a for t, a in doc.elements if t == "details" and "class" not in a]
        self.assertEqual(len(disclosures), 1)
        text = doc.body_text
        self.assertIn("Evidence (3)", text)
        for token in ("T0", "bare fact", "T1", "cited file", "src/a.py", "T2", "api call",
                      "repos/o/r", "404"):
            self.assertIn(token, text)

    def test_no_evidence_and_no_rationale_omit_their_markup(self):
        r = CriterionResult(id="x.y", title="X", pillar="P", level=1, scope="repository",
                            gating=True, status=Status.PASS)
        doc = _parse(report_mod.render_html(self._rep(results=[r])))
        # No class-less <details>: every rendered disclosure is a facet menu or an
        # education block, and the evidence disclosure never appears without evidence.
        self.assertFalse([a for t, a in doc.elements if t == "details" and "class" not in a])
        self.assertNotIn("rationale", {a.get("class") for _, a in doc.elements})

    def test_advisory_improvements_keep_effort_group_order(self):
        results = [
            CriterionResult(id="a.2", title="Protect branch", pillar="Security", level=3,
                            scope="repository", gating=False, status=Status.FAIL,
                            rationale="unprotected", fix_kind="github_setting"),
            CriterionResult(id="a.1", title="Adopt formatter", pillar="Style", level=2,
                            scope="repository", gating=False, status=Status.FAIL,
                            rationale="no formatter", fix_kind="scaffold"),
        ]
        doc = _parse(report_mod.render_html(self._rep(results=results)))
        text = doc.body_text
        self.assertIn("Advisory Improvements", text)
        self.assertLess(text.index("Quick wins"), text.index("GitHub settings"))
        self.assertIn("Adopt formatter", text)
        self.assertIn("L3 · Security", text)
        self.assertIn("unprotected", text)

    def test_no_advisory_improvements_omits_section(self):
        r = CriterionResult(id="a.1", title="A", pillar="P", level=1, scope="repository",
                            gating=False, status=Status.PASS)
        doc = _parse(report_mod.render_html(self._rep(results=[r])))
        self.assertNotIn("advisory-improvements-heading", doc.ids)

    def test_judgment_disclosure_both_states(self):
        results = [
            CriterionResult(id="judgment.naming", title="Naming Consistency", pillar="Style",
                            level=2, scope="repository", gating=False, status=Status.UNKNOWN),
            CriterionResult(id="judgment.pii", title="PII Handling", pillar="Security", level=3,
                            scope="repository", gating=False, status=Status.WAIVED),
        ]
        text = _parse(report_mod.render_html(self._rep(results=results))).body_text
        self.assertIn("Agent Judgments (advisory, never scored)", text)
        self.assertIn("To assess: Naming Consistency", text)
        self.assertIn("Ignored judgments (1): PII Handling", text)
        self.assertIn(".ra1/config.json", text)

    def test_judgment_disclosure_assess_only(self):
        r = CriterionResult(id="judgment.x", title="X", pillar="P", level=2, scope="repository",
                            gating=False, status=Status.UNKNOWN)
        text = _parse(report_mod.render_html(self._rep(results=[r]))).body_text
        self.assertIn("To assess: X", text)
        self.assertNotIn("Ignored judgments", text)

    def test_judgment_disclosure_ignored_only(self):
        r = CriterionResult(id="judgment.x", title="X", pillar="P", level=2, scope="repository",
                            gating=False, status=Status.WAIVED)
        text = _parse(report_mod.render_html(self._rep(results=[r]))).body_text
        self.assertIn("Ignored judgments (1): X", text)
        self.assertNotIn("To assess", text)

    def test_no_judgments_omits_section(self):
        doc = _parse(report_mod.render_html(self._rep()))
        self.assertNotIn("judgments-heading", doc.ids)

    def test_agent_advisory_replaces_the_deterministic_disclosure(self):
        doc = _parse(report_mod.render_html(self._rep(advisory=["Consider tightening X."])))
        text = doc.body_text
        self.assertIn("Advisory (non-gating, agent-authored)", text)
        self.assertIn("Consider tightening X.", text)
        self.assertNotIn("the score above is deterministic", text)

    def test_disclosure_when_no_agent_advisory(self):
        doc = _parse(report_mod.render_html(self._rep()))
        self.assertIn("the score above is deterministic", doc.body_text)
        self.assertNotIn("advisory-heading", doc.ids)

    def test_labelled_sections_reference_real_headings(self):
        root, rep = _report(GOODISH)
        self.addCleanup(rmtree, root)
        doc = _parse(report_mod.render_html(rep))
        labelled = [v for _, n, v in doc.attrs if n == "aria-labelledby"]
        self.assertTrue(labelled)
        for value in labelled:
            for target in value.split():  # aria-labelledby takes an ID list
                self.assertIn(target, doc.ids)
        for required in ("status-heading", "pillars-heading", "applications-heading",
                         "actions-heading", "criteria-heading", "pillar-1-heading"):
            self.assertIn(required, doc.ids)
        self.assertEqual(doc.tags[-2:], ["footer", "p"])  # footer is main's last child


_HOSTILE = '<script>alert("xss")</script> & <img src=x onerror=alert(1)> \'quoted\''
_SENTINEL_PATH = "/abs/secret/leaked-path"


class TestHtmlSafety(unittest.TestCase):
    """Repository content is data. It must never become markup, an attribute, or a request."""

    def _hostile(self):
        evidence = [Evidence(summary=_HOSTILE, tier="T0", source=_HOSTILE, detail=_HOSTILE),
                    Evidence(summary="upstream advisory", tier="T2",
                             source="https://example.com/advisories/1")]
        results = _traced([
            # AC/DC-mapped on purpose: the disclosure (and its one authored anchor) must
            # render even under maximal repository hostility.
            CriterionResult(id="x.y", title=_HOSTILE, pillar=_HOSTILE, level=1,
                            scope="repository", gating=True, status=Status.FAIL,
                            rationale=_HOSTILE, evidence=evidence, fix_kind="scaffold",
                            acdc_stage="verify", acdc_loop="inner",
                            passed_apps=0, evaluated_apps=1),
            CriterionResult(id="judgment.z", title=_HOSTILE, pillar="P", level=2,
                            scope="repository", gating=False, status=Status.WAIVED),
            CriterionResult(id="adv.a", title=_HOSTILE, pillar="P", level=2, scope="repository",
                            gating=False, status=Status.FAIL, rationale=_HOSTILE),
        ])
        return Report(
            project_path=_SENTINEL_PATH, schema_version="2", engine_version=_HOSTILE,
            registry_version=_HOSTILE, detector_version=_HOSTILE, commit=_HOSTILE,
            branch=_HOSTILE, generated_at=_HOSTILE,
            repository={"identity_kind": "local_path", "name": _HOSTILE},
            detection=Detection(project_type="unknown",
                                apps=[App(path=_HOSTILE, languages=[_HOSTILE], runtime=_HOSTILE,
                                          deploy_surface=_HOSTILE)]),
            results=results, advisory=[_HOSTILE],
            score=ScoreSummary(level=0, level_name=_HOSTILE, pass_rate=0.0, gating_passed=0,
                               gating_total=1,
                               levels=[LevelScore(level=1, name=_HOSTILE, passed=0, total=1,
                                                  achieved=False)]))

    def test_document_stays_well_formed_and_complete(self):
        out = report_mod.render_html(self._hostile())
        self.assertTrue(out.startswith("<!doctype html>\n"))
        self.assertTrue(out.endswith("</html>\n"))
        text = _parse(out).body_text
        for heading in ("Readiness Status", "Applications Discovered",
                        "Criteria Results", "Advisory Improvements",
                        "Agent Judgments (advisory, never scored)",
                        "Advisory (non-gating, agent-authored)",
                        "Assessment Boundary"):
            self.assertIn(heading, text)

    def test_hostile_strings_survive_as_text(self):
        text = _parse(report_mod.render_html(self._hostile())).body_text
        self.assertIn(_HOSTILE, text)
        self.assertIn('<script>alert("xss")</script>', text)
        self.assertIn("<img src=x onerror=alert(1)>", text)
        self.assertIn("'quoted'", text)

    def test_no_attacker_supplied_element_or_attribute(self):
        doc = _parse(report_mod.render_html(self._hostile()))
        for tag in ("script", "img", "link", "iframe", "object", "embed", "form"):
            self.assertNotIn(tag, doc.tags, f"<{tag}> reached the document")
        # Exactly one anchor may exist: the authored Sonar citation, attributes verbatim.
        self.assertEqual(doc.find("a"),
                         [{"href": report_mod._SONAR_ACDC_URL, "target": "_blank",
                           "rel": "noopener noreferrer"}])
        forbidden = {"src", "srcset", "action", "formaction", "style"}
        for tag, name, value in doc.attrs:
            self.assertNotIn(name, forbidden, f"<{tag} {name}> reached the document")
            self.assertFalse(name.startswith("on"), f"<{tag} {name}> reached the document")
            if name == "href":
                self.assertEqual(value, report_mod._SONAR_ACDC_URL,
                                 "a data-derived href reached the document")

    def test_url_shaped_evidence_stays_plain_text(self):
        doc = _parse(report_mod.render_html(self._hostile()))
        self.assertIn("https://example.com/advisories/1", doc.body_text)
        # The URL survives as text only — it must never become an anchor.
        self.assertEqual([a["href"] for a in doc.find("a")], [report_mod._SONAR_ACDC_URL])

    def test_absolute_project_path_is_never_emitted(self):
        out = report_mod.render_html(self._hostile())
        self.assertNotIn(_SENTINEL_PATH, out)
        self.assertNotIn("leaked-path", out)

    def test_head_declares_a_locked_down_offline_policy(self):
        doc = _parse(report_mod.render_html(self._hostile()))
        metas = doc.find("meta")
        self.assertEqual([m["charset"] for m in metas if "charset" in m], ["utf-8"])
        self.assertEqual(
            [m["content"] for m in metas if m.get("http-equiv") == "Content-Security-Policy"],
            ["default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
             "form-action 'none'"])
        self.assertEqual([m["content"] for m in metas if m.get("name") == "color-scheme"],
                         ["light dark"])
        self.assertEqual(doc.tags.count("style"), 1)

    def test_artifact_fetches_nothing_at_render_time(self):
        """The Single-File Rule: rendering makes no network request, ever — not even a font.

        Deliberate navigation is not a fetch: the one authored Sonar anchor loads only if
        a reader clicks it, and the report renders identically if they never do.
        """
        doc = _parse(report_mod.render_html(self._hostile()))
        fetching = {"script", "link", "img", "iframe", "object", "embed", "source", "track",
                    "audio", "video", "form", "base"}
        for tag in fetching:
            self.assertNotIn(tag, doc.tags, f"<{tag}> reached the document")
        forbidden = {"src", "srcset", "action", "formaction", "poster", "data", "ping",
                     "integrity", "crossorigin", "background"}
        for tag, name, value in doc.attrs:
            self.assertNotIn(name, forbidden, f"<{tag} {name}> reached the document")
            self.assertFalse(name.startswith("on"), f"<{tag} {name}> reached the document")
            if name == "href":
                self.assertEqual(value, report_mod._SONAR_ACDC_URL,
                                 "a data-derived href reached the document")

    def test_stylesheet_is_self_contained(self):
        for banned in ("url(", "@import", "@font-face", "://"):
            self.assertNotIn(banned, report_mod._HTML_STYLE, f"{banned} reached the stylesheet")

    def test_no_attribute_value_carries_a_scheme(self):
        # Body text may hold a URL — evidence sources are legitimate. Attributes may not,
        # with one authored exception: the Sonar citation href, an exact constant.
        doc = _parse(report_mod.render_html(self._hostile()))
        for tag, name, value in doc.attrs:
            if name == "href" and value == report_mod._SONAR_ACDC_URL:
                continue
            self.assertNotIn("://", value, f"<{tag} {name}> carries a scheme")

    def test_no_repository_text_reaches_an_attribute(self):
        """The narrowed Don't: repository TEXT never reaches an attribute value.

        The chart made the original blanket rule ("no report data in an attribute") false:
        SVG geometry is necessarily report-derived. So the rule is narrowed to what it
        actually protects, and the narrowing is enforced here rather than just reworded in
        DESIGN.md. Repository strings stay out; finite server-computed numbers may pass.
        """
        doc = _parse(report_mod.render_html(self._hostile()))
        for fragment in ("script", "alert", "xss", "onerror", "quoted"):
            for tag, name, value in doc.attrs:
                self.assertNotIn(fragment, value,
                                 f"repository text reached <{tag} {name}>")

    def test_chart_geometry_is_finite_numbers_only(self):
        """The sole report-derived attribute values allowed: plain numeric SVG geometry."""
        geometry = {"points", "cx", "cy", "x", "y", "x1", "y1", "x2", "y2", "width", "height",
                    "r", "viewbox"}
        doc = _parse(report_mod.render_html(self._hostile()))
        seen = 0
        for tag, name, value in doc.attrs:
            if name in geometry:
                seen += 1
                self.assertRegex(value, r"^-?[0-9]+(\.[0-9]+)?([ ,]-?[0-9]+(\.[0-9]+)?)*$",
                                 f"<{tag} {name}> is not finite numeric geometry")
        self.assertGreater(seen, 0, "no geometry attributes found to validate")

class TestPillarCoverage(unittest.TestCase):
    """The radar reads score.pillars, the engine's own gating-only denominator."""

    def _rep(self, pillars, **kw):
        score = ScoreSummary(level=1, level_name="Functional", pass_rate=0.5, gating_passed=1,
                             gating_total=2, levels=[], pillars=pillars)
        if "results" in kw:
            kw["results"] = _traced(kw["results"])
        return Report(project_path="/p", schema_version="2", engine_version="0.6.0",
                      registry_version="0.6.0", detector_version="0.5.0", score=score, **kw)

    def test_radar_and_key_read_the_engine_denominator(self):
        # Advisory and skipped criteria must not move coverage: score.pillars already
        # excludes them, so the section must never re-aggregate over d.results.
        pillars = {"Docs": {"passed": 3, "total": 4}, "Build": {"passed": 1, "total": 2},
                   "Security": {"passed": 0, "total": 2}}
        noise = [CriterionResult(id="a.1", title="advisory", pillar="Docs", level=1,
                                 scope="repository", gating=False, status=Status.FAIL),
                 CriterionResult(id="s.1", title="skipped", pillar="Docs", level=1,
                                 scope="repository", gating=True, status=Status.SKIPPED)]
        doc = _parse(report_mod.render_html(self._rep(pillars, results=noise)))
        text = doc.body_text
        self.assertIn("pillars-heading", doc.ids)
        self.assertIn("Docs 3/4", text)
        self.assertIn("Build 1/2", text)
        self.assertIn("Security 0/2", text)
        # The accessible summary is <desc> TEXT, so it lands in body_text, not an attribute.
        self.assertIn("Docs 3 of 4, Build 1 of 2, Security 0 of 2", text)
        # The two noise criteria are in the document but changed no coverage number.
        self.assertIn("advisory", text)

    def test_radar_axes_match_pillar_count_and_stay_inside_the_box(self):
        pillars = {f"P{i}": {"passed": i, "total": 4} for i in range(1, 6)}
        doc = _parse(report_mod.render_html(self._rep(pillars)))
        radar = [a for t, a in doc.elements if t == "svg" and a.get("class") == "radar"]
        self.assertEqual(len(radar), 1)
        dots = [a for t, a in doc.elements if a.get("class") == "radar-dot"]
        self.assertEqual(len(dots), 5)
        for dot in dots:
            self.assertLessEqual(abs(float(dot["cx"]) - 120), 84.001)
            self.assertLessEqual(abs(float(dot["cy"]) - 120), 84.001)

    def test_two_pillars_render_the_key_without_a_radar(self):
        # Two axes is a line, not a shape. The exact counts still have to survive.
        doc = _parse(report_mod.render_html(
            self._rep({"Docs": {"passed": 1, "total": 2}, "Build": {"passed": 2, "total": 2}})))
        self.assertNotIn("radar", [a.get("class") for _, a in doc.elements])
        self.assertIn("Docs 1/2", doc.body_text)

    def test_zero_total_pillar_does_not_divide_by_zero(self):
        pillars = {"A": {"passed": 0, "total": 0}, "B": {"passed": 1, "total": 1},
                   "C": {"passed": 0, "total": 2}}
        doc = _parse(report_mod.render_html(self._rep(pillars)))
        self.assertIn("A 0/0", doc.body_text)

    def test_no_score_omits_the_section(self):
        rep = Report(project_path="/p", schema_version="2", engine_version="0.6.0",
                     registry_version="0.6.0", detector_version="0.5.0")
        self.assertNotIn("pillars-heading", _parse(report_mod.render_html(rep)).ids)


class TestFacets(unittest.TestCase):
    """CSS-only filtering: every id is an enum value or an ordinal, never repository text."""

    def _rep(self, results):
        return Report(project_path="/p", schema_version="2", engine_version="0.6.0",
                      registry_version="0.6.0", detector_version="0.5.0",
                      results=_traced(results))

    def _results(self):
        return [CriterionResult(id=f"x.{i}", title=f"C{i}", pillar=p, level=1,
                                scope="repository", gating=True, status=s)
                for i, (p, s) in enumerate([("Docs", Status.PASS), ("Docs", Status.FAIL),
                                            ("Build", Status.SKIPPED)])]

    def test_one_checkbox_per_present_status_and_pillar(self):
        doc = _parse(report_mod.render_html(self._rep(self._results())))
        boxes = {a["id"]: a for t, a in doc.elements if t == "input"}
        self.assertEqual(set(boxes), {"f-s-pass", "f-s-fail", "f-s-skipped", "f-p1", "f-p2"})
        self.assertEqual([a.get("type") for a in boxes.values()], ["checkbox"] * 5)
        # Skipped is the noise floor: unchecked on arrival, everything else checked.
        self.assertNotIn("checked", boxes["f-s-skipped"])
        self.assertIn("checked", boxes["f-s-pass"])

    def test_labels_bind_to_real_inputs_and_carry_counts(self):
        doc = _parse(report_mod.render_html(self._rep(self._results())))
        ids = {a["id"] for t, a in doc.elements if t == "input"}
        labels = [a["for"] for t, a in doc.elements if t == "label"]
        self.assertEqual(sorted(labels), sorted(ids))
        self.assertIn("Pass 1", doc.body_text)
        self.assertIn("Docs 2", doc.body_text)

    def test_menus_are_collapsed_icon_led_details_in_status_pillar_order(self):
        doc = _parse(report_mod.render_html(self._rep(self._results())))
        menus = [a for t, a in doc.elements
                 if t == "details" and a.get("class") == "facet-menu"]
        # No loop facets here, so exactly Status and Pillar menus, in that order.
        self.assertEqual([m["id"] for m in menus], ["status-facets", "pillar-facets"])
        for menu in menus:
            self.assertEqual(menu["name"], "criteria-filters")
            self.assertNotIn("open", menu)
        triggers = [a for t, a in doc.elements if a.get("class") == "facet-trigger"]
        self.assertEqual(len(triggers), 2)
        text = doc.body_text
        self.assertIn("Status 3 options", text)
        self.assertIn("Pillar 2 options", text)
        self.assertLess(text.index("Status 3 options"), text.index("Pillar 2 options"))

    def test_every_checkbox_sits_immediately_before_its_label_inside_a_menu(self):
        """Nested controls: the adjacent-sibling state rules require input+label adjacency,
        and closed menus must own their controls so they leave the tab sequence."""
        elements = _parse(report_mod.render_html(self._rep(self._results()))).elements
        inputs = [i for i, (t, a) in enumerate(elements) if t == "input"]
        self.assertEqual(len(inputs), 5)
        first_fieldset = next(i for i, (t, _) in enumerate(elements) if t == "fieldset")
        for i in inputs:
            tag, attrs = elements[i + 1]
            self.assertEqual(tag, "label")
            self.assertEqual(attrs["for"], elements[i][1]["id"])
            # Every control is nested inside a panel's fieldset — none is hoisted.
            self.assertGreater(i, first_fieldset)

    def test_generated_css_hides_rows_by_status_and_groups_by_pillar(self):
        css = report_mod._filter_css(report_mod._facet_model(self._rep(self._results())))
        self.assertIn(".report:has(#f-s-fail:not(:checked)) .criteria-body"
                      " .criterion.status-fail { display: none; }", css)
        # Pillars are shown by a live pair, never hidden by an option rule: the section
        # appears exactly when one of its (status, loop) pairs is fully checked.
        self.assertIn(".criteria-body .pillar { display: none; }", css)
        self.assertIn(".report:has(#f-s-skipped:checked):has(#f-p2:checked)"
                      " .criteria-body .p2 { display: block; }", css)
        self.assertNotIn("#f-p2:not(:checked)", css)
        # Nested checkboxes cannot govern rows with sibling combinators: :has() is the
        # mechanism now, and the generated grammar is exactly this shape.
        self.assertIn(":has(", css)
        self.assertNotIn(" ~ ", css)

    def test_empty_state_covers_a_cross_facet_zero_match(self):
        """Fail-only crossed with Build-only matches nothing, yet a status is still checked.

        The old "all statuses off" rule missed exactly this: p1 goes when its pillar facet
        clears, p2 goes on its skipped owner rule, and the reader is left with a blank
        region and no explanation. The message is now visible unless a surviving
        (status, pillar) pair says otherwise.
        """
        css = report_mod._filter_css(report_mod._facet_model(self._rep(self._results())))
        # p1 holds pass + fail, p2 holds skipped only. Those are the three live pairs.
        for sid, cls in (("f-s-pass", "p1"), ("f-s-fail", "p1"), ("f-s-skipped", "p2")):
            self.assertIn(f".report:has(#{sid}:checked):has(#f-{cls}:checked)"
                          " .criteria-body .criteria-empty { display: none; }", css)
        # Fail x Build is NOT a live pair, so nothing hides the message for that selection.
        self.assertNotIn("#f-s-fail:checked):has(#f-p2:checked", css)
        self.assertNotIn("#f-s-pass:checked):has(#f-p2:checked", css)
        # And the old blanket rule is gone: the message no longer waits for every status off.
        self.assertNotIn(".criteria-empty { display: block; }", css)

    def test_generated_css_is_authored_constants_only(self):
        hostile = [CriterionResult(id="x.1", title=_HOSTILE, pillar=_HOSTILE, level=1,
                                   scope="repository", gating=True, status=Status.FAIL)]
        css = report_mod._filter_css(report_mod._facet_model(self._rep(hostile)))
        self.assertNotIn("script", css)
        self.assertRegex(css, r"^[\sA-Za-z0-9_.,:;()~#\[\]=\"{}%/*-]*$")

    def test_a_pillar_group_shows_only_while_a_live_pair_is_checked(self):
        """A heading that outlives its own rows is a visibly broken filter.

        Pillar visibility is inverted: sections default to hidden and each live
        (status, loop) pair contributes one show chain. Emptiness is therefore the
        absence of a firing chain — with every status of a pillar off, nothing can
        resurrect its heading.
        """
        css = report_mod._filter_css(report_mod._facet_model(self._rep(self._results())))
        # p1 holds fail + pass, p2 holds only skipped: exactly three show chains exist.
        self.assertIn(".report:has(#f-s-fail:checked):has(#f-p1:checked)"
                      " .criteria-body .p1 { display: block; }", css)
        self.assertIn(".report:has(#f-s-pass:checked):has(#f-p1:checked)"
                      " .criteria-body .p1 { display: block; }", css)
        self.assertIn(".report:has(#f-s-skipped:checked):has(#f-p2:checked)"
                      " .criteria-body .p2 { display: block; }", css)
        # No other combination can show a section: fail x Build is not a live pair.
        self.assertNotIn("#f-s-fail:checked):has(#f-p2:checked) .criteria-body .p2", css)
        self.assertNotIn("#f-s-pass:checked):has(#f-p2:checked) .criteria-body .p2", css)

    def test_narrow_and_print_rules_keep_menus_usable_and_paper_complete(self):
        css = report_mod._STATIC_CSS
        narrow = css.split("@media (max-width: 720px)", 1)[1]
        self.assertIn(".facets { grid-template-columns: minmax(0, 1fr); }", narrow)
        self.assertIn(".facet-panel { position: static; max-height: none;"
                      " margin-top: var(--space-1); }", narrow)
        printed = css.split("@media print", 1)[1]
        self.assertIn(".facets, .facet-input { display: none; }", printed)
        # Criteria stay grids on paper so the badge rail survives print.
        self.assertIn(".criteria-body .criterion { display: grid !important; }", printed)
        self.assertIn(".criteria-body .pillar { display: block !important; }", printed)
        # Closed disclosures — education included — print expanded.
        self.assertIn("details:not([open]) > *:not(summary) { display: block; }", printed)

    def test_no_criteria_means_no_facets(self):
        doc = _parse(report_mod.render_html(self._rep([])))
        self.assertNotIn("input", doc.tags)
        self.assertEqual(report_mod._filter_css(([], [], [], [])), "")


class TestLoopFacets(unittest.TestCase):
    """The AC/DC loop axis: menu options, row classes, and the status x pillar x loop hole."""

    def _rep(self, results):
        return Report(project_path="/p", schema_version="2", engine_version="0.8.0",
                      registry_version="0.7.0", detector_version="0.5.0",
                      results=_traced(results))

    def _results(self):
        # One pillar on purpose: the loop hole only shows when status and loop disagree
        # inside a single section.
        return [
            CriterionResult(id="b.check", title="Check", pillar="Build", level=2,
                            scope="repository", gating=False, status=Status.FAIL,
                            acdc_stage="verify", acdc_loop="inner"),
            CriterionResult(id="t.gate", title="Gate", pillar="Build", level=4,
                            scope="repository", gating=False, status=Status.PASS,
                            acdc_stage="verify", acdc_loop="outer"),
            CriterionResult(id="d.readme", title="README", pillar="Build", level=1,
                            scope="repository", gating=True, status=Status.PASS),
        ]

    def test_loop_options_render_with_counts_and_default_checked(self):
        doc = _parse(report_mod.render_html(self._rep(self._results())))
        boxes = {a["id"]: a for t, a in doc.elements if t == "input"}
        self.assertIn("f-l-inner", boxes)
        self.assertIn("f-l-outer", boxes)
        self.assertNotIn("f-l-both", boxes)  # no both-loop rows, no option
        self.assertIn("checked", boxes["f-l-inner"])
        self.assertIn("AC/DC loop", doc.body_text)
        self.assertIn("Inner 1", doc.body_text)
        self.assertIn("Outer 1", doc.body_text)

    def test_no_loop_menu_without_mapped_criteria(self):
        results = [CriterionResult(id="d.readme", title="README", pillar="Docs", level=1,
                                   scope="repository", gating=True, status=Status.PASS)]
        doc = _parse(report_mod.render_html(self._rep(results)))
        self.assertNotIn("AC/DC loop", doc.body_text)
        self.assertNotIn("f-l-inner", {a["id"] for t, a in doc.elements if t == "input"})
        menus = [a for t, a in doc.elements if t == "details"
                 and a.get("class") == "facet-menu"]
        self.assertEqual([m["id"] for m in menus], ["status-facets", "pillar-facets"])

    def test_mapped_rows_carry_loop_classes(self):
        html = report_mod.render_html(self._rep(self._results()))
        self.assertIn("criterion status-fail loop-inner", html)
        self.assertIn("criterion status-pass loop-outer", html)
        # The unmapped row must carry no loop class, or a loop filter would hide it.
        self.assertIn('class="row criterion status-pass"', html)

    def test_generated_css_filters_rows_by_loop(self):
        css = report_mod._filter_css(report_mod._facet_model(self._rep(self._results())))
        self.assertIn(".report:has(#f-l-inner:not(:checked)) .criteria-body"
                      " .criterion.loop-inner { display: none; }", css)
        self.assertIn(".report:has(#f-l-outer:not(:checked)) .criteria-body"
                      " .criterion.loop-outer { display: none; }", css)

    def test_empty_state_covers_the_status_loop_cross_hole(self):
        """Fail x Outer matches no row in a pillar holding fail-inner and pass-outer.

        A status option and a loop option are both checked, so a naive rule would call the
        region populated. Only the live pairs get show/empty-hide chains, so that
        selection leaves the empty message visible.
        """
        css = report_mod._filter_css(report_mod._facet_model(self._rep(self._results())))
        # The three live pairs, each with the loop clause it needs.
        self.assertIn(".report:has(#f-s-fail:checked):has(#f-l-inner:checked)"
                      ":has(#f-p1:checked) .criteria-body .p1 { display: block; }", css)
        self.assertIn(".report:has(#f-s-pass:checked):has(#f-l-outer:checked)"
                      ":has(#f-p1:checked) .criteria-body .p1 { display: block; }", css)
        self.assertIn(".report:has(#f-s-pass:checked):has(#f-p1:checked)"
                      " .criteria-body .p1 { display: block; }", css)
        self.assertIn(".report:has(#f-s-fail:checked):has(#f-l-inner:checked)"
                      ":has(#f-p1:checked) .criteria-body .criteria-empty"
                      " { display: none; }", css)
        # Fail x Outer is NOT live: no chain may hide the empty message for it.
        self.assertNotIn("#f-s-fail:checked):has(#f-l-outer:checked", css)
        self.assertNotIn("#f-s-pass:checked):has(#f-l-inner:checked", css)

    def test_nested_controls_follow_status_loop_pillar_order(self):
        """Menus render Status → AC/DC loop → Pillar, and each menu's checkboxes sit inside
        its own panel immediately ahead of their labels."""
        doc = _parse(report_mod.render_html(self._rep(self._results())))
        menus = [a for t, a in doc.elements
                 if t == "details" and a.get("class") == "facet-menu"]
        self.assertEqual([m["id"] for m in menus],
                         ["status-facets", "loop-facets", "pillar-facets"])
        ids = [a["id"] for t, a in doc.elements if t == "input"]
        # Statuses in urgency order, then loops in inner/outer order, then pillars.
        self.assertEqual(ids, ["f-s-fail", "f-s-pass", "f-l-inner", "f-l-outer", "f-p1"])

    def test_triggers_follow_status_loop_pillar_order_and_stay_collapsed(self):
        doc = _parse(report_mod.render_html(self._rep(self._results())))
        text = doc.body_text
        self.assertIn("Status 2 options", text)
        self.assertIn("AC/DC loop 2 options", text)
        self.assertIn("Pillar 1 options", text)
        positions = [text.index(label) for label in
                     ("Status 2 options", "AC/DC loop 2 options", "Pillar 1 options")]
        self.assertEqual(positions, sorted(positions))
        menus = [a for t, a in doc.elements
                 if t == "details" and a.get("class") == "facet-menu"]
        self.assertEqual(len([a for t, a in doc.elements
                              if a.get("class") == "facet-trigger"]), 3)
        for menu in menus:
            self.assertEqual(menu["name"], "criteria-filters")
            self.assertNotIn("open", menu)


class TestHtmlEducation(unittest.TestCase):
    """The three teaching disclosures: levels, pillars, and the AC/DC mapping."""

    def _rep(self, **kw):
        kw.setdefault("project_path", "/p")
        kw.setdefault("schema_version", "2")
        kw.setdefault("engine_version", "0.9.0")
        kw.setdefault("registry_version", "0.7.0")
        kw.setdefault("detector_version", "0.5.0")
        if "results" in kw:
            kw["results"] = _traced(kw["results"])
        return Report(**kw)

    def _score(self, levels, pillars=None):
        return ScoreSummary(level=1, level_name="Functional", pass_rate=0.5,
                            gating_passed=4, gating_total=8, levels=levels,
                            pillars=pillars or {})

    def _five_levels(self, level5_total=0):
        return [LevelScore(level=n, name=f"Name{n}", passed=0,
                           total=4 if n < 5 else level5_total, achieved=n == 1)
                for n in range(1, 6)]

    def test_level_disclosure_teaches_cumulative_gates_with_canonical_names(self):
        rep = self._rep(score=self._score(self._five_levels()))
        doc = _parse(report_mod.render_html(rep))
        text = doc.body_text
        self.assertIn("Each level is cumulative. A gate clears when at least 80% of its "
                      "applicable gating criteria pass and every lower level has cleared. "
                      "Advisory, skipped, and waived criteria do not move the level; an "
                      "unknown gating result counts as not passed.", text)
        # Names come from model.LEVEL_NAMES even though the LevelScore rows carry others.
        for number, name in ((1, "Functional"), (2, "Documented"), (3, "Standardized"),
                             (4, "Optimized"), (5, "Autonomous")):
            self.assertIn(f"{number} {name}", text)
        for description in report_mod._LEVEL_EDUCATION.values():
            self.assertIn(description, text)

    def test_empty_level_says_so_only_when_it_has_no_gating_criteria(self):
        note = "No gating criteria are defined for this level yet."
        rep = self._rep(score=self._score(self._five_levels(level5_total=0)))
        text = _parse(report_mod.render_html(rep)).body_text
        self.assertEqual(text.count(note), 1)  # level 5 only
        rep = self._rep(score=self._score(self._five_levels(level5_total=2)))
        self.assertNotIn(note, _parse(report_mod.render_html(rep)).body_text)

    def test_pillar_disclosure_reuses_all_nine_mapping_descriptions(self):
        pillars = {"Documentation": {"passed": 3, "total": 4},
                   "Build System": {"passed": 1, "total": 2},
                   "Testing": {"passed": 2, "total": 2}}
        rep = self._rep(score=self._score(self._five_levels(), pillars))
        text = _parse(report_mod.render_html(rep)).body_text
        self.assertIn("Pillars organize checks by the kind of support an agent needs. "
                      "Coverage uses applicable gating criteria only; advisory, skipped, "
                      "and waived criteria do not change the chart.", text)
        self.assertEqual(len(report_mod._PILLAR_ELI5), 9)
        for name, why in report_mod._PILLAR_ELI5.items():
            self.assertIn(name, text)
            self.assertIn(why, text)

    def test_education_disclosures_render_collapsed(self):
        pillars = {"Documentation": {"passed": 1, "total": 2},
                   "Build System": {"passed": 1, "total": 2},
                   "Testing": {"passed": 2, "total": 2}}
        rep = self._rep(score=self._score(self._five_levels(), pillars))
        blocks = [a for t, a in _parse(report_mod.render_html(rep)).elements
                  if t == "details" and a.get("class") == "education"]
        self.assertEqual([b["id"] for b in blocks],
                         ["levels-education", "pillars-education"])
        for block in blocks:
            self.assertNotIn("open", block)

    def _acdc_rep(self):
        results = [CriterionResult(id="b.check", title="Check", pillar="Build", level=2,
                                   scope="repository", gating=True, status=Status.PASS,
                                   acdc_stage="verify", acdc_loop="inner")]
        return self._rep(results=results)

    def test_acdc_disclosure_quotes_and_cites_the_source_exactly(self):
        doc = _parse(report_mod.render_html(self._acdc_rep()))
        text = doc.body_text
        self.assertIn("Sonar’s Agent Centric Development Cycle surrounds generated code "
                      "with Guide → Verify → Solve. Ready Agent 1 uses AC/DC stage and "
                      "loop as advisory metadata; it never changes the Level 1–5 score.",
                      text)
        for term in ("Inner", "Outer", "Both"):
            self.assertIn(term, text)
        self.assertIn("Fast feedback during the agent’s reasoning process: local "
                      "guidance, a single verify command, and post-edit checks.", text)
        self.assertIn("Broader verification after the agent considers the task complete: "
                      "CI, coverage and changed-code quality gates, and branch "
                      "protection.", text)
        self.assertIn("Guidance that must remain available during local work and final "
                      "verification.", text)
        self.assertIn("“The inner loop: Guide-Verify-Solve happens in each agentic "
                      "reasoning loop, ensuring that the agent stays on track as it "
                      "methodically works to achieve the plans.”", text)
        self.assertIn("“The outer loop: Guide-Verify-Solve happens once the agent has "
                      "‘finished’ its work.”", text)
        self.assertIn("Sonar, “The future is AC/DC: the Agent Centric Development "
                      "Cycle”", text)
        self.assertEqual(doc.find("a"),
                         [{"href": report_mod._SONAR_ACDC_URL, "target": "_blank",
                           "rel": "noopener noreferrer"}])
        quotes = [a for t, a in doc.elements if t == "blockquote"]
        self.assertEqual(quotes, [{"class": "education-quote"}])

    def test_no_acdc_disclosure_without_mapped_loop_results(self):
        results = [CriterionResult(id="d.readme", title="README", pillar="Docs", level=1,
                                   scope="repository", gating=True, status=Status.PASS)]
        doc = _parse(report_mod.render_html(self._rep(results=results)))
        self.assertNotIn("How AC/DC loops map to this report", doc.body_text)
        self.assertNotIn("Guide-Verify-Solve", doc.body_text)
        self.assertEqual(doc.find("a"), [])


class TestRowAnatomy(unittest.TestCase):
    """The criterion entry: rail grid, fixed tag slots, and the --status-color token."""

    def _rep(self, results):
        return Report(project_path="/p", schema_version="2", engine_version="0.9.1",
                      registry_version="0.7.0", detector_version="0.5.0",
                      results=_traced(results))

    def _crit(self, **kw):
        base = dict(id="x.y", title="X", pillar="P", level=2, scope="repository",
                    gating=True, status=Status.FAIL, rationale="r",
                    passed_apps=0, evaluated_apps=1)
        base.update(kw)
        return _trace(CriterionResult(**base))

    def test_badge_is_the_rail_column_ahead_of_the_head(self):
        elements = _parse(report_mod.render_html(self._rep([self._crit()]))).elements
        li = next(i for i, (t, a) in enumerate(elements)
                  if a.get("class", "").startswith("row criterion"))
        self.assertEqual(elements[li + 1][1].get("class"), "badge")
        head_index = next(i for i, (t, a) in enumerate(elements)
                          if a.get("class") == "row-head")
        self.assertGreater(head_index, li + 1)

    def test_tag_slots_render_in_fixed_order_and_absent_slots_collapse(self):
        mapped = self._crit(acdc_stage="verify", acdc_loop="outer")
        doc = _parse(report_mod.render_html(self._rep([mapped])))
        slots = [a["class"] for _, a in doc.elements
                 if a.get("class", "").startswith("row-")
                 and a["class"] not in ("row-head", "row-title", "row-tags")]
        self.assertEqual(slots, ["row-status", "row-stake", "row-loop", "row-score"])
        self.assertIn("Fail Level 2 gate outer loop · verify 0/1", doc.body_text)
        # Unmapped, non-partial pass: loop and score slots must not render at all.
        clean = self._crit(status=Status.PASS, passed_apps=1)
        doc = _parse(report_mod.render_html(self._rep([clean])))
        slots = {a.get("class") for _, a in doc.elements}
        self.assertNotIn("row-loop", slots)
        self.assertNotIn("row-score", slots)
        self.assertIn("row-status", slots)
        self.assertIn("row-stake", slots)

    def test_status_color_token_replaces_per_status_meta_tinting(self):
        css = report_mod._STATIC_CSS
        for status, token in (("pass", "--status-pass"), ("fail", "--status-fail"),
                              ("unknown", "--status-warn")):
            self.assertIn(f".criterion.status-{status} {{ --status-color: var({token}); }}",
                          css)
        # The badge stroke, the blocking fill, and the status word all consume the token.
        self.assertIn(".criterion > .badge { color: var(--status-color); }", css)
        self.assertIn(".needs-action > .badge { background: var(--status-color); }", css)
        self.assertIn(".row-status { color: var(--status-color);", css)
        # The old wall of tinted meta is gone: only the status word carries color.
        self.assertNotIn(".row-meta { color: var(--status-fail)", css)
        self.assertNotIn(".status-fail .row-meta", css)

    def test_criterion_is_a_rail_grid(self):
        css = report_mod._STATIC_CSS
        self.assertIn("grid-template-columns: 1.35rem minmax(0, 1fr);", css)
        self.assertIn(".criterion > .badge { grid-column: 1; grid-row: 1; }", css)
        self.assertIn(".criterion > :not(.badge) { grid-column: 2; }", css)


class TestActionLayer(unittest.TestCase):
    """What needs doing, in plain language, only on the rows that need doing something."""

    def _rep(self, results):
        return Report(project_path="/p", schema_version="2", engine_version="0.6.0",
                      registry_version="0.6.0", detector_version="0.5.0",
                      results=_traced(results))

    def _crit(self, **kw):
        base = dict(id="x.y", title="X", pillar="Documentation", level=2, scope="repository",
                    gating=True, status=Status.FAIL, rationale="why")
        return _trace(CriterionResult(**{**base, **kw}))

    def test_action_copy_matches_what_ra1_fix_actually_does(self):
        # recipes.apply_plan writes only plan["auto"], so nothing but the scaffold branch
        # may mention --apply, and no branch may claim a draft is written for the user.
        scaffold = report_mod._action(self._crit(fix_kind="scaffold"))
        propose = report_mod._action(self._crit(fix_kind="propose"))
        setting = report_mod._action(self._crit(fix_kind="github_setting"))
        manual = report_mod._action(self._crit(fix_kind=""))
        self.assertIn("--apply", scaffold)
        for text in (propose, setting, manual):
            self.assertNotIn("--apply", text)
        # `propose` must never imply ra1 writes the content, and `github_setting` must
        # never imply it applies the setting: recipes.py only ever prints those.
        self.assertIn("you write it", propose)
        self.assertIn("you apply it", setting)
        # No registered remediation means no action line: a row of "Manual work, no
        # scaffold covers this" tells the reader nothing the rationale has not said.
        self.assertEqual(manual, "")

    def test_unknown_action_copy_branches_on_whether_it_is_scored(self):
        # score.py::_status_counts scores UNKNOWN 0/1, so a deterministic unknown counts
        # against the level. A judgment.* row says so in its own rationale, so the action
        # line stays silent rather than repeating the same fact underneath it.
        deterministic = report_mod._action(self._crit(status=Status.UNKNOWN))
        judgment = report_mod._action(self._crit(id="judgment.readme", gating=False,
                                                status=Status.UNKNOWN))
        self.assertIn("counts as not passed", deterministic)
        self.assertEqual(judgment, "")

    def test_each_sentence_appears_only_where_it_is_true(self):
        """The two next-step sentences have different, narrower scopes than "not settled".

        Remediation is true of any failure, so a suggested row keeps it: an advisory failure
        blocks nothing but is still worth doing. "Counts as not passed" is true only of a
        gating unknown, because score.summarize filters to r.gating, so an advisory unknown
        never reaches the level or the pass rate.
        """
        advisory_fail = self._crit(gating=False, status=Status.FAIL, fix_kind="scaffold")
        self.assertTrue(report_mod._suggested(advisory_fail))
        self.assertIn("--apply", report_mod._action(advisory_fail))
        self.assertNotIn("counts as not passed", report_mod._action(advisory_fail))

        advisory_unknown = self._crit(id="docs.thing", gating=False, status=Status.UNKNOWN)
        self.assertFalse(report_mod._blocking(advisory_unknown))
        self.assertEqual(report_mod._action(advisory_unknown), "",
                         "an advisory unknown never reaches the score, so it must not claim to")
        # Even with a remediation kind registered, an unknown is not a failure to remediate.
        self.assertEqual(report_mod._action(
            self._crit(gating=False, status=Status.UNKNOWN, fix_kind="scaffold")), "")

    def test_no_settled_row_ever_renders_a_next_step(self):
        rows = [self._crit(id=f"a.{i}", title=f"T{i}", gating=False, status=s, fix_kind="scaffold")
                for i, s in enumerate((Status.UNKNOWN, Status.PASS, Status.SKIPPED,
                                       Status.WAIVED))]
        doc = _parse(report_mod.render_html(self._rep(rows)))
        settled = [a for _, a in doc.elements
                   if a.get("class", "").startswith("row criterion")
                   and "needs-action" not in a["class"] and "suggested" not in a["class"]]
        self.assertEqual(len(settled), 4)
        self.assertEqual(sum(1 for _, a in doc.elements if a.get("class") == "next-step"), 0)

    def test_three_tiers_blocking_suggested_settled(self):
        """Only a gate can block. An advisory failure is worth doing and blocks nothing."""
        blocking = (self._crit(status=Status.FAIL),
                    self._crit(id="docs.thing", status=Status.UNKNOWN))
        for r in blocking:
            self.assertTrue(report_mod._blocking(r))
            self.assertFalse(report_mod._suggested(r))
        advisory_fail = self._crit(gating=False, status=Status.FAIL)
        self.assertFalse(report_mod._blocking(advisory_fail))
        self.assertTrue(report_mod._suggested(advisory_fail))
        # A judgment never enters the score, so it is neither blocking nor suggested.
        judgment = self._crit(id="judgment.x", gating=False, status=Status.UNKNOWN)
        self.assertFalse(report_mod._blocking(judgment))
        self.assertFalse(report_mod._suggested(judgment))
        for settled in (Status.PASS, Status.SKIPPED, Status.WAIVED):
            r = self._crit(status=settled)
            self.assertFalse(report_mod._blocking(r) or report_mod._suggested(r))

    def test_pillar_state_separates_blocking_from_suggested(self):
        # A count that lumps advisory nits in with a blocked gate teaches the reader to
        # ignore the count.
        blocked = [self._crit(id="a.1", status=Status.FAIL),
                   self._crit(id="judgment.x", gating=False, status=Status.UNKNOWN)]
        self.assertIn("1 blocking",
                      _parse(report_mod.render_html(self._rep(blocked))).body_text)
        advisory_only = [self._crit(id="a.2", gating=False, status=Status.FAIL),
                         self._crit(id="a.3", gating=False, status=Status.FAIL)]
        text = _parse(report_mod.render_html(self._rep(advisory_only))).body_text
        self.assertIn("2 suggested", text)
        self.assertNotIn("blocking", text)

    def test_rows_sort_by_urgency_then_gate_then_level(self):
        rows = [
            self._crit(id="d.pass", title="Passing", status=Status.PASS),
            self._crit(id="d.adv", title="Advisory fail", gating=False),
            self._crit(id="d.l3", title="Gate three", level=3),
            self._crit(id="d.l1", title="Gate one", level=1),
        ]
        text = _parse(report_mod.render_html(self._rep(rows))).body_text
        for earlier, later in (("Gate one", "Gate three"), ("Gate three", "Advisory fail"),
                               ("Advisory fail", "Passing")):
            self.assertLess(text.index(earlier), text.index(later),
                            f"{earlier} should precede {later}")

    def test_tiers_render_as_contiguous_blocks(self):
        """Blocking rows are boxed and the rest are plain, so they must not interleave.

        Sorting by status first put a boxed gate, then a plain advisory, then another boxed
        gate: two treatments alternating down the page, which reads as a styling bug.
        """
        rows = [
            self._crit(id="a.1", title="Gate fail", status=Status.FAIL, level=2),
            self._crit(id="a.2", title="Advisory fail", gating=False, status=Status.FAIL),
            self._crit(id="a.3", title="Gate unknown", status=Status.UNKNOWN, level=3),
            self._crit(id="a.4", title="Passing", status=Status.PASS),
        ]
        doc = _parse(report_mod.render_html(self._rep(rows)))
        tiers = [("needs-action" if "needs-action" in a["class"] else
                  "suggested" if "suggested" in a["class"] else "settled")
                 for _, a in doc.elements
                 if a.get("class", "").startswith("row criterion")]
        self.assertEqual(tiers, ["needs-action", "needs-action", "suggested", "settled"])
        text = doc.body_text
        # Both gates lead, lowest level first, and the advisory failure follows them.
        self.assertLess(text.index("Gate fail"), text.index("Gate unknown"))
        self.assertLess(text.index("Gate unknown"), text.index("Advisory fail"))

    def test_settled_rows_carry_no_action(self):
        for status in (Status.PASS, Status.SKIPPED, Status.WAIVED):
            self.assertEqual(report_mod._action(self._crit(status=status)), "")

    def test_stakes_name_the_level_a_gate_blocks(self):
        self.assertEqual(report_mod._stakes(self._crit(level=3)), "Level 3 gate")
        self.assertEqual(report_mod._stakes(self._crit(gating=False)), "advisory")

    def test_only_actionable_rows_render_the_action_and_the_fill(self):
        rows = [self._crit(id="a.1", title="Broken", fix_kind="scaffold"),
                self._crit(id="a.2", title="Fine", status=Status.PASS)]
        doc = _parse(report_mod.render_html(self._rep(rows)))
        classes = [a.get("class", "") for _, a in doc.elements]
        self.assertEqual(sum("needs-action" in c for c in classes), 1)
        self.assertEqual(sum(c == "next-step" for c in classes), 1)

    def test_pillar_header_states_purpose_and_open_count(self):
        rows = [self._crit(id="a.1", status=Status.FAIL),
                self._crit(id="a.2", status=Status.UNKNOWN),
                self._crit(id="a.3", status=Status.PASS)]
        text = _parse(report_mod.render_html(self._rep(rows))).body_text
        self.assertIn("What an agent reads before it touches your code.", text)
        self.assertIn("2 blocking", text)  # the gating fail + gating unknown, not the pass

    def test_a_clean_pillar_says_so(self):
        doc = _parse(report_mod.render_html(self._rep([self._crit(status=Status.PASS)])))
        self.assertIn("all clear", doc.body_text)
        self.assertIn("tone-clear", [a.get("class", "").split()[-1]
                                     for _, a in doc.elements if a.get("class")])

    def test_an_unknown_pillar_still_gets_a_header(self):
        doc = _parse(report_mod.render_html(self._rep([self._crit(pillar="Brand New")])))
        text = doc.body_text
        self.assertIn("Brand New", text)
        self.assertIn("1 blocking", text)


class TestDistribution(unittest.TestCase):
    def test_segments_are_proportional_and_span_the_full_width(self):
        svg = report_mod._distribution([("pass", 3), ("fail", 1), ("skipped", 0)])
        widths = [float(w) for w in re.findall(r'width="([\d.]+)"', svg)]
        self.assertEqual(len(widths), 2)  # zero-count statuses are dropped, not drawn
        self.assertAlmostEqual(sum(widths), 100.0, places=2)
        self.assertAlmostEqual(widths[0], 75.0, places=2)

    def test_no_criteria_draws_nothing(self):
        self.assertEqual(report_mod._distribution([("pass", 0)]), "")



class TestTypeLayer(unittest.TestCase):
    """Every type role is rendered exactly as `theme.TYPE_ROLES` declares it.

    The bug this defends against is silent: a selector documented as Title 600 that never
    sets font-weight falls to the UA default (bold 700) and every existing test still
    passes, because the DOM is unchanged. So assert the stylesheet, not the markup.
    """

    def _rules(self):
        """[(selector, {property: value})] for every rule in the hand-written stylesheet."""
        rules = []
        for chunk in report_mod._STATIC_CSS.split("}"):
            selector, _, body = chunk.partition("{")
            declarations = [d.strip() for d in body.split(";") if d.strip()]
            rules.append((" ".join(selector.split()),
                          dict(d.split(":", 1) for d in declarations if ":" in d)))
        return rules

    def test_every_role_is_assigned_a_selector(self):
        self.assertEqual(set(report_mod._ROLE_SELECTORS), set(theme.TYPE_ROLES))

    def test_generated_block_declares_every_role_property(self):
        css = report_mod._HTML_STYLE
        for role, props in theme.TYPE_ROLES.items():
            block = f"{report_mod._ROLE_SELECTORS[role]} {{\n"
            self.assertIn(block, css, f"{role} rule missing")
            declared = css.split(block, 1)[1].split("}", 1)[0]
            for prop, token in props.items():
                self.assertIn(f"  {prop}: var(--{token});\n", declared,
                              f"{role} does not set {prop} from --{token}")

    def test_no_hand_written_rule_shadows_a_role_property(self):
        owned = {sel.strip()
                 for selectors in report_mod._ROLE_SELECTORS.values()
                 for sel in selectors.replace("\n", " ").split(",")}
        for selector, declarations in self._rules():
            for part in selector.split(","):
                if part.strip() in owned:
                    clash = sorted(set(declarations) & set(theme.ROLE_OWNED))
                    self.assertEqual(clash, [], f"{part.strip()} re-declares {clash}")

    def test_role_sizes_are_distinct_and_correctly_bounded(self):
        rem = {role: float(theme.SCALE_TOKENS[props["font-size"]].removesuffix("rem"))
               for role, props in theme.TYPE_ROLES.items()}
        self.assertEqual(len(set(rem.values())), len(rem), "two roles share a size")
        self.assertEqual(max(rem, key=rem.get), "display")
        self.assertEqual(min(rem, key=rem.get), "label")
        # Body is the reading size; Title is a compact UI size and sits below it.
        self.assertGreater(rem["headline"], rem["body"])
        self.assertGreater(rem["body"], rem["title"])
        self.assertGreater(rem["title"], rem["meta"])


class TestCliFormats(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_multi_format_out(self):
        root = make_repo(GOODISH)
        self.addCleanup(rmtree, root)
        out = root.parent / (root.name + "_out")
        code, printed = self._run(["report", "--project", str(root), "--format",
                                   "markdown,junit,sarif,github,html", "--out", str(out)])
        self.assertEqual(code, 0)
        for name in ("report.md", "report.xml", "report.sarif", "report.txt", "report.html"):
            self.assertTrue((out / name).exists(), f"missing {name}")
        self.assertIn("# Agent Readiness Report", printed)  # markdown printed first
        self.assertTrue((out / "report.html").read_text().startswith("<!doctype html>"))

    def test_html_run_writes_no_sidecar_assets(self):
        """One file is the whole artifact: no stylesheet, no font, no assets directory."""
        root = make_repo(GOODISH)
        self.addCleanup(rmtree, root)
        out = root.parent / (root.name + "_out")
        code, _ = self._run(["report", "--project", str(root),
                             "--format", "html", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         [".commit.json", "report.html"])

    def test_html_primary_format_is_printed_verbatim(self):
        root = make_repo(GOODISH)
        self.addCleanup(rmtree, root)
        out = root.parent / (root.name + "_out")
        code, printed = self._run(["report", "--project", str(root),
                                   "--format", "html,json", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(printed, (out / "report.html").read_text())
        json.loads((out / "report.json").read_text())

    def test_parse_report_formats_normalizes_without_collapsing_repeats(self):
        self.assertEqual(cli._parse_report_formats(" , "), ["markdown"])
        self.assertEqual(cli._parse_report_formats(""), ["markdown"])
        self.assertEqual(cli._parse_report_formats("JSON,json"), ["json", "json"])
        self.assertEqual(cli._parse_report_formats(" MD , Checks "), ["md", "checks"])

    def test_whitespace_case_and_alias_keep_legacy_artifact_names(self):
        root = make_repo(GOODISH)
        self.addCleanup(rmtree, root)
        out = root.parent / (root.name + "_out")
        code, _ = self._run(["report", "--project", str(root), "--format",
                             " MD , Checks ,ANNOTATIONS", "--out", str(out)])
        self.assertEqual(code, 0)
        for name in ("report.md", "report.txt", "report.annotations"):
            self.assertTrue((out / name).exists(), f"missing {name}")
        self.assertEqual((out / "report.txt").read_text(),
                         (out / "report.annotations").read_text())

    def test_unsupported_format_fails_before_scanning_or_writing(self):
        root = make_repo(GOODISH)
        self.addCleanup(rmtree, root)
        out = root.parent / (root.name + "_out")
        err = io.StringIO()
        with mock.patch.object(cli, "analyze") as analyze_mock, \
                mock.patch("readiness.history.repo_identity") as identity_mock, \
                redirect_stderr(err):
            code, printed = self._run(["report", "--project", str(root),
                                       "--format", "json,htlm", "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertEqual(printed, "")
        self.assertEqual(err.getvalue(),
                         "ra1 report: unsupported report format 'htlm'; supported formats: "
                         "json, markdown, html, github, junit, sarif\n")
        analyze_mock.assert_not_called()
        identity_mock.assert_not_called()
        self.assertFalse(out.exists())

    def test_fail_on_hits_real_failure(self):
        root = make_repo(BARE)
        self.addCleanup(rmtree, root)
        code, _ = self._run(["report", "--project", str(root)
            , "--fail-on", "docs.readme"])
        self.assertEqual(code, 1)

    def test_min_level_on_real_score(self):
        root = make_repo(BARE)
        self.addCleanup(rmtree, root)
        code, _ = self._run(["report", "--project", str(root), "--min-level", "1"])
        self.assertEqual(code, 1)  # bare repo is level 0

class TestLocationRedaction(unittest.TestCase):
    """Phase 1 invariant: no serialized report or markdown carries the raw absolute path."""

    def _rep(self, repository=None, project_path="."):
        return Report(project_path=project_path, schema_version="2", engine_version="0.4.0",
                      registry_version="0.4.0", detector_version="0.4.0", repository=repository)

    def test_to_dict_omits_raw_project_path(self):
        rep = self._rep(project_path="/abs/secret/path")
        d = rep.to_dict()
        self.assertNotIn("project_path", d)
        self.assertNotIn("/abs/secret/path", json.dumps(d))

    def test_location_origin_shows_owner_name(self):
        rep = self._rep(repository={"identity_kind": "origin", "owner": "acme", "name": "widget"})
        self.assertEqual(report_mod._location(rep), "acme/widget")

    def test_location_origin_without_owner_falls_back_to_name(self):
        rep = self._rep(repository={"identity_kind": "origin", "name": "widget"})
        self.assertEqual(report_mod._location(rep), "widget")

    def test_location_local_path_shows_name_only(self):
        rep = self._rep(repository={"identity_kind": "local_path", "name": "widget",
                                    "project_path_hash": "abc"}, project_path="/home/user/widget")
        self.assertEqual(report_mod._location(rep), "widget")

    def test_location_no_repository_uses_basename_not_abspath(self):
        rep = self._rep(repository=None, project_path="/home/user/secret-proj")
        self.assertEqual(report_mod._location(rep), "local repository")

    def test_markdown_subtitle_redacts_abspath(self):
        rep = self._rep(repository={"identity_kind": "local_path", "name": "proj",
                                    "project_path_hash": "h"}, project_path="/home/user/proj")
        md = report_mod.render_markdown(rep)
        self.assertNotIn("/home/user", md)
        self.assertIn("· local repository_", md)


class TestRendererBranchGaps(unittest.TestCase):
    def _traced_unknown_bp(self):
        r = CriterionResult(id="security.branch_protection", title="Branch protection",
                            pillar="Security & Governance", level=2, scope="repository",
                            gating=True, status=Status.UNKNOWN,
                            rationale="could not be read", passed_apps=0, evaluated_apps=1)
        code = "security.branch_protection.github_unavailable"
        r.decision_trace = DecisionTrace(
            reason_code=code, rule_ref="checks.security.branch_protection",
            steps=[DecisionStep(kind="rule", code="rule.applied", message="rule"),
                   DecisionStep(kind="evaluation", code=code, message="eval"),
                   DecisionStep(kind="conclusion", code="conclusion.unknown",
                                message="Result: unknown.")])
        return r

    def _report(self, **kw):
        kw.setdefault("project_path", ".")
        kw.setdefault("schema_version", "2")
        kw.setdefault("engine_version", "0.11.0")
        kw.setdefault("registry_version", "0.8.0")
        kw.setdefault("detector_version", "0.6.0")
        return Report(**kw)

    def test_markdown_partial_t2_notice(self):
        # Requested-but-unreadable branch protection is "not verified", never "not
        # protected" — the partial-T2 notice says exactly that.
        rep = self._report(github_available=True)
        rep.results = [self._traced_unknown_bp()]
        md = report_mod.render_markdown(rep)
        self.assertIn("Partial T2 evidence", md)
        self.assertIn("not verified", md)

    def test_markdown_requested_t2_without_partial_has_no_notice(self):
        rep = self._report(github_available=True)
        rep.results = []
        md = report_mod.render_markdown(rep)
        self.assertNotIn("Partial T2 evidence", md)
        self.assertNotIn("were not requested", md)

    def test_markdown_defined_level_with_everything_excluded(self):
        rep = self._report()
        rep.score = ScoreSummary(
            level=0, level_name="None", pass_rate=0.0, gating_passed=0, gating_total=0,
            levels=[LevelScore(level=2, name="Documented", passed=0, total=0,
                               achieved=False, defined=True, defined_total=3)])
        md = report_mod.render_markdown(rep)
        self.assertIn("all 3 defined criteria were skipped/waived", md)

    def test_markdown_result_row_tolerates_out_of_range_ref_and_unknown_step(self):
        r = {"id": "x.y", "title": "T", "pillar": "P", "level": 1, "gating": True,
             "status": "fail", "rationale": "r", "fix_kind": "", "acdc_stage": "",
             "acdc_loop": "", "passed_apps": 0, "evaluated_apps": 1, "evidence": [],
             "decision_trace": {
                 "reason_code": "check.fail", "rule_ref": "checks.x.y",
                 "steps": [
                     {"kind": "rule", "code": "rule.applied", "message": "m",
                      "evidence_refs": []},
                     {"kind": "observation", "code": "evidence.observed", "message": "m",
                      "evidence_refs": [7]},          # out of range: skipped, never crashes
                     {"kind": "mystery", "code": "z", "message": "m",
                      "evidence_refs": []}],           # unknown kind: no row emitted
                 "limitations": []}}
        rows = report_mod._markdown_result_row(r, expand=True)
        self.assertTrue(any("Why this result" in row for row in rows))
        self.assertFalse(any("mystery" in row for row in rows))

    def test_markdown_boundary_absent(self):
        self.assertEqual(report_mod._markdown_boundary({}), [])

    def test_location_dict_origin_owner(self):
        d = {"repository": {"identity_kind": "origin", "owner": "acme", "name": "widget"}}
        self.assertEqual(report_mod._location_dict(d), "acme/widget")

    def test_canonical_view_result_without_trace(self):
        data = {"schema_version": "3", "engine_version": "0.11.0",
                "registry_version": "0.8.0", "detector_version": "0.6.0", "commit": "",
                "branch": "", "github_available": False,
                "generated_at": "2026-01-01T00:00:00+00:00",
                "results": [{"id": "x.y", "title": "t", "pillar": "P", "level": 1,
                             "scope": "repository", "gating": True, "status": "pass",
                             "rationale": "ok", "evidence": [], "app_path": ".",
                             "fixable": False, "fix_kind": "", "acdc_stage": "",
                             "acdc_loop": "", "passed_apps": 1, "evaluated_apps": 1}]}
        view = report_mod._canonical_view(data)
        self.assertIsNone(view.results[0].decision_trace)

    def test_html_partial_t2_callout(self):
        rep = self._report(github_available=True)
        rep.results = [self._traced_unknown_bp()]
        html = report_mod.render_html(rep)
        self.assertIn("Partial T2 evidence", html)
        self.assertIn("not verified", html)

    def test_html_requested_t2_without_partial_has_no_callout(self):
        rep = self._report(github_available=True)
        rep.results = []
        html = report_mod.render_html(rep)
        self.assertNotIn("Partial T2 evidence", html)
        self.assertNotIn("were not requested", html)

    def test_trace_disclosure_empty_without_trace(self):
        r = CriterionResult(id="x.y", title="t", pillar="P", level=1, scope="repository",
                            gating=True, status=Status.PASS)
        self.assertEqual(report_mod._trace_disclosure(r, 0), "")

    def test_trace_disclosure_ignores_unknown_step_kind(self):
        r = CriterionResult(id="x.y", title="t", pillar="P", level=1, scope="repository",
                            gating=True, status=Status.PASS)
        r.decision_trace = DecisionTrace(
            reason_code="check.pass", rule_ref="x.y",
            steps=[DecisionStep(kind="rule", code="rule.applied", message="m"),
                   DecisionStep(kind="mystery", code="z", message="m")])
        out = report_mod._trace_disclosure(r, 0)
        self.assertIn("<details", out)
        self.assertNotIn("mystery", out)

    def test_html_criterion_without_trace_renders_row(self):
        r = CriterionResult(id="x.y", title="t", pillar="P", level=1, scope="repository",
                            gating=True, status=Status.PASS, rationale="ok",
                            passed_apps=1, evaluated_apps=1)
        out = []
        report_mod._html_criterion(out, r, 0)
        self.assertTrue(out)

    def test_html_boundary_absent_is_a_noop(self):
        import types
        out = []
        report_mod._html_boundary(out, types.SimpleNamespace())
        self.assertEqual(out, [])

    def test_history_list_empty_entries(self):
        text = report_mod.render_history_list({"repository": {}, "entries": []})
        self.assertIn("_(none)_", text)

    def test_history_diff_detector_changed_note(self):
        text = report_mod.render_history_diff(
            {"from": "a", "to": "b", "comparable": True,
             "score_delta": {"level": {"from": 1, "to": 2}},
             "detector_changed": True})
        self.assertIn("detector version changed", text)



if __name__ == "__main__":
    unittest.main()
