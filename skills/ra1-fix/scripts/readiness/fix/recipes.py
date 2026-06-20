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
import re
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
    "loop.loop_runs_dir": ("loop-runs/README.md", "loop/loop-runs-README.md"),
    "loop.denylist": (".omp/rules/denylist.md", "loop/denylist.md"),
    "loop.signal_schema": ("signals/README.md", "loop/signals-README.md"),
    "loop.pr_artifact_template": (".omp/commands/pr-artifact-template.md", "loop/pr-artifact-template.md"),
}

_GH_COMMANDS = {
    "security.branch_protection": "gh api -X PUT repos/{owner}/{repo}/branches/{branch}/protection -f required_pull_request_reviews… (review first)",
    "security.secret_scanning": "Enable in Settings → Code security & analysis (or via gh api).",
    "taskdisc.issue_labeling": "gh label create 'priority:high' --color B60205 ; gh label create 'area:core' …",
    "taskdisc.backlog_health": "Triage open issues and apply labels.",
}

# Deterministic --instructions grammar: phrases map to pillars, nothing else is interpreted.
_PILLAR_ALIASES = {
    "docs": "Documentation", "documentation": "Documentation", "readme": "Documentation",
    "security": "Security & Governance", "governance": "Security & Governance",
    "style": "Style & Validation", "lint": "Style & Validation", "validation": "Style & Validation",
    "build": "Build System", "ci": "Build System",
    "test": "Testing", "tests": "Testing", "testing": "Testing",
    "dev": "Dev Environment", "devenv": "Dev Environment", "environment": "Dev Environment",
    "task": "Task Discovery", "backlog": "Task Discovery", "discovery": "Task Discovery",
}
_INSTR_RE = re.compile(r"(prioriti[sz]e|do not touch|don't touch|skip|avoid)\s+([a-z]+)", re.I)


def _match_pillar(target, known_pillars):
    for word in re.findall(r"[a-z]+", target.lower()):
        pillar = _PILLAR_ALIASES.get(word)
        if pillar and pillar in known_pillars:
            return pillar
    return None


def parse_instructions(text, known_pillars):
    """Map the small documented keyword grammar to pillar focus.

    Returns ``{pillar_prioritize, pillar_exclude, unsupported}``. Free-form text that matches no
    grammar phrase is reported as ``unsupported`` (annotated, never silently filtering the plan).
    """
    prioritize, exclude, recognized = set(), set(), False
    for m in _INSTR_RE.finditer(text or ""):
        verb = m.group(1).lower().replace("'", "")
        pillar = _match_pillar(m.group(2), known_pillars)
        if not pillar:
            continue
        recognized = True
        (prioritize if verb.startswith("prioriti") else exclude).add(pillar)
    unsupported = bool((text or "").strip()) and not recognized
    return {"pillar_prioritize": prioritize, "pillar_exclude": exclude, "unsupported": unsupported}


def _focus_excludes(r, crit, focus, include):
    """True when a failing criterion should be dropped from the plan given the focus filters.

    Precedence: explicit ``--include``/``--exclude`` win over instruction-derived pillar filters.
    Advisory (non-gating) work that is not a safe scaffold requires explicit inclusion.
    """
    cid = r["id"]
    if cid in (focus.get("exclude") or set()):
        return True
    if include and cid not in include:
        return True
    if cid in include:
        return False  # explicit include overrides instruction-derived and advisory rules
    if crit.get("pillar", "") in (focus.get("pillar_exclude") or set()):
        return True
    kind = (crit.get("fix") or {}).get("kind", "")
    if r.get("gating") is False and kind != "scaffold":
        return True  # non-scaffold advisory/prose needs explicit --include
    return False


def _prioritize(plan, pillars, by_id):
    pillars = set(pillars or [])
    if not pillars:
        return
    def rank(cid):
        return 0 if (by_id.get(cid) or {}).get("pillar") in pillars else 1
    plan["auto"].sort(key=lambda it: rank(it["id"]))
    plan["propose"].sort(key=lambda it: rank(it["id"]))
    plan["github"].sort(key=lambda it: rank(it["id"]))
    plan["manual"].sort(key=rank)


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


def build_plan(root, report, registry=None, focus=None):
    root = Path(root)
    registry = registry or score.load_registry()
    by_id = {c["id"]: c for c in registry}
    langs = _languages(report)
    focus = focus or {}
    include = set(focus.get("include") or [])
    plan = {"auto": [], "propose": [], "github": [], "manual": []}
    seen = set()
    for r in report.get("results", []):
        if r.get("status") != "fail":
            continue
        crit = by_id.get(r["id"]) or {}
        if _focus_excludes(r, crit, focus, include):
            continue
        fix = crit.get("fix") or {}
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
    _prioritize(plan, focus.get("pillar_prioritize"), by_id)
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
        path.write_text(existing + sep + "\n# Added by ra1-fix\n" + "\n".join(additions) + "\n", encoding="utf-8")
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


def format_plan(plan, result=None, dry_run=True, notes=None):
    lines = [f"# ra1-fix plan{' (dry run — no files written)' if dry_run else ''}", ""]
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
    if notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in notes]
    lines += ["", "## Verify",
              "- Re-run `ra1 report` with the same origin/history settings to confirm the new level."]
    return "\n".join(lines) + "\n"


def _focus(args, registry):
    parsed = parse_instructions(getattr(args, "instructions", None),
                                {c.get("pillar", "") for c in registry})
    focus = {
        "include": set(getattr(args, "include", None) or []),
        "exclude": set(getattr(args, "exclude", None) or []),
        "pillar_exclude": parsed["pillar_exclude"],
        "pillar_prioritize": parsed["pillar_prioritize"],
    }
    notes = []
    if parsed["unsupported"]:
        notes.append("instructions not recognized as focus grammar; no filtering applied: "
                     f"{getattr(args, 'instructions', None)!r}")
    return focus, notes


def run_fix(args) -> int:
    root = Path(args.project)
    if getattr(args, "latest", False) and not getattr(args, "report", None):
        from .. import history
        report, reason = history.resolve_latest(root, history_dir=getattr(args, "history_dir", None))
        if report is None:
            sys.stderr.write(f"ra1 fix: {reason}\n")
            return 2
    else:
        report = load_report(args, root)
        if report is None:
            sys.stderr.write("ra1 fix: no report found; run `ra1 report` first.\n")
            return 2
    registry = score.load_registry()
    focus, notes = _focus(args, registry)
    plan = build_plan(root, report, registry=registry, focus=focus)
    if not getattr(args, "apply", False):
        print(format_plan(plan, dry_run=True, notes=notes))
        return 0
    if worktree_dirty(root) and not getattr(args, "force", False):
        sys.stderr.write("ra1 fix: working tree has uncommitted changes; commit/stash or use --force.\n")
        return 1
    result = apply_plan(root, plan, write=True)
    print(format_plan(plan, result=result, dry_run=False, notes=notes))
    return 0
