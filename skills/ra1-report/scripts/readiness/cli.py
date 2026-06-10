#!/usr/bin/env python3
"""agent-readiness CLI — `readiness <command>`.

Run directly:  python3 <skill>/scripts/readiness/cli.py report --project .
The script adds its package parent to sys.path so `import readiness...` works whether it
lives in engine/ or is vendored into a skill's scripts/ directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from readiness import version                       # noqa: E402
from readiness.run import analyze                   # noqa: E402

_SUN = ["  ▟█████▙ ", " ▐███████▌", "  ╲╲╲┃╱╱╱ "]


def render_banner(color: bool = True) -> str:
    mag = "\033[1;35m" if color else ""   # neon magenta
    cyan = "\033[36m" if color else ""    # neon cyan
    dim = "\033[2m" if color else ""
    off = "\033[0m" if color else ""
    rows = [
        f"{mag}R E A D Y   A G E N T   1{off}",
        f"{cyan}is your codebase ready for the agents?{off}",
        f"{dim}▮ insert coin · clear the gates · level up{off}",
    ]
    out = [f"  {_SUN[i]}   {rows[i]}" for i in range(3)]
    out += ["", f"{dim}  deterministic · cited · clear-to-merge{off}"]
    return "\n".join(out)


def cmd_banner(args) -> int:
    print(render_banner(sys.stdout.isatty()))
    return 0


def _render(report, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(report.to_dict(), indent=2, sort_keys=False)
    from readiness import report as report_mod
    return report_mod.render(report, fmt)


def cmd_report(args) -> int:
    report = analyze(args.project, {"no_github": args.no_github,
                                    "exec": args.exec_t3, "exec_timeout": args.exec_timeout})
    formats = [f.strip() for f in args.format.split(",") if f.strip()] or ["json"]
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    _ext = {"json": "json", "markdown": "md", "md": "md", "sarif": "sarif", "junit": "xml",
            "github": "txt", "checks": "txt"}
    primary = None
    for fmt in formats:
        text = _render(report, fmt)
        if primary is None:
            primary = text
        if out_dir:
            name = "report." + _ext.get(fmt, fmt)
            (out_dir / name).write_text(text, encoding="utf-8")
    if out_dir:
        (out_dir / "latest.json").write_text(_render(report, "json"), encoding="utf-8")
    print(primary if primary is not None else "")

    # Exit gating (M3 wires --min-level / --fail-on against the deterministic level).
    return _gate(report, args)


def _gate(report, args) -> int:
    if getattr(args, "min_level", None):
        level = report.score.level if report.score else 0
        if level < args.min_level:
            sys.stderr.write(f"ra1: level {level} < required {args.min_level}\n")
            return 1
    if getattr(args, "fail_on", None) and report.results:
        failing = {r.id for r in report.results if r.status.value == "fail"}
        hit = sorted(failing & set(args.fail_on))
        if hit:
            sys.stderr.write(f"ra1: failing required criteria: {', '.join(hit)}\n")
            return 1
    return 0


def cmd_detect(args) -> int:
    from readiness.detect import detect
    print(json.dumps(detect(args.project).to_dict(), indent=2))
    return 0


def cmd_version(args) -> int:
    print(json.dumps(version.version_stamp(), indent=2))
    return 0


def cmd_formats(args) -> int:
    print("\n".join(["json", "markdown", "github", "junit", "sarif"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ra1",
        description="Ready Agent 1 — is your codebase ready for the agents? Score readiness, "
                    "cite every check, clear the gates.",
        epilog="READY? Clear all five gates. Start with `ra1 report`.",
    )
    sub = parser.add_subparsers(dest="command")

    p_report = sub.add_parser("report", help="Readiness scan — score the repo (clear the gates)")
    p_report.add_argument("--project", default=".", help="Path to the repo (default: cwd)")
    p_report.add_argument("--format", default="json", help="Comma list: json,markdown,github,junit,sarif")
    p_report.add_argument("--out", default=None, help="Directory to write report artifacts")
    p_report.add_argument("--no-github", action="store_true", help="Disable T2 GitHub API checks")
    p_report.add_argument("--exec", dest="exec_t3", action="store_true",
                          help="Opt in to T3 execution (sandboxed copy, allowlisted test cmd; advisory only)")
    p_report.add_argument("--exec-timeout", type=int, default=120,
                          help="T3 execution timeout in seconds (default 120)")
    p_report.add_argument("--min-level", type=int, default=None, help="Exit non-zero if below this level")
    p_report.add_argument("--fail-on", nargs="*", default=None, help="Exit non-zero if these criterion ids fail")
    p_report.set_defaults(func=cmd_report)

    p_detect = sub.add_parser("detect", help="Print project-type detection")
    p_detect.add_argument("--project", default=".")
    p_detect.set_defaults(func=cmd_detect)

    p_fix = sub.add_parser("fix", help="The Loadout — apply safe remediation scaffolds")
    p_fix.add_argument("--project", default=".")
    p_fix.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    p_fix.add_argument("--force", action="store_true", help="Apply even if the worktree is dirty")
    p_fix.add_argument("--report", default=None, help="Path to a latest.json report")
    p_fix.set_defaults(func=_cmd_fix)

    sub.add_parser("version", help="Print version stamps").set_defaults(func=cmd_version)
    sub.add_parser("formats", help="List supported report formats").set_defaults(func=cmd_formats)
    sub.add_parser("banner", help="Print the Ready Agent 1 banner").set_defaults(func=cmd_banner)
    return parser


def _cmd_fix(args) -> int:
    from readiness.fix import recipes
    return recipes.run_fix(args)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        print(render_banner(sys.stdout.isatty()))
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
