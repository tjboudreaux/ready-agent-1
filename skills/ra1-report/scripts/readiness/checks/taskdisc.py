"""Task Discovery checks (issue/PR hygiene)."""
from __future__ import annotations

import re
from datetime import UTC
from statistics import median

from ._helpers import ev, failed, parse_iso, passed, skipped, unknown

# GitHub's default label set; a real taxonomy means labels beyond these.
_DEFAULT_LABELS = {
    "bug", "documentation", "duplicate", "enhancement", "good first issue",
    "help wanted", "invalid", "question", "wontfix",
}


def issue_templates(ctx):
    files = ctx.static.glob([".github/ISSUE_TEMPLATE/*", ".github/ISSUE_TEMPLATE.md",
                             ".github/issue_template.md"])
    if files:
        return passed("Issue templates present.", [ev("issue templates", source=files[0])])
    return failed("Missing issue templates (.github/ISSUE_TEMPLATE/).")


def pr_templates(ctx):
    files = ctx.static.glob([".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md",
                             ".github/PULL_REQUEST_TEMPLATE/*", "docs/pull_request_template.md",
                             "PULL_REQUEST_TEMPLATE.md"])
    if files:
        return passed("PR template present.", [ev("PR template", source=files[0])],
                      reason_code="taskdisc.pr_templates.present")
    return failed("Missing PR template.", reason_code="taskdisc.pr_templates.missing")


def issue_labeling(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read label taxonomy.",
                       reason_code="taskdisc.issue_labeling.github_unavailable")
    obs = ctx.github.labels()
    if obs.state == "unreadable":
        return unknown("Labels could not be read; not verified.",
                       reason_code="taskdisc.issue_labeling.observation_unreadable",
                       limitations=["The selected GitHub control was not verified."])
    labels = {label.lower() for label in (obs.value if obs.state == "present" else ())}
    custom = labels - _DEFAULT_LABELS
    if custom or ctx.static.glob([".github/labels.yml", ".github/labeler.yml"]):
        return passed(f"{len(custom)} custom label(s) beyond the defaults.",
                       [ev("label taxonomy", tier="T2")],
                       reason_code="taskdisc.issue_labeling.configured")
    return failed("Only default labels; no priority/area taxonomy.",
                  reason_code="taskdisc.issue_labeling.unconfigured")


def backlog_health(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read backlog.",
                       reason_code="taskdisc.backlog_health.github_unavailable")
    obs = ctx.github.open_issues()
    if obs.state == "unreadable":
        return unknown("Backlog could not be read; not verified.",
                       reason_code="taskdisc.backlog_health.observation_unreadable",
                       limitations=["The selected GitHub control was not verified."])
    issues = obs.value if obs.state == "present" else ()
    if not issues:
        return passed("No open issues needing hygiene.",
                      reason_code="taskdisc.backlog_health.healthy")
    labeled = [i for i in issues if i.has_labels]
    ratio = len(labeled) / len(issues)
    if ratio >= 0.7:
        return passed(f"{int(ratio * 100)}% of open issues are labeled.",
                       [ev("backlog hygiene", tier="T2")],
                       reason_code="taskdisc.backlog_health.healthy")
    return failed(f"Only {int(ratio * 100)}% of open issues are labeled (<70%).",
                  reason_code="taskdisc.backlog_health.unhealthy")


def actionable_backlog_items(ctx):
    """Pass when most open issues are actionable: labeled or milestoned AND carrying a body.

    Stricter than backlog_health (labels only): an actionable item also needs context to work on."""
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read backlog items.")
    obs = ctx.github.open_issues()
    if obs.state == "unreadable":
        return unknown("Backlog could not be read.")
    issues = obs.value if obs.state == "present" else ()
    if not issues:
        return passed("No open issues to assess.")
    actionable = [i for i in issues if (i.has_labels or i.has_milestone) and i.has_body]
    ratio = len(actionable) / len(issues)
    if ratio >= 0.6:
        return passed(
            f"{int(ratio * 100)}% of open issues are actionable (labeled/milestoned + body).",
                      [ev("actionable backlog", tier="T2")])
    return failed(f"Only {int(ratio * 100)}% of open issues are actionable (<60%).")


def review_latency(ctx):
    """Pass when median first-review latency on recent merged PRs is ≤ 48 hours."""

    if not ctx.github.available:
        return skipped("no GitHub API")
    obs = ctx.github.recent_merged_prs(20)
    if obs.state == "unreadable":
        return unknown("Merged PRs could not be read; not verified.",
                       limitations=["The selected GitHub control was not verified."])
    prs = obs.value if obs.state == "present" else ()
    latencies = []
    for pr in prs:
        created = parse_iso(pr.created_at)
        review_obs = ctx.github.pr_first_review_iso(pr.number)
        if review_obs.state == "unreadable":
            return unknown("PR reviews could not be read; not verified.",
                           limitations=["The selected GitHub control was not verified."])
        first = parse_iso(review_obs.value if review_obs.state == "present" else None)
        if not created or not first:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if first.tzinfo is None:
            first = first.replace(tzinfo=UTC)
        latencies.append((first - created).total_seconds() / 3600.0)
    if len(latencies) < 5:
        return skipped("insufficient reviewed PRs")
    med = median(latencies)
    evidence = [ev(f"median first-review {med:.1f}h (n={len(latencies)})", tier="T2")]
    if med <= 48:
        return passed(
            f"Median first-review latency {med:.1f}h ≤ 48h (n={len(latencies)}).",
            evidence,
        )
    return failed(f"Median first-review latency {med:.1f}h > 48h (n={len(latencies)}).", evidence)


# --- PR evidence contract + concurrent-agent protocol (advisory) ----------------------

_PR_TEMPLATE_BASENAMES = ("pull_request_template.md", "PULL_REQUEST_TEMPLATE.md")


def _pr_template_candidates(ctx) -> list[str]:
    """The shared, case-insensitive PR-template discovery used by both PR criteria."""
    found = set()
    for base in ("", "docs/", ".github/"):
        for name in _PR_TEMPLATE_BASENAMES:
            if ctx.static.exists_any([base + name]):
                found.add(base + name)
        for path in ctx.static.glob([base + "PULL_REQUEST_TEMPLATE/*.md"]):
            found.add(path)
    return sorted(found)


_EVIDENCE_GROUPS = (
    ("intent", re.compile(r"(?i)\b(summary|what|why)\b")),
    ("verification", re.compile(r"(?i)\b(test|tests|testing|verif|evidence)\b")),
    ("risk", re.compile(r"(?i)(risk|blast radius)")),
    ("recovery", re.compile(r"(?i)\b(rollback|revert|backout)\b")),
)


def pr_evidence_contract(ctx):
    """At least one PR template must carry intent/verification/risk/recovery sections."""
    candidates = _pr_template_candidates(ctx)
    if not candidates:
        return failed(
            "No PR template found; the evidence contract cannot be evaluated.",
            reason_code="taskdisc.pr_evidence_contract.sections_incomplete",
            limitations=["PR template content does not prove authors complete or reviewers "
                         "enforce it."])
    best_missing = None
    for path in candidates:
        text = ctx.static.read(path)
        if text is None:
            return unknown(
                "A PR template candidate could not be read safely.",
                reason_code="taskdisc.pr_evidence_contract.template_indeterminate",
                limitations=["Files beyond documented bounds or safety rules are reported "
                             "unavailable rather than inspected."])
        missing = [name for name, needle in _EVIDENCE_GROUPS if not needle.search(text)]
        if not missing:
            return passed(
                "PR template carries intent, verification, risk, and recovery sections.",
                [ev("PR evidence contract", source=path, tier="T0")],
                reason_code="taskdisc.pr_evidence_contract.complete",
                limitations=["PR template content does not prove authors complete or "
                             "reviewers enforce it."])
        if best_missing is None or len(missing) < len(best_missing[1]):
            best_missing = (path, missing)
    path, missing = best_missing
    return failed(
        f"Closest PR template is missing section group(s): {', '.join(missing)}.",
        [ev("closest PR template", source=path, tier="T0")],
        reason_code="taskdisc.pr_evidence_contract.sections_incomplete",
        limitations=["PR template content does not prove authors complete or reviewers "
                     "enforce it."])


_SHARED_INSTRUCTION_FILES = (
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursorrules", ".windsurfrules",
    ".github/copilot-instructions.md",
)
_SHARED_INSTRUCTION_GLOBS = (
    ".github/instructions/*.instructions.md", ".cursor/rules/*.mdc",
)


def _shared_instruction_texts(ctx) -> list[tuple[str, str]]:
    """(path, text) of recognized shared agent instruction files, bounded and ordered."""
    out = []
    for path in _SHARED_INSTRUCTION_FILES:
        text = ctx.static.read(path)
        if text is not None:
            out.append((path, text))
    for pattern in _SHARED_INSTRUCTION_GLOBS:
        for path in ctx.static.glob([pattern]):
            text = ctx.static.read(path)
            if text is not None:
                out.append((path, text))
    return out


_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s.*$")


def _normative_statements(text: str) -> list[str]:
    """List items and standalone normative sentences, stripped of decoys."""
    text = _FRONTMATTER_RE.sub("", text)
    text = _FENCE_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    statements = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lstrip().startswith(("- ", "* ", "+ ")):
            statements.append(stripped.lstrip()[2:].strip())
        elif stripped.endswith((".", "!", ":")) and len(stripped) > 20:
            statements.append(stripped)
        elif re.match(r"^\d+[.)]\s", stripped):
            statements.append(re.sub(r"^\d+[.)]\s+", "", stripped))
    return [s for s in statements if s]


_PROTOCOL_GROUPS = (
    ("isolation", re.compile(
        r"(?i)(require|use|assign|claim|must use|work in|create)\b.{0,80}"
        r"\b(separate|own|dedicated|per-task)\s+(worktree|task branch|branch)|"
        r"\b(file|directory) ownership\b|\bown(ing|s)? (a |the )?(files|areas|directories)|"
        r"\bisolat\w+\b")),
    ("coordination", re.compile(
        r"(?i)\b(coordinate|coordinate with|message|notify|lock|claim|announce)\b.{0,80}"
        r"\b(before|when)\b.{0,60}\b(touch|edit|change|modify)\w*\b|"
        r"\b(overlapping|shared|same)\s+(file|files|surface|area)\b.{0,80}"
        r"\b(coordinate|lock|claim|message|notify)\b")),
    ("preservation", re.compile(
        r"(?i)\b(re-read|refresh|preserve|keep|never overwrite|never revert|never delete|"
        r"do not overwrite|do not revert|do not delete)\b.{0,80}"
        r"\b(unexpected|user|concurrent|other)\b|\b(unexpected|user|concurrent)\s+changes?\b.{0,80}"
        r"\b(preserve|keep|re-read|refresh|never overwrite)\b")),
    ("verification", re.compile(
        r"(?i)\b(after|before)\s+(merge|merging|integration|integrating|rebase|rebasing|"
        r"conflict resolution|resolving conflicts)\b.{0,100}"
        r"\b(run|execute|re-run)\b.{0,60}\b(test|tests|test suite|verify|verification|"
        r"check|checks|suite)\b|\b(run|execute)\s+(the\s+)?(canonical|full)\s+(test|verify|"
        r"check)\b.{0,80}\b(after|before)\s+(merge|integration)\b")),
)


def _distinct_group_assignment(statements: list[str]) -> bool:
    """Bipartite matching: four groups need four *distinct* qualifying statements."""
    # groups x statements -> match matrix
    matches = []
    for _name, needle in _PROTOCOL_GROUPS:
        matches.append([i for i, s in enumerate(statements) if needle.search(s)])

    def assign(group_index, used):
        if group_index == len(matches):
            return True
        for statement_index in matches[group_index]:
            if statement_index not in used:
                if assign(group_index + 1, used | {statement_index}):
                    return True
        return False

    return assign(0, set())


def concurrent_agent_protocol(ctx):
    """Recognized shared instructions must document the four-part concurrent-work protocol."""
    texts = _shared_instruction_texts(ctx)
    if not texts:
        return failed(
            "No recognized shared agent instruction file documents a concurrent-agent "
            "work protocol.",
            reason_code="taskdisc.concurrent_agent_protocol.instructions_missing",
            limitations=["Documented protocol statements do not prove isolation, "
                         "coordination, collision avoidance, merge correctness, or "
                         "verification execution."])
    contributing = []
    groups_found = [False] * len(_PROTOCOL_GROUPS)
    for _path, text in texts:
        statements = _normative_statements(text)
        for index, (_name, needle) in enumerate(_PROTOCOL_GROUPS):
            if any(needle.search(s) for s in statements):
                groups_found[index] = True
    all_statements = [s for _path, text in texts for s in _normative_statements(text)]
    if _distinct_group_assignment(all_statements):
        for path, text in texts:
            statements = _normative_statements(text)
            if any(needle.search(s) for s in statements for _n, needle in _PROTOCOL_GROUPS):
                contributing.append(path)
        return passed(
            "Shared instructions document isolation/ownership, overlap coordination, "
            "change preservation, and post-integration verification.",
            [ev("concurrent-agent protocol", source=p, tier="T0")
             for p in contributing[:4]],
            reason_code="taskdisc.concurrent_agent_protocol.complete",
            limitations=["Documented protocol statements do not prove isolation, "
                         "coordination, collision avoidance, merge correctness, or "
                         "verification execution."])
    missing = [name for (name, _n), found in zip(_PROTOCOL_GROUPS, groups_found, strict=True)
               if not found]
    if missing:
        rationale = ("Concurrent-agent protocol is missing statement group(s): "
                     + ", ".join(missing) + ".")
    else:
        rationale = ("Concurrent-agent protocol statements exist but cannot be matched to "
                     "four distinct normative statements.")
    return failed(
        rationale,
        [ev("shared instructions", source=p, tier="T0") for p, _t in texts[:4]],
        reason_code="taskdisc.concurrent_agent_protocol.families_incomplete",
        limitations=["Documented protocol statements do not prove isolation, "
                     "coordination, collision avoidance, merge correctness, or "
                     "verification execution."])
