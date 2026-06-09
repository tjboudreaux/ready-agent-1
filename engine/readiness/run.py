"""Orchestrator: detect -> collect -> evaluate -> Report.

The scoring step (``score.evaluate``) is imported lazily so the engine still produces a
detection-only report if scoring is unavailable, and so the import graph stays acyclic.
"""
from __future__ import annotations

from pathlib import Path

from . import version
from .collectors import GitCollector, GithubCollector, StaticCollector
from .detect import detect
from .model import Report


def build_collectors(root, options):
    options = options or {}
    static = StaticCollector(root)
    git = GitCollector(root, runner=options.get("git_runner"))
    if options.get("no_github"):
        github = GithubCollector(root, runner=lambda args: None)
    else:
        github = GithubCollector(root, runner=options.get("github_runner"))
    return static, git, github


def analyze(root, options=None) -> Report:
    options = options or {}
    root = Path(root)
    static, git, github = build_collectors(root, options)
    detection = detect(root, static)

    vs = version.version_stamp()
    report = Report(
        project_path=str(root),
        schema_version=vs["schema_version"],
        engine_version=vs["engine_version"],
        registry_version=vs["registry_version"],
        detector_version=vs["detector_version"],
        commit=git.head_sha(),
        branch=git.branch(),
        github_available=github.available,
        detection=detection,
    )

    try:
        from .score import evaluate
    except ImportError:
        evaluate = None
    if evaluate is not None:
        results, score = evaluate(root, detection, static, git, github, options)
        report.results = results
        report.score = score
    return report
