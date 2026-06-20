import io
import json
import unittest
from contextlib import redirect_stdout
from xml.etree import ElementTree as ET

from readiness import cli, report as report_mod
from readiness.model import CriterionResult, Evidence, Report, Status
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
    return root, analyze(root, {"no_github": True})


class TestMarkdown(unittest.TestCase):
    def test_sections_present(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        md = report_mod.render_markdown(rep)
        self.assertIn("# Agent Readiness Report", md)
        self.assertIn("Level 0", md)
        self.assertIn("## Criteria", md)
        self.assertIn("## Action Items", md)
        self.assertIn("Quick wins", md)  # gitignore scaffold

    def test_unknown_type_warning(self):
        root, rep = _report({"Makefile": "all:\n\techo hi\n"})
        self.addCleanup(rmtree, root)
        md = report_mod.render_markdown(rep)
        self.assertIn("unknown", md.lower())


    def test_non_gating_failures_render_as_advisory_improvements(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = [
            CriterionResult(id="docs.readme", title="README", pillar="Documentation", level=1,
                            scope="repository", gating=True, status=Status.FAIL, rationale="missing",
                            passed_apps=0, evaluated_apps=1),
            CriterionResult(id="loop.loop_runs_dir", title="Loop Run Log README", pillar="Documentation", level=2,
                            scope="repository", gating=False, status=Status.FAIL, rationale="missing loop log",
                            fix_kind="scaffold", passed_apps=0, evaluated_apps=1),
        ]
        md = report_mod.render_markdown(rep)
        self.assertIn("**Loop Run Log README** (**advisory**, L2, 0/1): missing loop log", md)
        self.assertIn("## Advisory Improvements", md)
        self.assertIn("- Loop Run Log README (L2, Documentation) — missing loop log", md)
        action_section = md.split("## Advisory Improvements")[0].split("## Action Items", 1)[1]
        self.assertNotIn("Loop Run Log README", action_section)
        self.assertIn("README", action_section)

class TestGithub(unittest.TestCase):
    def test_annotations(self):
        root, rep = _report(BARE)
        self.addCleanup(rmtree, root)
        gh = report_mod.render_github(rep)
        self.assertIn("::warning", gh)
        self.assertIn("::notice::Agent Readiness Level", gh)


    def test_non_gating_failures_omit_annotations(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = [CriterionResult(id="loop.loop_runs_dir", title="Loop Run Log README",
                                       pillar="Documentation", level=2, scope="repository",
                                       gating=False, status=Status.FAIL, rationale="missing")]
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
        rep.results = [CriterionResult(
            id="style.large_file", title="Large File", pillar="Style & Validation", level=1,
            scope="repository", gating=True, status=Status.FAIL, rationale="src/big.py is huge",
            evidence=[Evidence(summary="huge", source="src/big.py")],
        )]
        doc = json.loads(report_mod.render_sarif(rep))
        res = doc["runs"][0]["results"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["ruleId"], "style.large_file")
        self.assertEqual(res[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"], "src/big.py")

    def test_non_gating_failures_omitted_from_sarif(self):
        rep = Report(project_path=".", schema_version="1", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = [CriterionResult(
            id="loop.loop_runs_dir", title="Loop Run Log README", pillar="Documentation", level=2,
            scope="repository", gating=False, status=Status.FAIL, rationale="missing",
            evidence=[Evidence(summary="loop", source="loop-runs/README.md")],
        )]
        doc = json.loads(report_mod.render_sarif(rep))
        self.assertEqual(doc["runs"][0]["results"], [])


class TestRenderDispatch(unittest.TestCase):
    def test_json_and_fallback(self):
        root, rep = _report(GOODISH)
        self.addCleanup(rmtree, root)
        json.loads(report_mod.render(rep, "json"))
        json.loads(report_mod.render(rep, "totally-unknown-format"))  # falls back to JSON


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
        self.assertIn("## Action Items", md)
        self.assertIn("highest-impact", md)


class TestRenderCoverage(unittest.TestCase):
    def _rep(self, results, advisory=None, score=None):
        rep = Report(project_path=".", schema_version="2", engine_version="0.3.0",
                     registry_version="0.3.0", detector_version="0.3.0")
        rep.results = results
        if advisory:
            rep.advisory = advisory
        rep.score = score
        return rep

    def test_no_action_items_when_all_pass(self):
        rep = self._rep([CriterionResult(id="docs.readme", title="README", pillar="Docs", level=1,
                         scope="repository", gating=True, status=Status.PASS,
                         passed_apps=1, evaluated_apps=1)])
        self.assertNotIn("## Action Items", report_mod.render_markdown(rep))

    def test_agent_advisory_rendered(self):
        md = report_mod.render_markdown(self._rep([], advisory=["Consider tightening X."]))
        self.assertIn("## Advisory (non-gating, agent-authored)", md)
        self.assertIn("Consider tightening X.", md)

    def test_github_annotation_with_source_skips_non_file_evidence(self):
        r = CriterionResult(id="docs.api_schema_docs", title="API Schema", pillar="Docs", level=3,
                            scope="repository", gating=True, status=Status.FAIL, rationale="missing",
                            evidence=[Evidence(summary="api", source="repos/o/r"),
                                      Evidence(summary="schema", source="src/openapi.yaml")])
        gh = report_mod.render_github(self._rep([r]))
        self.assertIn("file=src/openapi.yaml", gh)

    def test_sarif_dedups_rule_for_repeated_id(self):
        ev = [Evidence(summary="x", source="src/a.py")]
        results = [
            CriterionResult(id="x.y", title="X", pillar="P", level=2, scope="application",
                            gating=True, status=Status.FAIL, rationale="r", evidence=ev, app_path="a"),
            CriterionResult(id="x.y", title="X", pillar="P", level=2, scope="application",
                            gating=True, status=Status.FAIL, rationale="r", evidence=ev, app_path="b"),
        ]
        doc = json.loads(report_mod.render_sarif(self._rep(results)))
        rule_ids = [ru["id"] for ru in doc["runs"][0]["tool"]["driver"]["rules"]]
        self.assertEqual(rule_ids.count("x.y"), 1)
        self.assertEqual(len(doc["runs"][0]["results"]), 2)


class TestCliFormats(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_multi_format_out(self):
        root = make_repo(GOODISH)
        self.addCleanup(rmtree, root)
        out = root / "_out"
        code, printed = self._run(["report", "--project", str(root), "--no-github",
                                   "--format", "markdown,junit,sarif,github", "--out", str(out)])
        self.assertEqual(code, 0)
        for name in ("report.md", "report.xml", "report.sarif", "report.txt", "latest.json"):
            self.assertTrue((out / name).exists(), f"missing {name}")
        self.assertIn("# Agent Readiness Report", printed)  # markdown printed first

    def test_fail_on_hits_real_failure(self):
        root = make_repo(BARE)
        self.addCleanup(rmtree, root)
        code, _ = self._run(["report", "--project", str(root), "--no-github", "--fail-on", "docs.readme"])
        self.assertEqual(code, 1)

    def test_min_level_on_real_score(self):
        root = make_repo(BARE)
        self.addCleanup(rmtree, root)
        code, _ = self._run(["report", "--project", str(root), "--no-github", "--min-level", "1"])
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
        self.assertEqual(report_mod._location(rep), "secret-proj")

    def test_markdown_subtitle_redacts_abspath(self):
        rep = self._rep(repository={"identity_kind": "local_path", "name": "proj",
                                    "project_path_hash": "h"}, project_path="/home/user/proj")
        md = report_mod.render_markdown(rep)
        self.assertNotIn("/home/user", md)
        self.assertIn("· proj", md)



if __name__ == "__main__":
    unittest.main()
