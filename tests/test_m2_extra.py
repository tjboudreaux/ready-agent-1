import unittest

from readiness import score
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.detect import detect
from readiness.model import Status
from tests._util import make_repo, rmtree, fake_runner


class TestScoreInternals(unittest.TestCase):
    def test_lang_match(self):
        self.assertTrue(score._lang_match(["*"], ["python"]))
        self.assertTrue(score._lang_match(["python"], ["python", "npm"]))
        self.assertFalse(score._lang_match(["python"], ["npm"]))

    def test_type_match(self):
        self.assertEqual(score._type_match(["*"], "library"), "match")
        self.assertEqual(score._type_match(["service"], "unknown"), "unknown")
        self.assertEqual(score._type_match(["service", "api"], "service"), "match")
        self.assertEqual(score._type_match(["service"], "library"), "skip")

    def test_load_waivers_from_file(self):
        root = make_repo({".agents/readiness/waivers.json": '[{"id":"docs.readme","reason":"r","owner":"t"}]'})
        self.addCleanup(rmtree, root)
        waivers = score.load_waivers(root, {})
        self.assertIn("docs.readme", waivers)

    def test_load_waivers_missing_and_malformed(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self.assertEqual(score.load_waivers(root, {}), {})
        bad = make_repo({".agents/readiness/waivers.json": "{not json"})
        self.addCleanup(rmtree, bad)
        self.assertEqual(score.load_waivers(bad, {}), {})


class TestAggregationPaths(unittest.TestCase):
    def _eval_naming(self, files):
        root = make_repo(files)
        self.addCleanup(rmtree, root)
        static = StaticCollector(root)
        det = detect(root, static)
        git = GitCollector(root, runner=fake_runner({}))
        gh = GithubCollector(root, runner=fake_runner({}))
        results, _ = score.evaluate(root, det, static, git, gh, {})
        return next(r for r in results if r.id == "testing.test_naming")

    def test_mixed_pass_and_skip_aggregates_pass(self):
        r = self._eval_naming({
            "package.json": '{"workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a"}',
            "packages/a/tests/test_x.py": "def test_x(): pass\n",
            "packages/b/package.json": '{"name":"b"}',
        })
        self.assertEqual(r.status, Status.PASS)

    def test_all_skip_aggregates_skip(self):
        r = self._eval_naming({
            "package.json": '{"workspaces":["packages/*"]}',
            "packages/a/package.json": '{"name":"a"}',
            "packages/b/package.json": '{"name":"b"}',
        })
        self.assertEqual(r.status, Status.SKIPPED)


if __name__ == "__main__":
    unittest.main()
