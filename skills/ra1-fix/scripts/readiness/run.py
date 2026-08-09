"""Orchestrator: detect -> collect -> evaluate -> Report.

The public invocation contract is closed: :class:`AnalyzeOptions` carries exactly the three
public knobs (``github``, ``exec``, normalized ``exec_timeout``); every other input comes
from its named engine authority. Tests and embedders use the separate keyword-only
:class:`AnalyzeDependencies` channel for bounded fakes and a frozen clock — it can never
be constructed from CLI/config/report data and forces ``inputs.profile: "injected"``.

The scoring step (``score.evaluate``) is imported lazily so the engine still produces a
detection-only report if scoring is unavailable, and so the import graph stays acyclic.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import history, version
from .collectors import ExecCollector, GitCollector, GithubCollector, StaticCollector
from .collectors.exec import normalize_exec_timeout
from .detect import detect
from .model import Report
from .score import evaluate


@dataclass(frozen=True)
class AnalyzeOptions:
    """The closed public options object. Unknown/legacy keys are rejected by construction."""

    github: bool = False
    exec: bool = False
    exec_timeout: int = 120

    def __post_init__(self):
        for name in ("github", "exec"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a bool")
        object.__setattr__(self, "exec_timeout",
                           normalize_exec_timeout(self.exec_timeout))


@dataclass(frozen=True)
class AnalyzeDependencies:
    """The keyword-only typed injection channel (tests/embedders only).

    Supplying any fake collector, fixture input, or frozen clock makes the report
    ``inputs.profile: "injected"`` — ineligible for persistence, history comparison, or
    fix authority. Host-proxy/GitHub-auth authorities are the private CLI path and do not
    mark a report injected.
    """

    git_runner: object = None
    github_runner: object = None
    exec_runner: object = None
    readiness_config: object = None
    detect_config: object = None
    waivers: object = None
    now: object = None
    generated_at: object = None
    repository: object = None
    registry_path: object = None
    toolchain: object = None
    github_origin: object = None
    host_proxy_authority: object = None
    github_auth_authority: object = None

    def injected(self) -> bool:
        return any(getattr(self, name) is not None for name in (
            "git_runner", "github_runner", "exec_runner", "readiness_config",
            "detect_config", "waivers", "now", "generated_at", "repository",
            "registry_path", "toolchain", "github_origin"))


def _coerce_options(options) -> AnalyzeOptions:
    if options is None:
        return AnalyzeOptions()
    if isinstance(options, AnalyzeOptions):
        return options
    if isinstance(options, dict):
        legacy = sorted(options.keys())
        raise ValueError(
            "legacy analyze options are rejected; use AnalyzeOptions(github=, exec=, "
            f"exec_timeout=) — got keys: {', '.join(legacy)}")
    raise ValueError("options must be an AnalyzeOptions instance")


def build_collectors(root, options: AnalyzeOptions, deps: AnalyzeDependencies):
    static = StaticCollector(root)
    git = GitCollector(root, toolchain=deps.toolchain, runner=deps.git_runner,
                       static=static)
    if options.github:
        origin = deps.github_origin
        if origin is None:
            origin = git.origin_identity()
        github = GithubCollector(
            root, origin=tuple(origin or ()), auth=deps.github_auth_authority,
            toolchain=deps.toolchain, proxy=deps.host_proxy_authority,
            runner=deps.github_runner)
    else:
        github = GithubCollector(root, origin=(), runner=deps.github_runner
                                 if deps.github_runner else None)
    exec_collector = ExecCollector(
        root, {"exec": options.exec, "exec_timeout": options.exec_timeout},
        toolchain=deps.toolchain, runner=deps.exec_runner, static=static)
    return static, git, github, exec_collector


def analyze(root, options=None, *, deps=None) -> Report:
    opts = _coerce_options(options)
    deps = deps or AnalyzeDependencies()
    if not isinstance(deps, AnalyzeDependencies):
        raise ValueError("deps must be an AnalyzeDependencies instance")
    root = Path(root)
    static, git, github, exec_collector = build_collectors(root, opts, deps)
    internal = {"_exec": exec_collector, "_deps": {
        "readiness_config": deps.readiness_config,
        "detect_config": deps.detect_config,
        "waivers": deps.waivers,
        "now": deps.now,
        "generated_at": deps.generated_at,
        "registry_path": deps.registry_path,
    }}
    detection = detect(root, static, internal)

    vs = version.version_stamp()
    head = git.head_sha()
    branch = git.branch()
    repository = deps.repository
    if repository is None:
        repository = history.repo_identity(root, git_collector=git)
    report = Report(
        project_path=str(root),
        schema_version=vs["schema_version"],
        engine_version=vs["engine_version"],
        registry_version=vs["registry_version"],
        detector_version=vs["detector_version"],
        commit=head.value if head.state == "present" else "",
        branch=branch.value if branch.state == "present" else "",
        github_available=bool(opts.github and github.available),
        generated_at=deps.generated_at or history.now_iso(),
        repository=repository,
        detection=detection,
    )

    report.results, report.score = evaluate(root, detection, static, git, github, internal)
    # Derived last and from the finished report: gaps explain the results, so they can never
    # be an input to them.
    from .detect import load_readiness_config
    from .gaps import derive_gaps
    report.gaps = derive_gaps(report, load_readiness_config(static, internal),
                          static)
    report.assessment_provenance = _provenance(
        report, opts, deps, static, git, github, exec_collector, detection)
    for collector in (static, git, github, exec_collector):
        close = getattr(collector, "close", None)
        if close is not None:
            close()
    return report


def _provenance(report, opts: AnalyzeOptions, deps: AnalyzeDependencies, static, git,
                github, exec_collector, detection) -> dict:
    """Engine-recorded unsigned association metadata, built exactly once after scoring.

    Reads immutable final collector summaries and never triggers a late observation. It is
    neither authenticated provenance nor an attestation and cannot establish report
    integrity.
    """
    repository = report.repository or {}
    github_requested = bool(opts.github)
    github_complete = github.collection_complete if github_requested else False
    git_complete = git.collection_complete and git.availability().state == "present"
    execution = exec_collector.provenance
    waivers_source = "repository_file" if _waivers_file_present(static) else "none"
    return {
        "trust": "unsigned_unverified",
        "predicate_type": "agent-readiness/assessment/v1",
        "builder": {
            "id": "ra1-engine",
            "engine_version": report.engine_version,
            "platform": sys.platform if sys.platform in ("linux", "darwin") else "other",
        },
        "subject": {
            "repository_identity_kind": repository.get("identity_kind", ""),
            "repository_identity_hash": repository.get("identity_hash", ""),
            "commit": report.commit,
            "branch": report.branch,
        },
        "materials": {
            "registry_version": report.registry_version,
            "detector_version": report.detector_version,
            "report_schema_version": report.schema_version,
        },
        "invocation": {
            "inputs": {
                "profile": "injected" if deps.injected() else "repository",
            },
            "static": {
                "collection_complete": bool(static.collection_complete
                                            and not detection.repository_indeterminate),
            },
            "github": {
                "requested": github_requested,
                "host_proxy": bool(deps.host_proxy_authority),
                "available": bool(github.available) if github_requested else False,
                "collection_complete": github_complete,
            },
            "git": {
                "resource_profile": _git_resource_profile(),
                "metadata_profile": git.metadata_profile() or "absent",
                "collection_complete": git_complete,
            },
            "execution": {
                "requested": execution["requested"],
                "timeout_seconds": execution["timeout_seconds"],
                "completed": execution["completed"],
                "successful": execution["successful"],
            },
            "waivers": {
                "source": waivers_source,
            },
        },
        "generated_at": report.generated_at,
    }


def _git_resource_profile() -> str:
    from . import process
    return process.git_resource_profile() or "unavailable"


def _waivers_file_present(static) -> bool:
    from . import safe_io
    try:
        obs = static.exists_observation([".ra1/waivers.json"])
        return obs.state is safe_io.PresenceState.PRESENT
    except safe_io.RepositoryInputError:
        return False
