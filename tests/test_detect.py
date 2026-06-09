import unittest

from readiness.detect import detect
from tests._util import make_repo, rmtree


class TestDetect(unittest.TestCase):
    def _detect(self, files):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        return detect(root)

    def test_python_library(self):
        d = self._detect({"pyproject.toml": '[project]\nname = "mylib"\nversion = "1.0"\n'})
        self.assertEqual(d.project_type, "library")
        self.assertFalse(d.is_monorepo)
        self.assertEqual(len(d.apps), 1)
        self.assertIn("python", d.languages)

    def test_fastapi_service_without_dockerfile(self):
        d = self._detect({"pyproject.toml": '[project]\nname = "svc"\ndependencies = ["fastapi", "uvicorn"]\n'})
        self.assertEqual(d.project_type, "service")
        self.assertEqual(d.apps[0].prod_facing, "unknown")

    def test_service_with_dockerfile_is_prod_facing(self):
        d = self._detect({
            "pyproject.toml": '[project]\nname = "svc"\ndependencies = ["fastapi"]\n',
            "Dockerfile": "FROM python:3.11\n",
        })
        self.assertEqual(d.project_type, "service")
        self.assertEqual(d.apps[0].prod_facing, True)

    def test_express_service(self):
        d = self._detect({"package.json": '{"name":"api","dependencies":{"express":"^4"}}'})
        self.assertEqual(d.project_type, "service")

    def test_cli_via_bin(self):
        d = self._detect({"package.json": '{"name":"tool","bin":{"tool":"./cli.js"}}'})
        self.assertEqual(d.project_type, "cli")

    def test_frontend(self):
        d = self._detect({"package.json": '{"name":"web","dependencies":{"react":"^18","react-dom":"^18"}}'})
        self.assertEqual(d.project_type, "frontend")

    def test_infra(self):
        d = self._detect({"main.tf": 'resource "aws_s3_bucket" "b" {}\n'})
        self.assertEqual(d.project_type, "infra")

    def test_ambiguous_is_unknown_low_confidence(self):
        d = self._detect({"README.md": "# hi"})
        self.assertEqual(d.project_type, "unknown")
        self.assertLess(d.confidence, 0.5)

    def test_monorepo_npm_workspaces(self):
        d = self._detect({
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a","dependencies":{"express":"^4"}}',
            "packages/b/package.json": '{"name":"b","main":"index.js"}',
        })
        self.assertTrue(d.is_monorepo)
        self.assertEqual(d.project_type, "monorepo-root")
        self.assertEqual(len(d.apps), 2)
        paths = {a.path for a in d.apps}
        self.assertEqual(paths, {"packages/a", "packages/b"})

    def test_detection_serializes(self):
        d = self._detect({"go.mod": "module x\n"})
        data = d.to_dict()
        self.assertIn("project_type", data)
        self.assertIn("apps", data)
        self.assertIsInstance(data["confidence"], float)


if __name__ == "__main__":
    unittest.main()
