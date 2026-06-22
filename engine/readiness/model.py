"""Core data model for the readiness engine.

Everything is a plain dataclass with an explicit ``to_dict`` so the JSON renderer is
deterministic and never leaks Python types. Status values serialize to their string value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"
    WAIVED = "waived"


# Statuses that count as "satisfied" when computing level gates.
PASSING = {Status.PASS}
# Statuses excluded from the denominator of a level gate.
EXCLUDED_FROM_GATE = {Status.SKIPPED, Status.WAIVED}


@dataclass
class Evidence:
    """A single cited fact that justifies a verdict."""

    summary: str
    tier: str = "T0"           # T0 static | T1 git | T2 gh API | T4 agent
    source: str = ""           # file path, git ref, or gh api endpoint
    detail: str = ""

    def to_dict(self) -> dict:
        return {"summary": self.summary, "tier": self.tier, "source": self.source, "detail": self.detail}


@dataclass
class Verdict:
    """What a check function returns. skip/waive are normally applied structurally by the scorer."""

    status: Status
    rationale: str = ""
    evidence: list = field(default_factory=list)  # list[Evidence]


@dataclass
class App:
    """A unit that criteria can be scoped to. The repository root is modelled as path '.'."""

    path: str = "."
    languages: list = field(default_factory=list)
    runtime: str = "unknown"
    deploy_surface: str = "unknown"  # none | library | service | frontend | cli | data | infra | unknown
    prod_facing: object = "unknown"  # True | False | "unknown"
    test_cmd: str = ""
    ci_jobs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "languages": list(self.languages),
            "runtime": self.runtime,
            "deploy_surface": self.deploy_surface,
            "prod_facing": self.prod_facing,
            "test_cmd": self.test_cmd,
            "ci_jobs": list(self.ci_jobs),
        }


@dataclass
class Detection:
    project_type: str = "unknown"
    confidence: float = 0.0
    signals: list = field(default_factory=list)   # human-readable reasons
    languages: list = field(default_factory=list)
    apps: list = field(default_factory=list)       # list[App]
    is_monorepo: bool = False
    opt_in: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "project_type": self.project_type,
            "confidence": round(self.confidence, 3),
            "signals": list(self.signals),
            "languages": list(self.languages),
            "is_monorepo": self.is_monorepo,
            "apps": [a.to_dict() for a in self.apps],
            "opt_in": dict(self.opt_in),
        }


@dataclass
class CriterionResult:
    id: str
    title: str
    pillar: str
    level: int
    scope: str            # repository | application
    gating: bool
    status: Status
    rationale: str = ""
    evidence: list = field(default_factory=list)   # list[Evidence]
    app_path: str = "."
    fixable: bool = False
    fix_kind: str = ""
    passed_apps: int = 0      # apps passing this criterion (repository scope: 1 if pass else 0)
    evaluated_apps: int = 0   # apps assessed (repository scope: 1 if applicable, 0 if skipped/waived)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "pillar": self.pillar,
            "level": self.level,
            "scope": self.scope,
            "gating": self.gating,
            "status": self.status.value,
            "rationale": self.rationale,
            "evidence": [e.to_dict() for e in self.evidence],
            "app_path": self.app_path,
            "fixable": self.fixable,
            "fix_kind": self.fix_kind,
            "passed_apps": self.passed_apps,
            "evaluated_apps": self.evaluated_apps,
        }


@dataclass
class LevelScore:
    level: int
    name: str
    passed: int
    total: int          # gating criteria at this level, excluding skipped/waived
    achieved: bool

    @property
    def ratio(self) -> float:
        return (self.passed / self.total) if self.total else 1.0

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "name": self.name,
            "passed": self.passed,
            "total": self.total,
            "ratio": round(self.ratio, 3),
            "achieved": self.achieved,
        }


@dataclass
class ScoreSummary:
    level: int                     # highest achieved level
    level_name: str
    pass_rate: float               # gating pass / gating applicable
    gating_passed: int
    gating_total: int
    levels: list = field(default_factory=list)        # list[LevelScore]
    pillars: dict = field(default_factory=dict)       # pillar -> {passed,total}
    recommendations: list = field(default_factory=list)  # top gating next-actions (deterministic)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "level_name": self.level_name,
            "pass_rate": round(self.pass_rate, 3),
            "gating_passed": self.gating_passed,
            "gating_total": self.gating_total,
            "levels": [l.to_dict() for l in self.levels],
            "pillars": self.pillars,
            "recommendations": list(self.recommendations),
        }


@dataclass
class Report:
    project_path: str
    schema_version: str
    engine_version: str
    registry_version: str
    detector_version: str
    commit: str = ""
    branch: str = ""
    github_available: bool = False
    generated_at: str = ""
    repository: Optional[dict] = None
    detection: Optional[Detection] = None
    results: list = field(default_factory=list)        # list[CriterionResult]
    score: Optional[ScoreSummary] = None
    advisory: list = field(default_factory=list)        # filled by the agent layer; engine leaves []

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "engine_version": self.engine_version,
            "registry_version": self.registry_version,
            "detector_version": self.detector_version,
            "commit": self.commit,
            "branch": self.branch,
            "github_available": self.github_available,
            "generated_at": self.generated_at,
            "repository": self.repository,
            "detection": self.detection.to_dict() if self.detection else None,
            "score": self.score.to_dict() if self.score else None,
            "results": [r.to_dict() for r in self.results],
            "advisory": list(self.advisory),
        }


LEVEL_NAMES = {
    1: "Functional",
    2: "Documented",
    3: "Standardized",
    4: "Optimized",
    5: "Autonomous",
}
