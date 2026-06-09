"""The fix engine: plan and apply remediation from a readiness report.

Safety model (from review):
- AUTO-APPLY only genuinely-missing *config* scaffolds; idempotent, never overwrites a non-empty file.
- PROPOSE prose/tests as drafts (the engine never writes them — the skill drafts for human review).
- GITHUB settings are a manual checklist, never auto-applied and never bundled with code.
- Refuses on a dirty worktree unless --force. The engine touches files only; git branch/commit is the
  skill's controlled action (and never pushes without confirmation).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .. import score

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"

# criterion id -> (target path in repo, template filename in templates/)
STATIC_SCAFFOLDS = {
    "build.ci_present": (".github/workflows/readiness.yml", "ci/readiness.yml"),
    "security.security_md": ("SECURITY.md", "SECURITY.md"),
    "taskdisc.issue_templates": (".github/ISSUE_TEMPLATE/bug_report.md", "ISSUE_TEMPLATE/bug_report.md"),
    "taskdisc.pr_templates": (".github/pull_request_template.md", "pull_request_template.md"),
    "security.dependency_update_automation": (".github/dependabot.yml", "dependabot.yml"),
    "devenv.devcontainer": (".devcontainer/devcontainer.json", "devcontainer.json"),
    "style.precommit_hooks": (".pre-commit-config.yaml", "precommit-config.yaml"),
    "security.codeowners": ("CODEOWNERS", "CODEOWNERS"),
    "devenv.env_template": (".env.example", "env.example"),
}

_GH_COMMANDS = {
    "security.branch_protection": "gh api -X PUT repos/{owner}/{repo}/branches/{branch}/protection -f required_pull_request_reviews… (review first)",
    "security.secret_scanning": "Enable in Settings → Code security & analysis (or via gh api).",
    "taskdisc.issue_labeling": "gh label create 'priority:high' --color B60205 ; gh label create 'area:core' …",
    "taskdisc.backlog_health": "Triage open issues and apply labels.",
}


def _languages(report):
    return (report.get("detection") or {}).get("languages", [])


def resolve_scaffold(cid, langs):
    if cid in STATIC_SCAFFOLDS:
        return STATIC_SCAFFOLDS[cid]
    if cid == "style.linter_config":
        return (".eslintrc.json", "eslintrc.json") if "npm" in langs else ("ruff.toml", "ruff.toml")
    if cid == "style.formatter":
        return (".prettierrc.json", "prettierrc.json") if "npm" in langs else ("ruff.toml", "ruff.toml")
    if cid == "security.gitignore_comprehensive":
        return (".gitignore", "__gitignore_append__")
    return None


def _nonempty(path: Path) -> bool:
    return path.exists() and bool(path.read_text(errors="ignore").strip())


def load_report(args, root):
    path = Path(args.report) if getattr(args, "report", None) else root / ".agents" / "readiness" / "latest.json"
    if not Path(path).exists():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def build_plan(root, report, registry=None):
    root = Path(root)
    registry = registry or score.load_registry()
    by_id = {c["id"]: c for c in registry}
    langs = _languages(report)
    plan = {"auto": [], "propose": [], "github": [], "manual": []}
    seen = set()
    for r in report.get("results", []):
        if r.get("status") != "fail":
            continue
        fix = (by_id.get(r["id"]) or {}).get("fix") or {}
        kind = fix.get("kind", "")
        if kind == "scaffold":
            res = resolve_scaffold(r["id"], langs)
            if not res:
                plan["manual"].append(r["id"])
                continue
            target, template = res
            if target in seen:
                continue
            seen.add(target)
            plan["auto"].append({"id": r["id"], "target": target, "template": template,
                                 "exists": _nonempty(root / target)})
        elif kind == "propose":
            plan["propose"].append({"id": r["id"], "title": r.get("title", "")})
        elif kind == "github_setting":
            plan["github"].append({"id": r["id"], "title": r.get("title", ""),
                                   "command": _GH_COMMANDS.get(r["id"], "Configure in repository settings.")})
        else:
            plan["manual"].append(r["id"])
    return plan


def apply_plan(root, plan, templates_dir=None, write=True):
    root = Path(root)
    templates_dir = Path(templates_dir or TEMPLATES_DIR)
    written, skipped = [], []
    for item in plan["auto"]:
        target = root / item["target"]
        if item["template"] == "__gitignore_append__":
            (written if _apply_gitignore(target, write) else skipped).append(item["target"])
            continue
        if _nonempty(target):
            skipped.append(item["target"])
            continue
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((templates_dir / item["template"]).read_text(encoding="utf-8"), encoding="utf-8")
        written.append(item["target"])
    return {"written": written, "skipped": skipped}


def _apply_gitignore(path: Path, write=True):
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    blob = existing.lower()
    additions = []
    if not any(k in blob for k in (".env", "secret", ".pem")):
        additions += [".env", ".env.*", "*.pem"]
    if not any(k in blob for k in ("__pycache__", "node_modules", "dist", "build", ".venv")):
        additions += ["__pycache__/", "node_modules/", "dist/", "build/", ".venv/"]
    if not additions:
        return False
    if write:
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        path.write_text(existing + sep + "\n# Added by sheldon-fix\n" + "\n".join(additions) + "\n", encoding="utf-8")
    return True


def worktree_dirty(root, runner=None):
    runner = runner or _git_status_runner
    out = runner(root, ["status", "--porcelain"])
    if out is None:
        return None
    return bool(out.strip())


def _git_status_runner(root, args):  # pragma: no cover - subprocess boundary
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def format_plan(plan, result=None, dry_run=True):
    lines = [f"# sheldon-fix plan{' (dry run — no files written)' if dry_run else ''}", ""]
    lines.append("## Auto-apply (safe config scaffolds)")
    if not plan["auto"]:
        lines.append("- (none)")
    for it in plan["auto"]:
        if result and it["target"] in result.get("written", []):
            state = "written"
        elif it["exists"]:
            state = "exists → skipped"
        else:
            state = "would create"
        lines.append(f"- `{it['target']}` ({it['id']}) — {state}")
    if plan["propose"]:
        lines += ["", "## Propose (drafts for human review — NOT auto-written)"]
        lines += [f"- {it['id']}: {it['title']}" for it in plan["propose"]]
    if plan["github"]:
        lines += ["", "## GitHub settings (apply manually, confirm first — never bundled with code)"]
        lines += [f"- {it['id']}: {it['command']}" for it in plan["github"]]
    if plan["manual"]:
        lines += ["", "## Manual"] + [f"- {cid}" for cid in plan["manual"]]
    return "\n".join(lines) + "\n"


def run_fix(args) -> int:
    root = Path(args.project)
    report = load_report(args, root)
    if report is None:
        sys.stderr.write("sheldon fix: no report found; run `readiness report` first.\n")
        return 2
    plan = build_plan(root, report)
    if not getattr(args, "apply", False):
        print(format_plan(plan, dry_run=True))
        return 0
    if worktree_dirty(root) and not getattr(args, "force", False):
        sys.stderr.write("sheldon fix: working tree has uncommitted changes; commit/stash or use --force.\n")
        return 1
    result = apply_plan(root, plan, write=True)
    print(format_plan(plan, result=result, dry_run=False))
    return 0
