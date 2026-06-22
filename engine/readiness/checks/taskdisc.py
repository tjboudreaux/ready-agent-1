"""Task Discovery checks (issue/PR hygiene)."""
from __future__ import annotations

from ._helpers import ev, failed, passed, skipped

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
