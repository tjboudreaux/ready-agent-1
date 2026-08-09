"""Root-confined, bounded, no-follow filesystem authority (pure stdlib, POSIX only).

This module is the *sole* engine boundary for repository-controlled and generated-artifact
I/O. Every read, discovery walk, scaffold create, authorized replace, isolated copy, and
Git-metadata admission flows through retained directory-file-descriptor handles; no engine
code opens repository paths by pathname.

Platform contract: the engine requires a POSIX runtime with directory-fd primitives
(``O_DIRECTORY``, ``O_NOFOLLOW``, fd-relative ``open``/``stat``/``mkdir``/``unlink``/
``link``/``rename``, ``dir_fd`` enumeration, stable ``fstat``, and nonblocking
``fcntl.flock`` on directory handles). :func:`safe_io_supported` probes those primitives
once against an engine-owned temporary directory; operational CLI commands fail closed
with :data:`SAFE_IO_UNSUPPORTED_MESSAGE` when the probe fails.

Nothing here is configurable through repository files, environment, library options, or
CLI flags: every cap is an engine constant.
"""
from __future__ import annotations

import errno
import fcntl
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum

# --------------------------------------------------------------------------- messages
SAFE_IO_UNSUPPORTED_MESSAGE = (
    "ra1: safe_io_unsupported: required POSIX filesystem primitives are unavailable"
)

# ----------------------------------------------- caps (engine constants)
MAX_REPO_TEXT_BYTES = 2_097_152
MAX_DISCOVERY_ENTRIES = 200_000
MAX_DISCOVERY_MATCHES = 20_000
MAX_DISCOVERY_PATH_BYTES = 16_777_216
MAX_DISCOVERY_DEPTH = 64
MAX_CANDIDATES_PER_CRITERION = 256
MAX_CONFIG_PATTERNS = 128
MAX_CONFIG_PATTERN_BYTES = 512

MAX_CONFIG_BYTES = 1_048_576
MAX_GIT_CONFIG_BYTES = 1_048_576
MAX_GIT_SNAPSHOT_ENTRIES = 200_000
MAX_GIT_SNAPSHOT_DEPTH = 64
MAX_GIT_FILE_BYTES = 536_870_912
MAX_GIT_SNAPSHOT_BYTES = 1_073_741_824
MAX_GITDIR_FILE_BYTES = 4_096

MAX_T3_COPY_ENTRIES = 50_000
MAX_T3_COPY_DEPTH = 64
MAX_T3_FILE_BYTES = 134_217_728
MAX_T3_COPY_BYTES = 1_073_741_824

MAX_SAFE_COPY_FILES = 20_000
MAX_SAFE_COPY_FILE_BYTES = 67_108_864
MAX_SAFE_COPY_BYTES = 536_870_912

COPY_CHUNK_BYTES = 1_048_576

# Directories the discovery walk never descends into (engine-fixed).
FIXED_IGNORE_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "dist", "build",
    "__pycache__", ".mypy_cache", ".tox",
})


class RepositoryInputError(ValueError):
    """Programmer misuse of the repository-I/O boundary (bad pattern, bad limit, bad type)."""


class SafeIoUnsupportedError(RuntimeError):
    """The host lacks the required POSIX filesystem primitives."""


# --------------------------------------------------------------------------- observations
class RepoReadState(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    UNSAFE_PATH = "unsafe_path"
    OVERSIZE = "oversize"
    UNSUPPORTED = "unsupported"


class RepoDiscoveryState(StrEnum):
    OK = "ok"
    UNREADABLE = "unreadable"
    UNSAFE_PATH = "unsafe_path"
    OVERFLOW = "overflow"
    UNSUPPORTED = "unsupported"


# State -> allowlisted, data-free reason codes. A reason outside the allowlist is an engine
# contract bug and refused by the observation invariant.
READ_REASONS = {
    RepoReadState.OK: frozenset({""}),
    RepoReadState.MISSING: frozenset({"not_found"}),
    RepoReadState.UNREADABLE: frozenset({"permission_denied", "io_error", "decode_error",
                                          "stale_identity"}),
    RepoReadState.UNSAFE_PATH: frozenset({"invalid_path", "root_escape", "symlink",
                                           "hardlink", "special_file", "identity_race"}),
    RepoReadState.OVERSIZE: frozenset({"too_large"}),
    RepoReadState.UNSUPPORTED: frozenset({"unsupported_platform"}),
}

DISCOVERY_REASONS = {
    RepoDiscoveryState.OK: frozenset({""}),
    RepoDiscoveryState.UNREADABLE: frozenset({"permission_denied", "io_error"}),
    RepoDiscoveryState.UNSAFE_PATH: frozenset({"invalid_pattern", "root_escape", "symlink",
                                                "hardlink", "special_file", "identity_race"}),
    RepoDiscoveryState.OVERFLOW: frozenset({"entry_overflow", "match_overflow",
                                             "path_bytes_overflow", "depth_overflow"}),
    RepoDiscoveryState.UNSUPPORTED: frozenset({"unsupported_platform"}),
}


@dataclass(frozen=True)
class RootedBytesObservation:
    """The only return shape of the low-level read primitives. Never a partial payload."""

    state: RepoReadState
    data: bytes = b""
    reason_code: str = ""

    def __post_init__(self):
        if not isinstance(self.state, RepoReadState):
            raise RepositoryInputError("state must be a RepoReadState")
        if self.state is RepoReadState.OK:
            if self.reason_code != "":
                raise RepositoryInputError("OK observation carries no reason code")
            if not isinstance(self.data, (bytes, bytearray)):
                raise RepositoryInputError("OK observation data must be bytes")
        else:
            if self.data:
                raise RepositoryInputError("non-OK observation must have an empty payload")
            if self.reason_code not in READ_REASONS[self.state]:
                raise RepositoryInputError(
                    f"reason code {self.reason_code!r} not allowed for {self.state.value}")


@dataclass(frozen=True)
class RepoFileObservation:
    """A repository text file after exactly one strict UTF-8 mapping."""

    state: RepoReadState
    text: str = ""
    reason_code: str = ""

    def __post_init__(self):
        if not isinstance(self.state, RepoReadState):
            raise RepositoryInputError("state must be a RepoReadState")
        if self.state is RepoReadState.OK:
            if self.reason_code != "":
                raise RepositoryInputError("OK observation carries no reason code")
            if not isinstance(self.text, str):
                raise RepositoryInputError("OK observation text must be str")
        else:
            if self.text:
                raise RepositoryInputError("non-OK observation must have an empty payload")
            if self.reason_code not in READ_REASONS[self.state]:
                raise RepositoryInputError(
                    f"reason code {self.reason_code!r} not allowed for {self.state.value}")


@dataclass(frozen=True)
class RepoDiscoveryObservation:
    """The only return shape of the discovery primitive."""

    state: RepoDiscoveryState
    paths: tuple = ()
    reason_code: str = ""

    def __post_init__(self):
        if not isinstance(self.state, RepoDiscoveryState):
            raise RepositoryInputError("state must be a RepoDiscoveryState")
        if self.state is RepoDiscoveryState.OK:
            if self.reason_code != "":
                raise RepositoryInputError("OK observation carries no reason code")
            paths = tuple(self.paths)
            if any(not isinstance(p, str) or not p for p in paths):
                raise RepositoryInputError("OK discovery paths must be nonempty strings")
            if len(set(paths)) != len(paths):
                raise RepositoryInputError("OK discovery paths must be unique")
            if list(paths) != sorted(paths):
                raise RepositoryInputError("OK discovery paths must be sorted")
            object.__setattr__(self, "paths", paths)
        else:
            if self.paths:
                raise RepositoryInputError("non-OK discovery must have an empty payload")
            if self.reason_code not in DISCOVERY_REASONS[self.state]:
                raise RepositoryInputError(
                    f"reason code {self.reason_code!r} not allowed for {self.state.value}")


class PresenceState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class PresenceObservation:
    """The `exists_any` contract: never collapse an unsafe state into ``absent``."""

    state: PresenceState
    path: str = ""          # set only for PRESENT
    reason_code: str = ""   # set only for INDETERMINATE

    def __post_init__(self):
        if not isinstance(self.state, PresenceState):
            raise RepositoryInputError("state must be a PresenceState")
        if self.state is PresenceState.PRESENT:
            if not self.path or self.reason_code:
                raise RepositoryInputError("PRESENT requires a path and no reason code")
        else:
            if self.path:
                raise RepositoryInputError("non-PRESENT carries no path")
            if self.state is PresenceState.ABSENT and self.reason_code:
                raise RepositoryInputError("ABSENT carries no reason code")
            if self.state is PresenceState.INDETERMINATE and not self.reason_code:
                raise RepositoryInputError("INDETERMINATE requires a reason code")


# --------------------------------------------------------------------------- capability probe
_PROBE_STATE = {"checked": False, "supported": False}


def safe_io_supported() -> bool:
    """One cached live probe of the required POSIX directory-fd primitive set."""
    if _PROBE_STATE["checked"]:
        return _PROBE_STATE["supported"]
    _PROBE_STATE["checked"] = True
    _PROBE_STATE["supported"] = _probe()
    return _PROBE_STATE["supported"]


def _probe() -> bool:
    if os.name != "posix":
        return False
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_EXCL")
    if any(not hasattr(os, name) for name in required):
        return False
    if not hasattr(fcntl, "flock"):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="ra1-probe-") as tmp:
            root_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                # fd-relative directory mutation + traversal
                os.mkdir("sub", 0o700, dir_fd=root_fd)
                sub_fd = os.open("sub", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=root_fd)
                try:
                    file_fd = os.open("f", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                      0o600, dir_fd=sub_fd)
                    os.write(file_fd, b"x")
                    os.close(file_fd)
                    st = os.stat("f", dir_fd=sub_fd, follow_symlinks=False)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        return False
                    # no-follow stat must report a symlink as a link
                    os.link("f", "hard", src_dir_fd=sub_fd, dst_dir_fd=sub_fd)
                    os.unlink("hard", dir_fd=sub_fd)
                    os.symlink("f", os.path.join(tmp, "sub", "sym"))
                    lst = os.stat("sym", dir_fd=sub_fd, follow_symlinks=False)
                    if not stat.S_ISLNK(lst.st_mode):
                        return False
                    try:
                        os.open("sym", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sub_fd)
                        return False  # O_NOFOLLOW must refuse a final-component symlink
                    except OSError as exc:
                        if exc.errno != errno.ELOOP:
                            return False
                    os.unlink("sym", dir_fd=sub_fd)
                    # fd-based directory enumeration
                    if "f" not in os.listdir(sub_fd):
                        return False
                    # stable fstat on an open handle
                    probe_fd = os.open("f", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sub_fd)
                    try:
                        fstat_st = os.fstat(probe_fd)
                    finally:
                        os.close(probe_fd)
                    if not stat.S_ISREG(fstat_st.st_mode):
                        return False
                    os.unlink("f", dir_fd=sub_fd)
                    # nonblocking shared + exclusive flock on directory handles
                    fcntl.flock(sub_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    fcntl.flock(sub_fd, fcntl.LOCK_UN)
                    fcntl.flock(sub_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(sub_fd, fcntl.LOCK_UN)
                    # fd-relative rename / replace
                    file_fd = os.open("g", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                                      dir_fd=sub_fd)
                    os.close(file_fd)
                    os.rename("g", "h", src_dir_fd=sub_fd, dst_dir_fd=sub_fd)
                    os.unlink("h", dir_fd=sub_fd)
                finally:
                    os.close(sub_fd)
            finally:
                os.close(root_fd)
        return True
    except (OSError, AttributeError, TypeError):
        return False


def _require_supported() -> None:
    if not safe_io_supported():
        raise SafeIoUnsupportedError(SAFE_IO_UNSUPPORTED_MESSAGE)


def _force_probe_unsupported() -> None:
    """Test hook: force the capability probe to report unsupported."""
    _PROBE_STATE["checked"] = True
    _PROBE_STATE["supported"] = False


def _reset_probe() -> None:
    """Test hook: clear the cached probe result."""
    _PROBE_STATE["checked"] = False
    _PROBE_STATE["supported"] = False


# --------------------------------------------------------------------------- root authority
class RootAuthority:
    """A retained directory handle for one caller-authorized root.

    The root path is caller-selected input, so admission canonicalizes it once
    (``os.path.realpath``) to establish the physical workspace boundary, then walks the
    physical path from the filesystem root one component at a time with directory fds and
    ``O_NOFOLLOW``. Everything *beneath* this handle is repository-controlled and is only
    ever addressed fd-relative with no-follow semantics.
    """

    __slots__ = ("fd", "path")

    def __init__(self, fd: int, path: str):
        self.fd = fd
        self.path = path

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def __enter__(self) -> RootAuthority:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def acquire_root(path) -> RootAuthority:
    """Open the caller-authorized root directory as a retained no-follow handle."""
    _require_supported()
    physical = os.path.realpath(os.fspath(path))
    if not os.path.isabs(physical):
        raise RepositoryInputError("root must resolve to an absolute path")
    cur = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in (c for c in physical.split("/") if c):
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=cur)
            os.close(cur)
            cur = nxt
        st = os.fstat(cur)
        if not stat.S_ISDIR(st.st_mode):
            raise RepositoryInputError("root is not a directory")
        return RootAuthority(cur, physical)
    except Exception:
        os.close(cur)
        raise


def open_subroot(auth: RootAuthority, relpath: str) -> RootAuthority:
    """Open a directory *beneath* an admitted root as its own retained handle."""
    dir_fd, final = _walk(auth, relpath)
    try:
        fd = os.open(final, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return RootAuthority(fd, _join(auth.path, relpath))


# --------------------------------------------------------------------------- path lexical checks
_COMPONENT_RE = re.compile(r"^[^/\\]+$")


def _validate_relpath(relpath: str) -> tuple:
    """Lexically validate a repository-relative POSIX path. Returns its components.

    Raises RepositoryInputError only for programmer misuse (wrong type). Lexical content
    problems are reported to the caller via the returned empty tuple so read primitives can
    classify them as UNSAFE_PATH observations instead.
    """
    if not isinstance(relpath, str):
        raise RepositoryInputError("relpath must be a string")
    if not relpath or "\x00" in relpath or relpath.startswith("/") or "\\" in relpath:
        return ()
    if relpath.startswith("~") or re.match(r"^[A-Za-z]:", relpath):
        return ()
    parts = tuple(p for p in relpath.split("/"))
    if any(p in ("", ".", "..") for p in parts):
        return ()
    return parts


def _lexically_valid(relpath: str) -> bool:
    return bool(_validate_relpath(relpath))


def _join(root_path: str, relpath: str) -> str:
    return root_path.rstrip("/") + "/" + relpath


def _walk(auth: RootAuthority, relpath: str):
    """Walk every component but the last through retained dir fds.

    Returns ``(dir_fd, final_name)``; the caller owns ``dir_fd``. Raises OSError (ELOOP on a
    symlinked intermediate, ENOENT, …) which the observation primitives classify.
    """
    parts = _validate_relpath(relpath)
    if not parts:
        raise RepositoryInputError(f"invalid repository-relative path: {relpath!r}")
    cur = os.dup(auth.fd)
    try:
        for component in parts[:-1]:
            try:
                nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=cur)
            except OSError as exc:
                if exc.errno == errno.ENOTDIR:
                    # Darwin reports ENOTDIR (not ELOOP) for a symlinked intermediate;
                    # lstat the component to distinguish a symlink (refuse) from a plain
                    # file where a directory was expected (ordinary MISSING).
                    try:
                        st = os.stat(component, dir_fd=cur, follow_symlinks=False)
                    except OSError:
                        raise exc from None
                    if stat.S_ISLNK(st.st_mode):
                        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP),
                                      component) from exc
                raise
            os.close(cur)
            cur = nxt
        return cur, parts[-1]
    except Exception:
        os.close(cur)
        raise


def _classify_oserror(exc: OSError) -> RootedBytesObservation:
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        return RootedBytesObservation(RepoReadState.MISSING, reason_code="not_found")
    if exc.errno in (errno.EACCES, errno.EPERM):
        return RootedBytesObservation(RepoReadState.UNREADABLE,
                                      reason_code="permission_denied")
    if exc.errno == errno.ELOOP:
        return RootedBytesObservation(RepoReadState.UNSAFE_PATH, reason_code="symlink")
    return RootedBytesObservation(RepoReadState.UNREADABLE, reason_code="io_error")


# --------------------------------------------------------------------------- reads
def _read_fd_bounded(fd, max_bytes: int) -> bytes:
    """Read at most ``max_bytes + 1`` bytes from an open fd (the +1 detects oversize)."""
    chunks = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(fd, min(COPY_CHUNK_BYTES, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _stat_signature(st) -> tuple:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def read_rooted_regular(auth: RootAuthority, relpath: str, *,
                        max_bytes: int = MAX_REPO_TEXT_BYTES) -> RootedBytesObservation:
    """Read one regular file beneath the root through retained handles, bounded.

    Never returns ``None``, a raw ``OSError``, a partial payload, or a scalar fallback.
    """
    _require_supported()
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise RepositoryInputError("max_bytes must be a positive int")
    if not _lexically_valid(relpath):
        return RootedBytesObservation(RepoReadState.UNSAFE_PATH, reason_code="invalid_path")
    try:
        dir_fd, final = _walk(auth, relpath)
    except RepositoryInputError:
        return RootedBytesObservation(RepoReadState.UNSAFE_PATH, reason_code="invalid_path")
    except OSError as exc:
        return _classify_oserror(exc)
    try:
        try:
            fd = os.open(final, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=dir_fd)
        except OSError as exc:
            return _classify_oserror(exc)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                reason = "special_file" if not stat.S_ISLNK(st.st_mode) else "symlink"
                return RootedBytesObservation(RepoReadState.UNSAFE_PATH, reason_code=reason)
            if st.st_nlink != 1:
                return RootedBytesObservation(RepoReadState.UNSAFE_PATH,
                                              reason_code="hardlink")
            sig_before = _stat_signature(st)
            data = _read_fd_bounded(fd, max_bytes)
            st_after = os.fstat(fd)
            if _stat_signature(st_after) != sig_before:
                return RootedBytesObservation(RepoReadState.UNREADABLE,
                                              reason_code="stale_identity")
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
    if len(data) > max_bytes:
        return RootedBytesObservation(RepoReadState.OVERSIZE, reason_code="too_large")
    return RootedBytesObservation(RepoReadState.OK, data=data)


def read_explicit_regular(path, *, max_bytes: int = MAX_REPO_TEXT_BYTES
                          ) -> RootedBytesObservation:
    """Read one explicit caller-authorized file path (e.g. ``fix --report PATH``).

    The path is caller-selected (not repository-derived), so its components are walked from
    the filesystem root; the final component still gets no-follow/regular/single-link
    treatment and the same bound.
    """
    _require_supported()
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise RepositoryInputError("max_bytes must be a positive int")
    raw = os.fspath(path)
    if not isinstance(raw, str) or "\x00" in raw:
        return RootedBytesObservation(RepoReadState.UNSAFE_PATH, reason_code="invalid_path")
    physical = os.path.realpath(raw)
    try:
        auth = acquire_root(os.path.dirname(physical) or "/")
    except OSError as exc:
        return _classify_oserror(exc)
    try:
        return read_rooted_regular(auth, os.path.basename(physical), max_bytes=max_bytes)
    finally:
        auth.close()


# --------------------------------------------------------------------------- glob patterns
_PATTERN_MAX = MAX_CONFIG_PATTERN_BYTES


def validate_discovery_pattern(pattern: str) -> str | None:
    """Validate one pattern against the configured-pattern grammar.

    Returns an error category string, or ``None`` when the pattern is valid. Grammar:
    relative POSIX literals plus ``*``, ``?`` and ``**``; no NUL, absolute/drive/UNC forms,
    ``..``, backslash, bracket/brace/extglob, negation, or empty patterns.
    """
    if not isinstance(pattern, str):
        return "invalid_pattern"
    if not pattern or len(pattern.encode("utf-8", "replace")) > _PATTERN_MAX:
        return "invalid_pattern"
    if "\x00" in pattern or pattern.startswith("/") or pattern.startswith("!"):
        return "invalid_pattern"
    if "\\" in pattern or re.match(r"^[A-Za-z]:", pattern) or pattern.startswith("//"):
        return "invalid_pattern"
    if any(c in pattern for c in "[]{}") or pattern.startswith("(") or pattern.startswith("?"):
        return "invalid_pattern"
    if pattern.startswith("?") or pattern.startswith("+"):
        return "invalid_pattern"
    parts = pattern.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return "invalid_pattern"
        if "**" in part and part != "**":
            return "invalid_pattern"
    return None


def _segment_regex(seg: str) -> str:
    out = []
    for ch in seg:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _glob_regex(pattern: str) -> re.Pattern:
    segs = pattern.split("/")
    if all(s == "**" for s in segs):
        return re.compile(r".*")
    rx = ""
    need_slash = False
    for i, seg in enumerate(segs):
        if seg == "**":
            if i == 0:
                rx += r"(?:[^/]+/)*"
                need_slash = False
            else:
                rx += r"(?:/[^/]+)*"
                need_slash = True
            continue
        rx += ("/" if need_slash else "") + _segment_regex(seg)
        need_slash = True
    return re.compile(rx + r"\Z")


def compile_engine_patterns(patterns) -> tuple:
    """Compile engine-owned constant patterns. Programmer misuse raises (tests catch it)."""
    compiled = []
    for pat in patterns:
        err = validate_discovery_pattern(pat)
        if err is not None:
            raise RepositoryInputError(f"invalid engine discovery pattern: {pat!r}")
        compiled.append(_glob_regex(pat))
    return tuple(compiled)


# --------------------------------------------------------------------------- discovery
def discover_rooted_regular(auth: RootAuthority, patterns, *,
                            max_entries: int = MAX_DISCOVERY_ENTRIES,
                            max_matches: int = MAX_DISCOVERY_MATCHES,
                            max_path_bytes: int = MAX_DISCOVERY_PATH_BYTES,
                            max_depth: int = MAX_DISCOVERY_DEPTH,
                            ) -> RepoDiscoveryObservation:
    """Bounded no-follow discovery of regular files matching ``patterns`` beneath the root.

    Patterns are engine constants (or config patterns already validated by the caller);
    invalid grammar here is programmer misuse and raises. Repository-derived states return
    the typed observation; no partial result set is ever returned.
    """
    _require_supported()
    for name, value in (("max_entries", max_entries), ("max_matches", max_matches),
                        ("max_path_bytes", max_path_bytes), ("max_depth", max_depth)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise RepositoryInputError(f"{name} must be a positive int")
    if isinstance(patterns, str):
        patterns = [patterns]
    compiled = compile_engine_patterns(list(patterns))

    matches: set = set()
    entries = 0
    path_bytes = 0

    def overflow(reason):
        return RepoDiscoveryObservation(RepoDiscoveryState.OVERFLOW, reason_code=reason)

    # Stack of (dir_fd, relative_dir, depth). dir_fd ownership moves into the loop.
    try:
        root_dup = os.dup(auth.fd)
    except OSError as exc:
        return RepoDiscoveryObservation(
            RepoDiscoveryState.UNREADABLE,
            reason_code="permission_denied" if exc.errno in (errno.EACCES, errno.EPERM)
            else "io_error")
    stack = [(root_dup, "", 0)]
    try:
        while stack:
            dir_fd, rel_dir, depth = stack.pop()
            try:
                try:
                    names = sorted(os.listdir(dir_fd))
                except OSError as exc:
                    return RepoDiscoveryObservation(
                        RepoDiscoveryState.UNREADABLE,
                        reason_code="permission_denied"
                        if exc.errno in (errno.EACCES, errno.EPERM) else "io_error")
                for name in names:
                    if name in ("\x00",) or "/" in name:
                        return RepoDiscoveryObservation(RepoDiscoveryState.UNSAFE_PATH,
                                                        reason_code="special_file")
                    rel = f"{rel_dir}/{name}" if rel_dir else name
                    entries += 1
                    if entries > max_entries:
                        return overflow("entry_overflow")
                    path_bytes += len(rel.encode("utf-8", "replace"))
                    if path_bytes > max_path_bytes:
                        return overflow("path_bytes_overflow")
                    try:
                        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    except OSError as exc:
                        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                            continue  # vanished mid-walk: not evidence either way
                        return RepoDiscoveryObservation(
                            RepoDiscoveryState.UNREADABLE,
                            reason_code="permission_denied"
                            if exc.errno in (errno.EACCES, errno.EPERM) else "io_error")
                    if stat.S_ISDIR(st.st_mode):
                        if name in FIXED_IGNORE_DIRS:
                            continue
                        if depth + 1 > max_depth:
                            return overflow("depth_overflow")
                        try:
                            child = os.open(name,
                                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                            dir_fd=dir_fd)
                        except OSError as exc:
                            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                                continue
                            if exc.errno == errno.ELOOP:
                                return RepoDiscoveryObservation(
                                    RepoDiscoveryState.UNSAFE_PATH, reason_code="symlink")
                            return RepoDiscoveryObservation(
                                RepoDiscoveryState.UNREADABLE,
                                reason_code="permission_denied"
                                if exc.errno in (errno.EACCES, errno.EPERM) else "io_error")
                        stack.append((child, rel, depth + 1))
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue  # symlinks/specials are never matches, never descended
                    if any(rx.match(rel) for rx in compiled):
                        if st.st_nlink != 1:
                            return RepoDiscoveryObservation(
                                RepoDiscoveryState.UNSAFE_PATH, reason_code="hardlink")
                        matches.add(rel)
                        if len(matches) > max_matches:
                            return overflow("match_overflow")
            finally:
                os.close(dir_fd)
    except RepositoryInputError:
        raise
    finally:
        for leftover_fd, _rel, _depth in stack:
            try:
                os.close(leftover_fd)
            except OSError:
                pass
    return RepoDiscoveryObservation(RepoDiscoveryState.OK,
                                    paths=tuple(sorted(matches)))


def exists_rooted(auth: RootAuthority, patterns, *,
                  max_entries: int = MAX_DISCOVERY_ENTRIES,
                  max_path_bytes: int = MAX_DISCOVERY_PATH_BYTES,
                  max_depth: int = MAX_DISCOVERY_DEPTH) -> PresenceObservation:
    """`exists_any` as a three-state observation: never degrade unsafe state to ``absent``."""
    obs = discover_rooted_regular(auth, patterns, max_entries=max_entries, max_matches=1,
                                  max_path_bytes=max_path_bytes, max_depth=max_depth)
    if obs.state is RepoDiscoveryState.OK:
        if obs.paths:
            return PresenceObservation(PresenceState.PRESENT, path=obs.paths[0])
        return PresenceObservation(PresenceState.ABSENT)
    return PresenceObservation(PresenceState.INDETERMINATE,
                               reason_code=f"{obs.state.value}:{obs.reason_code}")


# --------------------------------------------------------------------------- write targets
def validate_write_target(auth: RootAuthority, relpath: str) -> tuple:
    """Validate a bounded, nonempty relative write target beneath the root.

    Returns the component tuple. Rejects NUL/absolute/drive/UNC/traversal/link parents and
    link/non-regular existing targets. Programmer misuse (wrong type) raises.
    """
    parts = _validate_relpath(relpath)
    if not parts:
        raise RepositoryInputError(f"unsafe write target: {relpath!r}")
    # Prove parent components are real no-follow directories (or missing).
    cur = os.dup(auth.fd)
    try:
        for component in parts[:-1]:
            try:
                nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=cur)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                    break  # missing components are creatable later via ensure_rooted_directory
                if exc.errno == errno.ELOOP:
                    raise RepositoryInputError(
                        f"write target has symlinked parent: {relpath!r}") from None
                raise
            os.close(cur)
            cur = nxt
        try:
            st = os.stat(parts[-1], dir_fd=cur, follow_symlinks=False)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                return parts
            raise
        if not stat.S_ISREG(st.st_mode):
            raise RepositoryInputError(f"write target is not a regular file: {relpath!r}")
    finally:
        os.close(cur)
    return parts


def ensure_rooted_directory(auth: RootAuthority, relpath: str, *, mode: int = 0o700) -> None:
    """Create only missing relative directory components with fd-relative mkdir."""
    parts = _validate_relpath(relpath)
    if not parts:
        raise RepositoryInputError(f"invalid directory path: {relpath!r}")
    cur = os.dup(auth.fd)
    try:
        for component in parts:
            try:
                os.mkdir(component, mode, dir_fd=cur)
            except FileExistsError:
                pass
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=cur)
            os.close(cur)
            cur = nxt
    finally:
        os.close(cur)


def create_rooted_exclusive(auth: RootAuthority, relpath: str, data: bytes, *,
                            mode: int = 0o600) -> bool:
    """The only scaffold writer: fd-relative ``O_EXCL`` create; never mutates an existing target.

    Returns True when the file was created, False when it already existed.
    """
    _require_supported()
    validate_write_target(auth, relpath)
    dir_fd, final = _walk_create_parents(auth, relpath)
    try:
        try:
            fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         mode, dir_fd=dir_fd)
        except FileExistsError:
            return False
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    finally:
        os.close(dir_fd)


def _walk_create_parents(auth: RootAuthority, relpath: str):
    """Create missing parent components (bounded by the validated relpath) and walk to them."""
    parts = _validate_relpath(relpath)
    if not parts:
        raise RepositoryInputError(f"invalid path: {relpath!r}")
    cur = os.dup(auth.fd)
    try:
        for component in parts[:-1]:
            try:
                nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=cur)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                    os.mkdir(component, 0o700, dir_fd=cur)
                    nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=cur)
                else:
                    raise
            os.close(cur)
            cur = nxt
        return cur, parts[-1]
    except Exception:
        os.close(cur)
        raise


def _write_all(fd, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def atomic_replace_rooted(auth: RootAuthority, relpath: str, data: bytes) -> None:
    """Authorized generated-artifact replace: temp sibling, fsync, fd-relative replace.

    Reserved for engine-generated report/history/manifest/vendor artifacts under an
    explicitly authorized root. Never follows links; refuses a non-regular final target.
    """
    _require_supported()
    validate_write_target(auth, relpath)
    dir_fd, final = _walk_create_parents(auth, relpath)
    tmp_name = f".{final}.ra1-tmp"
    try:
        try:
            st = os.stat(final, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode):
                raise RepositoryInputError(f"replace target is not regular: {relpath!r}")
            mode = stat.S_IMODE(st.st_mode)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                mode = 0o600
            else:
                raise
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     mode, dir_fd=dir_fd)
        try:
            _write_all(fd, data)
            os.fsync(fd)
            st2 = os.stat(final, dir_fd=dir_fd, follow_symlinks=False) \
                if _exists(dir_fd, final) else None
            if st2 is not None and not stat.S_ISREG(st2.st_mode):
                raise RepositoryInputError(f"replace target changed type: {relpath!r}")
        finally:
            os.close(fd)
        os.replace(tmp_name, final, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(dir_fd)


def _exists(dir_fd, name) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except OSError:
        return False


def unlink_rooted(auth: RootAuthority, relpath: str) -> bool:
    """No-follow unlink of one regular file beneath the root. False when missing."""
    dir_fd, final = _walk(auth, relpath)
    try:
        st = os.stat(final, dir_fd=dir_fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise RepositoryInputError(f"unlink target is not regular: {relpath!r}")
        os.unlink(final, dir_fd=dir_fd)
        os.fsync(dir_fd)
        return True
    except FileNotFoundError:
        return False
    finally:
        os.close(dir_fd)


# --------------------------------------------------------------------------- directory locks
def lock_directory(fd: int, *, exclusive: bool) -> bool:
    """Nonblocking flock on a directory handle. False when contended."""
    op = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    try:
        fcntl.flock(fd, op)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def unlock_directory(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)


# --------------------------------------------------------------------------- policy JSON merge
_POLICY_TARGETS = frozenset({".ra1/config.json", ".ra1/waivers.json"})
_ANSWER_LOCK = ".ra1/.answer.lock"


def merge_rooted_policy_json(auth: RootAuthority, relpath: str, mutate) -> tuple:
    """The sole policy-write authority for the two reviewable ``.ra1`` JSON inputs.

    ``relpath`` must be exactly ``.ra1/config.json`` or ``.ra1/waivers.json``. ``mutate`` is
    an engine callback ``mutate(parsed) -> new_object`` that changes only its one rederived
    key/waiver set. Returns ``(created, new_object)``. Coordinates RA1 writers through an
    engine-owned lock file and revalidates content before the final replace.
    """
    _require_supported()
    if relpath not in _POLICY_TARGETS:
        raise RepositoryInputError(f"not a policy merge target: {relpath!r}")
    ensure_rooted_directory(auth, ".ra1", mode=0o755)
    lock_auth_dir = os.dup(auth.fd)
    lock_fd = None
    try:
        ra1_fd = os.open(".ra1", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=lock_auth_dir)
        try:
            lock_fd = os.open(".answer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                              0o600, dir_fd=ra1_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            final = relpath.rsplit("/", 1)[-1]
            existing_obs = _read_dir_regular(ra1_fd, final, MAX_CONFIG_BYTES)
            created = existing_obs.state is RepoReadState.MISSING
            if existing_obs.state is RepoReadState.OK:
                import json as _json
                try:
                    parsed = _json.loads(existing_obs.data.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise RepositoryInputError(
                        f"policy file is not valid JSON: {relpath}") from exc
            elif existing_obs.state is RepoReadState.MISSING:
                parsed = None
            else:
                raise RepositoryInputError(
                    f"policy file unreadable ({existing_obs.state.value}): {relpath}")
            new_value = mutate(parsed)
            import json as _json
            data = (_json.dumps(new_value, indent=2) + "\n").encode("utf-8")
            if created:
                fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o644, dir_fd=ra1_fd)
                try:
                    _write_all(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:
                tmp = f".{final}.ra1-tmp"
                try:
                    os.unlink(tmp, dir_fd=ra1_fd)
                except FileNotFoundError:
                    pass
                st = os.stat(final, dir_fd=ra1_fd, follow_symlinks=False)
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             stat.S_IMODE(st.st_mode), dir_fd=ra1_fd)
                try:
                    _write_all(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                # Revalidate before the final replace: content drift under the lock refuses.
                recheck = _read_dir_regular(ra1_fd, final, MAX_CONFIG_BYTES)
                if (recheck.state is not RepoReadState.OK
                        or recheck.data != existing_obs.data):
                    try:
                        os.unlink(tmp, dir_fd=ra1_fd)
                    except OSError:
                        pass
                    raise RepositoryInputError(f"policy file changed during merge: {relpath}")
                os.replace(tmp, final, src_dir_fd=ra1_fd, dst_dir_fd=ra1_fd)
            os.fsync(ra1_fd)
            return created, new_value
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            os.close(ra1_fd)
    finally:
        os.close(lock_auth_dir)


def _read_dir_regular(dir_fd, name, max_bytes) -> RootedBytesObservation:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError as exc:
        return _classify_oserror(exc)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return RootedBytesObservation(RepoReadState.UNSAFE_PATH, reason_code="special_file")
        if st.st_nlink != 1:
            return RootedBytesObservation(RepoReadState.UNSAFE_PATH, reason_code="hardlink")
        data = _read_fd_bounded(fd, max_bytes)
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        return RootedBytesObservation(RepoReadState.OVERSIZE, reason_code="too_large")
    return RootedBytesObservation(RepoReadState.OK, data=data)


# --------------------------------------------------------------------------- safe copy tree
def safe_copy_tree(src_auth: RootAuthority, dst_auth: RootAuthority, *,
                   max_entries: int = MAX_T3_COPY_ENTRIES,
                   max_depth: int = MAX_T3_COPY_DEPTH,
                   max_file_bytes: int = MAX_T3_FILE_BYTES,
                   max_total_bytes: int = MAX_T3_COPY_BYTES,
                   exclude_names: frozenset = frozenset({".git", ".agents"}),
                   exclude_root_paths: frozenset = frozenset({".ra1/reports"}),
                   ) -> None:
    """Copy stable single-link regular files/dirs beneath a root, preserving permission bits.

    Raises RepositoryInputError on links, special files, swaps, or cap overflow; the caller
    removes the partial destination tree. Never follows symlinks.
    """
    _require_supported()
    entries = 0
    total_bytes = 0
    stack = [(os.dup(src_auth.fd), os.dup(dst_auth.fd), "", 0)]
    try:
        while stack:
            sfd, dfd, rel_dir, depth = stack.pop()
            try:
                names = sorted(os.listdir(sfd))
                for name in names:
                    rel = f"{rel_dir}/{name}" if rel_dir else name
                    if name in exclude_names or rel in exclude_root_paths:
                        continue
                    entries += 1
                    if entries > max_entries:
                        raise RepositoryInputError("safe copy entry cap exceeded")
                    st = os.stat(name, dir_fd=sfd, follow_symlinks=False)
                    if stat.S_ISDIR(st.st_mode):
                        if depth + 1 > max_depth:
                            raise RepositoryInputError("safe copy depth cap exceeded")
                        os.mkdir(name, stat.S_IMODE(st.st_mode), dir_fd=dfd)
                        stack.append((
                            os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=sfd),
                            os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=dfd),
                            rel, depth + 1))
                        continue
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise RepositoryInputError(f"unsafe copy member: {rel!r}")
                    if st.st_size > max_file_bytes:
                        raise RepositoryInputError("safe copy file size cap exceeded")
                    total_bytes += st.st_size
                    if total_bytes > max_total_bytes:
                        raise RepositoryInputError("safe copy total size cap exceeded")
                    s_file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=sfd)
                    try:
                        s_st = os.fstat(s_file)
                        if not stat.S_ISREG(s_st.st_mode) or s_st.st_nlink != 1:
                            raise RepositoryInputError(
                                f"unsafe copy member (swapped): {rel!r}")
                        sig = _stat_signature(s_st)
                        d_file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                         | os.O_NOFOLLOW, stat.S_IMODE(st.st_mode),
                                         dir_fd=dfd)
                        try:
                            remaining = st.st_size
                            while remaining > 0:
                                chunk = os.read(s_file, min(COPY_CHUNK_BYTES, remaining))
                                if not chunk:
                                    break
                                _write_all(d_file, chunk)
                                remaining -= len(chunk)
                            os.fsync(d_file)
                        finally:
                            os.close(d_file)
                        if _stat_signature(os.fstat(s_file)) != sig:
                            raise RepositoryInputError(f"copy source changed: {rel!r}")
                    finally:
                        os.close(s_file)
            finally:
                os.close(sfd)
                os.close(dfd)
    finally:
        while stack:
            sfd, dfd, _rel, _depth = stack.pop()
            os.close(sfd)
            os.close(dfd)


# --------------------------------------------------------------------------- Git authority
@dataclass(frozen=True)
class GitSnapshotAuthority:
    """An immutable, engine-owned, flattened ``.git`` snapshot plus its sanitized projection.

    ``snapshot_path`` is the retained worktree-view snapshot root: it contains the flattened
    ``.git`` and, on demand, a copy-only view of the worktree so Git always runs with fixed
    relative ``--git-dir=.git --work-tree=.`` beneath it. The view is never hardlinked:
    linking would mutate repository inodes (``st_nlink``/ctime) and trip the engine's own
    multiply-linked-file refusals.
    """

    snapshot_path: str          # engine temp root: ".git/" (+ lazily built worktree view)
    origin: tuple = ()          # sanitized (host, owner, name) or ()
    origin_malformed: bool = False  # a present origin URL failed identity normalization
    metadata_profile: str = ""  # "primary" | "linked_worktree"
    object_format: str = "sha1"
    _tempdir: object = field(default=None, compare=False, repr=False)
    _state: dict = field(default=None, compare=False, repr=False)

    def close(self) -> None:
        if self._state is not None:
            root_auth = self._state.get("root_auth")
            if root_auth is not None:
                root_auth.close()
                self._state["root_auth"] = None
        if self._tempdir is not None:
            self._tempdir.cleanup()

    def ensure_gitignore_view(self) -> None:
        """Copy only ``.gitignore`` files (plus parent dirs) into the view, for check-ignore."""
        if self._state is None:
            raise TypeError("authority is closed")
        if not self._state.get("gitignore_view"):
            _build_view(self._state["root_auth"], self, stats=self._state["stats"],
                        only_gitignore=True)
            self._state["gitignore_view"] = True

    def ensure_full_view(self) -> None:
        """Copy the full worktree (minus ``.git`` and engine-fixed ignores) into the view."""
        if self._state is None:
            raise TypeError("authority is closed")
        if not self._state.get("full_view"):
            _build_view(self._state["root_auth"], self, stats=self._state["stats"],
                        only_gitignore=False)
            self._state["full_view"] = True
            self._state["gitignore_view"] = True


@dataclass(frozen=True)
class GitAuthorityRefusal:
    """Why Git authority could not be established (typed, data-free)."""

    reason: str  # no_git | unsafe_metadata | unsupported_topology | overflow | io_error


# Config keys that make Git follow repository-selected external state: never admitted.
_GIT_CONFIG_REJECT_KEYS = re.compile(
    r"(?i)^(include|includeif)\.|^include$|^includeif$|"
    r"promisor|partialclone|alternates|extensions\.(worktreeconfig|objectformat-?!)|"
    r"^core\.worktree$"
)
_ALLOWED_EXTENSIONS = {"objectformat": {"sha1", "sha256"}, "refstorage": {"reftable"}}

# Files/dirs flattened into the snapshot from a metadata root, source-relative.
_PRIMARY_ALLOWLIST = frozenset({
    "HEAD", "packed-refs", "shallow", "grafts", "index", "objects", "refs", "reftable",
    "commit-graph",
})
_LINKED_COMMON_ALLOWLIST = frozenset({
    "objects", "refs", "packed-refs", "reftable", "shallow", "grafts", "commit-graph",
})
_LINKED_OWN_ALLOWLIST = frozenset({"HEAD", "index"})


def acquire_git_authority(auth: RootAuthority):
    """Admit the repository's Git metadata and return an immutable snapshot authority.

    Accepts exactly two topologies: a primary checkout (root ``.git`` directory) or a
    standard reciprocal current-user linked worktree (root ``.git`` gitfile). Returns a
    :class:`GitSnapshotAuthority` or :class:`GitAuthorityRefusal`. The snapshot is the only
    thing Git ever executes against; repository-selected external metadata is never
    followed at Git time.
    """
    _require_supported()
    try:
        st = os.stat(".git", dir_fd=auth.fd, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return GitAuthorityRefusal("no_git")
        return GitAuthorityRefusal("io_error")
    if stat.S_ISDIR(st.st_mode):
        return _admit_primary(auth)
    if stat.S_ISREG(st.st_mode):
        return _admit_linked(auth)
    return GitAuthorityRefusal("unsafe_metadata")


def _owned_safe(st) -> bool:
    """Current-euid-owned and not group/world writable."""
    return (st.st_uid == os.geteuid()
            and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)))


def _same_identity(fd_a: int, fd_b: int) -> bool:
    """Identity proof by ``(st_dev, st_ino)`` — never by path strings."""
    a, b = os.fstat(fd_a), os.fstat(fd_b)
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _walk_candidate(base_fd: int | None, path_text: str, *, expect: str):
    """Descriptor-walk an absolute or base-relative candidate, component by component.

    No ``realpath``, no pathname opens: every component is opened fd-relative with
    ``O_NOFOLLOW`` (a symlink anywhere rejects), and repository-supplied ``..`` components
    are refused (only engine-constructed constants may walk upwards, and never through
    this function). ``expect`` is ``"dir"`` or ``"file"`` for the final component.
    Returns an owned fd for the target.
    """
    if not path_text or "\x00" in path_text:
        raise RepositoryInputError("empty or NUL-bearing candidate path")
    if os.path.isabs(path_text):
        cur = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        parts = [p for p in path_text.split("/") if p]
    else:
        if base_fd is None:
            raise RepositoryInputError("relative candidate without a base authority")
        cur = os.dup(base_fd)
        parts = [p for p in path_text.split("/") if p and p != "."]
    if not parts:
        os.close(cur)
        raise RepositoryInputError("candidate path has no components")
    out = None
    try:
        for component in parts[:-1]:
            if component == "..":
                raise RepositoryInputError("candidate path may not contain '..'")
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=cur)
            os.close(cur)
            cur = None
            cur = nxt
        final = parts[-1]
        if final == "..":
            raise RepositoryInputError("candidate path may not contain '..'")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if expect == "dir":
            flags |= os.O_DIRECTORY
        out = os.open(final, flags, dir_fd=cur)
        os.close(cur)
        cur = None
        st = os.fstat(out)
        if expect == "dir" and not stat.S_ISDIR(st.st_mode):
            raise RepositoryInputError("candidate is not a directory")
        if expect == "file" and not stat.S_ISREG(st.st_mode):
            raise RepositoryInputError("candidate is not a regular file")
        result = out
        out = None
        return result
    finally:
        if cur is not None:
            os.close(cur)
        if out is not None:
            os.close(out)


def _admit_primary(auth: RootAuthority):
    st = os.stat(".git", dir_fd=auth.fd, follow_symlinks=False)
    if not _owned_safe(st):
        return GitAuthorityRefusal("unsafe_metadata")
    try:
        meta = open_subroot(auth, ".git")
    except OSError:
        return GitAuthorityRefusal("io_error")
    try:
        projection = _project_git_config(meta)
        if isinstance(projection, GitAuthorityRefusal):
            return projection
        return _flatten(auth, meta, None, projection, "primary")
    finally:
        meta.close()


def _read_bounded_fd(fd, max_bytes) -> bytes | None:
    data = _read_fd_bounded(fd, max_bytes)
    return data if len(data) <= max_bytes else None


def _admit_linked(auth: RootAuthority):
    st = os.stat(".git", dir_fd=auth.fd, follow_symlinks=False)
    if not _owned_safe(st) or st.st_nlink != 1:
        return GitAuthorityRefusal("unsafe_metadata")
    if st.st_size > MAX_GITDIR_FILE_BYTES:
        return GitAuthorityRefusal("unsafe_metadata")
    fd = os.open(".git", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=auth.fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return GitAuthorityRefusal("unsafe_metadata")
        raw = _read_bounded_fd(fd, MAX_GITDIR_FILE_BYTES)
    finally:
        os.close(fd)
    if raw is None:
        return GitAuthorityRefusal("unsafe_metadata")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return GitAuthorityRefusal("unsafe_metadata")
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        return GitAuthorityRefusal("unsupported_topology")
    target = lines[0][len("gitdir: "):].strip()
    if not target or "\x00" in target or any(ord(c) < 0x20 for c in target):
        return GitAuthorityRefusal("unsafe_metadata")
    # The linked worktree ID (final component) must match the exact ID grammar before any
    # walk: parent identity alone would admit spaces, control-adjacent, or oversized IDs.
    worktree_id = target.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", worktree_id or ""):
        return GitAuthorityRefusal("unsupported_topology")
    # Descriptor-walk the candidate (relative from the worktree root, or absolute from the
    # filesystem root) with no realpath and no symlinked components.
    try:
        linked_fd = _walk_candidate(auth.fd, target, expect="dir")
    except (OSError, RepositoryInputError):
        return GitAuthorityRefusal("unsafe_metadata")
    linked = RootAuthority(linked_fd, target)
    try:
        if not _owned_safe(os.fstat(linked.fd)):
            return GitAuthorityRefusal("unsafe_metadata")
        # commondir must be exactly "../..": the linked dir's grandparent is the common dir.
        commondir = _read_metadata_file(linked, "commondir", 64)
        if commondir != "../..":
            return GitAuthorityRefusal("unsupported_topology")
        try:
            parent_fd = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=linked.fd)
        except OSError:
            return GitAuthorityRefusal("unsafe_metadata")
        try:
            common_fd = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent_fd)
        except OSError:
            os.close(parent_fd)
            return GitAuthorityRefusal("unsafe_metadata")
        common = RootAuthority(common_fd, "")
        try:
            if not _owned_safe(os.fstat(common.fd)):
                return GitAuthorityRefusal("unsupported_topology")
            # Exact physical shape <common>/worktrees/<id>: the linked dir's parent must be
            # <common>/worktrees, proven by identity — never by path strings.
            try:
                worktrees_fd = os.open("worktrees",
                                       os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=common.fd)
            except OSError:
                return GitAuthorityRefusal("unsupported_topology")
            try:
                if not _same_identity(parent_fd, worktrees_fd):
                    return GitAuthorityRefusal("unsupported_topology")
            finally:
                os.close(worktrees_fd)
            # The linked dir's gitdir back-reference must resolve to the root .git file,
            # proven by descriptor-walk plus (st_dev, st_ino) identity.
            back = _read_metadata_file(linked, "gitdir", MAX_GITDIR_FILE_BYTES)
            if back is None:
                return GitAuthorityRefusal("unsupported_topology")
            back = back.rstrip("\n")
            try:
                back_fd = _walk_candidate(linked.fd, back, expect="file")
            except (OSError, RepositoryInputError):
                return GitAuthorityRefusal("unsupported_topology")
            try:
                root_git_fd = os.open(".git", os.O_RDONLY | os.O_NOFOLLOW
                                      | os.O_NONBLOCK, dir_fd=auth.fd)
                try:
                    if not _same_identity(back_fd, root_git_fd):
                        return GitAuthorityRefusal("unsupported_topology")
                finally:
                    os.close(root_git_fd)
            finally:
                os.close(back_fd)
            projection = _project_git_config(common)
            if isinstance(projection, GitAuthorityRefusal):
                return projection
            return _flatten(auth, common, linked, projection, "linked_worktree")
        finally:
            os.close(parent_fd)
            common.close()
    finally:
        linked.close()


def _read_metadata_file(meta: RootAuthority, name: str, max_bytes: int) -> str | None:
    """Read one admitted linked/common metadata file with ownership/mode proof.

    Every admitted metadata file must be current-euid-owned, non-group/world-writable,
    no-follow, regular, and stable across the read — its contents authorize topology, so
    shared or writable metadata can never admit a worktree.
    """
    try:
        st = os.stat(name, dir_fd=meta.fd, follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or not _owned_safe(st):
        return None
    obs = read_rooted_regular(meta, name, max_bytes=max_bytes)
    if obs.state is not RepoReadState.OK:
        return None
    try:
        return obs.data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None


def _project_git_config(meta: RootAuthority):
    """Parse bounded admitted config; reject external-state syntax; project sanitized fields.

    Returns ``{"origin": (host, owner, name)|(), "object_format": str, "refstorage": str}``
    or a :class:`GitAuthorityRefusal`.
    """
    obs = read_rooted_regular(meta, "config", max_bytes=MAX_GIT_CONFIG_BYTES)
    if obs.state is RepoReadState.MISSING:
        return GitAuthorityRefusal("unsupported_topology")
    if obs.state is not RepoReadState.OK:
        return GitAuthorityRefusal("unsafe_metadata")
    try:
        text = obs.data.decode("utf-8")
    except UnicodeDecodeError:
        return GitAuthorityRefusal("unsupported_topology")
    import configparser
    parser = configparser.RawConfigParser(strict=True)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error:
        return GitAuthorityRefusal("unsupported_topology")
    origin = ()
    projection_malformed = False
    object_format = "sha1"
    refstorage = ""
    filemode = True
    for section in parser.sections():
        lower = section.lower()
        for key, value in parser.items(section):
            k = f"{lower}.{key.lower()}"
            v = (value or "").strip()
            if k.startswith(("include.", "includeif.")) or k in ("include", "includeif"):
                return GitAuthorityRefusal("unsupported_topology")
            if "promisor" in k or "partialclone" in k or "alternates" in k:
                return GitAuthorityRefusal("unsupported_topology")
            if k == "core.worktree":
                return GitAuthorityRefusal("unsupported_topology")
            if k == "core.bare" and v.lower() in ("true", "yes", "on", "1"):
                return GitAuthorityRefusal("unsupported_topology")
            if k == "core.filemode":
                filemode = v.lower() not in ("false", "no", "off", "0")
            if k.startswith("extensions."):
                name = k[len("extensions."):]
                allowed = _ALLOWED_EXTENSIONS.get(name)
                if allowed is None or v.lower() not in allowed:
                    return GitAuthorityRefusal("unsupported_topology")
                if name == "objectformat":
                    object_format = v.lower()
                elif name == "refstorage":
                    refstorage = v.lower()
            if lower == 'remote "origin"' and key.lower() == "url":
                if not v:
                    projection_malformed = True
                else:
                    origin = _parse_origin_identity(v)
                    if not origin:
                        projection_malformed = True
    # Promisor/alternates marker files are also rejected.
    for marker in ("objects/info/alternates", "objects/info/http-alternates"):
        probe = read_rooted_regular(meta, marker, max_bytes=64)
        if probe.state is RepoReadState.OK:
            return GitAuthorityRefusal("unsupported_topology")
    return {"origin": origin, "origin_malformed": projection_malformed,
            "object_format": object_format, "refstorage": refstorage,
            "filemode": filemode}


def _parse_origin_identity(url: str) -> tuple:
    """Sanitized ``(host, owner, name)`` from an origin URL; ``()`` when unparsable."""
    from urllib.parse import unquote, urlsplit
    u = url.strip()
    if "@" in u and "://" not in u.split("@", 1)[0]:
        # scp-style git@host:owner/repo.git
        try:
            host_path = u.split("@", 1)[1]
            host, path = host_path.split(":", 1)
        except ValueError:
            return ()
    else:
        try:
            split = urlsplit(u)
        except ValueError:
            return ()
        host = split.hostname or ""
        path = split.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    try:
        path = unquote(path, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return ()
    parts = [p for p in path.split("/") if p]
    if not host or len(parts) < 1:
        return ()
    owner = "/".join(parts[:-1])
    name = parts[-1]
    return (host, owner, name)


def _flatten(auth: RootAuthority, common: RootAuthority, linked: RootAuthority | None,
             projection: dict, profile: str):
    """Copy the admitted metadata allowlist into one engine-owned standalone snapshot."""
    tmp = tempfile.TemporaryDirectory(prefix="ra1-git-")
    try:
        snap_root = acquire_root(tmp.name)
        try:
            os.mkdir(".git", 0o700, dir_fd=snap_root.fd)
            snap_git = open_subroot(snap_root, ".git")
            try:
                allow = (_PRIMARY_ALLOWLIST if linked is None
                         else _LINKED_COMMON_ALLOWLIST)
                stats = {"entries": 0, "bytes": 0}
                ok = _copy_git_allowlist(common, snap_git, allow, stats)
                if not ok:
                    return GitAuthorityRefusal("overflow")
                if linked is not None:
                    for name in _LINKED_OWN_ALLOWLIST:
                        obs_state = _copy_one_metadata(linked, snap_git, name, stats)
                        if obs_state == "overflow":
                            return GitAuthorityRefusal("overflow")
                    # Referenced shared index files ride along with the linked index.
                    try:
                        names = os.listdir(linked.fd)
                    except OSError:
                        names = []
                    for name in sorted(names):
                        if name.startswith("sharedindex."):
                            state = _copy_one_metadata(linked, snap_git, name, stats)
                            if state == "overflow":
                                return GitAuthorityRefusal("overflow")
                _write_sanitized_config(snap_git, projection, profile)
            finally:
                snap_git.close()
        finally:
            snap_root.close()
        authority = GitSnapshotAuthority(
            snapshot_path=os.path.join(tmp.name),
            origin=projection.get("origin") or (),
            origin_malformed=bool(projection.get("origin_malformed")),
            metadata_profile=profile,
            object_format=projection.get("object_format", "sha1"),
            _tempdir=tmp,
            _state={"root_auth": acquire_root(auth.path), "stats": stats,
                    "gitignore_view": False, "full_view": False},
        )
        return authority
    except RepositoryInputError:
        tmp.cleanup()
        return GitAuthorityRefusal("overflow")
    except OSError:
        tmp.cleanup()
        return GitAuthorityRefusal("io_error")
    except BaseException:
        tmp.cleanup()
        raise


def _copy_git_allowlist(src: RootAuthority, dst: RootAuthority, allow, stats) -> bool:
    """Fd-copy allowlisted metadata entries. False on cap overflow; raises on unsafe input."""
    for name in sorted(allow):
        state = _copy_one_metadata(src, dst, name, stats)
        if state == "overflow":
            return False
    return True


def _copy_one_metadata(src: RootAuthority, dst: RootAuthority, name: str, stats) -> str:
    try:
        st = os.stat(name, dir_fd=src.fd, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return "absent"
        raise RepositoryInputError(f"unreadable git metadata: {name}") from None
    if stat.S_ISDIR(st.st_mode):
        os.mkdir(name, 0o700, dir_fd=dst.fd)
        stats["entries"] += 1
        if stats["entries"] > MAX_GIT_SNAPSHOT_ENTRIES:
            return "overflow"
        s_sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=src.fd)
        d_sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dst.fd)
        try:
            _copy_git_dir(s_sub, d_sub, name, stats, 1)
        finally:
            os.close(s_sub)
            os.close(d_sub)
        return "ok"
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise RepositoryInputError(f"unsafe git metadata member: {name}")
    _copy_git_file(src.fd, dst.fd, name, st, stats)
    return "ok"


def _copy_git_dir(sfd, dfd, rel: str, stats: dict, depth: int) -> None:
    if depth > MAX_GIT_SNAPSHOT_DEPTH:
        raise RepositoryInputError("git snapshot depth cap exceeded")
    for name in sorted(os.listdir(sfd)):
        child_rel = f"{rel}/{name}"
        st = os.stat(name, dir_fd=sfd, follow_symlinks=False)
        stats["entries"] += 1
        if stats["entries"] > MAX_GIT_SNAPSHOT_ENTRIES:
            raise RepositoryInputError("git snapshot entry cap exceeded")
        if stat.S_ISDIR(st.st_mode):
            os.mkdir(name, 0o700, dir_fd=dfd)
            s_sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=sfd)
            d_sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            try:
                _copy_git_dir(s_sub, d_sub, child_rel, stats, depth + 1)
            finally:
                os.close(s_sub)
                os.close(d_sub)
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise RepositoryInputError(f"unsafe git metadata member: {child_rel}")
        _copy_git_file(sfd, dfd, name, st, stats)


def _copy_git_file(sfd, dfd, name: str, st, stats: dict) -> None:
    if st.st_size > MAX_GIT_FILE_BYTES:
        raise RepositoryInputError("git snapshot file size cap exceeded")
    stats["bytes"] += st.st_size
    if stats["bytes"] > MAX_GIT_SNAPSHOT_BYTES:
        raise RepositoryInputError("git snapshot total size cap exceeded")
    s_file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=sfd)
    try:
        s_st = os.fstat(s_file)
        if not stat.S_ISREG(s_st.st_mode) or s_st.st_nlink != 1:
            raise RepositoryInputError(
                f"unsafe git metadata member (swapped): {name}")
        sig = _stat_signature(s_st)
        d_file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=dfd)
        try:
            remaining = st.st_size
            while remaining > 0:
                chunk = os.read(s_file, min(COPY_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                _write_all(d_file, chunk)
                remaining -= len(chunk)
        finally:
            os.close(d_file)
        if _stat_signature(os.fstat(s_file)) != sig:
            raise RepositoryInputError(f"git metadata changed during copy: {name}")
    finally:
        os.close(s_file)


def _view_file_copy(sfd, dfd, name: str, st, stats: dict) -> None:
    """Copy one worktree file into the view, preserving permission bits and mtime."""
    if st.st_size > MAX_GIT_FILE_BYTES:
        raise RepositoryInputError("git snapshot file size cap exceeded")
    stats["bytes"] += st.st_size
    if stats["bytes"] > MAX_GIT_SNAPSHOT_BYTES:
        raise RepositoryInputError("git snapshot total size cap exceeded")
    s_file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=sfd)
    try:
        s_st = os.fstat(s_file)
        if not stat.S_ISREG(s_st.st_mode) or s_st.st_nlink != 1:
            raise RepositoryInputError(
                f"unsafe worktree member (swapped): {name}")
        sig = _stat_signature(s_st)
        try:
            d_file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             stat.S_IMODE(st.st_mode), dir_fd=dfd)
        except FileExistsError:
            return  # an earlier view tier already copied this exact path
        try:
            remaining = st.st_size
            while remaining > 0:
                chunk = os.read(s_file, min(COPY_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                _write_all(d_file, chunk)
                remaining -= len(chunk)
        finally:
            os.close(d_file)
        os.utime(name, ns=(st.st_atime_ns, st.st_mtime_ns), dir_fd=dfd,
                 follow_symlinks=False)
        if _stat_signature(os.fstat(s_file)) != sig:
            raise RepositoryInputError(f"worktree file changed during view copy: {name}")
    finally:
        os.close(s_file)


def _view_dir(sfd, dfd, rel: str, depth: int, stats: dict, view_root_abs: str,
              only_gitignore: bool) -> None:
    if depth > MAX_GIT_SNAPSHOT_DEPTH:
        raise RepositoryInputError("git snapshot depth cap exceeded")
    for name in sorted(os.listdir(sfd)):
        if name == ".git":
            continue  # every .git name is foreign metadata; the snapshot carries its own
        child_rel = f"{rel}/{name}" if rel else name
        try:
            st = os.stat(name, dir_fd=sfd, follow_symlinks=False)
        except OSError as exc:
            raise RepositoryInputError(f"worktree view member vanished: {child_rel}") from exc
        stats["entries"] += 1
        if stats["entries"] > MAX_GIT_SNAPSHOT_ENTRIES:
            raise RepositoryInputError("git snapshot entry cap exceeded")
        if stat.S_ISDIR(st.st_mode):
            try:
                os.mkdir(name, stat.S_IMODE(st.st_mode), dir_fd=dfd)
            except FileExistsError:
                pass
            s_sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=sfd)
            d_sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            try:
                _view_dir(s_sub, d_sub, child_rel, depth + 1, stats, view_root_abs,
                          only_gitignore)
            finally:
                os.close(s_sub)
                os.close(d_sub)
            continue
        if only_gitignore and name != ".gitignore":
            continue
        if not stat.S_ISREG(st.st_mode):
            # Links and special files are never partial evidence: the view refuses.
            raise RepositoryInputError(f"unsafe worktree view member: {child_rel}")
        if st.st_nlink != 1:
            raise RepositoryInputError(f"multiply-linked worktree member: {child_rel}")
        _view_file_copy(sfd, dfd, name, st, stats)


def _build_view(auth: RootAuthority, authority: GitSnapshotAuthority, *, stats: dict,
                only_gitignore: bool) -> None:
    """Populate the snapshot root with a copy-only worktree view tier."""
    view = acquire_root(authority.snapshot_path)
    src = os.dup(auth.fd)
    dst = os.dup(view.fd)
    try:
        _view_dir(src, dst, "", 0, stats, view.path, only_gitignore)
    finally:
        os.close(src)
        os.close(dst)
        view.close()


def _write_sanitized_config(snap_git: RootAuthority, projection: dict, profile: str) -> None:
    """The snapshot gets an engine-authored config only — never the repository's raw one.

    ``checkstat = minimal`` + ``trustctime = false`` let the copy-only worktree view hit
    git's mtime/size fast path (copies cannot preserve inode/dev/ctime); correctness still
    comes from content hashing when those differ.
    """
    lines = [
        "[core]",
        "\trepositoryformatversion = 1" if projection.get("object_format") != "sha1"
        or projection.get("refstorage") else "\trepositoryformatversion = 0",
        "\tbare = false",
        f"\tfilemode = {'true' if projection.get('filemode', True) else 'false'}",
        "\tlogallrefupdates = false",
        "\tcheckstat = minimal",
        "\ttrustctime = false",
    ]
    if projection.get("object_format") and projection["object_format"] != "sha1":
        lines += ["[extensions]", f"\tobjectformat = {projection['object_format']}"]
        if projection.get("refstorage"):
            lines.append(f"\trefstorage = {projection['refstorage']}")
    elif projection.get("refstorage"):
        lines += ["[extensions]", f"\trefstorage = {projection['refstorage']}"]
    fd = os.open("config", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=snap_git.fd)
    try:
        _write_all(fd, ("\n".join(lines) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
