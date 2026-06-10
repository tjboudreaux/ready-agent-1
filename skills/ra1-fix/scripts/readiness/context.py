"""The Context passed to every check function.

Checks are pure with respect to I/O: they read from the collectors on the context and
return a Verdict. This keeps them deterministic and unit-testable with fake collectors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .collectors.static import StaticCollector
from .collectors.git import GitCollector
from .collectors.github import GithubCollector
from .model import App, Detection


@dataclass
class Context:
    root: Path
    detection: Detection
    static: StaticCollector
    git: GitCollector
    github: GithubCollector
    app: App
    options: dict = field(default_factory=dict)
    exec: object = None  # ExecCollector when T3 is opted in (None otherwise)

    def app_static(self) -> StaticCollector:
        """Collector scoped to the current application's directory (repo root for '.')."""
        return self.static.within(self.app.path)
