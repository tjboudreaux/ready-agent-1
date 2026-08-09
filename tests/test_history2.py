"""Branch-focused tests for readiness.history: typed sources, storage, manifests,
index validation, reader locks, and every schema-3 delta incomparability reason.

Complements test_history.py; schema-3 fixtures come from real ``analyze`` scans.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from readiness import history, safe_io
from readiness.run import analyze

from ._util import make_repo, rmtree

GIT = shutil.which("git")

TS1 = "2026-06-20T00:00:00+00:00"
TS2 = "2026-06-21T00:00:00+00:00"


def _git(root, *args):
    subprocess.run(["git", "-C", os.fspath(root), *args],
                   check=True, capture_output=True)


def _make_git_repo():
    root = make_repo({"README.md": "# fixture\n"})
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ra1-tests@example.invalid")
    _git(root, "config", "user.name", "RA1 Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


def _index_entry(file="snap.json", **over):
    entry = {"timestamp": TS1, "file": file, "bytes": 10, "sha256": "a" * 64,
             "schema_version": "3", "engine_version": "0.11.0",
             "registry_version": "0.8.0", "detector_version": "0.6.0",
             "level": 1, "pass_rate": 0.5, "gating_passed": 1, "gating_total": 2,
             "commit": ""}
    entry.update(over)
    return entry


def _write_generation(root: Path, files: dict):
    """Write ``files`` plus a .commit.json binding their exact bytes/digests."""
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "version": "1",
        "files": [
            {"path": rel, "bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for rel, data in sorted(files.items())
        ],
    }
    (root / ".commit.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")


def _report_dict(identity, generated_at=TS1):
    """Minimal report dict accepted by store_history/plan_history_write."""
    return {"repository": identity, "generated_at": generated_at,
            "schema_version": "3", "commit": ""}


class _NoIdentityCollector:
    def origin_malformed(self):
        return True

    def origin_identity(self):
        return None


class TempDirTestCase(unittest.TestCase):
    def _tmp(self):
        path = make_repo({})
        self.addCleanup(rmtree, path)
        return path


# --------------------------------------------------------------------------- typed sources
class TestHistorySourceContract(TempDirTestCase):
    def test_root_authority_type_enforced(self):
        with self.assertRaises(ValueError):
            history.HistorySource("current", object())

    def test_admit_rejects_a_plain_file(self):
        root = self._tmp()
        (root / "afile").write_text("x", encoding="utf-8")
        self.assertIsNone(history.admit_history_source("current", root / "afile"))


class TestAdmitOrCreateRoot(TempDirTestCase):
    def test_depth_cap_refused(self):
        root = self._tmp()
        deep = root.joinpath(*[f"d{i}" for i in range(9)])
        with self.assertRaises(safe_io.RepositoryInputError) as ctx:
            history.admit_or_create_root(deep)
        self.assertIn("depth cap", str(ctx.exception))

    def test_no_existing_ancestor_refused(self):
        with mock.patch.object(history.safe_io, "acquire_root",
                               side_effect=FileNotFoundError):
            with self.assertRaises(safe_io.RepositoryInputError) as ctx:
                history.admit_or_create_root("/nonexistent/ra1/deep/root")
        self.assertIn("no existing ancestor", str(ctx.exception))

    def test_creation_failure_closes_and_propagates(self):
        root = self._tmp()
        with mock.patch.object(history.safe_io, "ensure_rooted_directory",
                               side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                history.admit_or_create_root(root / "missing")


# --------------------------------------------------------------------------- storage
class TestStoreHistoryBranches(TempDirTestCase):
    def _source(self, out):
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        return source

    def _identity(self, root):
        return history.repo_identity(root)

    def test_same_timestamp_snapshots_get_unique_names(self):
        root, out = self._tmp(), self._tmp() / "reports"
        source = self._source(out)
        identity = self._identity(root)
        first = history.store_history(_report_dict(identity), source,
                                      write_latest=False)
        second = history.store_history(_report_dict(identity), source,
                                       write_latest=False)
        first_name = Path(first[0][0]).name
        second_name = Path(second[0][0]).name
        self.assertNotEqual(first_name, second_name)
        self.assertTrue(second_name.endswith("-1.json"), second_name)
        # write_latest=False: no latest.json is written
        self.assertFalse((out / "latest.json").exists())

    def test_invalid_index_refuses_write(self):
        root, out = self._tmp(), self._tmp() / "reports"
        source = self._source(out)
        identity = self._identity(root)
        history.store_history(_report_dict(identity), source)
        bucket = out / "history" / identity["identity_hash"]
        (bucket / "index.json").write_bytes(b"garbage")
        with self.assertRaises(ValueError) as ctx:
            history.store_history(_report_dict(identity, TS2), source)
        self.assertIn("history index unreadable", str(ctx.exception))

    def test_existing_snapshot_file_refuses_overwrite(self):
        root, out = self._tmp(), self._tmp() / "reports"
        source = self._source(out)
        identity = self._identity(root)
        bucket = out / "history" / identity["identity_hash"]
        bucket.mkdir(parents=True)
        # an untracked file colliding with the would-be snapshot name
        (bucket / (history._safe_ts(TS1) + ".json")).write_bytes(b"junk")
        with self.assertRaises(ValueError) as ctx:
            history.store_history(_report_dict(identity), source)
        self.assertIn("already exists", str(ctx.exception))


# --------------------------------------------------------------------------- index validation
class TestLoadIndex(TempDirTestCase):
    def _bucket(self, files=None):
        root = self._tmp()
        for rel, data in (files or {}).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, bytes):
                path.write_bytes(data)
            else:
                path.write_text(data, encoding="utf-8")
        auth = safe_io.acquire_root(root)
        self.addCleanup(auth.close)
        return root, auth

    def _index_json(self, entries):
        return json.dumps({"version": "2", "entries": entries})

    def test_missing_index_is_none(self):
        _, auth = self._bucket()
        self.assertIsNone(history._load_index(auth))

    def test_unreadable_index_is_invalid(self):
        root, auth = self._bucket()
        (root / "index.json").mkdir()  # a directory is not a readable regular file
        self.assertEqual(history._load_index(auth), "invalid")

    def test_malformed_json_is_invalid(self):
        _, auth = self._bucket({"index.json": b"{not json"})
        self.assertEqual(history._load_index(auth), "invalid")

    def test_wrong_version_or_shape_is_invalid(self):
        _, auth = self._bucket({"index.json": self._index_json([]).replace('"2"', '"1"')})
        self.assertEqual(history._load_index(auth), "invalid")
        _, auth = self._bucket({"index.json": json.dumps({"version": "2",
                                                          "entries": {}})})
        self.assertEqual(history._load_index(auth), "invalid")

    def test_duplicate_files_are_invalid(self):
        entries = [_index_entry("a.json"), _index_entry("a.json")]
        _, auth = self._bucket({"index.json": self._index_json(entries)})
        self.assertEqual(history._load_index(auth), "invalid")

    def test_valid_index_is_sorted(self):
        entries = [_index_entry("b.json", timestamp=TS2),
                   _index_entry("a.json", timestamp=TS1)]
        _, auth = self._bucket({"index.json": self._index_json(entries)})
        loaded = history._load_index(auth)
        self.assertEqual([e["file"] for e in loaded], ["a.json", "b.json"])

    def test_invalid_entry_variants(self):
        variants = {
            "entry not a dict": ["x"],
            "missing key": [_index_entry(commit=None)],
            "empty timestamp": [_index_entry(timestamp="")],
            "bad file grammar": [_index_entry(file="bad name.json")],
            "bytes out of range": [_index_entry(bytes=0)],
            "bad sha256": [_index_entry(sha256="zz")],
            "level not int": [_index_entry(level="1")],
            "pass_rate bool": [_index_entry(pass_rate=True)],
            "gating_passed not int": [_index_entry(gating_passed="x")],
            "version too long": [_index_entry(engine_version="v" * 33)],
            "commit not hex": [_index_entry(commit="xyz")],
        }
        # "missing key" needs an actual absent key, not None
        broken = _index_entry()
        del broken["commit"]
        variants["missing key"] = [broken]
        for label, entries in variants.items():
            with self.subTest(label=label):
                _, auth = self._bucket({"index.json": self._index_json(entries)})
                self.assertEqual(history._load_index(auth), "invalid")


class TestLoadLegacyIndex(TempDirTestCase):
    def _bucket(self, content):
        root = self._tmp()
        if content is not None:
            (root / "index.json").write_text(content, encoding="utf-8")
        auth = safe_io.acquire_root(root)
        self.addCleanup(auth.close)
        return root, auth

    def test_missing_is_none(self):
        _, auth = self._bucket(None)
        self.assertIsNone(history._load_legacy_index(auth))

    def test_unreadable_is_invalid(self):
        root, auth = self._bucket(None)
        (root / "index.json").mkdir()
        self.assertEqual(history._load_legacy_index(auth), "invalid")

    def test_malformed_json_is_invalid(self):
        _, auth = self._bucket("{nope")
        self.assertEqual(history._load_legacy_index(auth), "invalid")

    def test_non_list_is_invalid(self):
        _, auth = self._bucket(json.dumps({"version": "2", "entries": []}))
        self.assertEqual(history._load_legacy_index(auth), "invalid")

    def test_bad_entries_are_invalid(self):
        _, auth = self._bucket(json.dumps(["x"]))
        self.assertEqual(history._load_legacy_index(auth), "invalid")
        _, auth = self._bucket(json.dumps([{"file": "bad name.json"}]))
        self.assertEqual(history._load_legacy_index(auth), "invalid")

    def test_valid_entries_sorted(self):
        content = json.dumps([{"timestamp": TS2, "file": "b.json"},
                              {"timestamp": TS1, "file": "a.json"}])
        _, auth = self._bucket(content)
        loaded = history._load_legacy_index(auth)
        self.assertEqual([e["file"] for e in loaded], ["a.json", "b.json"])


class TestReadSnapshot(TempDirTestCase):
    def test_missing_and_malformed(self):
        root = self._tmp()
        (root / "bad.json").write_text("{nope", encoding="utf-8")
        auth = safe_io.acquire_root(root)
        self.addCleanup(auth.close)
        self.assertIsNone(history._read_snapshot(auth, "missing.json"))
        self.assertIsNone(history._read_snapshot(auth, "bad.json"))


# --------------------------------------------------------------------------- reader lock
class TestReaderLock(TempDirTestCase):
    def test_busy_writer_blocks_every_reader(self):
        root, out = self._tmp(), self._tmp() / "reports"
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        fd = os.open(os.fspath(out), os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.assertTrue(safe_io.lock_directory(fd, exclusive=True))
            listing, reason = history.list_history(source, root)
            self.assertIsNone(listing)
            self.assertEqual(reason,
                             "persistence busy; retry after the active writer finishes")
            self.assertIsNone(history.load_snapshot(source, root, "latest"))
            report, reason = history.resolve_latest(source, root)
            self.assertIsNone(report)
            self.assertEqual(reason,
                             "persistence busy; retry after the active writer finishes")
        finally:
            safe_io.unlock_directory(fd)
            os.close(fd)

    def test_incomplete_generation_blocks_readers(self):
        root, out = self._tmp(), self._tmp() / "reports"
        out.mkdir()
        (out / "report.json").write_text("{}", encoding="utf-8")  # no manifest
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        listing, reason = history.list_history(source, root)
        self.assertIsNone(listing)
        self.assertEqual(reason, "persistence.incomplete")


# -------------------------------------------------------------------------- list/load/resolve edges
class TestListLoadResolveEdges(TempDirTestCase):
    def _source(self, out):
        source = history.admit_or_create_current_source(out)
        self.addCleanup(source.close)
        return source

    def test_identity_unavailable(self):
        root, out = self._tmp(), self._tmp() / "reports"
        source = self._source(out)
        stub = _NoIdentityCollector()
        listing, reason = history.list_history(source, root, git_collector=stub)
        self.assertIsNone(listing)
        self.assertEqual(reason, "no repository identity (origin remote required)")
        self.assertIsNone(
            history.load_snapshot(source, root, "latest", git_collector=stub))
        report, reason = history.resolve_latest(source, root, git_collector=stub)
        self.assertIsNone(report)
        self.assertEqual(reason, "no repository identity (origin remote required)")

    def test_load_without_bucket(self):
        root, out = self._tmp(), self._tmp() / "reports"
        source = self._source(out)
        self.assertIsNone(history.load_snapshot(source, root, "latest"))

    def test_load_legacy_with_malformed_index(self):
        root = self._tmp()
        identity = history.repo_identity(root)
        legacy_root = self._tmp()
        bucket = legacy_root / identity["identity_hash"]
        bucket.mkdir(parents=True)
        (bucket / "index.json").write_text("{nope", encoding="utf-8")
        source = history.admit_history_source("legacy", legacy_root)
        self.addCleanup(source.close)
        self.assertIsNone(history.load_snapshot(source, root, "latest"))

    def test_resolve_with_malformed_index(self):
        root = self._tmp()
        identity = history.repo_identity(root)
        ih = identity["identity_hash"]
        out = self._tmp()
        _write_generation(out, {f"history/{ih}/index.json": b"garbage"})
        source = history.admit_history_source("current", out)
        self.addCleanup(source.close)
        report, reason = history.resolve_latest(source, root)
        self.assertIsNone(report)
        self.assertEqual(reason, "history index unreadable")

    def test_resolve_with_empty_index(self):
        root = self._tmp()
        identity = history.repo_identity(root)
        ih = identity["identity_hash"]
        out = self._tmp()
        _write_generation(out, {f"history/{ih}/index.json":
                                b'{"version": "2", "entries": []}'})
        source = history.admit_history_source("current", out)
        self.addCleanup(source.close)
        report, reason = history.resolve_latest(source, root)
        self.assertIsNone(report)
        self.assertEqual(reason, "no readiness history for this repository")

    def test_resolve_with_unreadable_snapshot(self):
        root = self._tmp()
        identity = history.repo_identity(root)
        ih = identity["identity_hash"]
        entry = _index_entry("snap.json")
        out = self._tmp()
        _write_generation(out, {
            f"history/{ih}/index.json":
                json.dumps({"version": "2", "entries": [entry]}).encode("utf-8"),
            f"history/{ih}/snap.json": b"{not json",
        })
        source = history.admit_history_source("current", out)
        self.addCleanup(source.close)
        report, reason = history.resolve_latest(source, root)
        self.assertIsNone(report)
        self.assertEqual(reason, "history snapshot unreadable")

    def test_resolve_with_missing_snapshot_file(self):
        root = self._tmp()
        identity = history.repo_identity(root)
        ih = identity["identity_hash"]
        entry = _index_entry("ghost.json")
        out = self._tmp()
        # the manifest declares only the index; the indexed snapshot does not exist
        _write_generation(out, {
            f"history/{ih}/index.json":
                json.dumps({"version": "2", "entries": [entry]}).encode("utf-8"),
        })
        source = history.admit_history_source("current", out)
        self.addCleanup(source.close)
        report, reason = history.resolve_latest(source, root)
        self.assertIsNone(report)
        self.assertEqual(reason, "history snapshot unreadable")


# --------------------------------------------------------------------- schema-3 delta scope reasons
class TestSchema3ScopeReasons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._root = make_repo({"README.md": "# fixture\n"})
        cls._base = analyze(cls._root).to_dict()

    @classmethod
    def tearDownClass(cls):
        rmtree(cls._root)

    def _pair(self):
        old = copy.deepcopy(self._base)
        new = copy.deepcopy(self._base)
        old["generated_at"] = TS1
        new["generated_at"] = TS2
        # a non-git fixture scan records git collection incomplete; normalize to a
        # complete absent-Git scope so later preconditions are exercised in isolation
        for report in (old, new):
            report["assessment_provenance"]["invocation"]["git"][
                "collection_complete"] = True
        return old, new

    def _reason(self, old, new, git_collector=None):
        result = history.delta(old, new, git_collector=git_collector)
        self.assertFalse(result["comparable"])
        return result["reason"]

    def test_identity_unavailable(self):
        old, new = self._pair()
        old["repository"] = None
        self.assertEqual(self._reason(old, new), "repository identity unavailable")

    def test_order_invalid(self):
        old, new = self._pair()
        old["generated_at"], new["generated_at"] = TS2, TS1
        self.assertEqual(self._reason(old, new), "assessment order invalid")
        old, new = self._pair()
        old["generated_at"] = "not-a-date"
        self.assertEqual(self._reason(old, new), "assessment order invalid")

    def test_static_incomplete(self):
        old, new = self._pair()
        old["assessment_provenance"]["invocation"]["static"][
            "collection_complete"] = False
        self.assertEqual(self._reason(old, new), "evidence scope incomplete: static")

    def test_git_presence_mismatch(self):
        old, new = self._pair()
        old["assessment_provenance"]["invocation"]["git"][
            "metadata_profile"] = "absent"
        new["assessment_provenance"]["invocation"]["git"][
            "metadata_profile"] = "primary"
        self.assertEqual(self._reason(old, new),
                         "evidence scope mismatch: git presence")

    def test_git_incomplete(self):
        old, new = self._pair()
        old["assessment_provenance"]["invocation"]["git"][
            "collection_complete"] = False
        self.assertEqual(self._reason(old, new), "evidence scope incomplete: git")

    def test_github_requested_mismatch(self):
        old, new = self._pair()
        new["assessment_provenance"]["invocation"]["github"]["requested"] = True
        self.assertEqual(self._reason(old, new),
                         "evidence scope mismatch: github requested")

    def test_github_incomplete(self):
        old, new = self._pair()
        for report in (old, new):
            report["assessment_provenance"]["invocation"]["github"][
                "requested"] = True
        new["assessment_provenance"]["invocation"]["github"][
            "collection_complete"] = False
        self.assertEqual(self._reason(old, new),
                         "evidence scope incomplete: github")

    def test_execution_requested_mismatch(self):
        old, new = self._pair()
        new["assessment_provenance"]["invocation"]["execution"].update(
            {"requested": True, "completed": True, "successful": True})
        self.assertEqual(self._reason(old, new),
                         "evidence scope mismatch: execution requested")

    def test_execution_timeout_mismatch(self):
        old, new = self._pair()
        for report, timeout in ((old, 120), (new, 60)):
            report["assessment_provenance"]["invocation"]["execution"].update(
                {"requested": True, "completed": True, "successful": True,
                 "timeout_seconds": timeout})
        self.assertEqual(self._reason(old, new),
                         "evidence scope mismatch: execution timeout")

    def test_execution_unsuccessful(self):
        old, new = self._pair()
        for report in (old, new):
            report["assessment_provenance"]["invocation"]["execution"].update(
                {"requested": True, "completed": True, "successful": False,
                 "timeout_seconds": 120})
        self.assertEqual(self._reason(old, new),
                         "evidence scope incomplete: execution")

    def test_execution_complete_proceeds_to_ancestry(self):
        old, new = self._pair()
        for report in (old, new):
            report["assessment_provenance"]["invocation"]["execution"].update(
                {"requested": True, "completed": True, "successful": True,
                 "timeout_seconds": 120})
        # execution scope passes; the empty fixture commits cannot prove ancestry
        self.assertEqual(self._reason(old, new), "commit ancestry not proven")

    def test_empty_generated_at_is_order_invalid(self):
        old, new = self._pair()
        old["generated_at"] = ""
        self.assertEqual(self._reason(old, new), "assessment order invalid")

    def _commits(self, old, new, old_commit, new_commit):
        for report, commit in ((old, old_commit), (new, new_commit)):
            report["commit"] = commit
            report["assessment_provenance"]["subject"]["commit"] = commit

    def test_invalid_commit_ids(self):
        old, new = self._pair()
        self._commits(old, new, "zzzz", "b" * 40)
        self.assertEqual(self._reason(old, new), "commit ancestry not proven")
        old, new = self._pair()
        self._commits(old, new, "a" * 40, "b" * 64)  # length mismatch
        self.assertEqual(self._reason(old, new), "commit ancestry not proven")

    def test_no_git_collector_no_ancestry(self):
        old, new = self._pair()
        self._commits(old, new, "a" * 40, "b" * 40)
        self.assertEqual(self._reason(old, new), "commit ancestry not proven")

    def test_ancestry_negative_or_unreadable(self):
        old, new = self._pair()
        self._commits(old, new, "a" * 40, "b" * 40)
        for obs in (SimpleNamespace(state="present", value=False),
                    SimpleNamespace(state="unreadable", value=None)):
            with self.subTest(obs=obs):
                stub = SimpleNamespace(is_ancestor=lambda o, n, _obs=obs: _obs)
                self.assertEqual(self._reason(old, new, git_collector=stub),
                                 "commit ancestry not proven")

    def test_ancestry_positive_with_real_git(self):
        if not GIT:
            self.skipTest("git required")
        from readiness.collectors.git import GitCollector
        root = _make_git_repo()
        self.addCleanup(rmtree, root)
        old = analyze(root).to_dict()
        _git(root, "commit", "-qm", "second", "--allow-empty")
        new = analyze(root).to_dict()
        collector = GitCollector(root)
        try:
            result = history.delta(old, new, git_collector=collector)
        finally:
            collector.close()
        self.assertTrue(result["comparable"], result.get("reason"))
        self.assertIn("criteria_changes", result)


# --------------------------------------------------------------------------- generation validation
class TestValidateGeneration(TempDirTestCase):
    def _auth(self, root):
        auth = safe_io.acquire_root(root)
        self.addCleanup(auth.close)
        return auth

    def test_empty_dir_is_a_valid_first_generation(self):
        self.assertEqual(history.validate_generation(self._auth(self._tmp())), "")

    def test_committed_generation_validates(self):
        out = self._tmp()
        _write_generation(out, {"report.json": b"{}"})
        self.assertEqual(history.validate_generation(self._auth(out)), "")

    def test_stray_files_without_manifest_are_incomplete(self):
        out = self._tmp()
        (out / "report.json").write_text("{}", encoding="utf-8")
        self.assertEqual(history.validate_generation(self._auth(out)),
                         "persistence.incomplete")

    def test_unreadable_manifest_is_incomplete(self):
        out = self._tmp()
        (out / ".commit.json").mkdir()
        self.assertEqual(history.validate_generation(self._auth(out)),
                         "persistence.incomplete")

    def test_malformed_manifest_is_incomplete(self):
        out = self._tmp()
        (out / ".commit.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(history.validate_generation(self._auth(out)),
                         "persistence.incomplete")

    def test_manifest_shape(self):
        for manifest in ({"version": "2", "files": []},
                         {"version": "1", "files": {}},
                         {"version": "1", "files": ["x"]},
                         {"version": "1", "files": [{"path": 1}]}):
            with self.subTest(manifest=manifest):
                out = self._tmp()
                (out / ".commit.json").write_text(json.dumps(manifest),
                                                  encoding="utf-8")
                self.assertEqual(history.validate_generation(self._auth(out)),
                                 "persistence.incomplete")

    def test_declared_file_digest_mismatch(self):
        out = self._tmp()
        (out / "report.json").write_bytes(b"{}")
        manifest = {"version": "1", "files": [
            {"path": "report.json", "bytes": 2, "sha256": "0" * 64}]}
        (out / ".commit.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(history.validate_generation(self._auth(out)),
                         "persistence.incomplete")

    def test_declared_file_missing(self):
        out = self._tmp()
        manifest = {"version": "1", "files": [
            {"path": "report.json", "bytes": 2, "sha256": "0" * 64}]}
        (out / ".commit.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(history.validate_generation(self._auth(out)),
                         "persistence.incomplete")

    def test_undeclared_extra_file_is_incomplete(self):
        out = self._tmp()
        _write_generation(out, {"report.json": b"{}"})
        (out / "latest.json").write_text("{}", encoding="utf-8")  # undeclared
        self.assertEqual(history.validate_generation(self._auth(out)),
                         "persistence.incomplete")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "permission bits do not constrain root")
    def test_unreadable_inventory_is_incomplete(self):
        out = self._tmp()
        _write_generation(out, {"report.json": b"{}"})
        blocked = out / "history"
        blocked.mkdir()
        blocked.chmod(0o000)
        self.addCleanup(blocked.chmod, 0o755)
        self.assertEqual(history.validate_generation(self._auth(out)),
                         "persistence.incomplete")


# --------------------------------------------------------------------------- history manifest
class TestHistoryManifest(TempDirTestCase):
    def _auth(self, root):
        auth = safe_io.acquire_root(root)
        self.addCleanup(auth.close)
        return auth

    def _bucket(self, out, name="b" * 16):
        bucket = out / "history" / name
        bucket.mkdir(parents=True)
        return bucket

    def test_empty_root_has_empty_inventory(self):
        self.assertEqual(history.history_manifest(self._auth(self._tmp())), [])

    def test_invalid_index_refused(self):
        out = self._tmp()
        bucket = self._bucket(out)
        (bucket / "index.json").write_text("garbage", encoding="utf-8")
        with self.assertRaises(safe_io.RepositoryInputError):
            history.history_manifest(self._auth(out))

    def test_missing_snapshot_refused(self):
        out = self._tmp()
        bucket = self._bucket(out)
        index = {"version": "2", "entries": [_index_entry("ghost.json")]}
        (bucket / "index.json").write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaises(safe_io.RepositoryInputError) as ctx:
            history.history_manifest(self._auth(out))
        self.assertIn("snapshot unreadable", str(ctx.exception))

    def test_index_reread_failure_refused(self):
        out = self._tmp()
        bucket = self._bucket(out)
        (bucket / "snap.json").write_text("{}", encoding="utf-8")
        index = {"version": "2", "entries": [_index_entry("snap.json", bytes=2)]}
        (bucket / "index.json").write_text(json.dumps(index), encoding="utf-8")
        real_read = safe_io.read_rooted_regular
        calls = {"index": 0}

        def fake_read(auth, relpath, **kw):
            if relpath == "index.json":
                calls["index"] += 1
                if calls["index"] >= 2:
                    return safe_io.RootedBytesObservation(
                        safe_io.RepoReadState.UNREADABLE, reason_code="io_error")
            return real_read(auth, relpath, **kw)

        with mock.patch.object(history.safe_io, "read_rooted_regular", fake_read):
            with self.assertRaises(safe_io.RepositoryInputError):
                history.history_manifest(self._auth(out))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "permission bits do not constrain root")
    def test_unreadable_inventory_refused(self):
        out = self._tmp()
        blocked = out / "history"
        blocked.mkdir()
        blocked.chmod(0o000)
        self.addCleanup(blocked.chmod, 0o755)
        with self.assertRaises(safe_io.RepositoryInputError) as ctx:
            history.history_manifest(self._auth(out))
        self.assertIn("inventory unreadable", str(ctx.exception))


# ------------------------------------------------------------------------ plan/commit history write
class TestPlanHistoryWrite(TempDirTestCase):
    def _auth(self, root):
        auth = safe_io.acquire_root(root)
        self.addCleanup(auth.close)
        return auth

    def test_identity_required(self):
        with self.assertRaises(ValueError):
            history.plan_history_write({"repository": None}, self._auth(self._tmp()))
        with self.assertRaises(ValueError):
            history.plan_history_write({"repository": {}}, self._auth(self._tmp()))

    def test_invalid_index_refused(self):
        out = self._tmp()
        ih = "c" * 16
        bucket = out / "history" / ih
        bucket.mkdir(parents=True)
        (bucket / "index.json").write_text("garbage", encoding="utf-8")
        report = _report_dict({"identity_kind": "local_path", "identity_hash": ih})
        with self.assertRaises(ValueError) as ctx:
            history.plan_history_write(report, self._auth(out))
        self.assertIn("history index unreadable", str(ctx.exception))

    def test_cap_refuses_without_pruning(self):
        out = self._tmp()
        ih = "d" * 16
        bucket = out / "history" / ih
        bucket.mkdir(parents=True)
        entries = [_index_entry("s0.json"), _index_entry("s1.json")]
        (bucket / "index.json").write_text(
            json.dumps({"version": "2", "entries": entries}), encoding="utf-8")
        before = (bucket / "index.json").read_bytes()
        report = _report_dict({"identity_kind": "local_path", "identity_hash": ih})
        with mock.patch.object(history, "MAX_HISTORY_INDEX_ENTRIES", 2):
            with self.assertRaises(history.HistoryLimitError):
                history.plan_history_write(report, self._auth(out))
        # the refusal is read-only: the existing index is byte-identical
        self.assertEqual((bucket / "index.json").read_bytes(), before)

    def test_plan_and_commit_round_trip(self):
        out = self._tmp()
        ih = "e" * 16
        report = _report_dict({"identity_kind": "local_path", "identity_hash": ih})
        auth = self._auth(out)
        plan = history.plan_history_write(report, auth)
        bucket_rel, filename, payload, index_payload = plan
        self.assertEqual(bucket_rel, f"history/{ih}")
        self.assertTrue(filename.endswith(".json"))
        history.commit_history_write(auth, plan)
        self.assertEqual(json.loads(payload.decode("utf-8"))["repository"]
                         ["identity_hash"], ih)
        bucket = safe_io.open_subroot(auth, bucket_rel)
        self.addCleanup(bucket.close)
        entries = history._load_index(bucket)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["file"], filename)

    def test_commit_refuses_existing_snapshot(self):
        out = self._tmp()
        ih = "f" * 16
        report = _report_dict({"identity_kind": "local_path", "identity_hash": ih})
        auth = self._auth(out)
        plan = history.plan_history_write(report, auth)
        bucket_rel, filename, _payload, _index = plan
        bucket = out / bucket_rel
        bucket.mkdir(parents=True)
        (bucket / filename).write_bytes(b"junk")  # raced creation
        with self.assertRaises(ValueError) as ctx:
            history.commit_history_write(auth, plan)
        self.assertIn("already exists", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
