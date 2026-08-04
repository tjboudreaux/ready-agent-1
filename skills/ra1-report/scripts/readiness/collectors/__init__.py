"""Evidence collectors.

- ``StaticCollector`` (T0): files, globs, semantic config/manifest parsing.
- ``GitCollector``    (T1): read-only git history facts.
- ``GithubCollector`` (T2): GitHub API facts via ``gh`` (optional; ``available`` is False when
  absent).
- ``ExecCollector``   (T3): opt-in sandboxed execution of the repo's own test command (default OFF).
"""
from .exec import ExecCollector
from .git import GitCollector
from .github import GithubCollector
from .static import StaticCollector

__all__ = ["StaticCollector", "GitCollector", "GithubCollector", "ExecCollector"]
