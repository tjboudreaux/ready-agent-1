"""T2 GitHub evidence via ``gh``.

Optional by design: if there is no GitHub remote or ``gh`` is not authenticated,
``available`` is False and T2 criteria are *skipped* (never failed). All calls are
read-only API queries; the ``runner`` is injectable so tests need no network.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Optional


class GithubCollector:
    def __init__(self, root, runner=None):
        self.root = Path(root)
        self._runner = runner or self._default_runner
        self._cache: dict = {}
        self._slug = None
        self._available = None

    def _default_runner(self, args: List[str]) -> Optional[str]:  # pragma: no cover - subprocess boundary
        try:
            proc = subprocess.run(
                ["gh", *args], cwd=str(self.root),
                capture_output=True, text=True, timeout=25,
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

    def _api(self, path: str) -> Optional[object]:
        out = self._run(["api", path])
        if not out:
            return None
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return None

    # ----- availability / identity ------------------------------------------------------
    @property
    def available(self) -> bool:
        if self._available is None:
            out = self._run(["repo", "view", "--json", "nameWithOwner"])
            slug = None
            if out:
                try:
                    slug = json.loads(out).get("nameWithOwner")
                except (json.JSONDecodeError, ValueError, AttributeError):
                    slug = None
            self._slug = slug
            self._available = bool(slug)
        return self._available

    @property
    def slug(self) -> Optional[str]:
        return self._slug if self.available else None

    # ----- facts ------------------------------------------------------------------------
    def repo(self) -> Optional[dict]:
        if not self.available:
            return None
        data = self._api(f"repos/{self.slug}")
        return data if isinstance(data, dict) else None

    def default_branch(self) -> Optional[str]:
        repo = self.repo()
        return repo.get("default_branch") if repo else None

    def topics(self) -> List[str]:
        if not self.available:
            return []
        data = self._api(f"repos/{self.slug}/topics")
        if isinstance(data, dict) and isinstance(data.get("names"), list):
            return data["names"]
        repo = self.repo()
        if repo and isinstance(repo.get("topics"), list):
            return repo["topics"]
        return []

    def branch_protected(self, branch: Optional[str] = None) -> Optional[bool]:
        if not self.available:
            return None
        branch = branch or self.default_branch() or "main"
        data = self._api(f"repos/{self.slug}/branches/{branch}/protection")
        # None means 404/no access -> treat as "not protected" (False), not "unknown",
        # because availability was already confirmed.
        return bool(data) if data is not None else False

    def secret_scanning_enabled(self) -> Optional[bool]:
        repo = self.repo()
        if not repo:
            return None
        saa = repo.get("security_and_analysis") or {}
        ss = (saa.get("secret_scanning") or {}).get("status")
        pp = (saa.get("secret_scanning_push_protection") or {}).get("status")
        return ss == "enabled" or pp == "enabled"

    def workflows(self) -> List[dict]:
        if not self.available:
            return []
        data = self._api(f"repos/{self.slug}/actions/workflows")
        if isinstance(data, dict) and isinstance(data.get("workflows"), list):
            return data["workflows"]
        return []

    def recent_runs(self, n: int = 20) -> List[dict]:
        if not self.available:
            return []
        data = self._api(f"repos/{self.slug}/actions/runs?per_page={n}")
        if isinstance(data, dict) and isinstance(data.get("workflow_runs"), list):
            return data["workflow_runs"]
        return []

    def labels(self) -> List[str]:
        if not self.available:
            return []
        data = self._api(f"repos/{self.slug}/labels?per_page=100")
        if isinstance(data, list):
            return [l.get("name", "") for l in data if isinstance(l, dict)]
        return []

    def open_issues(self, n: int = 50) -> List[dict]:
        """Real issues only (the issues endpoint also returns PRs)."""
        if not self.available:
            return []
        data = self._api(f"repos/{self.slug}/issues?state=open&per_page={n}")
        if isinstance(data, list):
            return [i for i in data if isinstance(i, dict) and "pull_request" not in i]
        return []

    def recent_merged_prs(self, n: int = 20) -> List[dict]:
        """Up to ``n`` recently updated closed PRs that were merged."""
        if not self.available:
            return []
        merged: List[dict] = []
        for page in range(1, 4):
            data = self._api(
                f"repos/{self.slug}/pulls?state=closed&sort=updated&direction=desc&per_page=50&page={page}"
            )
            if not isinstance(data, list):
                break
            if not data:
                break
            for pr in data:
                if isinstance(pr, dict) and pr.get("merged_at"):
                    merged.append(pr)
                    if len(merged) >= n:
                        return merged
        return merged

    def pr_first_review_iso(self, number: int) -> Optional[str]:
        """Earliest review ``submitted_at`` for a PR (any reviewer, including bots)."""
        if not self.available:
            return None
        data = self._api(f"repos/{self.slug}/pulls/{number}/reviews")
        if not isinstance(data, list) or not data:
            return None
        times = [
            rev.get("submitted_at")
            for rev in data
            if isinstance(rev, dict) and rev.get("submitted_at")
        ]
        return min(times) if times else None
