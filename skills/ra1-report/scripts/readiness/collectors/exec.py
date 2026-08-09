"""T3 execution evidence: runs the repo's own allowlisted command — OFF by default.

Opt-in only (constructed disabled unless ``options.exec`` is true; CLI-only), allowlisted
commands mapped to fixed :class:`process.ToolId` argv, a bounded copy-only isolated
worktree (``.git``, ``.agents``, and ``.ra1/reports`` excluded; links/special files/cap
overflow refuse the run), a scrubbed minimal environment, and the bounded process
launcher. T3 remains advisory: refusal yields unavailable evidence plus a limitation —
never absence or failure credit — and provenance records requested/completed/successful
exactly (``successful ⇒ completed ⇒ requested``).

This is an isolated copy and scrubbed environment, not a kernel-enforced filesystem or
network sandbox; a descendant that deliberately daemonizes is outside the stdlib
guarantee. True isolation is the runner's responsibility.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .. import process, safe_io

DEFAULT_EXEC_TIMEOUT_SECONDS = 120
MAX_EXEC_TIMEOUT_SECONDS = 3_600

ALLOWED_TEST_CMDS = {
    "pytest": (process.ToolId.PYTEST, ("-q",)),
    "npm test": (process.ToolId.NPM, ("test", "--silent")),
    "go test ./...": (process.ToolId.GO, ("test", "./...")),
    "cargo test": (process.ToolId.CARGO, ("test", "--quiet")),
}

ALLOWED_SMOKE_CMDS = {
    "npm run smoke": (process.ToolId.NPM, ("run", "smoke", "--silent")),
    "npm run healthcheck": (process.ToolId.NPM, ("run", "healthcheck", "--silent")),
    "make smoke": (process.ToolId.MAKE, ("smoke",)),
}

ALLOWED_BUILD_CMDS = {
    "devcontainer build": (process.ToolId.DEVCONTAINER,
                           ("build", "--workspace-folder", ".")),
}


def normalize_exec_timeout(value) -> int:
    """The one timeout normalizer: omitted/None → 120; exact int in 1..3600 else ValueError."""
    if value is None:
        return DEFAULT_EXEC_TIMEOUT_SECONDS
    if type(value) is not int or not 1 <= value <= MAX_EXEC_TIMEOUT_SECONDS:
        raise ValueError(f"exec timeout must be an int in 1..{MAX_EXEC_TIMEOUT_SECONDS}")
    return value


class ExecCollector:
    def __init__(self, root, options=None, *, toolchain=None, runner=None, static=None):
        options = options or {}
        self.root = Path(root)
        self.enabled = bool(options.get("exec"))
        self.timeout = normalize_exec_timeout(options.get("exec_timeout"))
        self._toolchain = toolchain
        self._runner = runner  # test injection: fn(tool_id, argv, cwd_handle, env, timeout)
        self._static = static
        self._cache: dict = {}
        self._copy_dir = None
        self._copy_auth = None
        self._copy_failed = False
        # Provenance counters (§4.6): successful ⇒ completed ⇒ requested.
        self.requested = self.enabled
        self.completed = True   # stays True only while every spawned run reached an exit
        self.successful = True  # stays True only while every spawned run exited 0
        self._spawned = 0

    def close(self) -> None:
        if self._copy_auth is not None:
            self._copy_auth.close()
            self._copy_auth = None
        if self._copy_dir is not None:
            import shutil
            shutil.rmtree(self._copy_dir, ignore_errors=True)
            self._copy_dir = None

    # ----- provenance ---------------------------------------------------------------------
    @property
    def provenance(self) -> dict:
        completed = bool(self._spawned) and self.completed
        successful = completed and self.successful
        return {"requested": self.requested, "timeout_seconds": self.timeout,
                "completed": completed, "successful": successful}

    # ----- isolated copy --------------------------------------------------------------------
    def _copy(self):
        """The bounded isolated worktree copy, built once. ``None`` on refusal."""
        if self._copy_failed:
            return None
        if self._copy_auth is not None:
            return self._copy_auth
        try:
            src = safe_io.acquire_root(self.root)
            self._copy_dir = tempfile.mkdtemp(prefix="ra1-exec-")
            os.chmod(self._copy_dir, 0o700)
            worktree = os.path.join(self._copy_dir, "worktree")
            os.mkdir(worktree, 0o700)
            dst = safe_io.acquire_root(worktree)
            try:
                safe_io.safe_copy_tree(src, dst)
            finally:
                src.close()
            self._copy_auth = dst
            return self._copy_auth
        except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
            self._copy_failed = True
            self.completed = False
            self.successful = False
            self.close()
            return None

    # ----- execution ------------------------------------------------------------------------
    def run_allowed(self, allowlist, cmd, app_path: str = ".") -> dict | None:
        """Run an allowlisted ``cmd`` under the contract.

        ``None`` when disabled; ``{"allowed": False, ...}`` when ``cmd`` is not on the
        allowlist (and therefore NOT executed); otherwise the mapped runner result."""
        if not self.enabled:
            return None
        resolved = allowlist.get(cmd)
        if resolved is None:
            return {"cmd": cmd, "allowed": False, "returncode": None, "timed_out": False}
        tool_id, argv = resolved
        key = (cmd, app_path)
        if key not in self._cache:
            self._cache[key] = self._execute(tool_id, argv, app_path)
        return {"cmd": cmd, "allowed": True, "argv": [tool_id.value, *argv],
                **self._cache[key]}

    def _execute(self, tool_id, argv, app_path: str) -> dict:
        copy = self._copy()
        if copy is None:
            return {"returncode": None, "timed_out": False, "unavailable": True,
                    "state": "unavailable"}
        try:
            if app_path not in (".", "", None):
                run_root = safe_io.open_subroot(copy, str(app_path))
            else:
                run_root = copy
        except (OSError, safe_io.RepositoryInputError):
            self.completed = False
            self.successful = False
            return {"returncode": None, "timed_out": False, "unavailable": True,
                    "state": "unavailable"}
        env = {"PATH": "/usr/bin:/bin", "HOME": self._copy_dir, "LANG": "C.UTF-8",
               "CI": "1", "NO_COLOR": "1"}
        try:
            if self._runner is not None:
                result = self._runner(tool_id, argv, run_root.fd, env, self.timeout)
                if not isinstance(result, process.BoundedProcessResult):
                    raise TypeError("injected exec runners must return BoundedProcessResult")
            else:
                if self._toolchain is None:
                    self._toolchain = process.resolve_toolchain(self.root)
                result = process.run_bounded_process(
                    tool_id, argv, toolchain=self._toolchain, cwd_handle=run_root.fd,
                    env=env, timeout_seconds=self.timeout)
        finally:
            if run_root is not copy:
                run_root.close()
        self._spawned += 1
        if result.state is process.ProcessState.OK:
            return {"returncode": 0, "timed_out": False, "state": "ok"}
        self.successful = False
        if result.state is process.ProcessState.NONZERO:
            return {"returncode": result.returncode, "timed_out": False,
                    "state": "nonzero"}
        self.completed = False
        if result.state is process.ProcessState.TIMEOUT:
            return {"returncode": None, "timed_out": True, "state": "timeout"}
        return {"returncode": None, "timed_out": False, "unavailable": True,
                "state": "unavailable"}

    def run_test_cmd(self, test_cmd: str, app_path: str = ".") -> dict | None:
        """Execute the detected test command under the contract (see ``run_allowed``)."""
        return self.run_allowed(ALLOWED_TEST_CMDS, test_cmd, app_path)

    def run_smoke_cmd(self, smoke_cmd: str, app_path: str = ".") -> dict | None:
        """Execute a declared smoke/healthcheck command under the contract."""
        return self.run_allowed(ALLOWED_SMOKE_CMDS, smoke_cmd, app_path)

    def run_build_cmd(self, build_cmd: str, app_path: str = ".") -> dict | None:
        """Execute an environment build command (e.g. devcontainer build) under the contract."""
        return self.run_allowed(ALLOWED_BUILD_CMDS, build_cmd, app_path)
