"""Core data model for the readiness engine.

Everything is a plain dataclass with an explicit ``to_dict`` so the JSON renderer is
deterministic and never leaks Python types. Status values serialize to their string value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
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
        return {"summary": self.summary, "tier": self.tier,
                "source": self.source, "detail": self.detail}


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
    # none | library | service | frontend | cli | data | infra | unknown
    deploy_surface: str = "unknown"
    prod_facing: object = "unknown"  # True | False | "unknown"
    test_cmd: str = ""
    ci_jobs: list = field(default_factory=list)
    # How well the surface above is evidenced, and every type the directory could be
    # ({"type", "confidence", "signal"}, strongest first). Advisory: the score reads
    # deploy_surface, while the gaps layer reads these to ask a precise question.
    type_confidence: float = 0.0
    type_candidates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "languages": list(self.languages),
            "runtime": self.runtime,
            "deploy_surface": self.deploy_surface,
            "prod_facing": self.prod_facing,
            "test_cmd": self.test_cmd,
            "ci_jobs": list(self.ci_jobs),
            "type_confidence": round(self.type_confidence, 3),
            "type_candidates": [dict(c) for c in self.type_candidates],
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
    # Ranked alternatives for the root classification, strongest first. Empty for a
    # monorepo root, where the per-app lists on App carry the ambiguity instead.
    candidates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_type": self.project_type,
            "confidence": round(self.confidence, 3),
            "signals": list(self.signals),
            "languages": list(self.languages),
            "is_monorepo": self.is_monorepo,
            "apps": [a.to_dict() for a in self.apps],
            "opt_in": dict(self.opt_in),
            "candidates": [dict(c) for c in self.candidates],
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
    # AC/DC verification-loop classification from the registry ("" when unmapped):
    # acdc_stage is guide|verify|solve; acdc_loop is inner|outer|both.
    acdc_stage: str = ""
    acdc_loop: str = ""
    # repository scope: passed_apps is 1 if pass else 0; evaluated_apps is 1 if applicable,
    # 0 if skipped/waived
    passed_apps: int = 0
    evaluated_apps: int = 0

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
            "acdc_stage": self.acdc_stage,
            "acdc_loop": self.acdc_loop,
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
            "levels": [lvl.to_dict() for lvl in self.levels],
            "pillars": self.pillars,
            "recommendations": list(self.recommendations),
        }


@dataclass
class Gap:
    """A question the scanner cannot answer for itself, and what answering it unblocks.

    Advisory by construction: a gap never enters the score. It records what the engine
    could not determine, which criteria are stuck on it, and exactly where an answer is
    written so the *engine* can re-evaluate. An answer supplies an input; it never
    supplies a verdict.
    """

    id: str
    kind: str                 # detection | config | capability
    question: str             # authored, precise, never repository text
    why: str                  # what answering it changes, one sentence
    answer: dict = field(default_factory=dict)   # where/how the answer is recorded
    evidence: list = field(default_factory=list)  # what the scanner did see (strings)
    options: list = field(default_factory=list)   # accepted values, when closed
    blocks: list = field(default_factory=list)    # criterion ids stuck on this gap
    blocked_gating: int = 0                        # of `blocks`, how many are gating
    levels: list = field(default_factory=list)     # levels those gating criteria sit at
    waivable: bool = False    # true when "we do this outside this repo" is a real answer

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "question": self.question,
            "why": self.why,
            "answer": dict(self.answer),
            "evidence": list(self.evidence),
            "options": list(self.options),
            "blocks": list(self.blocks),
            "blocked_gating": self.blocked_gating,
            "levels": list(self.levels),
            "waivable": self.waivable,
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
    repository: dict | None = None
    detection: Detection | None = None
    results: list = field(default_factory=list)        # list[CriterionResult]
    score: ScoreSummary | None = None
    # advisory is filled by the agent layer; the engine always leaves it empty
    advisory: list = field(default_factory=list)
    # Questions the scan could not answer for itself (list[Gap]). Advisory: derived from
    # the results after scoring, never an input to them.
    gaps: list = field(default_factory=list)

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
            "gaps": [g.to_dict() for g in self.gaps],
        }


LEVEL_NAMES = {
    1: "Functional",
    2: "Documented",
    3: "Standardized",
    4: "Optimized",
    5: "Autonomous",
}
