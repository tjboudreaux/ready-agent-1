"""The sole production child-process launcher (pure stdlib).

Every production subprocess the engine spawns — Git, ``gh``, and opted-in T3 commands —
goes through :func:`run_bounded_process` with:

- a closed :class:`ToolId` target resolved once from the inherited startup ``PATH``
  (host-authorized input; repository config/report/command content never modifies it);
- a fixed engine-owned Python shim launched as ``PYTHON_SHIM -I -S -P -c <source>``: the
  shim receives the admitted working-directory fd, ``fchdir``s into it, and ``execve``s the
  cached absolute target. ``-I/-P`` remove the untrusted startup cwd and environment from
  ``sys.path`` and ``-S`` disables ``site``; the shim imports only stdlib modules, and the
  target executable plus argv are *data arguments*, never import/search inputs;
- a caller-authored minimal environment (never the ambient one), no stdin, ``shell=False``;
- bounded combined stdout/stderr capture with a kill-the-process-group, close-pipes, reap
  sequence on output cap or timeout. No raw child output ever reaches a rationale, error,
  trace, provenance, or renderer.

Automatic Git additionally requires a :class:`safe_io.GitSnapshotAuthority`, runs inside
the sanitized immutable snapshot, gets CPU/core/wall/output/command/snapshot controls on
Linux and macOS, and a hard address-space cap on Linux only (Darwin rejects finite memory
limits with EINVAL; that gap is recorded in provenance and deferred, not hidden).

Every Git invocation runs with fixed relative ``--git-dir=.git`` and ``--work-tree=.``
inside the retained worktree-view snapshot: the snapshot root holds the flattened
``.git`` plus a hardlink-or-copy view of the worktree, so worktree-observing commands
(``status``, ``check-ignore``) answer from the same confined authority as object-database
commands. No absolute or repository-selected path ever enters argv.
"""
from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import StrEnum

from . import safe_io

# ----------------------------------------------------------------- caps (engine constants)
MAX_PROCESS_OUTPUT_BYTES = 1_048_576
PROCESS_CHUNK_BYTES = 65_536
MAX_PROCESS_ARGS = 128
MAX_PROCESS_ARG_BYTES = 4_096
MAX_GIT_ADDRESS_SPACE_BYTES = 2_147_483_648  # Linux only; Darwin has no hard memory cap
MAX_GIT_CPU_SECONDS = 30
MAX_GIT_COMMANDS_PER_AUTHORITY = 256
MAX_GIT_WALL_SECONDS_PER_AUTHORITY = 120
GIT_CONTROL_TIMEOUT_SECONDS = 30

_GH_CONFIG_MAX_FILE_BYTES = 262_144
_GH_CONFIG_FILES = ("hosts.yml", "config.yml")
_PROXY_KEYS = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")
_MAX_PROXY_VALUE_BYTES = 4_096
_MAX_PROXY_TOTAL_BYTES = 16_384
_MAX_TOKEN_BYTES = 8_192

_GITHUB_ENV_CONSTANTS = {
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "GH_TELEMETRY": "0",
    "NO_COLOR": "1",
}

_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}


class ToolId(StrEnum):
    PYTHON_SHIM = "python_shim"
    GIT = "git"
    GH = "gh"
    PYTEST = "pytest"
    NPM = "npm"
    GO = "go"
    CARGO = "cargo"
    MAKE = "make"
    DEVCONTAINER = "devcontainer"


_TOOL_BASENAMES = {
    ToolId.GIT: "git",
    ToolId.GH: "gh",
    ToolId.PYTEST: "pytest",
    ToolId.NPM: "npm",
    ToolId.GO: "go",
    ToolId.CARGO: "cargo",
    ToolId.MAKE: "make",
    ToolId.DEVCONTAINER: "devcontainer",
}


# --------------------------------------------------------------------------- toolchain
@dataclass(frozen=True)
class Toolchain:
    """Startup-resolved absolute tool paths. Repository data never influences them."""

    paths: tuple  # tuple[tuple[ToolId, str], ...]

    def get(self, tool_id: ToolId) -> str | None:
        for tid, path in self.paths:
            if tid == tool_id:
                return path
        return None


def resolve_toolchain(workspace_root=None, *, startup_path: str | None = None) -> Toolchain:
    """Resolve the closed tool set from nonempty absolute startup-PATH components.

    A candidate must be an executable regular file; it is physical-canonicalized once and
    the first PATH hit per tool wins. ``PYTHON_SHIM`` maps only to the physicalized
    ``sys.executable``.
    """
    path = startup_path if startup_path is not None else os.environ.get("PATH", "")
    tools: dict = {ToolId.PYTHON_SHIM: os.path.realpath(sys.executable)}
    for component in path.split(os.pathsep):
        if not component or not os.path.isabs(component):
            continue
        for tool_id, basename in _TOOL_BASENAMES.items():
            if tool_id in tools:
                continue
            candidate = os.path.join(component, basename)
            try:
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    tools[tool_id] = os.path.realpath(candidate)
            except OSError:
                continue
    return Toolchain(tuple(sorted(tools.items(), key=lambda kv: kv[0].value)))


# --------------------------------------------------------------------------- result type
class ProcessState(StrEnum):
    OK = "ok"
    NONZERO = "nonzero"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    RESOURCE_LIMIT = "resource_limit"
    UNSUPPORTED = "unsupported"
    SPAWN_ERROR = "spawn_error"


@dataclass(frozen=True)
class BoundedProcessResult:
    """The sole runner return type. No post-capture truncation; failures carry empty streams."""

    state: ProcessState
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""

    def __post_init__(self):
        if not isinstance(self.state, ProcessState):
            raise TypeError("state must be a ProcessState")
        if self.state is not ProcessState.OK and self.state is not ProcessState.NONZERO:
            if self.stdout or self.stderr:
                raise TypeError("failure results carry empty streams")
        if self.state is ProcessState.OK and self.returncode != 0:
            raise TypeError("ok results have returncode 0")
        if self.state is ProcessState.NONZERO and not isinstance(self.returncode, int):
            raise TypeError("nonzero results carry an integer returncode")
        if self.state in (ProcessState.TIMEOUT, ProcessState.OUTPUT_LIMIT,
                          ProcessState.RESOURCE_LIMIT, ProcessState.UNSUPPORTED,
                          ProcessState.SPAWN_ERROR) and self.returncode is not None:
            raise TypeError("non-exit states carry no returncode")


# --------------------------------------------------------------------------- shim sources
# Shim protocol: argv = [cwd_fd, status_fd, target, *args]. The status fd is a 1-byte
# close-on-exec pipe the parent trusts instead of child text: EOF means exec succeeded,
# b"D" fchdir failed, b"R" rlimit setup failed, b"X" execve failed.
_SHIM_PREAMBLE = """\
import os, sys
def _fail(fd, byte):
    try:
        os.write(fd, byte)
    except OSError:
        pass
    os._exit(127)
cwd_fd = int(sys.argv[1])
status_fd = int(sys.argv[2])
try:
    os.fchdir(cwd_fd)
except OSError:
    _fail(status_fd, b"D")
os.close(cwd_fd)
"""

_SHIM_EXEC = """\
target = sys.argv[3]
os.set_inheritable(status_fd, False)
try:
    os.execve(target, [target] + sys.argv[4:], os.environ)
except OSError:
    _fail(status_fd, b"X")
"""

_SHIM_BODY = _SHIM_PREAMBLE + _SHIM_EXEC

_GIT_SHIM_RLIMITS = """\
import resource
def _cap(res, cap):
    soft, hard = resource.getrlimit(res)
    target = cap
    if soft != resource.RLIM_INFINITY:
        target = min(target, soft)
    if hard != resource.RLIM_INFINITY:
        target = min(target, hard)
    resource.setrlimit(res, (target, target))
try:
    _cap(resource.RLIMIT_CPU, {cpu})
    _cap(resource.RLIMIT_CORE, 0)
    if sys.platform == "linux":
        _cap(resource.RLIMIT_AS, {addr})
except (OSError, ValueError):
    _fail(status_fd, b"R")
"""

GIT_SHIM_SOURCE = _SHIM_PREAMBLE + _GIT_SHIM_RLIMITS.format(
    cpu=MAX_GIT_CPU_SECONDS, addr=MAX_GIT_ADDRESS_SPACE_BYTES) + _SHIM_EXEC


def git_resource_profile() -> str | None:
    """The Git containment profile for this host, or ``None`` when Git cannot be contained.

    Linux requires RLIMIT_AS/CPU/CORE; Darwin deliberately requires only CPU/CORE because
    live Darwin probes reject finite memory-limit lowering with EINVAL (recorded as the
    ``darwin_cpu_core_no_hard_memory`` provenance profile, never hidden).
    """
    try:
        import resource
    except ImportError:
        return None
    required = ("getrlimit", "setrlimit", "RLIMIT_CPU", "RLIMIT_CORE", "RLIM_INFINITY")
    if any(not hasattr(resource, name) for name in required):
        return None
    if sys.platform == "linux":
        if not hasattr(resource, "RLIMIT_AS"):
            return None
        return "linux_as_cpu_core"
    if sys.platform == "darwin":
        return "darwin_cpu_core_no_hard_memory"
    return None


# --------------------------------------------------------------------------- authorities
@dataclass(frozen=True)
class HostProxyAuthority:
    """An explicit, CLI-created opt-in to forward exact captured proxy keys to ``gh`` only."""

    pairs: tuple  # tuple[tuple[str, str], ...]

    def env_overlay(self) -> dict:
        return dict(self.pairs)


class HostProxyError(ValueError):
    """Captured host proxy environment failed validation (exact CLI diagnostic follows)."""


def _has_forbidden_controls(value: str) -> bool:
    for ch in value:
        code = ord(ch)
        if ch == "\x00" or code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            return True
        if 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069:
            return True
    return False


def capture_host_proxy_authority(enabled: bool, startup_env) -> HostProxyAuthority | None:
    """Capture the exact proxy keys an explicit ``--host-proxy`` flag authorizes.

    Called at most once per top-level CLI invocation, before root admission. Disabled
    returns ``None``; invalid input raises :class:`HostProxyError` (the CLI writes the exact
    ``ra1: invalid host proxy environment`` diagnostic and performs no read/spawn/write).
    """
    if not enabled:
        return None
    pairs = []
    total = 0
    for key in _PROXY_KEYS:
        value = startup_env.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or _has_forbidden_controls(value):
            raise HostProxyError(key)
        size = len(value.encode("utf-8"))
        if size > _MAX_PROXY_VALUE_BYTES:
            raise HostProxyError(key)
        total += size
        # Ceiling reachability: 4 keys x _MAX_PROXY_VALUE_BYTES == _MAX_PROXY_TOTAL_BYTES,
        # so equality must also refuse or this guard could never fire.
        if total >= _MAX_PROXY_TOTAL_BYTES:
            raise HostProxyError(key)
        pairs.append((key, value))
    return HostProxyAuthority(tuple(pairs))


@dataclass(frozen=True)
class GithubAuthAuthority:
    """One validated host credential source for the fixed ``github.com`` T2 mode.

    Either a bounded startup token (stored privately, emitted only as child ``GH_TOKEN``)
    or an engine-owned temporary copy of the external ``gh`` config directory. Never
    repository-local, never derived from report/config/API data.
    """

    kind: str          # "token" | "config"
    _secret: str = field(default="", compare=False, repr=False)
    _tempdir: object = field(default=None, compare=False, repr=False)

    def env(self, proxy: HostProxyAuthority | None = None) -> dict:
        """The minimal GitHub child environment: auth + engine constants (+ opted-in proxy)."""
        env = dict(_GITHUB_ENV_CONSTANTS)
        if self.kind == "token":
            env["GH_TOKEN"] = self._secret
        else:
            env["GH_CONFIG_DIR"] = self._tempdir.name if self._tempdir is not None else ""
        if proxy is not None:
            env.update(proxy.env_overlay())
        env["PATH"] = "/usr/bin:/bin"
        return env

    def close(self) -> None:
        if self._tempdir is not None:
            self._tempdir.cleanup()


def capture_github_auth_authority(startup_env, workspace_authority=None
                                  ) -> GithubAuthAuthority | None:
    """Create the one GitHub credential authority for a GitHub-enabled invocation.

    Preference order: first nonempty startup ``GH_TOKEN``, then ``GITHUB_TOKEN`` (no
    fallback when the selected token is malformed — it simply is not used); otherwise
    exactly one config directory in documented ``gh`` precedence (``GH_CONFIG_DIR``, else
    ``XDG_CONFIG_HOME/gh``, else ``$HOME/.config/gh``), validated and copied into an
    engine-owned temporary directory. ``None`` when no usable source exists.
    """
    token = startup_env.get("GH_TOKEN")
    if not token:
        token = startup_env.get("GITHUB_TOKEN")
    if token:
        if (isinstance(token, str) and not _has_forbidden_controls(token)
                and len(token.encode("utf-8")) <= _MAX_TOKEN_BYTES):
            return GithubAuthAuthority(kind="token", _secret=token)
        return None
    candidates = []
    gh_dir = startup_env.get("GH_CONFIG_DIR")
    if gh_dir:
        candidates.append(gh_dir)
    else:
        xdg = startup_env.get("XDG_CONFIG_HOME")
        if xdg:
            candidates.append(os.path.join(xdg, "gh"))
        else:
            home = startup_env.get("HOME")
            if home:
                candidates.append(os.path.join(home, ".config", "gh"))
    for candidate in candidates:
        authority = _copy_gh_config(candidate)
        if authority is not None:
            return authority
    return None


def _copy_gh_config(config_dir: str) -> GithubAuthAuthority | None:
    """Copy bounded, validated ``gh`` config files into an engine temporary directory."""
    if not config_dir or "\x00" in config_dir:
        return None
    try:
        auth = safe_io.acquire_root(config_dir)
    except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
        return None
    try:
        st = os.fstat(auth.fd)
        if not (st.st_uid == os.geteuid() and not (st.st_mode & 0o022)):
            return None
        found = {}
        for name in _GH_CONFIG_FILES:
            obs = safe_io.read_rooted_regular(auth, name, max_bytes=_GH_CONFIG_MAX_FILE_BYTES)
            if obs.state is safe_io.RepoReadState.OK:
                found[name] = obs.data
        if "hosts.yml" not in found:
            return None
        tmp = tempfile.TemporaryDirectory(prefix="ra1-gh-config-")
        os.chmod(tmp.name, 0o700)
        tmp_auth = safe_io.acquire_root(tmp.name)
        try:
            for name, data in found.items():
                safe_io.create_rooted_exclusive(tmp_auth, name, data, mode=0o600)
        finally:
            tmp_auth.close()
        return GithubAuthAuthority(kind="config", _tempdir=tmp)
    except (OSError, safe_io.RepositoryInputError):
        return None
    finally:
        auth.close()


# --------------------------------------------------------------------------- bounded runner
def _validate_args(args) -> tuple:
    if not isinstance(args, (tuple, list)):
        raise TypeError("args must be a tuple/list of strings")
    out = []
    for arg in args:
        if not isinstance(arg, str) or "\x00" in arg:
            raise TypeError("process args must be NUL-free strings")
        if len(arg.encode("utf-8")) > MAX_PROCESS_ARG_BYTES:
            raise TypeError("process arg exceeds the byte cap")
        out.append(arg)
    if len(out) > MAX_PROCESS_ARGS:
        raise TypeError("too many process args")
    return tuple(out)


def _validate_env(env) -> dict:
    if not isinstance(env, dict):
        raise TypeError("env must be a dict of strings")
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("env keys/values must be strings")
        if "\x00" in key or "\x00" in value or "=" in key:
            raise TypeError("env keys/values must be NUL-free; keys must not contain '='")
    return dict(env)


def run_bounded_process(tool_id: ToolId, args, *, toolchain: Toolchain, cwd_handle: int,
                        env: dict, timeout_seconds) -> BoundedProcessResult:
    """Launch ``tool_id`` under the bounded contract and drain its output with caps."""
    if not isinstance(tool_id, ToolId):
        raise TypeError("tool_id must be a ToolId")
    argv_tail = _validate_args(args)
    child_env = _validate_env(env)
    if not isinstance(cwd_handle, int) or cwd_handle < 0:
        raise TypeError("cwd_handle must be a directory fd")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) \
            or timeout_seconds <= 0:
        raise TypeError("timeout_seconds must be a positive number")
    target = toolchain.get(tool_id)
    shim = toolchain.get(ToolId.PYTHON_SHIM)
    if target is None or shim is None:
        return BoundedProcessResult(ProcessState.UNSUPPORTED)
    shim_source = GIT_SHIM_SOURCE if tool_id is ToolId.GIT else _SHIM_BODY
    status_r, status_w = os.pipe()
    argv = [shim, "-I", "-S", "-P", "-c", shim_source,
            str(cwd_handle), str(status_w), target, *argv_tail]
    deadline = time.monotonic() + timeout_seconds
    try:
        proc = subprocess.Popen(
            argv, env=child_env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, pass_fds=(cwd_handle, status_w),
        )
    except (OSError, ValueError):
        os.close(status_r)
        return BoundedProcessResult(ProcessState.SPAWN_ERROR)
    finally:
        os.close(status_w)
    status = _read_status_byte(status_r, deadline)
    os.close(status_r)
    if status is None:
        _kill_group(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.wait()
        return BoundedProcessResult(ProcessState.TIMEOUT)
    if status != b"":
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.wait()
        if status == b"R":
            return BoundedProcessResult(ProcessState.RESOURCE_LIMIT)
        return BoundedProcessResult(ProcessState.SPAWN_ERROR)
    return _drain(proc, deadline)


def _read_status_byte(rfd: int, deadline: float) -> bytes | None:
    """Read the shim's trusted status byte; EOF (``b""``) means exec succeeded."""
    sel = selectors.DefaultSelector()
    sel.register(rfd, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            events = sel.select(min(remaining, 0.1))
            if not events:
                continue
            try:
                return os.read(rfd, 4)
            except OSError:
                return b""
    finally:
        sel.close()


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _drain(proc, deadline: float) -> BoundedProcessResult:
    """Drain stdout/stderr as bounded bytes; kill/close/reap on cap or timeout."""
    sel = selectors.DefaultSelector()
    streams = {}
    for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        os.set_blocking(stream.fileno(), False)
        sel.register(stream, selectors.EVENT_READ, name)
        streams[name] = stream
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    state = None
    try:
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                state = ProcessState.TIMEOUT
                break
            events = sel.select(min(remaining, 0.1))
            if not events:
                if proc.poll() is not None and not _data_pending(sel):
                    break
                continue
            for key, _mask in events:
                name = key.data
                try:
                    chunk = os.read(key.fileobj.fileno(), PROCESS_CHUNK_BYTES)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    try:
                        sel.unregister(key.fileobj)
                    except (KeyError, ValueError):
                        pass
                    continue
                total += len(chunk)
                if total > MAX_PROCESS_OUTPUT_BYTES:
                    state = ProcessState.OUTPUT_LIMIT
                    break
                buffers[name] += chunk
            if state is not None:
                break
        if state is None and proc.poll() is None and not sel.get_map():
            pass  # streams closed; child is exiting
        elif state is None:
            # Give a child whose pipes closed a bounded moment to exit.
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()) or 0.05)
            except subprocess.TimeoutExpired:
                state = ProcessState.TIMEOUT
    finally:
        if state in (ProcessState.TIMEOUT, ProcessState.OUTPUT_LIMIT):
            _kill_group(proc)
        for stream in streams.values():
            try:
                stream.close()
            except OSError:
                pass
        try:
            returncode = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            returncode = proc.wait()
        sel.close()
    if state is not None:
        return BoundedProcessResult(state)
    if returncode == 0:
        return BoundedProcessResult(
            ProcessState.OK, returncode=0,
            stdout=bytes(buffers["stdout"]).decode("utf-8", "replace"),
            stderr=bytes(buffers["stderr"]).decode("utf-8", "replace"))
    return BoundedProcessResult(
        ProcessState.NONZERO, returncode=returncode,
        stdout=bytes(buffers["stdout"]).decode("utf-8", "replace"),
        stderr=bytes(buffers["stderr"]).decode("utf-8", "replace"))


def _data_pending(sel) -> bool:
    events = sel.select(0)
    return bool(events)


# --------------------------------------------------------------------------- git wrapper
@dataclass
class GitBudget:
    """Per-authority command/wall budget for automatic Git."""

    commands: int = 0
    wall_seconds: float = 0.0


def run_git_bounded(authority, args, *, toolchain: Toolchain, budget: GitBudget,
                    timeout_seconds: int = GIT_CONTROL_TIMEOUT_SECONDS
                    ) -> BoundedProcessResult:
    """Run Git inside the sanitized snapshot authority with resource/network controls.

    ``authority`` must be a :class:`safe_io.GitSnapshotAuthority`; anything else is a
    contract error. Every invocation runs with fixed relative ``--git-dir=.git`` and
    ``--work-tree=.`` inside the retained worktree-view snapshot; callers pass only real
    Git arguments and can never influence paths. A budget exhaustion is
    ``resource_limit`` — never absence or failure credit.
    """
    if not isinstance(authority, safe_io.GitSnapshotAuthority):
        raise TypeError("git requires a GitSnapshotAuthority")
    if git_resource_profile() is None:
        return BoundedProcessResult(ProcessState.UNSUPPORTED)
    if budget.commands >= MAX_GIT_COMMANDS_PER_AUTHORITY \
            or budget.wall_seconds >= MAX_GIT_WALL_SECONDS_PER_AUTHORITY:
        return BoundedProcessResult(ProcessState.RESOURCE_LIMIT)
    argv_tail = _validate_args(args)
    try:
        cwd_handle = os.open(authority.snapshot_path,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return BoundedProcessResult(ProcessState.SPAWN_ERROR)
    try:
        start = time.monotonic()
        result = run_bounded_process(
            ToolId.GIT, ("--git-dir=.git", "--work-tree=.", *argv_tail),
            toolchain=toolchain, cwd_handle=cwd_handle, env=dict(_GIT_ENV),
            timeout_seconds=timeout_seconds)
        budget.commands += 1
        budget.wall_seconds += time.monotonic() - start
        return result
    finally:
        os.close(cwd_handle)
