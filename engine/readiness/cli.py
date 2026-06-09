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


def _render(report, fmt: str) -> str:
    try:
        from readiness import report as report_mod
    except ImportError:
        report_mod = None
    if fmt == "json" or report_mod is None:
        return json.dumps(report.to_dict(), indent=2, sort_keys=False)
    return report_mod.render(report, fmt)


def cmd_report(args) -> int:
    report = analyze(args.project, {"no_github": args.no_github})
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
            sys.stderr.write(f"readiness: level {level} < required {args.min_level}\n")
            return 1
    if getattr(args, "fail_on", None) and report.results:
        failing = {r.id for r in report.results if r.status.value == "fail"}
        hit = sorted(failing & set(args.fail_on))
        if hit:
            sys.stderr.write(f"readiness: failing required criteria: {', '.join(hit)}\n")
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
    parser = argparse.ArgumentParser(prog="readiness", description="Agent readiness analyzer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="Analyze a repo and emit a readiness report")
    p_report.add_argument("--project", default=".", help="Path to the repo (default: cwd)")
    p_report.add_argument("--format", default="json", help="Comma list: json,markdown,github,junit,sarif")
    p_report.add_argument("--out", default=None, help="Directory to write report artifacts")
    p_report.add_argument("--no-github", action="store_true", help="Disable T2 GitHub API checks")
    p_report.add_argument("--min-level", type=int, default=None, help="Exit non-zero if below this level")
    p_report.add_argument("--fail-on", nargs="*", default=None, help="Exit non-zero if these criterion ids fail")
    p_report.set_defaults(func=cmd_report)

    p_detect = sub.add_parser("detect", help="Print project-type detection")
    p_detect.add_argument("--project", default=".")
    p_detect.set_defaults(func=cmd_detect)

    p_fix = sub.add_parser("fix", help="Apply safe remediation scaffolds")
    p_fix.add_argument("--project", default=".")
    p_fix.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    p_fix.add_argument("--report", default=None, help="Path to a latest.json report")
    p_fix.set_defaults(func=_cmd_fix)

    sub.add_parser("version", help="Print version stamps").set_defaults(func=cmd_version)
    sub.add_parser("formats", help="List supported report formats").set_defaults(func=cmd_formats)
    return parser


def _cmd_fix(args) -> int:
    try:
        from readiness.fix import recipes
    except ImportError:
        sys.stderr.write("readiness: fix engine not available\n")
        return 2
    return recipes.run_fix(args)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
