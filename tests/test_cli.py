import io
import json
import unittest
from contextlib import redirect_stdout

from readiness import cli
from tests._util import make_repo, rmtree


def run(argv):
    """Run the CLI, returning (exit_code, stdout)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"pyproject.toml": '[project]\nname="lib"\nversion="1.0"\n', "README.md": "# lib"})
        self.addCleanup(rmtree, self.repo)

    def test_report_json(self):
        code, out = run(["report", "--project", str(self.repo), "--no-github", "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["detection"]["project_type"], "library")
        self.assertIn("engine_version", data)
        self.assertFalse(data["github_available"])

    def test_report_writes_out_dir(self):
        out_dir = self.repo / "_out"
        code, _ = run(["report", "--project", str(self.repo), "--no-github", "--out", str(out_dir)])
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "report.json").exists())
        self.assertTrue((out_dir / "latest.json").exists())
        json.loads((out_dir / "latest.json").read_text())  # valid JSON

    def test_version(self):
        code, out = run(["version"])
        self.assertEqual(code, 0)
        self.assertIn("engine_version", json.loads(out))

    def test_detect(self):
        code, out = run(["detect", "--project", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["project_type"], "library")

    def test_formats(self):
        code, out = run(["formats"])
        self.assertEqual(code, 0)
        self.assertIn("json", out)

    def test_min_level_gate_fails_when_unreachable(self):
        code, _ = run(["report", "--project", str(self.repo), "--no-github", "--min-level", "6"])
        self.assertEqual(code, 1)

    def test_no_min_level_passes(self):
        code, _ = run(["report", "--project", str(self.repo), "--no-github"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
