"""Tests for typed history sources, identity, storage, resolution, and deltas (0.11.0)."""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from readiness import history, model, version
from readiness.run import analyze

from ._util import make_repo, rmtree

GIT = shutil.which("git")

ORIGIN_URL = "https://user:secret@github.com/Owner/Repo.git"
TS1 = "2026-06-20T00:00:00+00:00"
TS2 = "2026-06-21T00:00:00+00:00"


def _git(root, *args):
    subprocess.run(["git", "-C", os.fspath(root), *args],
                   check=True, capture_output=True)


def _make_git_repo(files=None, *, origin=None):
    """A real git repository fixture with one commit; caller cleans up."""
    root = make_repo(files or {"README.md": "# fixture\n"})
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ra1-tests@example.invalid")
    _git(root, "config", "user.name", "RA1 Tests")
    if origin is not None:
        _git(root, "remote", "add", "origin", origin)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


def _schema3_report(root, **dep_kw):
    """One canonical schema-3 report dict from a real scan of ``root``."""
    from ._util import deps as _deps
    report = analyze(root, deps=_deps(**dep_kw) if dep_kw else None)
    data = report.to_dict()
    errors = model.validate_imported_report(data, "3")
    assert not errors, f"schema3 fixture invalid: {errors}"
    return data


def _schema2_result(cid, status, *, gating=True, level=1):
    return {
        "id": cid, "title": cid, "pillar": "Style & Validation", "level": level,
        "scope": "repo", "gating": gating, "status": status, "rationale": "",
        "evidence": [], "app_path": "", "fixable": False, "fix_kind": "",
        "passed_apps": 1, "evaluated_apps": 1,
    }


def _schema2_report(*, engine="0.10.0", registry="0.7.0", detector="0.6.0",
                    results=(), repository=None, generated_at=TS1):
    """A minimal dict that validates under ``model.validate_imported_report(d, "2")``."""
    results = list(results)
    gating = [r for r in results if r["gating"]]
    applicable = [r for r in gating if r["status"] not in ("skipped", "waived")]
    passed = [r for r in applicable if r["status"] == "pass"]
    return {
        "schema_version": "2",
        "engine_version": engine,
        "registry_version": registry,
        "detector_version": detector,
        "commit": "",
        "branch": "",
        "github_available": False,
        "generated_at": generated_at,
        "repository": repository,
        "detection": None,
        "score": {
            "level": 0,
            "level_name": "Ad hoc",
            "pass_rate": 0.0,
            "gating_passed": len(passed),
            "gating_total": len(applicable),
            "levels": [
                {"level": i, "name": f"L{i}", "passed": 0, "total": 0,
                 "ratio": 0.0, "achieved": False}
                for i in range(1, 6)
            ],
            "pillars": {},
            "recommendations": [],
        },
        "results": results,
        "advisory": [],
    }


def _write_legacy_history(root_path, identity_hash, snapshots):
    """Write an old-style history bucket: list-shaped index plus schema2 snapshots.

    Legacy roots hold per-identity buckets directly at the top level (no ``history/``
    intermediary); only current-mode roots use ``history/<identity_hash>``.
    """
    bucket = Path(root_path) / identity_hash
    bucket.mkdir(parents=True)
    entries = []
    for ts, filename, payload in snapshots:
        (bucket / filename).write_text(json.dumps(payload), encoding="utf-8")
        entries.append({"timestamp": ts, "file": filename})
    (bucket / "index.json").write_text(json.dumps(entries), encoding="utf-8")


class TempDirTestCase(unittest.TestCase):
    def _tmp(self):
        path = make_repo({})
        self.addCleanup(rmtree, path)
        return path


class TestRepoIdentity(TempDirTestCase):
    def test_origin_identity_no_secret_leak(self):
        if not GIT:
            self.skipTest("git required")
        root = _make_git_repo(origin=ORIGIN_URL)
        self.addCleanup(rmtree, root)
        ident = history.repo_identity(root)
        self.assertEqual(ident["identity_kind"], "origin")
        self.assertEqual(
            (ident["host"], ident["owner"], ident["name"]),
            ("github.com", "Owner", "Repo"),
        )
        self.assertRegex(ident["identity_hash"], r"\A[0-9a-f]{16}\Z")
        self.assertNotIn("user", json.dumps(ident))
        self.assertNotIn("secret", json.dumps(ident))

    def test_malformed_origin_never_falls_back(self):
        if not GIT:
            self.skipTest("git required")
        root = _make_git_repo(origin="https://github.com/")
        self.addCleanup(rmtree, root)
        self.assertIsNone(history.repo_identity(root))

    def test_local_path_identity_without_git(self):
        root = self._tmp()
        ident = history.repo_identity(root)
        self.assertEqual(ident["identity_kind"], "local_path")
        self.assertEqual(ident["name"], Path(os.path.realpath(root)).name)
        self.assertRegex(ident["identity_hash"], r"\A[0-9a-f]{16}\Z")

    def test_require_origin_without_origin(self):
        root = self._tmp()
        self.assertIsNone(history.repo_identity(root, require_origin=True))

    def test_distinct_repos_have_distinct_hashes(self):
        first, second = self._tmp(), self._tmp()
        self.assertNotEqual(
            history.repo_identity(first)["identity_hash"],
            history.repo_identity(second)["identity_hash"],
        )


class TestTypedSources(TempDirTestCase):
    def test_admit_missing_root_returns_none(self):
        tmp = self._tmp()
        self.assertIsNone(
            history.admit_history_source("current", tmp / "missing"))
        self.assertIsNone(
            history.admit_history_source("legacy", tmp / "missing"))

    def test_admit_existing_root(self):
        tmp = self._tmp()
        source = history.admit_history_source("legacy", tmp)
        self.assertIsNotNone(source)
        self.assertEqual(source.mode, "legacy")
        source.close()

    def test_invalid_mode_rejected(self):
        tmp = self._tmp()
        with self.assertRaises(ValueError):
            history.admit_history_source("bogus", tmp)

    def test_admit_or_create_current_source_creates_nested_root(self):
        tmp = self._tmp()
        out = tmp / "a" / "b" / "reports"
        source = history.admit_or_create_current_source(out)
        try:
            self.assertEqual(source.mode, "current")
            self.assertTrue(out.is_dir())
        finally:
            source.close()

    def test_current_source_path(self):
        project = self._tmp()
        self.assertEqual(history.current_source_path(project),
                         project / ".ra1" / "reports")
        self.assertEqual(history.current_source_path(project, out="custom"),
                         Path("custom"))


class TestStoreHistory(TempDirTestCase):
    def _source(self):
        out = self._tmp() / "reports"
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        return out, source

    def test_requires_current_mode(self):
        legacy_root = self._tmp()
        source = history.admit_history_source("legacy", legacy_root)
        self.addCleanup(source.close)
        report = _schema2_report()
        with self.assertRaises(ValueError):
            history.store_history(report, source)

    def test_requires_repository_identity(self):
        _out, source = self._source()
        with self.assertRaises(ValueError):
            history.store_history(_schema2_report(repository=None), source)

    def test_store_layout_and_index(self):
        root = self._tmp()
        report = _schema3_report(root)
        out, source = self._source()
        history.store_history(report, source)

        ih = report["repository"]["identity_hash"]
        bucket = out / "history" / ih
        self.assertTrue(bucket.is_dir())
        self.assertTrue((out / "latest.json").is_file())

        index = json.loads((bucket / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["version"], "2")
        self.assertEqual(len(index["entries"]), 1)
        entry = index["entries"][0]
        self.assertEqual(
            set(entry),
            {"timestamp", "file", "bytes", "sha256", "schema_version",
             "engine_version", "registry_version", "detector_version", "level",
             "pass_rate", "gating_passed", "gating_total", "commit"},
        )
        self.assertEqual(entry["schema_version"], "3")
        self.assertEqual(entry["engine_version"], version.ENGINE_VERSION)
        self.assertEqual(entry["timestamp"], report["generated_at"])
        snapshot = bucket / entry["file"]
        self.assertTrue(snapshot.is_file())
        stored = json.loads(snapshot.read_text(encoding="utf-8"))
        self.assertEqual(stored["generated_at"], report["generated_at"])
        latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["generated_at"], report["generated_at"])

    def test_multiple_snapshots_accumulate(self):
        root = self._tmp()
        first = _schema3_report(root, generated_at=TS1)
        second = _schema3_report(root, generated_at=TS2)
        _out, source = self._source()
        history.store_history(first, source)
        history.store_history(second, source)
        ih = first["repository"]["identity_hash"]
        index = json.loads((_out / "history" / ih / "index.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(len(index["entries"]), 2)
        timestamps = [e["timestamp"] for e in index["entries"]]
        self.assertEqual(timestamps, sorted(timestamps))
        latest = json.loads((_out / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["generated_at"], TS2)

    def test_history_cap_refuses_without_pruning(self):
        root = self._tmp()
        report = _schema3_report(root)
        _out, source = self._source()
        with mock.patch.object(history, "MAX_HISTORY_INDEX_ENTRIES", 1):
            history.store_history(report, source)
            with self.assertRaises(history.HistoryLimitError):
                history.store_history(_schema3_report(root, generated_at=TS2),
                                      source)


class TestListAndLoad(TempDirTestCase):
    def _source(self):
        out = self._tmp() / "reports"
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        return out, source

    def test_list_without_history(self):
        root = self._tmp()
        _out, source = self._source()
        listing, reason = history.list_history(source, root)
        self.assertIsNone(listing)
        self.assertEqual(reason, "no readiness history for this repository")

    def test_round_trip_list_load_latest(self):
        root = self._tmp()
        first = _schema3_report(root, generated_at=TS1)
        second = _schema3_report(root, generated_at=TS2)
        _out, source = self._source()
        history.store_history(first, source)
        history.store_history(second, source)

        listing, reason = history.list_history(source, root)
        self.assertEqual(reason, "")
        self.assertEqual(listing["repository"]["identity_hash"],
                         first["repository"]["identity_hash"])
        self.assertEqual(len(listing["entries"]), 2)
        for entry in listing["entries"]:
            self.assertEqual(entry["commit_status"], "committed")
            self.assertIn("id", entry)

        first_id = listing["entries"][0]["id"]
        snap = history.load_snapshot(source, root, first_id)
        self.assertEqual(snap["generated_at"], TS1)
        latest = history.load_snapshot(source, root, "latest")
        self.assertEqual(latest["generated_at"], TS2)

    def test_load_unknown_or_invalid_id(self):
        root = self._tmp()
        report = _schema3_report(root)
        _out, source = self._source()
        history.store_history(report, source)
        self.assertIsNone(history.load_snapshot(source, root, "missing"))
        self.assertIsNone(history.load_snapshot(source, root, "../escape"))
        self.assertIsNone(history.load_snapshot(source, root, ""))


class TestLegacySource(TempDirTestCase):
    def _legacy(self, snapshots):
        project = self._tmp()
        legacy_root = self._tmp()
        ih = history.repo_identity(project)["identity_hash"]
        _write_legacy_history(legacy_root, ih, snapshots)
        source = history.admit_history_source("legacy", legacy_root)
        self.addCleanup(source.close)
        return project, source

    def test_list_legacy_entries(self):
        snap = _schema2_report()
        project, source = self._legacy([
            (TS1, "2026-06-20T00-00-00-00-00.json", snap),
            (TS2, "2026-06-21T00-00-00-00-00.json", snap),
        ])
        listing, reason = history.list_history(source, project)
        self.assertEqual(reason, "")
        self.assertEqual(len(listing["entries"]), 2)
        self.assertEqual([e["commit_status"] for e in listing["entries"]],
                         ["legacy_uncommitted", "legacy_uncommitted"])
        self.assertEqual(listing["entries"][0]["id"],
                         "2026-06-20T00-00-00-00-00")

    def test_load_legacy_snapshot_by_id(self):
        snap = _schema2_report()
        project, source = self._legacy([
            (TS1, "2026-06-20T00-00-00-00-00.json", snap),
        ])
        loaded = history.load_snapshot(source, project,
                                       "2026-06-20T00-00-00-00-00")
        self.assertEqual(loaded["schema_version"], "2")
        self.assertEqual(loaded["engine_version"], "0.10.0")

    def test_legacy_never_resolves_latest(self):
        project, source = self._legacy([
            (TS1, "2026-06-20T00-00-00-00-00.json", _schema2_report()),
        ])
        report, reason = history.resolve_latest(source, project)
        self.assertIsNone(report)
        self.assertEqual(reason, "latest resolves only from a current reports root")

    def test_legacy_malformed_index_unreadable(self):
        project = self._tmp()
        legacy_root = self._tmp()
        ih = history.repo_identity(project)["identity_hash"]
        bucket = Path(legacy_root) / ih
        bucket.mkdir(parents=True)
        (bucket / "index.json").write_text('{"not":"a list"}', encoding="utf-8")
        source = history.admit_history_source("legacy", legacy_root)
        self.addCleanup(source.close)
        listing, reason = history.list_history(source, project)
        self.assertIsNone(listing)
        self.assertEqual(reason, "history index unreadable")


class TestResolveLatest(TempDirTestCase):
    def test_no_history(self):
        root = self._tmp()
        out = self._tmp() / "reports"
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        report, reason = history.resolve_latest(source, root)
        self.assertIsNone(report)
        self.assertEqual(reason, "no readiness history for this repository")

    def test_resolves_current_schema(self):
        root = self._tmp()
        stored = _schema3_report(root)
        out = self._tmp() / "reports"
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        history.store_history(stored, source)
        report, reason = history.resolve_latest(source, root)
        self.assertEqual(reason, "")
        self.assertEqual(report["generated_at"], stored["generated_at"])
        self.assertEqual(report["schema_version"], "3")

    def test_rejects_schema2_snapshot_with_rerun_wording(self):
        root = self._tmp()
        identity = history.repo_identity(root)
        legacy_report = _schema2_report(repository=identity)
        out = self._tmp() / "reports"
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        history.store_history(legacy_report, source)
        report, reason = history.resolve_latest(source, root)
        self.assertIsNone(report)
        self.assertEqual(
            reason,
            f"stored report schema 2; current schema {version.SCHEMA_VERSION}; rerun",
        )


class TestDelta(TempDirTestCase):
    def test_invalid_inputs(self):
        self.assertEqual(history.delta(None, {})["reason"], "invalid old report")
        bad_old = {"schema_version": "9"}
        self.assertEqual(history.delta(bad_old, {})["reason"],
                         "invalid old report")

    def test_invalid_new_report(self):
        old = _schema2_report()
        new = {"schema_version": "2", "engine_version": "0.10.0"}
        self.assertEqual(history.delta(old, new)["reason"], "invalid new report")

    def test_cross_schema_incomparable(self):
        root = self._tmp()
        old = _schema2_report()
        new = _schema3_report(root)
        result = history.delta(old, new)
        self.assertFalse(result["comparable"])
        # Contract: schema 2<->3 is exactly "version mismatch: schema_version".
        self.assertEqual(result["reason"], "version mismatch: schema_version")

    def test_schema2_engine_mismatch(self):
        old = _schema2_report(engine="0.9.1", registry="0.7.0", detector="0.5.0")
        new = _schema2_report(engine="0.10.0", registry="0.7.0", detector="0.5.0")
        result = history.delta(old, new)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "version mismatch: engine_version")

    def test_schema2_comparable_delta_has_no_detector_changed(self):
        old = _schema2_report(results=[_schema2_result("a", "pass")])
        new = _schema2_report(results=[_schema2_result("a", "fail")],
                              generated_at=TS2)
        result = history.delta(old, new)
        self.assertTrue(result["comparable"])
        self.assertNotIn("detector_changed", result)
        self.assertEqual(result["criteria_changes"],
                         [{"id": "a", "from": "pass", "to": "fail"}])
        self.assertEqual(result["newly_failing"], ["a"])
        self.assertEqual(result["newly_passing"], [])
        self.assertEqual(result["score_delta"]["gating_passed"],
                         {"from": 1, "to": 0})

    def test_schema3_identity_mismatch(self):
        first_root, second_root = self._tmp(), self._tmp()
        old = _schema3_report(first_root)
        new = _schema3_report(second_root)
        result = history.delta(old, new)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "repository identity mismatch")

    def test_schema3_injected_inputs_noncanonical(self):
        root = self._tmp()
        old = _schema3_report(root)
        new = _schema3_report(root, generated_at="2026-08-01T00:00:00+00:00")
        self.assertEqual(
            new["assessment_provenance"]["invocation"]["inputs"]["profile"],
            "injected",
        )
        result = history.delta(old, new)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "assessment inputs noncanonical")

    def test_schema3_requires_git_collector_for_ancestry(self):
        if not GIT:
            self.skipTest("git required")
        root = _make_git_repo()
        self.addCleanup(rmtree, root)
        old = _schema3_report(root)
        _git(root, "commit", "-qm", "second", "--allow-empty")
        new = _schema3_report(root)
        result = history.delta(old, new)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "commit ancestry not proven")

    def test_schema3_comparable_across_commits(self):
        if not GIT:
            self.skipTest("git required")
        from readiness.collectors.git import GitCollector
        root = _make_git_repo()
        self.addCleanup(rmtree, root)
        old = _schema3_report(root)
        _git(root, "commit", "-qm", "second", "--allow-empty")
        new = _schema3_report(root)
        collector = GitCollector(root)
        try:
            result = history.delta(old, new, git_collector=collector)
        finally:
            collector.close()
        self.assertTrue(result["comparable"], result.get("reason"))
        self.assertNotIn("detector_changed", result)
        self.assertIn("criteria_changes", result)
        self.assertEqual(result["score_delta"]["gating_total"]["from"],
                         result["score_delta"]["gating_total"]["to"])


if __name__ == "__main__":
    unittest.main()
