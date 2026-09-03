import json
import runpy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import readiness  # noqa: F401 — ensures engine is importable
from readiness import safe_io

from tests._util import rmtree

REPO = Path(readiness.__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import vendor  # noqa: E402


class TestVendor(unittest.TestCase):
    def _mk(self):
        tmp = Path(tempfile.mkdtemp(prefix="ar-vendor-"))
        shutil.copytree(REPO / "engine", tmp / "engine")
        shutil.copytree(REPO / "templates", tmp / "templates")
        for s in vendor.SKILLS:
            (tmp / "skills" / s).mkdir(parents=True)
            (tmp / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\n---\n")
        return tmp

    def test_vendor_writes_identical_copies(self):
        tmp = self._mk()
        self.addCleanup(rmtree, tmp)
        drift = vendor.vendor(tmp, write=True)
        self.assertEqual(drift, [])
        for s in vendor.SKILLS:
            vcli = tmp / "skills" / s / "scripts" / "readiness" / "cli.py"
            self.assertTrue(vcli.exists())
            self.assertEqual(
                             vcli.read_bytes(),
                             (tmp / "engine" / "readiness" / "cli.py").read_bytes())
            self.assertTrue((tmp / "skills" / s / "scripts" / "readiness" / "criteria" / "registry."
                "json").exists())
            self.assertTrue((tmp / "skills" / s / "templates" / "ruff.toml").exists())
            manifest = json.loads((tmp / "skills" / s / "manifest.json").read_text())
            self.assertIn("engine_version", manifest)

            for rel in [
                "loop/loop-runs-README.md",
                "loop/denylist.md",
                "loop/signals-README.md",
                "loop/pr-artifact-template.md",
            ]:
                self.assertEqual((tmp / "skills" / s / "templates" / rel).read_bytes(),
                                 (tmp / "templates" / rel).read_bytes())
    def test_check_detects_sync_and_drift(self):
        tmp = self._mk()
        self.addCleanup(rmtree, tmp)
        vendor.vendor(tmp, write=True)
        self.assertEqual(vendor.vendor(tmp, write=False), [])
        (tmp / "engine" / "readiness" / "version.py").write_text("ENGINE_VERSION = '9.9.9'\n")
        drift = vendor.vendor(tmp, write=False)
        self.assertTrue(any("version.py" in d for d in drift))

    def test_check_detects_manifest_drift(self):
        tmp = self._mk()
        self.addCleanup(rmtree, tmp)
        vendor.vendor(tmp, write=True)
        (tmp / "skills" / "ra1-report" / "manifest.json").write_text('{"stale": true}\n',
                                                                       encoding="utf-8")
        drift = vendor.vendor(tmp, write=False)
        self.assertIn("skills/ra1-report/manifest.json", drift)
        (tmp / "skills" / "ra1-report" / "manifest.json").unlink()
        drift = vendor.vendor(tmp, write=False)
        self.assertIn("skills/ra1-report/manifest.json", drift)

    def test_main_reports_drift_and_writes(self):
        tmp = self._mk()
        self.addCleanup(rmtree, tmp)
        old_root = vendor.ROOT
        try:
            vendor.ROOT = tmp
            self.assertEqual(vendor.main([]), 0)
            (tmp / "skills" / "ra1-report" / "manifest.json").unlink()
            self.assertEqual(vendor.main(["--check"]), 1)
        finally:
            vendor.ROOT = old_root

    def test_script_entrypoint_checks_sync(self):
        old_argv = sys.argv[:]
        try:
            sys.argv = [str(REPO / "scripts" / "vendor.py"), "--check"]
            with self.assertRaises(SystemExit) as cm:
                runpy.run_path(str(REPO / "scripts" / "vendor.py"), run_name="__main__")
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.argv = old_argv

    def test_loop_template_drift_detected(self):
        tmp = self._mk()
        self.addCleanup(rmtree, tmp)
        vendor.vendor(tmp, write=True)
        (tmp / "templates" / "loop" / "denylist.md").write_text("# changed\n", encoding="utf-8")
        drift = vendor.vendor(tmp, write=False)
        self.assertTrue(any("templates/loop/denylist.md" in d for d in drift))

    def test_unallowlisted_template_cruft_is_ignored(self):
        tmp = self._mk()
        self.addCleanup(rmtree, tmp)
        (tmp / "templates" / ".DS_Store").write_text("cruft", encoding="utf-8")
        (tmp / "templates" / "local-only.md").write_text("# local\n", encoding="utf-8")
        vendor.vendor(tmp, write=True)
        for s in vendor.SKILLS:
            self.assertFalse((tmp / "skills" / s / "templates" / ".DS_Store").exists())
            self.assertFalse((tmp / "skills" / s / "templates" / "local-only.md").exists())
        self.assertEqual(vendor.vendor(tmp, write=False), [])

    def test_real_repo_in_sync(self):
        # Guard: the committed vendored skills must match engine + templates.
        self.assertEqual(vendor.main(["--check"]), 0,
                         "vendored skills drifted — run scripts/vendor.py and re-commit")


class TestVendorSyncPaths(unittest.TestCase):
    def _mk(self):
        tmp = Path(tempfile.mkdtemp(prefix="ar-vendor-"))
        shutil.copytree(REPO / "engine", tmp / "engine")
        shutil.copytree(REPO / "templates", tmp / "templates")
        for s in vendor.SKILLS:
            (tmp / "skills" / s).mkdir(parents=True)
            (tmp / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\n---\n")
        self.addCleanup(rmtree, tmp)
        vendor.vendor(tmp, write=True)
        return tmp

    def test_write_replaces_drifted_file(self):
        tmp = self._mk()
        stale = tmp / "skills" / "ra1-fix" / "scripts" / "readiness" / "version.py"
        stale.write_text("# stale\n", encoding="utf-8")
        drift = vendor.vendor(tmp, write=True)
        self.assertEqual(drift, [])
        self.assertEqual(stale.read_bytes(),
                         (tmp / "engine" / "readiness" / "version.py").read_bytes())
        self.assertEqual(vendor.vendor(tmp, write=False), [])

    def test_write_removes_extra_generated_file(self):
        tmp = self._mk()
        extra = tmp / "skills" / "ra1-report" / "scripts" / "readiness" / "cruft.py"
        extra.write_text("# cruft\n", encoding="utf-8")
        extra_tpl = tmp / "skills" / "ra1-report" / "templates" / "cruft.md"
        extra_tpl.write_text("# cruft\n", encoding="utf-8")
        vendor.vendor(tmp, write=True)
        self.assertFalse(extra.exists())
        self.assertFalse(extra_tpl.exists())
        self.assertEqual(vendor.vendor(tmp, write=False), [])

    def test_check_reports_missing_generated_file(self):
        tmp = self._mk()
        (tmp / "skills" / "ra1-fix" / "templates" / "ruff.toml").unlink()
        drift = vendor.vendor(tmp, write=False)
        self.assertIn("skills/ra1-fix/templates/ruff.toml", drift)

    def test_check_reports_extra_generated_file(self):
        tmp = self._mk()
        (tmp / "skills" / "ra1-interview" / "scripts" / "readiness"
         / "cruft.py").write_text("# cruft\n", encoding="utf-8")
        drift = vendor.vendor(tmp, write=False)
        self.assertIn("skills/ra1-interview/scripts/readiness/cruft.py", drift)


class TestVendorGuards(unittest.TestCase):
    """The safe-I/O refusal paths: non-OK observations become typed errors."""

    def _mk(self):
        tmp = Path(tempfile.mkdtemp(prefix="ar-vendor-"))
        shutil.copytree(REPO / "engine", tmp / "engine")
        shutil.copytree(REPO / "templates", tmp / "templates")
        for s in vendor.SKILLS:
            (tmp / "skills" / s).mkdir(parents=True)
            (tmp / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\n---\n")
        self.addCleanup(rmtree, tmp)
        return tmp

    def test_engine_inventory_discovery_failure(self):
        obs = safe_io.RepoDiscoveryObservation(
            safe_io.RepoDiscoveryState.OVERFLOW, reason_code="match_overflow")
        with mock.patch.object(vendor.safe_io, "discover_rooted_regular",
                               return_value=obs):
            with self.assertRaises(safe_io.RepositoryInputError):
                vendor._engine_inventory(object())

    def test_read_canonical_failure(self):
        obs = safe_io.RootedBytesObservation(safe_io.RepoReadState.OVERSIZE,
                                             reason_code="too_large")
        with mock.patch.object(vendor.safe_io, "read_rooted_regular",
                               return_value=obs):
            with self.assertRaises(safe_io.RepositoryInputError):
                vendor._read_canonical(object(), "version.py")

    def test_read_generated_unsafe_entry(self):
        obs = safe_io.RootedBytesObservation(safe_io.RepoReadState.UNSAFE_PATH,
                                             reason_code="symlink")
        with mock.patch.object(vendor.safe_io, "read_rooted_regular",
                               return_value=obs):
            with self.assertRaises(safe_io.RepositoryInputError):
                vendor._read_generated(object(), "version.py")

    def test_generated_inventory_discovery_failure(self):
        obs = safe_io.RepoDiscoveryObservation(
            safe_io.RepoDiscoveryState.UNREADABLE, reason_code="io_error")
        with mock.patch.object(vendor.safe_io, "discover_rooted_regular",
                               return_value=obs):
            with self.assertRaises(safe_io.RepositoryInputError):
                vendor._generated_inventory(object())

    def test_mkdir_failure_closes_and_raises(self):
        tmp = self._mk()
        # "scripts" exists as a regular file: directory creation must fail closed.
        (tmp / "skills" / "ra1-report" / "scripts").write_text("not a dir\n",
                                                               encoding="utf-8")
        with self.assertRaises(NotADirectoryError):
            vendor.vendor(tmp, write=True)


if __name__ == "__main__":
    unittest.main()
