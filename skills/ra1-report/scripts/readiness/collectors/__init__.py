"""Evidence collectors.

- ``StaticCollector`` (T0): files, globs, semantic config/manifest parsing.
- ``GitCollector``    (T1): read-only git history facts.
- ``GithubCollector`` (T2): GitHub API facts via ``gh`` (optional; ``available`` is False when absent).
- ``ExecCollector``   (T3): opt-in sandboxed execution of the repo's own test command (default OFF).
"""
from .static import StaticCollector
from .git import GitCollector
from .github import GithubCollector
from .exec import ExecCollector

__all__ = ["StaticCollector", "GitCollector", "GithubCollector", "ExecCollector"]
