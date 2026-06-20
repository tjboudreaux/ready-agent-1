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
                            scope="repository", gating=True, status=Status.FAIL, rationale="missing"),
            CriterionResult(id="loop.loop_runs_dir", title="Loop Run Log README", pillar="Documentation", level=2,
                            scope="repository", gating=False, status=Status.FAIL, rationale="missing loop log",
                            fix_kind="scaffold"),
        ]
        md = report_mod.render_markdown(rep)
        self.assertIn("**Loop Run Log README** (**advisory**, L2): missing loop log", md)
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


if __name__ == "__main__":
    unittest.main()
