"""Evidence collectors.

- ``StaticCollector`` (T0): files, globs, semantic config/manifest parsing.
- ``GitCollector``    (T1): read-only git history facts.
- ``GithubCollector`` (T2): GitHub API facts via ``gh`` (optional; ``available`` is False when absent).
"""
from .static import StaticCollector
from .git import GitCollector
from .github import GithubCollector

__all__ = ["StaticCollector", "GitCollector", "GithubCollector"]
