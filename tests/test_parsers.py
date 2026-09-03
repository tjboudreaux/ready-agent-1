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

    def test_strip_jsonc_respects_backslash_escapes(self):
        """An escaped quote must not end the string, so `//` after it stays literal.

        Exercises the escape state machine in both passes -- comment stripping and trailing
        comma removal -- which a document without backslashes never reaches.
        """
        import json
        # The JSON value is:  say "hi" // not a comment
        text = '{\n  "a": "say \\"hi\\" // not a comment", // real comment\n  "b": 2,\n}'
        stripped = parsers.strip_jsonc(text)
        self.assertEqual(json.loads(stripped),
                         {"a": 'say "hi" // not a comment', "b": 2})

    def test_strip_jsonc_trailing_backslash_in_string(self):
        """A string ending in an escaped backslash keeps it and closes correctly."""
        import json
        text = '{"win": "C:\\\\dir\\\\", "n": 1,}'
        self.assertEqual(json.loads(parsers.strip_jsonc(text)), {"win": "C:\\dir\\", "n": 1})

    def test_load_ini_malformed_returns_none(self):
        # A continuation line with no preceding option is a configparser.Error.
        (self.root / "bad.cfg").write_text("  orphan = 1\n[section]\nk = v\n")
        self.assertIsNone(parsers.load_ini(self.root / "bad.cfg"))

    def test_load_jsonc(self):
        (self.root / "tsconfig.json").write_text('{\n  "compilerOptions": { "strict": true, } // '
                                                 'c\n}')
        data = parsers.load_jsonc(self.root / "tsconfig.json")
        self.assertEqual(data, {"compilerOptions": {"strict": True}})
        self.assertIsNone(parsers.load_jsonc(self.root / "missing.json"))

    def test_load_toml(self):
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = '
                                                  '["requests"]\n')
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


class TestReadEngineText(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({})
        self.addCleanup(rmtree, self.root)

    def test_oversize_returns_none(self):
        (self.root / "big.txt").write_text("x" * 64)
        self.assertIsNone(parsers.read_engine_text(self.root / "big.txt", max_bytes=8))

    def test_non_utf8_returns_none(self):
        (self.root / "bad.bin").write_bytes(b"\xff\xfe\x00")
        self.assertIsNone(parsers.read_engine_text(self.root / "bad.bin"))

    def test_load_jsonc_malformed_returns_none(self):
        (self.root / "bad.json").write_text("{not json")
        self.assertIsNone(parsers.load_jsonc(self.root / "bad.json"))

    def test_loads_jsonc_non_string_and_malformed(self):
        self.assertIsNone(parsers.loads_jsonc(None))
        self.assertIsNone(parsers.loads_jsonc("{not json"))


class TestStrictLoadJson(unittest.TestCase):
    def test_rejects_non_string_input(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json(123)

    def test_rejects_over_byte_cap(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json(" " * 32, max_bytes=8)

    def test_rejects_non_utf8_bytes(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json(b"\xff\xfe")

    def test_rejects_non_finite_numbers(self):
        for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.assertRaises(parsers.StrictJsonError):
                parsers.strict_load_json(text)

    def test_rejects_duplicate_keys(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json('{"a": 1, "a": 2}')

    def test_rejects_malformed_json(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json("{not json")

    def test_rejects_node_cap_after_count(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json("5", max_nodes=0)

    def test_rejects_node_cap_inside_dict(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json('{"a": 1, "b": 2}', max_nodes=2)

    def test_rejects_node_cap_inside_list(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json("[1, 2]", max_nodes=2)

    def test_rejects_depth_cap(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json("[[[1]]]", max_depth=2)

    def test_rejects_string_byte_cap(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json('{"key": "value"}', max_string_bytes=2)

    def test_require_object_root(self):
        with self.assertRaises(parsers.StrictJsonError):
            parsers.strict_load_json("[1]", require_object=True)
        self.assertEqual(parsers.strict_load_json('{"a": 1}', require_object=True),
                         {"a": 1})


if __name__ == "__main__":
    unittest.main()
