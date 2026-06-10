"""T3 execution evidence: runs the repo's own test command — OFF by default.

The sandbox contract is enforced by construction, not by OS primitives (pure stdlib has no
kernel sandbox; that residual risk is why this collector is opt-in and advisory-only):

- **opt-in only** — constructed disabled unless ``options["exec"]`` is truthy; when disabled
  every method returns ``None`` and no subprocess is ever spawned.
- **command allowlist** — only the exact test commands the detector emits (pytest / npm test /
  go test / cargo test) map to fixed argv lists; nothing is passed through a shell.
- **scrubbed env** — a minimal environment (PATH + neutral HOME/LANG); no tokens or secrets.
- **isolated copy** — the worktree is copied to a temp dir (``.git``/``.agents`` excluded)
  and the command runs there, never in the user's tree.
- **hard timeout** — ``subprocess.run(..., timeout=...)``, default 120s.

True network isolation is the runner's responsibility (e.g. a jailed CI job); we do not
claim it. T3-derived criteria stay ``gating: false`` until they graduate through the
labeled-fixture evals (see references/pillars.md).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

ALLOWED_TEST_CMDS = {
    "pytest": ["pytest", "-q"],
    "npm test": ["npm", "test", "--silent"],
    "go test ./...": ["go", "test", "./..."],
    "cargo test": ["cargo", "test", "--quiet"],
}


class ExecCollector:
    def __init__(self, root, options=None, runner=None):
        options = options or {}
        self.root = Path(root)
        self.enabled = bool(options.get("exec"))
        self.timeout = int(options.get("exec_timeout") or 120)
        # ``runner`` is injectable so tests never spawn a real subprocess.
        self._runner = runner or options.get("exec_runner") or self._default_runner
        self._cache: dict = {}

    def run_test_cmd(self, test_cmd: str, app_path: str = ".") -> Optional[dict]:
        """Execute the detected test command under the contract.

        Returns ``None`` when disabled; ``{"allowed": False, ...}`` when the command is not
        on the allowlist (and therefore was NOT executed); otherwise
        ``{"allowed": True, "returncode": int|None, "timed_out": bool, "argv": [...]}``.
        """
        if not self.enabled:
            return None
        argv = ALLOWED_TEST_CMDS.get(test_cmd)
        if argv is None:
            return {"cmd": test_cmd, "allowed": False, "returncode": None, "timed_out": False}
        key = (test_cmd, app_path)
        if key not in self._cache:
            self._cache[key] = self._runner(argv, app_path)
        return {"cmd": test_cmd, "allowed": True, "argv": argv, **self._cache[key]}

    def _default_runner(self, argv, app_path) -> dict:  # pragma: no cover - subprocess boundary
        with tempfile.TemporaryDirectory(prefix="ra1-exec-") as tmp:
            copy = Path(tmp) / "worktree"
            shutil.copytree(self.root, copy,
                            ignore=shutil.ignore_patterns(".git", ".agents"), symlinks=True)
            env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp,
                   "LANG": "C.UTF-8", "CI": "1", "NO_COLOR": "1"}
            cwd = copy / app_path if app_path != "." else copy
            try:
                proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                                      text=True, timeout=self.timeout)
                return {"returncode": proc.returncode, "timed_out": False}
            except subprocess.TimeoutExpired:
                return {"returncode": None, "timed_out": True}
            except (OSError, subprocess.SubprocessError):
                return {"returncode": None, "timed_out": False}
