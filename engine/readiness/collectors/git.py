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
