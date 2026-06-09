import unittest

from readiness import parsers
from tests._util import make_repo, rmtree


class TestParsers(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({})
        self.addCleanup(rmtree, self.root)

    def test_read_text_present_and_missing(self):
        (self.root / "a.txt").write_text("hello")
        self.assertEqual(parsers.read_text(self.root / "a.txt"), "hello")
        self.assertIsNone(parsers.read_text(self.root / "nope.txt"))

    def test_load_json_valid_and_invalid(self):
        (self.root / "ok.json").write_text('{"a": 1}')
        (self.root / "bad.json").write_text("{not json}")
        self.assertEqual(parsers.load_json(self.root / "ok.json"), {"a": 1})
        self.assertIsNone(parsers.load_json(self.root / "bad.json"))
        self.assertIsNone(parsers.load_json(self.root / "missing.json"))

    def test_strip_jsonc_line_and_block_comments(self):
        text = '{\n  // a line comment\n  "a": 1, /* block */ "b": 2,\n}'
        self.assertEqual(parsers.strip_jsonc(text).count("//"), 0)
        # round-trips to valid json with trailing comma removed
        import json
        self.assertEqual(json.loads(parsers.strip_jsonc(text)), {"a": 1, "b": 2})

    def test_strip_jsonc_preserves_double_slash_in_strings(self):
        text = '{"url": "http://example.com//x"}'
        import json
        self.assertEqual(json.loads(parsers.strip_jsonc(text)), {"url": "http://example.com//x"})

    def test_load_jsonc(self):
        (self.root / "tsconfig.json").write_text('{\n  "compilerOptions": { "strict": true, } // c\n}')
        data = parsers.load_jsonc(self.root / "tsconfig.json")
        self.assertEqual(data, {"compilerOptions": {"strict": True}})
        self.assertIsNone(parsers.load_jsonc(self.root / "missing.json"))

    def test_load_toml(self):
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["requests"]\n')
        data = parsers.load_toml(self.root / "pyproject.toml")
        self.assertEqual(data["project"]["name"], "x")
        (self.root / "bad.toml").write_text("= broken")
        self.assertIsNone(parsers.load_toml(self.root / "bad.toml"))
        self.assertIsNone(parsers.load_toml(self.root / "missing.toml"))

    def test_load_ini(self):
        (self.root / "setup.cfg").write_text("[flake8]\nmax-line-length = 100\n")
        parser = parsers.load_ini(self.root / "setup.cfg")
        self.assertEqual(parser.get("flake8", "max-line-length"), "100")
        self.assertIsNone(parsers.load_ini(self.root / "missing.cfg"))


if __name__ == "__main__":
    unittest.main()
