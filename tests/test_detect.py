import json
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


class TestDetectPinning(unittest.TestCase):
    def _detect(self, files, options=None):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        return detect(root, options=options)

    def test_pin_overrides_unknown(self):
        d = self._detect({
            "README.md": "# hi",
            ".agents/readiness/config.json": '{"schema_version":"1","detect":{"project_type":"service"}}',
        })
        self.assertEqual(d.project_type, "service")
        self.assertGreaterEqual(d.confidence, 0.9)
        self.assertEqual(d.apps[0].deploy_surface, "service")
        self.assertTrue(any("pinned" in s and "config.json" in s for s in d.signals))

    def test_invalid_pin_ignored_with_signal(self):
        d = self._detect({
            "README.md": "# hi",
            ".agents/readiness/config.json": '{"detect":{"project_type":"spaceship"}}',
        })
        self.assertEqual(d.project_type, "unknown")
        self.assertTrue(any("ignored invalid project_type pin" in s for s in d.signals))

    def test_malformed_config_ignored(self):
        d = self._detect({
            "README.md": "# hi",
            ".agents/readiness/config.json": "{not json",
        })
        self.assertEqual(d.project_type, "unknown")


    def test_loop_ready_config_serializes_opt_in(self):
        d = self._detect({
            "README.md": "# hi",
            ".agents/readiness/config.json": json.dumps({"schema_version": "1", "loop_ready": True}),
        })
        self.assertEqual(d.opt_in, {"loop_ready": True})
        self.assertIs(d.to_dict()["opt_in"]["loop_ready"], True)

    def test_loop_ready_requires_literal_true(self):
        cases = [
            {},
            {".agents/readiness/config.json": "{not json"},
            {".agents/readiness/config.json": json.dumps({"loop_ready": "true"})},
            {".agents/readiness/config.json": json.dumps({"loop_ready": False})},
            {".agents/readiness/config.json": json.dumps(["loop_ready"])},
        ]
        for files in cases:
            with self.subTest(files=files):
                d = self._detect({"README.md": "# hi", **files})
                self.assertEqual(d.opt_in, {"loop_ready": False})
    def test_options_config_beats_file(self):
        d = self._detect(
            {
                "README.md": "# hi",
                ".agents/readiness/config.json": '{"detect":{"project_type":"library"}}',
            },
            options={"detect_config": {"detect": {"project_type": "cli"}}},
        )
        self.assertEqual(d.project_type, "cli")

    def test_monorepo_per_app_pin(self):
        d = self._detect({
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a","dependencies":{"express":"^4"}}',
            "packages/b/package.json": '{"name":"b","main":"index.js"}',
            ".agents/readiness/config.json": '{"detect":{"apps":{"packages/b":"frontend"}}}',
        })
        self.assertEqual(d.project_type, "monorepo-root")
        by_path = {a.path: a for a in d.apps}
        self.assertEqual(by_path["packages/b"].deploy_surface, "frontend")
        self.assertTrue(any("packages/b" in s and "pinned" in s for s in d.signals))

    def test_monorepo_root_pin_ignored_with_signal(self):
        d = self._detect({
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a","dependencies":{"express":"^4"}}',
            "packages/b/package.json": '{"name":"b","main":"index.js"}',
            ".agents/readiness/config.json": '{"detect":{"project_type":"service"}}',
        })
        self.assertEqual(d.project_type, "monorepo-root")
        self.assertTrue(any("root project_type pin ignored" in s for s in d.signals))

class TestAppDiscovery(unittest.TestCase):
    def _detect(self, files):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        return detect(root)

    def test_go_cmd_binaries_are_apps(self):
        d = self._detect({
            "go.mod": "module example.com/x\n\ngo 1.21\n",
            "cmd/api/main.go": "package main\nfunc main() {}\n",
            "cmd/worker/main.go": "package main\nfunc main() {}\n",
        })
        self.assertTrue(d.is_monorepo)
        paths = sorted(a.path for a in d.apps)
        self.assertEqual(paths, ["cmd/api", "cmd/worker"])
        self.assertTrue(all(a.deploy_surface == "cli" for a in d.apps))  # no web dep -> cli
        self.assertIn("go", d.languages)

    def test_go_cmd_service_classification(self):
        d = self._detect({
            "go.mod": "module x\n\ngo 1.21\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n",
            "cmd/api/main.go": "package main\nfunc main() {}\n",
            "cmd/worker/main.go": "package main\nfunc main() {}\n",
        })
        self.assertTrue(all(a.deploy_surface == "service" for a in d.apps))

    def test_maven_multi_module(self):
        d = self._detect({
            "pom.xml": ('<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
                        "<modules><module>svc</module><module>lib</module></modules></project>\n"),
            "svc/pom.xml": '<project xmlns="http://maven.apache.org/POM/4.0.0"></project>\n',
            "lib/pom.xml": '<project xmlns="http://maven.apache.org/POM/4.0.0"></project>\n',
        })
        self.assertTrue(d.is_monorepo)
        self.assertEqual(sorted(a.path for a in d.apps), ["lib", "svc"])
        self.assertIn("java", d.languages)

    def test_gradle_includes(self):
        d = self._detect({
            "settings.gradle": "include ':app', ':lib', ':ghost'\n",  # ghost dir absent -> skipped
            "app/build.gradle": "plugins { id 'application' }\n",
            "lib/build.gradle": "plugins { id 'java-library' }\n",
        })
        self.assertTrue(d.is_monorepo)
        self.assertEqual(sorted(a.path for a in d.apps), ["app", "lib"])

    def test_examples_are_not_inflated_into_apps(self):
        d = self._detect({
            "package.json": '{"name":"root","workspaces":["packages/*","examples/*"]}',
            "packages/real/package.json": '{"name":"real","dependencies":{"express":"^4"}}',
            "packages/real2/package.json": '{"name":"real2","dependencies":{"fastify":"^4"}}',
            "packages/empty/.keep": "",  # no manifest -> not an app
            "examples/demo/package.json": '{"name":"demo","dependencies":{"express":"^4"}}',
        })
        paths = [a.path for a in d.apps]
        self.assertIn("packages/real", paths)
        self.assertNotIn("examples/demo", paths)
        self.assertNotIn("packages/empty", paths)

    def test_npm_workspaces_packages_dict_form(self):
        d = self._detect({
            "package.json": '{"name":"root","workspaces":{"packages":["packages/*"]}}',
            "packages/a/package.json": '{"name":"a","dependencies":{"express":"^4"}}',
            "packages/b/package.json": '{"name":"b","dependencies":{"fastify":"^4"}}',
        })
        self.assertEqual(sorted(a.path for a in d.apps), ["packages/a", "packages/b"])

    def test_cargo_workspace_members(self):
        d = self._detect({
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
            "crates/a/Cargo.toml": '[package]\nname="a"\n',
            "crates/b/Cargo.toml": '[package]\nname="b"\n',
            "crates/empty/.keep": "",  # dir without manifest -> not an app
        })
        self.assertTrue(d.is_monorepo)
        self.assertEqual(sorted(a.path for a in d.apps), ["crates/a", "crates/b"])

    def test_invalid_app_pin_signal(self):
        d = self._detect({
            "package.json": '{"name":"root","workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a","dependencies":{"express":"^4"}}',
            "packages/b/package.json": '{"name":"b","dependencies":{"fastify":"^4"}}',
            ".agents/readiness/config.json": '{"detect":{"apps":{"packages/a":"notatype"}}}',
        })
        self.assertTrue(any("ignored invalid type pin" in s for s in d.signals))

    def test_single_language_signals(self):
        for files, lang in [
            ({"Cargo.toml": '[package]\nname="x"\n'}, "rust"),
            ({"Gemfile": "source 'x'\n"}, "ruby"),
        ]:
            d = self._detect(files)
            self.assertIn(lang, d.languages)
            self.assertTrue(any(s.startswith("languages:") for s in d.signals))


class TestDetectInternals(unittest.TestCase):
    def test_ignored_app_dir(self):
        from readiness import detect as det
        self.assertTrue(det._ignored_app_dir("examples/demo"))
        self.assertTrue(det._ignored_app_dir("vendor/lib"))
        self.assertFalse(det._ignored_app_dir("packages/api"))

    def test_go_cmd_apps_requires_go_mod_and_cmd(self):
        from readiness import detect as det
        no_go = make_repo({"cmd/api/main.go": "package main\n"})
        self.addCleanup(rmtree, no_go)
        self.assertEqual(det._go_cmd_apps(no_go), [])
        no_cmd = make_repo({"go.mod": "module x\n"})
        self.addCleanup(rmtree, no_cmd)
        self.assertEqual(det._go_cmd_apps(no_cmd), [])

    def test_maven_modules_malformed_pom(self):
        from readiness import detect as det
        root = make_repo({"pom.xml": "<project><modules><module>svc"})  # truncated/invalid
        self.addCleanup(rmtree, root)
        self.assertEqual(det._maven_modules(root), [])

    def test_detect_test_cmd_variants(self):
        from readiness import detect as det
        from readiness.collectors.static import StaticCollector
        cases = [
            ({"package.json": '{"scripts":{"test":"jest"}}'}, "npm test"),
            ({"pyproject.toml": '[tool.pytest.ini_options]\n'}, "pytest"),
            ({"go.mod": "module x\n"}, "go test ./..."),
            ({"Cargo.toml": '[package]\nname="x"\n'}, "cargo test"),
            ({"README.md": "# x"}, ""),
        ]
        for files, expected in cases:
            root = make_repo(files)
            self.addCleanup(rmtree, root)
            self.assertEqual(det._detect_test_cmd(StaticCollector(root)), expected)

    def test_malformed_config_is_ignored(self):
        bad = self._d({".agents/readiness/config.json": "{not json"})
        self.assertEqual(bad.project_type, "unknown")  # config error -> no pin, no crash
        nondict = self._d({"pyproject.toml": '[project]\nname="x"\n',
                           ".agents/readiness/config.json": '{"detect":"notadict"}'})
        self.assertEqual(nondict.project_type, "library")  # detect block ignored

    def test_config_via_options(self):
        root = make_repo({"pyproject.toml": '[project]\nname="x"\n'})
        self.addCleanup(rmtree, root)
        # explicit readiness_config option beats on-disk file (covers the options branch)
        d = detect(root, None, {"readiness_config": {"loop_ready": True}})
        self.assertTrue(d.opt_in["loop_ready"])
        # non-dict detect_config option is ignored without crashing
        d2 = detect(root, None, {"detect_config": "notadict"})
        self.assertEqual(d2.project_type, "library")

    def _d(self, files):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        return detect(root)


if __name__ == "__main__":
    unittest.main()
