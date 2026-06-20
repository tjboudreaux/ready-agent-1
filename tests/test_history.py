"""Tests for repository identity, local history storage, resolution, and deltas."""
import json
import tempfile
import unittest
from pathlib import Path

from readiness import history


def _origin(url):
    return lambda project, args: url


def _no_origin():
    return lambda project, args: None


class TestRedactAndParse(unittest.TestCase):
    def test_redact_strips_credentials(self):
        self.assertEqual(
            history.redact_origin_url("https://user:tok@github.com/o/r.git"),
            "https://github.com/o/r.git",
        )

    def test_redact_leaves_clean_url(self):
        self.assertEqual(history.redact_origin_url("https://github.com/o/r.git"),
                         "https://github.com/o/r.git")

    def test_redact_strips_ssh_scheme_credentials(self):
        self.assertEqual(
            history.redact_origin_url("ssh://user:pass@gitlab.com/o/r.git"),
            "ssh://gitlab.com/o/r.git",
        )

    def test_parse_https(self):
        self.assertEqual(history.parse_origin("https://github.com/owner/repo.git"),
                         ("github.com", "owner", "repo"))

    def test_parse_https_with_token(self):
        self.assertEqual(history.parse_origin("https://x:y@github.com/owner/repo.git"),
                         ("github.com", "owner", "repo"))

    def test_parse_ssh_scheme(self):
        self.assertEqual(history.parse_origin("ssh://git@github.com/owner/repo.git"),
                         ("github.com", "owner", "repo"))

    def test_parse_scp(self):
        self.assertEqual(history.parse_origin("git@github.com:owner/repo.git"),
                         ("github.com", "owner", "repo"))

    def test_parse_scp_nested_non_github(self):
        self.assertEqual(history.parse_origin("git@gitlab.example.com:group/sub/repo.git"),
                         ("gitlab.example.com", "group/sub", "repo"))

    def test_parse_unparseable(self):
        self.assertEqual(history.parse_origin("not a url"), ("", "", ""))

    def test_parse_no_path(self):
        self.assertEqual(history.parse_origin("https://github.com/"), ("github.com", "", ""))


class TestRepoIdentity(unittest.TestCase):
    def test_origin_identity_no_secret_leak(self):
        ident = history.repo_identity("/tmp/x", git_runner=_origin("https://u:tok@github.com/o/r.git"))
        self.assertEqual(ident["identity_kind"], "origin")
        self.assertEqual(ident["host"], "github.com")
        self.assertEqual(ident["owner"], "o")
        self.assertEqual(ident["name"], "r")
        self.assertEqual(ident["redacted_origin_url"], "https://github.com/o/r.git")
        self.assertNotIn("tok", json.dumps(ident))
        self.assertTrue(ident["identity_hash"])

    def test_local_identity_no_abspath_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = history.repo_identity(tmp, git_runner=_no_origin())
        self.assertEqual(ident["identity_kind"], "local_path")
        self.assertIn("project_path_hash", ident)
        self.assertNotIn(str(tmp), json.dumps(ident))

    def test_require_origin_without_origin_returns_none(self):
        self.assertIsNone(history.repo_identity("/tmp/x", require_origin=True, git_runner=_no_origin()))

    def test_origin_and_local_hashes_differ(self):
        o = history.repo_identity("/tmp/x", git_runner=_origin("https://github.com/o/r.git"))
        with tempfile.TemporaryDirectory() as tmp:
            l = history.repo_identity(tmp, git_runner=_no_origin())
        self.assertNotEqual(o["identity_hash"], l["identity_hash"])


class TestPaths(unittest.TestCase):
    def test_primary_out(self):
        self.assertEqual(history.primary_out("/p", out="/o"), Path("/o"))
        self.assertEqual(history.primary_out("/p"), Path("/p/.agents/readiness"))

    def test_history_root(self):
        self.assertEqual(history.history_root("/p", history_dir="/h"), Path("/h"))
        self.assertEqual(history.history_root("/p", out="/o"), Path("/o/history"))
        self.assertEqual(history.history_root("/p"), Path("/p/.agents/readiness/history"))

    def test_safe_ts(self):
        self.assertEqual(history._safe_ts("2026-06-20T13:45:01+00:00"), "2026-06-20T13-45-01-00-00")
        self.assertEqual(history._safe_ts(""), "unknown")

    def test_now_iso(self):
        self.assertIn("T", history.now_iso())


class TestIndex(unittest.TestCase):
    def test_load_missing(self):
        self.assertEqual(history.load_index("/no/such/index.json"), [])

    def test_load_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "index.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(history.load_index(p), [])

    def test_load_non_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "index.json"
            p.write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(history.load_index(p), [])


def _report(project, *, schema="2", detector="0.3.0", level=2, generated_at="2026-06-20T00:00:00+00:00"):
    ident = history.repo_identity(project, git_runner=_no_origin())
    return {
        "schema_version": schema, "engine_version": "0.3.0", "registry_version": "0.3.0",
        "detector_version": detector, "generated_at": generated_at, "commit": "abc",
        "repository": ident,
        "score": {"level": level, "pass_rate": 1.0, "gating_passed": 5, "gating_total": 5},
        "results": [{"id": "docs.readme", "status": "pass"}],
    }


class TestStoreHistory(unittest.TestCase):
    def test_requires_identity(self):
        with self.assertRaises(ValueError):
            history.store_history({"repository": None}, "/tmp/x")

    def test_writes_snapshot_latest_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            rep = _report(tmp)
            paths = history.store_history(rep, tmp)
            self.assertTrue(paths["snapshot"].exists())
            self.assertTrue(paths["latest"].exists())
            index = json.loads(paths["index"].read_text())
            self.assertEqual(len(index), 1)
            self.assertEqual(index[0]["level"], 2)
            self.assertEqual(json.loads(paths["latest"].read_text())["commit"], "abc")

    def test_second_write_appends_and_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            rep1 = _report(tmp, generated_at="2026-06-20T00:00:00+00:00", level=2)
            rep2 = _report(tmp, generated_at="2026-06-20T00:00:00+00:00", level=3)  # same ts -> unique name
            p1 = history.store_history(rep1, tmp)
            p2 = history.store_history(rep2, tmp)
            self.assertNotEqual(p1["snapshot"].name, p2["snapshot"].name)  # immutable, no clobber
            index = json.loads(p2["index"].read_text())
            self.assertEqual(len(index), 2)


class TestResolveLatest(unittest.TestCase):
    def test_no_identity(self):
        report, reason = history.resolve_latest("/tmp/x", require_origin=True, git_runner=_no_origin())
        self.assertIsNone(report)
        self.assertIn("origin", reason)

    def test_no_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, reason = history.resolve_latest(tmp, git_runner=_no_origin())
            self.assertIsNone(report)
            self.assertIn("no readiness history", reason)

    def test_unreadable_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = history.repo_identity(tmp, git_runner=_no_origin())
            bucket = history.history_root(tmp) / ident["identity_hash"]
            bucket.mkdir(parents=True)
            (bucket / "index.json").write_text(json.dumps([{"timestamp": "t", "file": "gone.json"}]))
            report, reason = history.resolve_latest(tmp, git_runner=_no_origin())
            self.assertIsNone(report)
            self.assertIn("unreadable", reason)

    def test_schema_1_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            history.store_history(_report(tmp, schema="1"), tmp)
            report, reason = history.resolve_latest(tmp, git_runner=_no_origin())
            self.assertIsNone(report)
            self.assertIn("schema 2", reason)

    def test_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            history.store_history(_report(tmp, level=4), tmp)
            report, reason = history.resolve_latest(tmp, git_runner=_no_origin())
            self.assertEqual(reason, "")
            self.assertEqual(report["score"]["level"], 4)


class TestDelta(unittest.TestCase):
    def _r(self, **kw):
        base = {"schema_version": "2", "engine_version": "0.3.0", "registry_version": "0.3.0",
                "detector_version": "0.3.0",
                "score": {"level": 2, "gating_passed": 5, "gating_total": 6},
                "results": [{"id": "a", "status": "fail"}, {"id": "b", "status": "pass"}]}
        base.update(kw)
        return base

    def test_incomparable_on_version_mismatch(self):
        d = history.delta(self._r(engine_version="0.2.0"), self._r())
        self.assertFalse(d["comparable"])
        self.assertIn("engine_version", d["reason"])

    def test_comparable_tracks_changes(self):
        old = self._r(results=[{"id": "a", "status": "fail"}, {"id": "b", "status": "pass"}])
        new = self._r(results=[{"id": "a", "status": "pass"}, {"id": "b", "status": "unknown"}],
                      score={"level": 3, "gating_passed": 6, "gating_total": 6})
        d = history.delta(old, new)
        self.assertTrue(d["comparable"])
        self.assertFalse(d["detector_changed"])
        self.assertEqual(d["newly_passing"], ["a"])
        self.assertEqual(d["newly_unknown"], ["b"])
        self.assertEqual(d["score_delta"]["level"], {"from": 2, "to": 3})

    def test_detector_change_flagged(self):
        d = history.delta(self._r(), self._r(detector_version="0.4.0"))
        self.assertTrue(d["comparable"])
        self.assertTrue(d["detector_changed"])

    def test_newly_failing(self):
        old = self._r(results=[{"id": "a", "status": "pass"}])
        new = self._r(results=[{"id": "a", "status": "fail"}])
        self.assertEqual(history.delta(old, new)["newly_failing"], ["a"])


if __name__ == "__main__":
    unittest.main()
