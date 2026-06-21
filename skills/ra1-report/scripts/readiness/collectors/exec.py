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

ALLOWED_SMOKE_CMDS = {
    "npm run smoke": ["npm", "run", "smoke", "--silent"],
    "npm run healthcheck": ["npm", "run", "healthcheck", "--silent"],
    "make smoke": ["make", "smoke"],
}

ALLOWED_BUILD_CMDS = {
    "devcontainer build": ["devcontainer", "build", "--workspace-folder", "."],
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

    def run_allowed(self, allowlist, cmd, app_path: str = ".") -> Optional[dict]:
        """Run an allowlisted ``cmd`` under the contract.

        ``None`` when disabled; ``{"allowed": False, ...}`` when ``cmd`` is not on ``allowlist``
        (and therefore NOT executed); otherwise the runner result with ``allowed: True``."""
        if not self.enabled:
            return None
        argv = allowlist.get(cmd)
        if argv is None:
            return {"cmd": cmd, "allowed": False, "returncode": None, "timed_out": False}
        key = (cmd, app_path)
        if key not in self._cache:
            self._cache[key] = self._runner(argv, app_path)
        return {"cmd": cmd, "allowed": True, "argv": argv, **self._cache[key]}

    def run_test_cmd(self, test_cmd: str, app_path: str = ".") -> Optional[dict]:
        """Execute the detected test command under the contract (see ``run_allowed``)."""
        return self.run_allowed(ALLOWED_TEST_CMDS, test_cmd, app_path)

    def run_smoke_cmd(self, smoke_cmd: str, app_path: str = ".") -> Optional[dict]:
        """Execute a declared smoke/healthcheck command under the contract."""
        return self.run_allowed(ALLOWED_SMOKE_CMDS, smoke_cmd, app_path)

    def run_build_cmd(self, build_cmd: str, app_path: str = ".") -> Optional[dict]:
        """Execute an environment build command (e.g. devcontainer build) under the contract."""
        return self.run_allowed(ALLOWED_BUILD_CMDS, build_cmd, app_path)

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
