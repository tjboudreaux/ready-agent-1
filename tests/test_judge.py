"""evals/judge.py: parse_judge guards unreachable through the stock JSON parser."""
import json
import unittest
from unittest import mock

from evals import judge


class TestParseJudgeGuards(unittest.TestCase):
    def test_non_dict_json_payload_returns_none(self):
        # The \{.*\} regex means the stock parser only ever yields a dict here; patch
        # json.loads to exercise the defensive non-dict guard directly.
        with mock.patch.object(json, "loads", return_value=[1, 2, 3]):
            self.assertIsNone(judge.parse_judge('{"a": 1}'))


if __name__ == "__main__":
    unittest.main()
