"""Core data model for the readiness engine.

Everything is a plain dataclass with an explicit ``to_dict`` so the JSON renderer is
deterministic and never leaks Python types. Status values serialize to their string value.
"""
from __future__ import annotations

import re
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
    limitations: list = field(default_factory=list)  # deterministic disclosure strings
    reason_code: str = field(default="", kw_only=True)


TRACE_KINDS = ("rule", "observation", "evaluation", "conclusion")


@dataclass
class DecisionStep:
    """One deterministic step of a criterion's decision trace (never model chain-of-thought)."""

    kind: str
    code: str
    message: str
    evidence_refs: list = field(default_factory=list)  # zero-based indexes into result.evidence

    def to_dict(self) -> dict:
        return {"kind": self.kind, "code": self.code, "message": self.message,
                "evidence_refs": list(self.evidence_refs)}


@dataclass
class DecisionTrace:
    """The deterministic, inspectable reasoning chain behind one criterion result."""

    version: str = "1"
    reason_code: str = ""
    rule_ref: str = ""        # exact registry `check` reference
    steps: list = field(default_factory=list)          # list[DecisionStep]
    limitations: list = field(default_factory=list)    # deterministic disclosure strings

    def to_dict(self) -> dict:
        return {"version": self.version, "reason_code": self.reason_code,
                "rule_ref": self.rule_ref,
                "steps": [s.to_dict() for s in self.steps],
                "limitations": list(self.limitations)}


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
    # Every surface this app serves, when a developer declared more than one (a fullstack
    # directory is a service *and* a frontend). Empty means "just deploy_surface", so the
    # applicability of an undeclared repository is unchanged.
    surfaces: list = field(default_factory=list)

    def match_surfaces(self) -> list:
        """The surfaces applicability is judged against: declared, else the inferred one."""
        return list(self.surfaces) or [self.deploy_surface]

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
            "surfaces": list(self.surfaces),
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
    # Every surface the root serves, when more than one was declared. Repository-scope
    # applicability reads this, so declaring ["frontend", "service"] and ["service",
    # "frontend"] score identically and only the display type differs.
    surfaces: list = field(default_factory=list)
    # Global degraded-input state: a malformed/unsafe/unreadable/oversize readiness-config,
    # detector, manifest, or waiver read that can change detection, applicability, or the
    # waiver set. When true the scorer returns blocking `unknown` for every criterion.
    repository_indeterminate: bool = False
    # The authored state literal: "input.repository_indeterminate" or
    # "input.legacy_policy_path" (legacy .agents/readiness policy present).
    indeterminate_reason: str = ""

    def match_surfaces(self) -> list:
        """The surfaces repository-scope applicability is judged against."""
        return list(self.surfaces) or [self.project_type]

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
            "surfaces": list(self.surfaces),
            "repository_indeterminate": self.repository_indeterminate,
            "indeterminate_reason": self.indeterminate_reason,
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
    # Deterministic decision trace. ``None`` only as a direct-construction compatibility
    # state; canonical schema-v3 report validation rejects null (every runtime path
    # attaches a non-empty valid trace before returning).
    decision_trace: DecisionTrace | None = None

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
            "decision_trace": self.decision_trace.to_dict() if self.decision_trace else None,
        }


@dataclass
class LevelScore:
    level: int
    name: str
    passed: int
    total: int          # gating criteria at this level, excluding skipped/waived
    achieved: bool
    defined: bool = False
    defined_total: int = 0   # every gating criterion at this Level

    @property
    def ratio(self) -> float:
        return (self.passed / self.total) if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "name": self.name,
            "passed": self.passed,
            "total": self.total,
            "ratio": round(self.ratio, 3),
            "achieved": self.achieved,
            "defined": self.defined,
            "defined_total": self.defined_total,
        }


def _empty_evidence_coverage() -> dict:
    """The exact stable evidence-coverage shape; zero-valued keys are always emitted."""
    return {
        "status_counts": {"pass": 0, "fail": 0, "unknown": 0, "skipped": 0, "waived": 0},
        "results_with_evidence": 0,
        "evidence_items": 0,
        "evidence_items_by_tier": {"T0": 0, "T1": 0, "T2": 0, "T3": 0, "T4": 0},
        "results_with_decision_trace": 0,
        "results_with_rule_step": 0,
        "results_with_limitations": 0,
        "evidence_items_referenced": 0,
        "evidence_items_unreferenced": 0,
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
    # Highest Level containing at least one gating criterion, or 0.
    max_available_level: int = 0
    # Every gating fail/unknown at the first unachieved defined Level, sorted (level,pillar,id).
    next_gate_actions: list = field(default_factory=list)
    evidence_coverage: dict = field(default_factory=_empty_evidence_coverage)

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
            "max_available_level": self.max_available_level,
            "next_gate_actions": [dict(a) for a in self.next_gate_actions],
            "evidence_coverage": {
                "status_counts": dict(self.evidence_coverage["status_counts"]),
                "results_with_evidence": self.evidence_coverage["results_with_evidence"],
                "evidence_items": self.evidence_coverage["evidence_items"],
                "evidence_items_by_tier": dict(
                    self.evidence_coverage["evidence_items_by_tier"]),
                "results_with_decision_trace": self.evidence_coverage[
                    "results_with_decision_trace"],
                "results_with_rule_step": self.evidence_coverage["results_with_rule_step"],
                "results_with_limitations": self.evidence_coverage["results_with_limitations"],
                "evidence_items_referenced": self.evidence_coverage[
                    "evidence_items_referenced"],
                "evidence_items_unreferenced": self.evidence_coverage[
                    "evidence_items_unreferenced"],
            },
        }


@dataclass
class Gap:
    """A question the scanner cannot answer for itself, and what answering it unblocks.

    Advisory by construction: a gap never enters the score. It records what the engine
    could not determine, which criteria are stuck on it, and exactly where an answer is
    written so the *engine* can re-evaluate. An answer supplies an input; it never
    supplies a verdict.

    ``choices`` are opaque engine IDs plus display-only labels and effects
    (``record`` | ``external_action`` | ``leave_unanswered``); the CLI rederives every
    mapping from the live scan, so choice text is never executable.
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
    recordable: bool = False
    input_kind: str = "unrecordable"  # single_choice | multi_choice | integer | unrecordable
    choices: list = field(default_factory=list)   # list[{id, label, effect}]
    value: object = None      # null except the CI-budget integer spec

    def to_dict(self) -> dict:
        """The dedicated ``gaps --format json`` item shape (superset of the report's
        non-executable projection, which omits choices/value/policy targets)."""
        return {
            "gap_id": self.id,
            "kind": self.kind,
            "question": self.question,
            "why": self.why,
            "recordable": self.recordable,
            "input_kind": self.input_kind,
            "choices": [dict(c) for c in self.choices],
            "value": self.value,
            "blocked_ids": list(self.blocks),
            "blocked_gating": self.blocked_gating,
            "levels": list(self.levels),
            "evidence": list(self.evidence),
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
    # Engine-recorded unsigned association metadata (§4.6); never authenticated provenance.
    assessment_provenance: dict | None = None

    def to_dict(self) -> dict:
        """The canonical schema-v3 public projection.

        Raises :class:`PublicReportValidationError` — and only that — when projection,
        finalization, or a trace invariant fails. ``project_path`` is process-local and is
        never serialized.
        """
        try:
            results = [finalize_public_result(r).to_dict() for r in self.results]
        except PublicReportValidationError:
            raise
        except Exception as exc:  # finalization must never leak repository data
            raise PublicReportValidationError("result finalization failed") from exc
        errors = []
        for result_dict in results:
            errors.extend(validate_decision_trace(result_dict))
        errors.extend(validate_required_reason_codes(results))
        if errors:
            raise PublicReportValidationError("invalid canonical decision trace")
        return {
            "schema_version": self.schema_version,
            "engine_version": self.engine_version,
            "registry_version": self.registry_version,
            "detector_version": self.detector_version,
            "commit": _public_commit(self.commit),
            "branch": _sanitize_public_text(self.branch, 255),
            "github_available": bool(self.github_available),
            "generated_at": str(self.generated_at),
            "repository": finalize_public_repository(self.repository),
            "detection": _public_detection(self.detection),
            "score": self.score.to_dict() if self.score else None,
            "results": results,
            "advisory": [_sanitize_public_text(str(a), 512) for a in self.advisory],
            "gaps": [_public_gap(g) for g in self.gaps],
            "assessment_boundary": assessment_boundary(),
            "assessment_provenance": _public_provenance(self.assessment_provenance),
        }


LEVEL_NAMES = {
    1: "Functional",
    2: "Documented",
    3: "Standardized",
    4: "Optimized",
    5: "Autonomous",
}


# --------------------------------------------------------------------------- public errors
class PublicReportValidationError(ValueError):
    """Canonical projection/finalization/trace invariant failure. Carries no repository data."""


# --------------------------------------------------------------------------- canonical boundary
def assessment_boundary() -> dict:
    """The canonical assessment boundary, as a fresh deep structure on every call.

    JSON/Markdown/HTML/GitHub render this same data; they never maintain duplicate prose.
    """
    return {
        "evidence_layers": [
            {
                "id": "repository",
                "tiers": ["T0", "T1"],
                "assesses": "bounded recognized repository files, configuration syntax, "
                            "and git-history facts selected by registered criteria",
                "does_not_prove": [
                    "a complete repository, security, or workflow review",
                    "absence of relevant unrecognized files or configuration",
                    "effective runtime enforcement",
                    "platform parser acceptance",
                    "workflow trigger reachability, successful runs, or released-artifact "
                    "coverage",
                ],
            },
            {
                "id": "github",
                "tiers": ["T2"],
                "assesses": "selected bounded readable GitHub.com API settings requested by "
                            "registered criteria",
                "does_not_prove": [
                    "unqueried repository, organization, identity, or enterprise settings",
                    "effective behavior outside the observed snapshot",
                    "continuous policy enforcement",
                ],
            },
            {
                "id": "execution",
                "tiers": ["T3"],
                "assesses": "bounded outputs from explicitly opted-in allowlisted commands "
                            "executed once in an isolated copy",
                "does_not_prove": [
                    "a general OS or network sandbox",
                    "continuous enforcement",
                    "test adequacy, production behavior, or evaluation validity beyond the "
                    "observed command",
                ],
            },
            {
                "id": "judgment",
                "tiers": ["T4"],
                "assesses": "bounded advisory agent-authored judgments over cited report "
                            "evidence",
                "does_not_prove": [
                    "deterministic gate satisfaction",
                    "review completeness, factual correctness, or organizational outcomes",
                ],
            },
        ],
        "not_assessed": [
            {"id": "runtime_isolation_egress",
             "label": "Effective runtime filesystem, process, network-egress, and sandbox "
                      "enforcement"},
            {"id": "credential_scope_identity",
             "label": "Production credential scope, broker policy, and distinct agent "
                      "identity"},
            {"id": "human_approval_recovery",
             "label": "Effective human approval, rollback, kill switches, incident "
                      "response, and recovery drills"},
            {"id": "untrusted_context_prompt_injection",
             "label": "Effective untrusted-context isolation and prompt-injection "
                      "resistance"},
            {"id": "runtime_agent_observability",
             "label": "Complete runtime agent, model, prompt, tool, and side-effect traces"},
            {"id": "cost_latency_capacity",
             "label": "Enforced cost, token, latency, rate, and capacity budgets"},
            {"id": "concurrent_coordination_execution",
             "label": "Effective multi-agent isolation, coordination, collision handling, "
                      "and merge correctness"},
            {"id": "evaluation_validity_outcomes",
             "label": "Evaluation representativeness, model generalization, production "
                      "correctness, and regression outcomes"},
            {"id": "organization_governance_adoption",
             "label": "Organization policy enforcement, ownership behavior, adoption, and "
                      "delivery or business outcome improvement"},
        ],
        "known_limitations": [
            {"id": "criterion_scope_non_exhaustive",
             "detail": "The registry is a finite allowlist of checks over bounded candidate "
                       "paths and API fields; files, controls, risks, ecosystems, and "
                       "provider settings outside those named inputs are not reviewed and "
                       "absence of a finding is not proof of absence."},
            {"id": "declarative_evidence_credit",
             "detail": "Some criteria intentionally credit recognized declarations or "
                       "configuration shape; a pass proves only the exact observed condition "
                       "named in that result's evidence and decision trace, not execution, "
                       "enforcement, effectiveness, or outcome."},
            {"id": "static_quality_tool_credit",
             "detail": "Baseline linter, formatter, and type gates may credit declared "
                       "tools/config and do not uniformly prove CI invocation."},
            {"id": "bounded_repository_scan",
             "detail": "Files or candidate sets beyond documented byte, depth, entry, or "
                       "match caps are reported unavailable/unknown rather than inspected."},
            {"id": "textual_workflow_wiring",
             "detail": "Recognized workflow shape does not prove platform acceptance, "
                       "trigger reachability, successful runs, distribution, verification, "
                       "or released-artifact coverage."},
            {"id": "ownership_syntax",
             "detail": "Recognized CODEOWNERS syntax does not prove identity, access, or "
                       "required review."},
            {"id": "permission_policy_shape",
             "detail": "Repository permission policy does not prove effective runtime "
                       "enforcement."},
            {"id": "machine_context_shape",
             "detail": "Recognized MCP/llms configuration shape does not prove server "
                       "availability, package or version authenticity, tool semantics, "
                       "effective permissions, or instruction safety."},
            {"id": "concurrent_protocol_shape",
             "detail": "Documented concurrent-agent protocol does not prove worktree "
                       "isolation, coordination, collision avoidance, merge correctness, or "
                       "verification execution."},
            {"id": "unsigned_assessment_provenance",
             "detail": "Assessment provenance is engine-recorded unsigned metadata; it does "
                       "not authenticate the repository, input, builder, or report "
                       "integrity."},
            {"id": "darwin_git_memory_containment",
             "detail": "On macOS, automatic Git has CPU, core, wall-time, output, "
                       "command-count, and immutable-snapshot caps but no hard address-space "
                       "or resident-memory cap; hard Git memory containment is deferred."},
            {"id": "linked_worktree_scope",
             "detail": "Automatic Git supports only primary checkouts and standard "
                       "reciprocal current-user linked worktrees; submodule gitfiles, custom "
                       "commondir/worktree configuration, unsafe/shared metadata, and "
                       "nonstandard topologies are unavailable."},
            {"id": "history_commit_lineage",
             "detail": "Schema3 history deltas use the current safe Git authority to prove "
                       "old-subject commit ancestry, but unsigned reports are not "
                       "authenticated and uncommitted worktree state has no independent "
                       "lineage proof."},
            {"id": "sensitive_text_redaction",
             "detail": "Output redaction covers defined credential, URL, path, and "
                       "control-character patterns but is not a general "
                       "secret-classification or DLP guarantee."},
            {"id": "local_execution_isolation",
             "detail": "Opt-in T3 uses an isolated copy and scrubbed environment, not a "
                       "kernel-enforced filesystem or network sandbox."},
            {"id": "local_persistence_atomicity",
             "detail": "Report persistence uses per-file atomic replacement plus a final "
                       "validated commit manifest, not a portable multi-file filesystem "
                       "transaction; a host crash or I/O failure can leave uncommitted "
                       "generated files that are refused until manually repaired."},
        ],
    }


# --------------------------------------------------------------------------- text hygiene
_CONTROL_RE = re.compile(
    "[\\x00-\\x1f\\x7f\\x80-\\x9f\\u202a-\\u202e\\u2066-\\u2069\\u200e\\u200f]")


def _normalize_controls(text: str) -> str:
    """Map NUL/C0/DEL/C1 and Unicode bidi override/isolate controls to visible spaces."""
    return _CONTROL_RE.sub(" ", text)


_OVERSIZE_SENTINEL = "[redacted oversized public text]"
_SOURCE_SENTINEL = "[redacted repository source]"

_SENSITIVE_PATTERNS = (
    ("pem block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credential", re.compile(
        r"(?i)(authorization\s*[:=]\s*(basic|bearer)\s+\S+|bearer\s+[a-z0-9._~+/=-]{8,})")),
    ("github token", re.compile(
        r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{10,}\b")),
    ("aws key", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")),
    ("secret assignment", re.compile(
        r"(?i)\b(password|passwd|pwd|token|api[_-]?key|access[_-]?key|client[_-]?secret)"
        r"\b\s*[:=]\s*\S+")),
    ("credential url", re.compile(
        r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+(:[^/\s@]+)?@")),
    ("absolute path", re.compile(
        r"(?<![\w.~])(?:/(?:Users|home|var|private|tmp|opt|etc|root|srv)\b[^\s]*|[A-Za-z]:\\[^\s]*)")),
)


def _sensitive_category(text: str) -> str | None:
    for category, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            return category
    return None


def _sanitize_public_text(value, max_bytes: int = 512) -> str:
    """One public-text boundary: normalize controls, cap, redact sensitive categories.

    A safe normalized value within the bound remains byte-identical; an oversize value is
    replaced in full (never prefix-truncated); a sensitive value becomes a stable category
    sentinel. Idempotent.
    """
    if not isinstance(value, str):
        value = str(value)
    text = _normalize_controls(value)
    category = _sensitive_category(text)
    if category is not None:
        return f"[redacted {category}]"
    if len(text.encode("utf-8")) > max_bytes:
        return _OVERSIZE_SENTINEL
    return text


_REPO_REL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~+@/=-]{0,255}\Z")


def _public_source(value, tier: str) -> str:
    """One evidence-source boundary: repository-relative POSIX, T2 endpoint id, or sentinel."""
    if not isinstance(value, str) or not value:
        return ""
    if value == ".":
        return "."  # the repository root marker is canonical, never redacted
    text = _normalize_controls(value)
    if _sensitive_category(text) is not None:
        return _SOURCE_SENTINEL
    if tier == "T2":
        # T2 sources are authored endpoint ids, not repository paths.
        if len(text.encode("utf-8")) <= 255 and "\x00" not in text and " " not in text \
                and not text.startswith("/") and ".." not in text:
            return text
        return _SOURCE_SENTINEL
    if text.startswith("/") or "\\" in text or text.startswith("~") \
            or re.match(r"^[A-Za-z]:", text):
        return _SOURCE_SENTINEL
    parts = text.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return _SOURCE_SENTINEL
    if len(text.encode("utf-8")) > 255:
        return _SOURCE_SENTINEL
    return text


def _public_commit(value) -> str:
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{4,64}", value or ""):
        return value
    return ""


def _finalize_trace(trace) -> DecisionTrace | None:
    """Sanitize one decision trace's public text; fail closed on machine-field anomalies.

    Step messages and limitations cross the same public-text boundary as rationale and
    evidence. Machine fields (``kind``/``code``/``rule_ref``/``version``) are engine
    grammar, not prose: control-normalized idempotently, and any anomaly drops the trace
    to ``None`` so canonical validation fails closed instead of emitting repaired codes.
    """
    if trace is None:
        return None
    if not isinstance(trace, DecisionTrace):
        return None
    version = _normalize_controls(str(trace.version))
    reason_code = _normalize_controls(str(trace.reason_code))
    rule_ref = _normalize_controls(str(trace.rule_ref))
    for machine_field in (version, reason_code, rule_ref):
        if not machine_field.isascii() or len(machine_field.encode("utf-8")) > 255:
            return None
    steps = []
    for step in trace.steps:
        kind = _normalize_controls(str(step.kind))
        code = _normalize_controls(str(step.code))
        if not kind.isascii() or not code.isascii() or len(code.encode("utf-8")) > 128:
            return None
        refs = [r for r in step.evidence_refs if type(r) is int and r >= 0]
        if len(refs) != len(step.evidence_refs):
            return None
        steps.append(DecisionStep(
            kind=kind, code=code,
            message=_sanitize_public_text(step.message, 512),
            evidence_refs=refs))
    limitations = []
    for limitation in trace.limitations:
        clean = _sanitize_public_text(limitation, 512)
        if clean and clean not in limitations:
            limitations.append(clean)
    return DecisionTrace(version=version, reason_code=reason_code, rule_ref=rule_ref,
                         steps=steps, limitations=limitations)


def finalize_public_result(result: CriterionResult) -> CriterionResult:
    """Idempotent public boundary for one result, applied before trace construction.

    Rationale/evidence text is control-normalized, byte-capped, and category-redacted;
    evidence sources are confined to repository-relative POSIX (or T2 endpoint ids); the
    decision trace's messages/limitations cross the same boundary.
    """
    rationale = _sanitize_public_text(result.rationale, 512)
    app_path = _public_source(result.app_path, "T0")
    if not app_path and result.app_path == ".":
        app_path = "."
    evidence = []
    for e in result.evidence:
        evidence.append(Evidence(
            summary=_sanitize_public_text(e.summary, 512),
            tier=e.tier,
            source=_public_source(e.source, e.tier),
            detail=_sanitize_public_text(e.detail, 512),
        ))
    return CriterionResult(
        id=result.id, title=result.title, pillar=result.pillar, level=result.level,
        scope=result.scope, gating=result.gating, status=result.status,
        rationale=rationale, evidence=evidence, app_path=app_path,
        fixable=result.fixable, fix_kind=result.fix_kind,
        acdc_stage=result.acdc_stage, acdc_loop=result.acdc_loop,
        passed_apps=result.passed_apps, evaluated_apps=result.evaluated_apps,
        decision_trace=_finalize_trace(result.decision_trace),
    )


_HOST_RE = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\Z")
_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+\Z")


def finalize_public_repository(repository):
    """Idempotent public boundary for the repository identity object.

    Schema3 accepts ``None`` or exact kind ``origin|local_path`` with required
    ``identity_hash``; the optional display sets are all-or-none per kind, and any
    malformed/over-cap/sensitive component omits the whole display set.
    """
    if repository is None:
        return None
    if not isinstance(repository, dict):
        return None
    kind = repository.get("identity_kind")
    identity_hash = repository.get("identity_hash")
    if kind not in ("origin", "local_path") or not isinstance(identity_hash, str) \
            or not re.fullmatch(r"[0-9a-f]{16}", identity_hash):
        return None
    out = {"identity_kind": kind, "identity_hash": identity_hash}
    if kind == "origin":
        host, owner, name = (repository.get("host"), repository.get("owner"),
                             repository.get("name"))
        if (isinstance(host, str) and isinstance(owner, str) and isinstance(name, str)
                and host and owner and name
                and all(len(v.encode("utf-8")) <= 255 for v in (host, owner, name))
                and _HOST_RE.match(host) and _SLUG_RE.match(owner) and _SLUG_RE.match(name)
                and "/" not in name
                and all(_sensitive_category(v) is None for v in (host, owner, name))):
            out["host"], out["owner"], out["name"] = host, owner, name
    else:
        name = repository.get("name")
        if (isinstance(name, str) and name and len(name.encode("utf-8")) <= 255
                and _SLUG_RE.match(name) and _sensitive_category(name) is None):
            out["name"] = name
    return out


_TEST_CMD_PROJECTION = {
    "npm test": "npm_test",
    "pytest": "pytest",
    "go test ./...": "go_test",
    "cargo test": "cargo_test",
}
_KNOWN_SURFACES = frozenset(
    {"library", "service", "frontend", "cli", "data", "infra", "unknown", "monorepo-root"})


def _public_app(app: App) -> dict:
    path = _public_source(app.path, "T0")
    if not path and app.path == ".":
        path = "."
    surfaces = [s for s in app.match_surfaces() if s in _KNOWN_SURFACES] or ["unknown"]
    return {
        "path": path,
        "languages": sorted({str(lang).lower() for lang in app.languages
                             if isinstance(lang, str)}),
        "runtime": app.runtime if app.runtime in _KNOWN_SURFACES else "unknown",
        "deploy_surface": surfaces[0],
        "prod_facing": app.prod_facing if app.prod_facing in (True, False) else "unknown",
        "test_cmd": _TEST_CMD_PROJECTION.get(app.test_cmd, ""),
        "ci_jobs": [],
        "type_confidence": round(float(app.type_confidence), 3),
        "type_candidates": [
            {"type": c.get("type") if c.get("type") in _KNOWN_SURFACES else "unknown",
             "confidence": round(float(c.get("confidence", 0.0)), 3),
             "signal": _sanitize_public_text(c.get("signal", ""), 255)}
            for c in app.type_candidates if isinstance(c, dict)
        ],
        "surfaces": surfaces if len(surfaces) > 1 else [],
    }


def _public_detection(detection: Detection | None):
    if detection is None:
        return None
    return {
        "project_type": detection.project_type
        if detection.project_type in _KNOWN_SURFACES else "unknown",
        "confidence": round(float(detection.confidence), 3),
        "signals": [_sanitize_public_text(s, 255) for s in detection.signals],
        "languages": sorted({str(lang).lower() for lang in detection.languages
                             if isinstance(lang, str)}),
        "is_monorepo": bool(detection.is_monorepo),
        "apps": [_public_app(a) for a in detection.apps],
        "opt_in": {"loop_ready": bool((detection.opt_in or {}).get("loop_ready"))},
        "candidates": [
            {"type": c.get("type") if c.get("type") in _KNOWN_SURFACES else "unknown",
             "confidence": round(float(c.get("confidence", 0.0)), 3),
             "signal": _sanitize_public_text(c.get("signal", ""), 255)}
            for c in detection.candidates if isinstance(c, dict)
        ],
        "surfaces": [s for s in detection.match_surfaces() if s in _KNOWN_SURFACES],
        "repository_indeterminate": bool(detection.repository_indeterminate),
        "indeterminate_reason": str(detection.indeterminate_reason),
    }


def _public_gap(g) -> dict:
    """The non-executable public gaps projection (no choice labels/values/policy targets)."""
    return {
        "gap_id": str(g.id),
        "kind": str(g.kind),
        "question": _sanitize_public_text(g.question, 512),
        "why": _sanitize_public_text(g.why, 512),
        "recordable": bool(g.recordable),
        "input_kind": str(g.input_kind),
        "blocked_ids": [str(b) for b in g.blocks],
        "blocked_gating": int(g.blocked_gating),
        "levels": sorted({int(level) for level in g.levels}),
        "evidence": [_sanitize_public_text(str(e), 255) for e in g.evidence],
    }


def _public_provenance(provenance):
    """Deep-copy the engine-recorded provenance; never repair a malformed one."""
    import copy
    if provenance is None:
        return None
    if not isinstance(provenance, dict) or provenance.get("trust") != "unsigned_unverified":
        raise PublicReportValidationError("invalid assessment provenance")
    return copy.deepcopy(provenance)


# --------------------------------------------------------------------------- trace validation
_FORBIDDEN_TRACE_KEYS = frozenset({"chain_of_thought", "thinking", "analysis", "confidence"})
_TRACE_STATUSES = frozenset({"pass", "fail", "unknown", "skipped", "waived"})


_REASON_CODE_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\Z")

# Changed criteria that must emit an exact typed direct-verdict code on every invoked
# direct-check path (engine-owned; not repository metadata). Table order after the
# positive code is deterministic primary-cause precedence within the same status.
REQUIRED_REASON_CODES = {
    "docs.agent_context_map": ("complete", "no_reference", "invalid_reference",
                               "missing_target", "thin_target", "indeterminate"),
    "taskdisc.pr_evidence_contract": ("complete", "sections_incomplete",
                                      "template_indeterminate"),
    "taskdisc.concurrent_agent_protocol": ("complete", "instructions_missing",
                                           "families_incomplete",
                                           "instructions_indeterminate"),
    "security.branch_protection_depth": ("complete", "not_protected",
                                         "controls_incomplete", "github_unavailable",
                                         "observation_unreadable"),
    "security.agent_config_ownership": ("complete", "targets_unowned", "targets_uncertain",
                                        "discovery_indeterminate"),
    "security.supply_chain_provenance": ("complete", "not_applicable", "wiring_incomplete",
                                         "syntax_indeterminate"),
    "docs.machine_context": ("configured", "fallback_configured", "missing",
                             "config_invalid", "literal_secret", "transport_unsafe",
                             "fallback_incomplete", "observation_indeterminate"),
    "build.check_command": ("configured", "missing", "malformed", "unresolved",
                            "observation_indeterminate"),
    "devenv.agent_hooks": ("wired", "missing", "unwired", "observation_indeterminate"),
    "security.agent_permissions": ("safe", "missing", "dangerous_allow",
                                   "secret_denies_incomplete",
                                   "consequence_guards_incomplete", "unsupported_mode",
                                   "malformed", "observation_indeterminate"),
    "security.gitignore_comprehensive": ("complete", "missing", "secrets_incomplete",
                                         "artifacts_incomplete",
                                         "report_output_unprotected",
                                         "policy_inputs_ignored",
                                         "observation_indeterminate"),
    "loop.denylist": ("complete", "missing", "families_incomplete",
                      "observation_indeterminate"),
    "build.release_automation": ("configured", "not_applicable",
                                 "applicability_indeterminate"),
    "taskdisc.pr_templates": ("present", "missing", "observation_indeterminate"),
    "security.branch_protection": ("protected", "not_protected", "github_unavailable",
                                   "observation_unreadable"),
    "security.secret_scanning": ("enabled", "disabled", "github_unavailable",
                                 "observation_unreadable"),
    "build.ci_runs_tests": ("verified", "workflows_missing", "tests_missing",
                            "github_unavailable", "observation_unreadable"),
    "taskdisc.issue_labeling": ("configured", "unconfigured", "github_unavailable",
                                "observation_unreadable"),
    "taskdisc.backlog_health": ("healthy", "unhealthy", "github_unavailable",
                                "observation_unreadable"),
    "style.linter_config": ("configured", "missing", "observation_indeterminate"),
    "style.formatter": ("configured", "missing", "observation_indeterminate"),
}


def validate_required_reason_codes(results: list) -> list[str]:
    """Projection-time enforcement of the typed direct-code contract (fail closed)."""
    errors = []
    for result in results:
        cid = result.get("id") if isinstance(result, dict) else None
        if cid not in REQUIRED_REASON_CODES:
            continue
        trace = result.get("decision_trace") or {}
        code = trace.get("reason_code") or ""
        structural = ("applicability.", "waiver.", "judgment.", "prerequisite.",
                      "aggregate.", "input.")
        if code.startswith(structural):
            continue  # the direct check did not decide this result
        if not (_REASON_CODE_RE.match(code) and code.isascii()
                and len(code.encode("utf-8")) <= 128 and code.startswith(cid + ".")):
            errors.append(f"{cid}: direct reason code missing or malformed")
            continue
        suffix = code[len(cid) + 1:]
        if suffix not in REQUIRED_REASON_CODES[cid]:
            errors.append(f"{cid}: reason code suffix {suffix!r} not allowlisted")
    return errors




def validate_decision_trace(result: dict) -> list[str]:
    """Validate one result dict's canonical schema-v3 decision trace. Returns error strings.

    Requires trace ``version == "1"``, non-empty ``reason_code``/``rule_ref``, exactly three
    steps when evidence is empty or four when evidence exists, and exact order ``rule``,
    optional ``observation``, ``evaluation``, ``conclusion`` — with ``rule.applied``,
    ``evidence.observed``, ``evaluation.code == reason_code``, ``conclusion.<status>``, a
    conclusion message matching the result status, empty evidence refs on non-observation
    steps, and every evidence index referenced exactly once on the observation step.
    """
    errors = []
    trace = result.get("decision_trace") if isinstance(result, dict) else None
    status = result.get("status") if isinstance(result, dict) else None
    evidence = result.get("evidence") if isinstance(result, dict) else None
    if trace is None:
        return ["decision_trace is null"]
    if not isinstance(trace, dict):
        return ["decision_trace is not an object"]
    errors.extend(_scan_forbidden_keys(trace, "decision_trace"))
    if set(trace.keys()) != {"version", "reason_code", "rule_ref", "steps", "limitations"}:
        errors.append("decision_trace keys are not exact")
    if trace.get("version") != "1":
        errors.append("decision_trace.version must be '1'")
    reason_code = trace.get("reason_code")
    if not isinstance(reason_code, str) or not reason_code:
        errors.append("decision_trace.reason_code must be non-empty")
    rule_ref = trace.get("rule_ref")
    if not isinstance(rule_ref, str) or not rule_ref:
        errors.append("decision_trace.rule_ref must be non-empty")
    limitations = trace.get("limitations")
    if not isinstance(limitations, list):
        errors.append("decision_trace.limitations must be a list")
        limitations = []
    elif (any(not isinstance(item, str) or not item for item in limitations)
          or len(set(limitations)) != len(limitations)):
        errors.append("decision_trace.limitations must be unique non-empty strings")
    steps = trace.get("steps")
    if not isinstance(steps, list):
        return errors + ["decision_trace.steps must be a list"]
    n_evidence = len(evidence) if isinstance(evidence, list) else 0
    expected_kinds = ["rule"] + (["observation"] if n_evidence else []) \
        + ["evaluation", "conclusion"]
    if len(steps) != len(expected_kinds):
        errors.append(
            f"decision_trace.steps must have exactly {len(expected_kinds)} step(s)")
        return errors
    refs_seen = []
    for index, (step, expected_kind) in enumerate(zip(steps, expected_kinds, strict=True)):
        if not isinstance(step, dict):
            errors.append(f"step {index} is not an object")
            continue
        if set(step.keys()) != {"kind", "code", "message", "evidence_refs"}:
            errors.append(f"step {index} keys are not exact")
        kind = step.get("kind")
        if kind not in TRACE_KINDS:
            errors.append(f"step {index} has unknown kind {kind!r}")
        if kind != expected_kind:
            errors.append(f"step {index} kind must be {expected_kind!r}")
        code = step.get("code")
        message = step.get("message")
        refs = step.get("evidence_refs")
        if not isinstance(message, str):
            errors.append(f"step {index} message must be a string")
        if not isinstance(refs, list):
            errors.append(f"step {index} evidence_refs must be a list")
            refs = []
        elif any(type(r) is not int or r < 0 or r >= n_evidence for r in refs):
            errors.append(f"step {index} has invalid/out-of-range evidence refs")
        if kind == "rule" and code != "rule.applied":
            errors.append("rule step code must be 'rule.applied'")
        if kind == "observation":
            if code != "evidence.observed":
                errors.append("observation step code must be 'evidence.observed'")
            refs_seen = list(refs)
        elif refs:
            errors.append(f"step {index} ({kind}) must not reference evidence")
        if kind == "evaluation":
            if code != reason_code:
                errors.append("evaluation step code must equal reason_code")
        if kind == "conclusion":
            if code != f"conclusion.{status}":
                errors.append(f"conclusion step code must be 'conclusion.{status}'")
            if isinstance(message, str) and status in _TRACE_STATUSES \
                    and message != f"Result: {status}.":
                errors.append("conclusion message must match the result status")
    if n_evidence and sorted(refs_seen) != list(range(n_evidence)):
        errors.append("observation step must reference every evidence index exactly once")
    return errors


def _scan_forbidden_keys(node, path: str) -> list[str]:
    """Reject private-reasoning keys anywhere inside the decision trace payload."""
    errors = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _FORBIDDEN_TRACE_KEYS:
                errors.append(f"forbidden key {key!r} at {path}")
            errors.extend(_scan_forbidden_keys(value, path))
    elif isinstance(node, list):
        for item in node:
            errors.extend(_scan_forbidden_keys(item, path))
    return errors


# --------------------------------------------------------------------------- imported reports
# Genuine released schema-2 (schema, engine, registry, detector) tuples. Derived from this
# repository's version history and CHANGELOG release records (0.4.0's bump commit was
# squashed in PR #5, but its release record pins schema 2 with engine/registry/detector
# 0.4.0). Anything else is an unknown schema/version combination: invalid, never
# "best effort".
SCHEMA2_RELEASED_TUPLES = frozenset({
    ("2", "0.4.0", "0.4.0", "0.4.0"),
    ("2", "0.5.0", "0.5.0", "0.5.0"),
    ("2", "0.6.0", "0.6.0", "0.5.0"),
    ("2", "0.7.0", "0.7.0", "0.5.0"),
    ("2", "0.8.0", "0.7.0", "0.5.0"),
    ("2", "0.8.1", "0.7.0", "0.5.0"),
    ("2", "0.9.0", "0.7.0", "0.5.0"),
    ("2", "0.9.1", "0.7.0", "0.5.0"),
    ("2", "0.10.0", "0.7.0", "0.5.0"),
    ("2", "0.10.0", "0.7.0", "0.6.0"),
})

_SCHEMA2_TOP_KEYS = frozenset({
    "schema_version", "engine_version", "registry_version", "detector_version",
    "commit", "branch", "github_available", "generated_at", "repository", "detection",
    "score", "results", "advisory", "gaps",
})
_SCHEMA2_REQUIRED_TOP_KEYS = _SCHEMA2_TOP_KEYS - {"gaps"}
_SCHEMA2_RESULT_KEYS = frozenset({
    "id", "title", "pillar", "level", "scope", "gating", "status", "rationale",
    "evidence", "app_path", "fixable", "fix_kind", "acdc_stage", "acdc_loop",
    "passed_apps", "evaluated_apps",
})
_SCHEMA2_REQUIRED_RESULT_KEYS = frozenset({
    "id", "title", "pillar", "level", "scope", "gating", "status", "rationale",
    "evidence", "app_path", "fixable", "fix_kind", "passed_apps", "evaluated_apps",
})
_SCHEMA2_EVIDENCE_KEYS = frozenset({"summary", "tier", "source", "detail"})
_SCHEMA2_SCORE_KEYS = frozenset({
    "level", "level_name", "pass_rate", "gating_passed", "gating_total", "levels",
    "pillars", "recommendations",
})
_SCHEMA2_LEVEL_KEYS = frozenset({"level", "name", "passed", "total", "ratio", "achieved"})
_STATUSES = frozenset({"pass", "fail", "unknown", "skipped", "waived"})
_TIERS = frozenset({"T0", "T1", "T2", "T3", "T4"})

_SCHEMA3_TOP_KEYS = _SCHEMA2_TOP_KEYS | {"assessment_boundary", "assessment_provenance"}
_SCHEMA3_RESULT_KEYS = _SCHEMA2_RESULT_KEYS | {"decision_trace"}
_SCHEMA3_SCORE_KEYS = _SCHEMA2_SCORE_KEYS | {
    "max_available_level", "next_gate_actions", "evidence_coverage",
}
_SCHEMA3_LEVEL_KEYS = _SCHEMA2_LEVEL_KEYS | {"defined", "defined_total"}
_COVERAGE_KEYS = frozenset({
    "status_counts", "results_with_evidence", "evidence_items", "evidence_items_by_tier",
    "results_with_decision_trace", "results_with_rule_step", "results_with_limitations",
    "evidence_items_referenced", "evidence_items_unreferenced",
})


class ImportedReportError(ValueError):
    """An imported report failed strict bounded validation (no repair, no rescore)."""


def _err(errors: list, message: str) -> None:
    errors.append(message)


def _check_evidence(item, errors, ctx: str) -> None:
    if not isinstance(item, dict):
        return _err(errors, f"{ctx}: evidence item not an object")
    if set(item.keys()) != _SCHEMA2_EVIDENCE_KEYS:
        return _err(errors, f"{ctx}: evidence keys not exact")
    if not isinstance(item["summary"], str) or not isinstance(item["source"], str) \
            or not isinstance(item["detail"], str):
        return _err(errors, f"{ctx}: evidence fields must be strings")
    if item["tier"] not in _TIERS:
        _err(errors, f"{ctx}: unknown evidence tier")


def _check_result_v2(result, errors, ctx: str, *,
                     allowed_keys=_SCHEMA2_RESULT_KEYS,
                     required_keys=_SCHEMA2_REQUIRED_RESULT_KEYS) -> None:
    if not isinstance(result, dict):
        return _err(errors, f"{ctx}: result not an object")
    if not required_keys <= set(result.keys()) <= allowed_keys:
        return _err(errors, f"{ctx}: result keys not allowed")
    if not isinstance(result["id"], str) or not result["id"]:
        return _err(errors, f"{ctx}: result id invalid")
    if result["status"] not in _STATUSES:
        return _err(errors, f"{ctx}: unknown status")
    if type(result["level"]) is not int or not 1 <= result["level"] <= 5:
        return _err(errors, f"{ctx}: level out of range")
    if type(result["gating"]) is not bool or not isinstance(result["title"], str) \
            or not isinstance(result["pillar"], str) \
            or not isinstance(result["scope"], str) \
            or not isinstance(result["rationale"], str) \
            or not isinstance(result["app_path"], str):
        return _err(errors, f"{ctx}: field types invalid")
    if type(result["passed_apps"]) is not int or type(result["evaluated_apps"]) is not int:
        return _err(errors, f"{ctx}: app counts invalid")
    if not isinstance(result["evidence"], list):
        return _err(errors, f"{ctx}: evidence not a list")
    for i, item in enumerate(result["evidence"]):
        _check_evidence(item, errors, f"{ctx}.evidence[{i}]")


def _check_score(score, results, errors, schema: int) -> None:
    if not isinstance(score, dict):
        return _err(errors, "score not an object")
    allowed = _SCHEMA3_SCORE_KEYS if schema == 3 else _SCHEMA2_SCORE_KEYS
    if set(score.keys()) != allowed:
        return _err(errors, "score keys not exact")
    for key in ("level", "gating_passed", "gating_total"):
        if type(score[key]) is not int:
            return _err(errors, f"score.{key} must be an integer")
    if not isinstance(score["level_name"], str) \
            or not isinstance(score["pass_rate"], (int, float)) \
            or isinstance(score["pass_rate"], bool):
        return _err(errors, "score scalar types invalid")
    levels = score["levels"]
    if not isinstance(levels, list) or len(levels) != 5:
        return _err(errors, "score.levels must have five entries")
    allowed_level_keys = _SCHEMA3_LEVEL_KEYS if schema == 3 else _SCHEMA2_LEVEL_KEYS
    for entry in levels:
        if not isinstance(entry, dict) or set(entry.keys()) != allowed_level_keys:
            return _err(errors, "score.levels entry keys not exact")
        if type(entry["achieved"]) is not bool or type(entry["level"]) is not int:
            return _err(errors, "score.levels entry types invalid")
        if schema == 3 and (type(entry["defined"]) is not bool
                            or type(entry["defined_total"]) is not int):
            return _err(errors, "score.levels defined fields invalid")
    if not isinstance(score["pillars"], dict) or not isinstance(
            score["recommendations"], list):
        return _err(errors, "score pillars/recommendations invalid")
    # Score counts recomputed only as validation invariants.
    gating = [r for r in results if isinstance(r, dict) and r.get("gating") is True]
    applicable = [r for r in gating if r.get("status") not in ("skipped", "waived")]
    passed = [r for r in applicable if r.get("status") == "pass"]
    if score["gating_total"] != len(applicable) or score["gating_passed"] != len(passed):
        _err(errors, "score gating counts inconsistent with results")
    if schema == 3:
        coverage = score["evidence_coverage"]
        if not isinstance(coverage, dict) or set(coverage.keys()) != _COVERAGE_KEYS:
            return _err(errors, "evidence_coverage keys not exact")
        counts = coverage["status_counts"]
        if not isinstance(counts, dict) \
                or set(counts.keys()) != {"pass", "fail", "unknown", "skipped", "waived"}:
            return _err(errors, "status_counts keys not exact")
        if any(type(v) is not int or v < 0 for v in counts.values()):
            return _err(errors, "status_counts values invalid")
        if sum(counts.values()) != len(results):
            _err(errors, "status_counts must partition results")
        if type(score["max_available_level"]) is not int \
                or not isinstance(score["next_gate_actions"], list):
            _err(errors, "score schema3 fields invalid")


def _validate_top(report, errors, schema: int) -> None:
    required = _SCHEMA2_REQUIRED_TOP_KEYS if schema == 2 else _SCHEMA3_TOP_KEYS
    allowed = _SCHEMA2_TOP_KEYS if schema == 2 else _SCHEMA3_TOP_KEYS
    if set(report.keys()) != allowed and not required <= set(report.keys()) <= allowed:
        return _err(errors, "top-level keys not allowed")
    if schema == 3 and set(report.keys()) != allowed:
        return _err(errors, "schema3 top-level keys must be exact")
    for key in ("schema_version", "engine_version", "registry_version",
                "detector_version"):
        if not isinstance(report[key], str):
            return _err(errors, f"{key} must be a string")
    if type(report["github_available"]) is not bool:
        return _err(errors, "github_available must be a bool")
    if not isinstance(report["commit"], str) or not isinstance(report["branch"], str) \
            or not isinstance(report["generated_at"], str):
        return _err(errors, "commit/branch/generated_at must be strings")
    if not isinstance(report["results"], list):
        return _err(errors, "results must be a list")
    if not isinstance(report["advisory"], list):
        _err(errors, "advisory must be a list")


def validate_imported_report(report, schema_version: str) -> list[str]:
    """Strict, non-rescoring validation of an imported schema-2 or schema-3 report dict.

    Returns a list of authored error strings (empty when valid). Validates types, exact
    allowlisted keys, enum/range constraints, released version/shape combinations, and
    cross-field invariants. Never repairs, normalizes, redacts, backfills, or recomputes a
    stored value into place.
    """
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report root is not an object"]
    if schema_version not in ("2", "3") \
            or report.get("schema_version") != schema_version:
        return ["schema literal mismatch"]
    _validate_top(report, errors, schema_version and int(schema_version))
    if errors:
        return errors
    if schema_version == "2":
        tuple_ = (report["schema_version"], report["engine_version"],
                  report["registry_version"], report["detector_version"])
        if tuple_ not in SCHEMA2_RELEASED_TUPLES:
            _err(errors, "unknown schema/version combination")
        if report.get("detection") is not None \
                and not isinstance(report["detection"], dict):
            _err(errors, "detection must be an object or null")
        for i, result in enumerate(report["results"]):
            _check_result_v2(result, errors, f"results[{i}]")
            if isinstance(result, dict) and "decision_trace" in result:
                _err(errors, f"results[{i}]: schema2 carries no decision trace")
        _check_score(report["score"], report["results"], errors, 2)
        if "assessment_boundary" in report or "assessment_provenance" in report:
            _err(errors, "schema2 carries no boundary/provenance")
        return errors
    # schema 3
    for i, result in enumerate(report["results"]):
        if not isinstance(result, dict) or set(result.keys()) != _SCHEMA3_RESULT_KEYS:
            _err(errors, f"results[{i}]: keys not exact schema3")
            continue
        _check_result_v2(result, errors, f"results[{i}]",
                         allowed_keys=_SCHEMA3_RESULT_KEYS,
                         required_keys=_SCHEMA3_RESULT_KEYS)
        errors.extend(f"results[{i}]: {e}" for e in validate_decision_trace(result))
    _check_score(report["score"], report["results"], errors, 3)
    errors.extend(_check_detection_v3(report.get("detection")))
    errors.extend(_check_advisory_gaps_v3(report))
    if report["assessment_boundary"] != assessment_boundary():
        _err(errors, "assessment_boundary does not match the canonical object")
    provenance = report["assessment_provenance"]
    errors.extend(_validate_provenance(provenance))
    repository = report["repository"]
    if repository is not None and finalize_public_repository(repository) != repository:
        _err(errors, "repository identity is not in canonical public form")
    return errors


_PUBLIC_APP_KEYS = frozenset({
    "path", "languages", "runtime", "deploy_surface", "prod_facing", "test_cmd",
    "ci_jobs", "type_confidence", "type_candidates", "surfaces",
})
_PUBLIC_DETECTION_KEYS = frozenset({
    "project_type", "confidence", "signals", "languages", "is_monorepo", "apps",
    "opt_in", "candidates", "surfaces", "repository_indeterminate",
    "indeterminate_reason",
})
_PUBLIC_CANDIDATE_KEYS = frozenset({"type", "confidence", "signal"})
_PUBLIC_GAP_KEYS = frozenset({
    "gap_id", "kind", "question", "why", "recordable", "input_kind", "blocked_ids",
    "blocked_gating", "levels", "evidence",
})
_PUBLIC_TYPES = frozenset({"library", "service", "frontend", "cli", "data", "infra",
                           "unknown", "monorepo-root"})
_TEST_CMD_ENUM = frozenset({"", "npm_test", "pytest", "go_test", "cargo_test"})
_GAP_KIND_ENUM = frozenset({"detection", "config", "capability"})
_INPUT_KIND_ENUM = frozenset({"single_choice", "multi_choice", "integer", "unrecordable"})


def _check_candidate(value, ctx: str) -> list[str]:
    if not isinstance(value, dict) or set(value.keys()) != _PUBLIC_CANDIDATE_KEYS:
        return [f"{ctx}: candidate keys not exact"]
    errors = []
    if value["type"] not in _PUBLIC_TYPES:
        errors.append(f"{ctx}: candidate type invalid")
    if not isinstance(value["signal"], str):
        errors.append(f"{ctx}: candidate signal invalid")
    if not isinstance(value["confidence"], (int, float)) \
            or isinstance(value["confidence"], bool):
        errors.append(f"{ctx}: candidate confidence invalid")
    return errors


def _check_detection_v3(detection) -> list[str]:
    if detection is None:
        return []
    if not isinstance(detection, dict) or set(detection.keys()) != _PUBLIC_DETECTION_KEYS:
        return ["detection keys not exact"]
    errors = []
    if detection["project_type"] not in _PUBLIC_TYPES:
        errors.append("detection.project_type invalid")
    if not isinstance(detection["confidence"], (int, float)) \
            or isinstance(detection["confidence"], bool):
        errors.append("detection.confidence invalid")
    if not isinstance(detection["signals"], list) \
            or not all(isinstance(s, str) for s in detection["signals"]):
        errors.append("detection.signals invalid")
    if not isinstance(detection["languages"], list) \
            or not all(isinstance(lang, str) for lang in detection["languages"]):
        errors.append("detection.languages invalid")
    if type(detection["is_monorepo"]) is not bool:
        errors.append("detection.is_monorepo invalid")
    if not isinstance(detection["opt_in"], dict) \
            or set(detection["opt_in"].keys()) != {"loop_ready"} \
            or type(detection["opt_in"]["loop_ready"]) is not bool:
        errors.append("detection.opt_in invalid")
    if type(detection["repository_indeterminate"]) is not bool \
            or not isinstance(detection["indeterminate_reason"], str):
        errors.append("detection indeterminate fields invalid")
    if not isinstance(detection["surfaces"], list) \
            or any(s not in _PUBLIC_TYPES for s in detection["surfaces"]):
        errors.append("detection.surfaces invalid")
    if not isinstance(detection["candidates"], list):
        errors.append("detection.candidates invalid")
    else:
        for i, candidate in enumerate(detection["candidates"]):
            errors.extend(_check_candidate(candidate, f"detection.candidates[{i}]"))
    if not isinstance(detection["apps"], list):
        errors.append("detection.apps invalid")
    else:
        for i, app in enumerate(detection["apps"]):
            errors.extend(_check_app_v3(app, f"detection.apps[{i}]"))
    return errors


def _check_app_v3(app, ctx: str) -> list[str]:
    if not isinstance(app, dict) or set(app.keys()) != _PUBLIC_APP_KEYS:
        return [f"{ctx}: app keys not exact"]
    errors = []
    if not isinstance(app["path"], str) or not app["path"]:
        errors.append(f"{ctx}: app.path invalid")
    if not isinstance(app["languages"], list) \
            or not all(isinstance(lang, str) for lang in app["languages"]):
        errors.append(f"{ctx}: app.languages invalid")
    if app["runtime"] not in _PUBLIC_TYPES or app["deploy_surface"] not in _PUBLIC_TYPES:
        errors.append(f"{ctx}: app surface enums invalid")
    if app["prod_facing"] not in (True, False, "unknown"):
        errors.append(f"{ctx}: app.prod_facing invalid")
    if app["test_cmd"] not in _TEST_CMD_ENUM:
        errors.append(f"{ctx}: app.test_cmd invalid")
    if app["ci_jobs"] != []:
        errors.append(f"{ctx}: app.ci_jobs must be empty")
    if not isinstance(app["surfaces"], list) \
            or any(s not in _PUBLIC_TYPES for s in app["surfaces"]):
        errors.append(f"{ctx}: app.surfaces invalid")
    if not isinstance(app["type_candidates"], list):
        errors.append(f"{ctx}: app.type_candidates invalid")
    else:
        for j, candidate in enumerate(app["type_candidates"]):
            errors.extend(_check_candidate(candidate, f"{ctx}.type_candidates[{j}]"))
    return errors


def _check_advisory_gaps_v3(report) -> list[str]:
    errors = []
    for i, item in enumerate(report["advisory"]):
        if not isinstance(item, str):
            errors.append(f"advisory[{i}] must be a string")
    gaps = report.get("gaps")
    if gaps is None:
        return errors
    if not isinstance(gaps, list):
        return errors + ["gaps must be a list"]
    for i, gap in enumerate(gaps):
        ctx = f"gaps[{i}]"
        if not isinstance(gap, dict) or set(gap.keys()) != _PUBLIC_GAP_KEYS:
            errors.append(f"{ctx}: keys not exact")
            continue
        if not isinstance(gap["gap_id"], str) or not gap["gap_id"]:
            errors.append(f"{ctx}: gap_id invalid")
        if gap["kind"] not in _GAP_KIND_ENUM:
            errors.append(f"{ctx}: kind invalid")
        if not isinstance(gap["question"], str) or not isinstance(gap["why"], str):
            errors.append(f"{ctx}: question/why invalid")
        if type(gap["recordable"]) is not bool or gap["input_kind"] not in _INPUT_KIND_ENUM:
            errors.append(f"{ctx}: recordable/input_kind invalid")
        if not isinstance(gap["blocked_ids"], list) \
                or not all(isinstance(b, str) for b in gap["blocked_ids"]):
            errors.append(f"{ctx}: blocked_ids invalid")
        if type(gap["blocked_gating"]) is not int or gap["blocked_gating"] < 0:
            errors.append(f"{ctx}: blocked_gating invalid")
        if not isinstance(gap["levels"], list) \
                or not all(type(level) is int and 1 <= level <= 5 for level in gap["levels"]):
            errors.append(f"{ctx}: levels invalid")
        if not isinstance(gap["evidence"], list) \
                or not all(isinstance(e, str) for e in gap["evidence"]):
            errors.append(f"{ctx}: evidence invalid")
    return errors


def _validate_provenance(provenance) -> list[str]:
    errors: list[str] = []
    if not isinstance(provenance, dict):
        return ["assessment_provenance not an object"]
    if provenance.get("trust") != "unsigned_unverified":
        return ["assessment_provenance.trust must be unsigned_unverified"]
    if set(provenance.keys()) != {"trust", "predicate_type", "builder", "subject",
                                  "materials", "invocation", "generated_at"}:
        return ["assessment_provenance keys not exact"]
    if provenance.get("predicate_type") != "agent-readiness/assessment/v1":
        errors.append("predicate_type invalid")
    builder = provenance.get("builder")
    if not isinstance(builder, dict) or set(builder.keys()) != {"id", "engine_version",
                                                                "platform"}:
        errors.append("builder keys not exact")
    elif builder["id"] != "ra1-engine" or builder["platform"] not in ("linux", "darwin"):
        errors.append("builder id/platform invalid")
    subject = provenance.get("subject")
    if not isinstance(subject, dict) or set(subject.keys()) != {
            "repository_identity_kind", "repository_identity_hash", "commit", "branch"}:
        errors.append("subject keys not exact")
    invocation = provenance.get("invocation")
    if not isinstance(invocation, dict) \
            or set(invocation.keys()) != {"inputs", "static", "github", "git", "execution",
                                          "waivers"}:
        errors.append("invocation keys not exact")
    else:
        inputs = invocation["inputs"]
        if not isinstance(inputs, dict) or set(inputs.keys()) != {"profile"} \
                or inputs["profile"] not in ("repository", "injected"):
            errors.append("invocation.inputs.profile invalid")
        execution = invocation["execution"]
        if isinstance(execution, dict):
            requested = bool(execution.get("requested"))
            completed = bool(execution.get("completed"))
            successful = bool(execution.get("successful"))
            if successful and not completed:
                errors.append("execution.successful requires completed")
            if completed and not requested:
                errors.append("execution.completed requires requested")
        else:
            errors.append("invocation.execution not an object")
    if not isinstance(provenance.get("generated_at"), str):
        errors.append("generated_at must be a string")
    return errors


def validate_legacy_fix_report_v1(report) -> list[str]:
    """Preserve schema 1 through the dedicated v0.2-shape validator.

    Accepted only from explicit ``fix --report PATH`` for plan-only output. Exact v0.2
    shape: score omits recommendations; results omit N/M and AC/DC; every free-text field
    (including ``project_path``) is untrusted data and never a write authority.
    """
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report root is not an object"]
    required = {"schema_version", "engine_version", "registry_version", "detector_version",
                "project_path", "commit", "branch", "github_available", "detection",
                "score", "results", "advisory"}
    if set(report.keys()) != required:
        return ["schema1 top-level keys not exact"]
    if report.get("schema_version") != "1":
        return ["schema1 literal mismatch"]
    score = report.get("score")
    if not isinstance(score, dict) or "recommendations" in score:
        return ["schema1 score shape invalid"]
    for i, result in enumerate(report.get("results", [])):
        if not isinstance(result, dict):
            return [f"schema1 results[{i}] not an object"]
        if "passed_apps" in result or "evaluated_apps" in result \
                or "acdc_stage" in result or "acdc_loop" in result:
            return [f"schema1 results[{i}] carries post-v0.2 fields"]
        if result.get("status") not in _STATUSES:
            return [f"schema1 results[{i}] unknown status"]
    return errors
