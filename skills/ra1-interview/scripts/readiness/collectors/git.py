"""T1 git evidence: read-only history facts (never executes repo code).

Every fact is a lossless :class:`CollectorObservation`: confirmed presence/absence, tool
unavailability, and unreadable state are never collapsed into one another. Production
commands run only inside the sanitized immutable Git snapshot authority through the
bounded process launcher; tests inject a runner returning the same
:class:`process.BoundedProcessResult` type (legacy scalar injections raise ``TypeError``).
"""
from __future__ import annotations

from pathlib import Path

from .. import process, safe_io
from ._observation import CollectorObservation as Obs
from ._observation import absent, present, unavailable, unreadable

_AGENT_MARKERS = (
    "claude", "droid", "factory", "codex", "cursor", "copilot", "gemini",
    "devin", "aider", "sweep", "co-authored-by: claude", "generated with",
)


class GitCollector:
    def __init__(self, root, *, toolchain: process.Toolchain | None = None,
                 authority=None, runner=None, static=None):
        self.root = Path(root)
        self._toolchain = toolchain
        self._authority = authority          # explicit injection (tests)
        self._authority_state = "pending"    # pending | ready | refusal
        self._refusal = None
        self._runner = runner                # test injection: fn(args) -> BoundedProcessResult
        self._static = static
        self._budget = process.GitBudget()
        self._cache: dict = {}
        self.collection_complete = True

    def close(self) -> None:
        if isinstance(self._authority, safe_io.GitSnapshotAuthority):
            self._authority.close()
            self._authority = None

    # ----- authority ---------------------------------------------------------------------
    def _admit(self) -> None:
        if self._authority_state != "pending":
            return
        self._authority_state = "ready"
        if self._runner is not None:
            return  # injected runner: no production authority is acquired
        if self._authority is not None:
            return  # injected authority (tests)
        if self._toolchain is None:
            self._toolchain = process.resolve_toolchain(self.root)
        if self._toolchain.get(process.ToolId.GIT) is None:
            self._authority_state = "refusal"
            self._refusal = "engine git unavailable"
            return
        if process.git_resource_profile() is None:
            self._authority_state = "refusal"
            self._refusal = "unsupported platform git profile"
            return
        try:
            auth = safe_io.acquire_root(self.root)
        except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
            self._authority_state = "refusal"
            self._refusal = "io_error"
            return
        result = safe_io.acquire_git_authority(auth)
        auth.close()
        if isinstance(result, safe_io.GitAuthorityRefusal):
            self._authority_state = "refusal"
            self._refusal = result.reason
            return
        self._authority = result

    def _run(self, args: tuple) -> process.BoundedProcessResult:
        self._admit()
        if self._runner is not None:
            result = self._runner(tuple(args))
            if not isinstance(result, process.BoundedProcessResult):
                raise TypeError("injected git runners must return BoundedProcessResult")
            return result
        if self._authority_state == "refusal":
            if self._refusal in ("engine git unavailable", "unsupported platform git profile"):
                return process.BoundedProcessResult(process.ProcessState.UNSUPPORTED)
            return process.BoundedProcessResult(process.ProcessState.SPAWN_ERROR)
        return process.run_git_bounded(self._authority, args, toolchain=self._toolchain,
                                       budget=self._budget)

    def _observe(self, key, produce):
        if key not in self._cache:
            obs = produce()
            self._cache[key] = obs
            if obs.state in ("unreadable", "unavailable"):
                self.collection_complete = False
        return self._cache[key]

    @staticmethod
    def _map_failure(result: process.BoundedProcessResult) -> Obs:
        if result.state is process.ProcessState.UNSUPPORTED:
            return unavailable("engine git unavailable")
        return unreadable(f"git process {result.state.value}")

    # ----- availability / provenance -------------------------------------------------------
    def availability(self) -> Obs:
        """present = repository with usable Git; absent = confirmed no .git."""
        def produce():
            self._admit()
            if self._authority_state == "refusal":
                if self._refusal == "no_git":
                    return absent()
                if self._refusal in ("engine git unavailable",
                                     "unsupported platform git profile"):
                    return unavailable(self._refusal)
                return unreadable(f"git authority {self._refusal}")
            result = self._run(("rev-parse", "--is-inside-work-tree"))
            if result.state is process.ProcessState.OK:
                if result.stdout.strip() == "true":
                    return present(True)
                return unreadable("malformed rev-parse output")
            if result.state is process.ProcessState.NONZERO:
                return absent()
            return self._map_failure(result)
        return self._observe(("available",), produce)

    def available(self) -> bool:
        """Boolean availability for structural fallbacks (present only)."""
        return self.availability().state == "present"

    def origin_identity(self) -> tuple:
        """Sanitized ``(host, owner, name)`` from admission, or ``()``."""
        self._admit()
        if isinstance(self._authority, safe_io.GitSnapshotAuthority):
            return self._authority.origin
        return ()

    def origin_malformed(self) -> bool:
        """True when a present origin URL failed identity normalization (never fall back)."""
        self._admit()
        if isinstance(self._authority, safe_io.GitSnapshotAuthority):
            return self._authority.origin_malformed
        return False

    def metadata_profile(self) -> str:
        """``primary`` | ``linked_worktree`` | ``""`` (absent/unavailable)."""
        self._admit()
        if isinstance(self._authority, safe_io.GitSnapshotAuthority):
            return self._authority.metadata_profile
        return ""

    def _fact(self, args, parse, *, nonzero="unreadable"):
        def produce():
            result = self._run(args)
            if result.state is process.ProcessState.OK:
                try:
                    return parse(result.stdout)
                except (ValueError, TypeError):
                    return unreadable("malformed git output")
            if result.state is process.ProcessState.NONZERO:
                if nonzero == "absent":
                    return absent()
                return unreadable(f"git exited {result.returncode}")
            return self._map_failure(result)
        return self._observe(args, produce)

    # ----- facts ------------------------------------------------------------------------
    def head_sha(self) -> Obs:
        return self._fact(("rev-parse", "HEAD"),
                          lambda out: present(out.strip()) if out.strip()
                          else unreadable("empty HEAD"))

    def branch(self) -> Obs:
        return self._fact(("rev-parse", "--abbrev-ref", "HEAD"),
                          lambda out: present(out.strip()) if out.strip()
                          else unreadable("empty branch"))

    def commit_count(self) -> Obs:
        return self._fact(("rev-list", "--count", "HEAD"),
                          lambda out: present(int(out.strip())))

    def commit_dates(self, n: int = 50) -> Obs:
        return self._fact(("log", f"-{n}", "--format=%cI"),
                          lambda out: present(tuple(ln.strip() for ln in out.splitlines())))

    def recent_messages(self, n: int = 50) -> Obs:
        return self._fact(("log", f"-{n}", "--format=%an%n%ae%n%B%n==="),
                          lambda out: present(out))

    def has_agent_coauthorship(self, n: int = 100) -> Obs:
        def produce():
            obs = self.recent_messages(n)
            if obs.state != "present":
                return obs
            blob = obs.value.lower()
            return present(any(marker in blob for marker in _AGENT_MARKERS))
        return self._observe(("coauthorship", n), produce)

    def tags(self) -> Obs:
        return self._fact(("tag",),
                          lambda out: present(tuple(ln.strip() for ln in out.splitlines()
                                                    if ln.strip())))

    def file_last_commit_iso(self, relpath: str) -> Obs:
        return self._fact(("log", "-1", "--format=%cI", "--", relpath),
                          lambda out: present(out.strip()) if out.strip() else absent(),
                          nonzero="absent")

    def most_recent_commit_iso(self) -> Obs:
        def produce():
            obs = self.commit_dates(1)
            if obs.state != "present":
                return obs
            if not obs.value:
                return absent()
            return present(obs.value[0])
        return self._observe(("most_recent",), produce)

    def recent_churn(self, n: int = 50) -> Obs:
        """Per-commit added+deleted LOC for the last ``n`` non-merge commits."""

        def parse(out):
            churns: list = []
            current: int | None = None
            for line in out.splitlines():
                if not line.strip():
                    continue
                if "\t" not in line:
                    if current is not None:
                        churns.append(current)
                    current = 0
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                added, deleted, path = parts[0], parts[1], parts[2]
                if added == "-" or deleted == "-":
                    continue
                if _churn_path_excluded(path):
                    continue
                if current is None:
                    current = 0
                current += int(added) + int(deleted)
            if current is not None:
                churns.append(current)
            return present(tuple(churns))

        return self._fact(("log", f"-{n}", "--no-merges", "--numstat", "--format=%H"),
                          parse)

    def commit_count_for(self, relpath: str) -> Obs:
        """Commit count for a file (with ``--follow``) or directory (without)."""
        kind = self._path_kind(relpath)
        if kind is None:
            return self._observe(("count_kind", relpath),
                                 lambda: unreadable("path kind indeterminate"))
        target = relpath.rstrip("/") or relpath
        if kind == "dir":
            args = ("rev-list", "--count", "HEAD", "--", target)
            return self._fact(args, lambda out: present(int(out.strip())))
        # --count is incompatible with --follow; count the one-line-per-commit output.
        args = ("log", "--follow", "--format=%H", "HEAD", "--", target)
        return self._fact(
            args, lambda out: present(len([ln for ln in out.splitlines() if ln.strip()])))

    def _path_kind(self, relpath: str) -> str | None:
        """``file`` | ``dir`` from safe collector observations; ``None`` when indeterminate.

        No direct filesystem access: an indeterminate safe observation propagates as
        ``None`` (the caller maps it to an unreadable observation) rather than guessing
        from path strings after a degraded read.
        """
        stripped = relpath.rstrip("/")
        if self._static is not None and stripped:
            exact = self._static.glob_repo_files([stripped])
            if exact.state is not safe_io.RepoDiscoveryState.OK:
                return None
            if stripped in exact.paths:
                return "file"
            beneath = self._static.glob_repo_files([stripped + "/**"])
            if beneath.state is not safe_io.RepoDiscoveryState.OK:
                return None
            if beneath.paths:
                return "dir"
        # Absent on disk (or no static collector): the name heuristic decides without I/O.
        name = stripped.rsplit("/", 1)[-1] if stripped else ""
        if relpath.endswith("/") or (name and Path(name).suffix == ""
                                     and name in _AGENT_DIRS):
            return "dir"
        return "file"

    def is_ancestor(self, old: str, new: str) -> Obs:
        """``git merge-base --is-ancestor OLD NEW``: only exit 0 proves lineage."""
        def produce():
            result = self._run(("merge-base", "--is-ancestor", old, new))
            if result.state is process.ProcessState.OK:
                return present(True)
            if result.state is process.ProcessState.NONZERO:
                if result.returncode == 1:
                    return present(False)
                return unreadable(f"merge-base exited {result.returncode}")
            return self._map_failure(result)
        return self._observe(("is_ancestor", old, new), produce)

    # ----- worktree-observing commands ------------------------------------------------------
    def status_porcelain(self) -> Obs:
        """``git status --porcelain`` inside the retained full worktree-view snapshot."""
        def produce():
            self._admit()
            if isinstance(self._authority, safe_io.GitSnapshotAuthority):
                try:
                    self._authority.ensure_full_view()
                except safe_io.RepositoryInputError:
                    return unreadable("worktree view unsafe")
            result = self._run(("status", "--porcelain"))
            if result.state is process.ProcessState.OK:
                return present(result.stdout)
            if result.state is process.ProcessState.NONZERO:
                return unreadable(f"git status exited {result.returncode}")
            return self._map_failure(result)
        return self._observe(("status",), produce)

    def check_ignore(self, probes: tuple) -> Obs:
        """One shared ``git check-ignore -v --no-index -- <probes>`` observation.

        Returns present records as ``((source, lineno, pattern, path), ...)``; exit 1 (no
        probe matched) is a confirmed empty present, never an error.
        """
        probes = tuple(str(p) for p in probes)

        def produce():
            self._admit()
            if isinstance(self._authority, safe_io.GitSnapshotAuthority):
                try:
                    self._authority.ensure_gitignore_view()
                except safe_io.RepositoryInputError:
                    return unreadable("gitignore view unsafe")
            result = self._run(("check-ignore", "-v", "--no-index", "--", *probes))
            if result.state is process.ProcessState.OK:
                records = []
                for line in result.stdout.splitlines():
                    if not line.strip():
                        continue
                    try:
                        head, path = line.split("\t", 1)
                        source, lineno, pattern = head.split(":", 2)
                    except ValueError:
                        return unreadable("malformed check-ignore output")
                    records.append((source, lineno, pattern, path))
                return present(tuple(records))
            if result.state is process.ProcessState.NONZERO and result.returncode == 1:
                return present(())
            if result.state is process.ProcessState.NONZERO:
                return unreadable(f"check-ignore exited {result.returncode}")
            return self._map_failure(result)

        return self._observe(("check-ignore", probes), produce)


_LOCK_BASENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "Cargo.lock", "Gemfile.lock", "composer.lock",
}
_CHURN_SKIP_PARTS = {"vendor", "node_modules", "dist"}
_AGENT_DIRS = {".claude", ".cursor", "skills", ".agents"}


def _churn_path_excluded(path: str) -> bool:
    parts = Path(path).parts
    if any(part in _CHURN_SKIP_PARTS for part in parts):
        return True
    base = Path(path).name
    return base in _LOCK_BASENAMES or base.endswith(".lock")
