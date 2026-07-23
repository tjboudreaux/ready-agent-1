"""T1 git evidence: read-only history facts (never executes repo code)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

_AGENT_MARKERS = (
    "claude", "droid", "factory", "codex", "cursor", "copilot", "gemini",
    "devin", "aider", "sweep", "co-authored-by: claude", "generated with",
)


class GitCollector:
    def __init__(self, root, runner=None):
        self.root = Path(root)
        # ``runner`` is injectable so tests can avoid a real subprocess.
        self._runner = runner or self._default_runner
        self._cache: dict = {}

    def _default_runner(self, args: List[str]) -> Optional[str]:  # pragma: no cover - subprocess boundary
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _run(self, args: List[str]) -> Optional[str]:
        key = tuple(args)
        if key not in self._cache:
            self._cache[key] = self._runner(args)
        return self._cache[key]

    def available(self) -> bool:
        return self._run(["rev-parse", "--is-inside-work-tree"]) is not None

    def head_sha(self) -> str:
        out = self._run(["rev-parse", "HEAD"])
        return out.strip() if out else ""

    def branch(self) -> str:
        out = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if out else ""

    def commit_count(self) -> int:
        out = self._run(["rev-list", "--count", "HEAD"])
        try:
            return int(out.strip()) if out else 0
        except ValueError:
            return 0

    def commit_dates(self, n: int = 50) -> List[str]:
        out = self._run(["log", f"-{n}", "--format=%cI"])
        return [ln.strip() for ln in out.splitlines()] if out else []

    def recent_messages(self, n: int = 50) -> str:
        out = self._run(["log", f"-{n}", "--format=%an%n%ae%n%B%n==="])
        return out or ""

    def has_agent_coauthorship(self, n: int = 100) -> bool:
        blob = self.recent_messages(n).lower()
        return any(marker in blob for marker in _AGENT_MARKERS)

    def tags(self) -> List[str]:
        out = self._run(["tag"])
        return [ln.strip() for ln in out.splitlines() if ln.strip()] if out else []

    def file_last_commit_iso(self, relpath: str) -> Optional[str]:
        out = self._run(["log", "-1", "--format=%cI", "--", relpath])
        return out.strip() if out and out.strip() else None

    def most_recent_commit_iso(self) -> Optional[str]:
        dates = self.commit_dates(1)
        return dates[0] if dates else None

    def recent_churn(self, n: int = 50) -> List[int]:
        """Per-commit added+deleted LOC for the last ``n`` non-merge commits.

        Skips binary numstat rows and vendor/lockfile noise. Unavailable → ``[]``.
        """
        out = self._run(["log", f"-{n}", "--no-merges", "--numstat", "--format=%H"])
        if not out:
            return []
        churns: List[int] = []
        current: Optional[int] = None
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
            try:
                current += int(added) + int(deleted)
            except ValueError:
                continue
        if current is not None:
            churns.append(current)
        return churns

    def commit_count_for(self, relpath: str) -> int:
        """Commit count for a file (with ``--follow``) or directory (without).

        When the path does not exist in a fake root, treat trailing ``/`` or known
        agent dirs as directories (no ``--follow``); otherwise follow renames.
        """
        path = self.root / relpath
        if path.is_dir():
            is_dir = True
        elif path.exists():
            is_dir = False
        else:
            name = relpath.rstrip("/").rsplit("/", 1)[-1]
            is_dir = relpath.endswith("/") or (
                Path(name).suffix == "" and name in _AGENT_DIRS
            )
        target = relpath.rstrip("/") or relpath
        if is_dir:
            args = ["rev-list", "--count", "HEAD", "--", target]
        else:
            args = ["rev-list", "--count", "--follow", "HEAD", "--", target]
        out = self._run(args)
        try:
            return int(out.strip()) if out else 0
        except ValueError:
            return 0


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
