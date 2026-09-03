"""The typed analyze() entrypoint: closed options, injection channel, provenance modes."""
import unittest
from unittest import mock

from readiness import run, safe_io
from readiness.run import AnalyzeDependencies, AnalyzeOptions, analyze

from tests._util import gh_runner, make_repo, rmtree


class TestAnalyzeOptions(unittest.TestCase):
    def test_non_bool_flags_rejected(self):
        with self.assertRaises(ValueError):
            AnalyzeOptions(github="yes")
        with self.assertRaises(ValueError):
            AnalyzeOptions(exec=1)

    def test_legacy_dict_options_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            analyze(".", {"github": True})
        self.assertIn("legacy analyze options are rejected", str(ctx.exception))

    def test_foreign_options_type_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            analyze(".", 123)
        self.assertIn("AnalyzeOptions", str(ctx.exception))

    def test_foreign_deps_type_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            analyze(".", deps="bogus")
        self.assertIn("AnalyzeDependencies", str(ctx.exception))


class TestAnalyzeModes(unittest.TestCase):
    def test_github_requested_without_origin_identity(self):
        # T2 opt-in with no usable origin: identity resolution runs, GitHub stays
        # unavailable, and provenance records the request honestly.
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        report = analyze(root, AnalyzeOptions(github=True),
                         deps=AnalyzeDependencies(github_runner=gh_runner({})))
        invocation = report.assessment_provenance["invocation"]
        self.assertTrue(invocation["github"]["requested"])
        self.assertFalse(invocation["github"]["available"])
        self.assertFalse(invocation["github"]["collection_complete"])

    def test_injected_repository_identity_is_used(self):
        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        identity = {"identity_kind": "local_path", "name": "x",
                    "identity_hash": "a" * 16}
        report = analyze(root, deps=AnalyzeDependencies(repository=identity))
        self.assertEqual(report.repository, identity)
        self.assertEqual(
            report.assessment_provenance["invocation"]["inputs"]["profile"], "injected")

    def test_collector_without_close_is_tolerated(self):
        # The cleanup loop is defensive: a collector whose close is unset is skipped.
        real_exec = run.ExecCollector

        def shadowed(*args, **kwargs):
            collector = real_exec(*args, **kwargs)
            collector.close = None
            return collector

        root = make_repo({"README.md": "# x"})
        self.addCleanup(rmtree, root)
        with mock.patch.object(run, "ExecCollector", new=shadowed):
            report = analyze(root)
        self.assertEqual(report.schema_version, "3")


class TestProvenanceHelpers(unittest.TestCase):
    def test_waivers_file_present_refusal_is_false(self):
        class FakeStatic:
            def exists_observation(self, patterns):
                raise safe_io.RepositoryInputError("nope")

        self.assertFalse(run._waivers_file_present(FakeStatic()))


if __name__ == "__main__":
    unittest.main()
