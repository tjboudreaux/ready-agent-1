"""Task Discovery checks (issue/PR hygiene)."""
from __future__ import annotations

from statistics import median

from ._helpers import ev, failed, parse_iso, passed, skipped

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
        return passed("PR template present.", [ev("PR template", source=files[0])])
    return failed("Missing PR template.")


def issue_labeling(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read label taxonomy.")
    labels = {l.lower() for l in ctx.github.labels()}
    custom = labels - _DEFAULT_LABELS
    if custom or ctx.static.glob([".github/labels.yml", ".github/labeler.yml"]):
        return passed(f"{len(custom)} custom label(s) beyond the defaults.", [ev("label taxonomy", tier="T2")])
    return failed("Only default labels; no priority/area taxonomy.")


def backlog_health(ctx):
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read backlog.")
    issues = ctx.github.open_issues()
    if not issues:
        return passed("No open issues needing hygiene.")
    labeled = [i for i in issues if i.get("labels")]
    ratio = len(labeled) / len(issues)
    if ratio >= 0.7:
        return passed(f"{int(ratio * 100)}% of open issues are labeled.", [ev("backlog hygiene", tier="T2")])
    return failed(f"Only {int(ratio * 100)}% of open issues are labeled (<70%).")


def actionable_backlog_items(ctx):
    """Pass when most open issues are actionable: labeled or milestoned AND carrying a body.

    Stricter than backlog_health (labels only): an actionable item also needs context to work on."""
    if not ctx.github.available:
        return skipped("No GitHub API; cannot read backlog items.")
    issues = ctx.github.open_issues()
    if not issues:
        return passed("No open issues to assess.")
    actionable = [i for i in issues
                  if (i.get("labels") or i.get("milestone")) and (i.get("body") or "").strip()]
    ratio = len(actionable) / len(issues)
    if ratio >= 0.6:
        return passed(f"{int(ratio * 100)}% of open issues are actionable (labeled/milestoned + body).",
                      [ev("actionable backlog", tier="T2")])
    return failed(f"Only {int(ratio * 100)}% of open issues are actionable (<60%).")


def review_latency(ctx):
    """Pass when median first-review latency on recent merged PRs is ≤ 48 hours."""
    from datetime import timezone

    if not ctx.github.available:
        return skipped("no GitHub API")
    prs = ctx.github.recent_merged_prs(20)
    latencies = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        created = parse_iso(pr.get("created_at"))
        first = parse_iso(ctx.github.pr_first_review_iso(pr.get("number")))
        if not created or not first:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        latencies.append((first - created).total_seconds() / 3600.0)
    if len(latencies) < 5:
        return skipped("insufficient reviewed PRs")
    med = median(latencies)
    evidence = [ev(f"median first-review {med:.1f}h (n={len(latencies)})", tier="T2")]
    if med <= 48:
        return passed(f"Median first-review latency {med:.1f}h ≤ 48h (n={len(latencies)}).", evidence)
    return failed(f"Median first-review latency {med:.1f}h > 48h (n={len(latencies)}).", evidence)
