import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import readiness  # noqa: F401 — ensures engine is importable
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
            self.assertEqual(vcli.read_bytes(), (tmp / "engine" / "readiness" / "cli.py").read_bytes())
            self.assertTrue((tmp / "skills" / s / "scripts" / "readiness" / "criteria" / "registry.json").exists())
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


if __name__ == "__main__":
    unittest.main()
