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

from readiness import report as report_mod  # noqa: E402
from readiness import version  # noqa: E402
from readiness.run import analyze  # noqa: E402

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


def _parse_report_formats(value: str) -> list[str]:
    """Validate the comma list up front so a typo never reaches the scan or the filesystem.

    Keeps the user's order and repeats, and keeps the pre-alias token so artifact names stay
    backward compatible. Raises ValueError naming the first unsupported token.
    """
    tokens = []
    for raw in value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        report_mod.normalize_format(token)
        tokens.append(token)
    return tokens or ["json"]


def cmd_report(args) -> int:
    try:
        formats = _parse_report_formats(args.format)
    except ValueError as exc:
        sys.stderr.write(f"ra1 report: {exc}\n")
        return 2

    from readiness import history
    identity = history.repo_identity(args.project, require_origin=args.require_origin)
    if args.require_origin and identity is None:
        sys.stderr.write("ra1 report: no 'origin' remote found; --require-origin needs one.\n")
        return 1
    report = analyze(args.project, {"no_github": args.no_github,
                                    "exec": args.exec_t3, "exec_timeout": args.exec_timeout,
                                    "repository": identity})
    rendered = [report_mod.render(report, fmt) for fmt in formats]

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        for fmt, text in zip(formats, rendered, strict=True):
            name = f"report.{report_mod.format_extension(fmt)}"
            (out_dir / name).write_text(text, encoding="utf-8")
    if args.store_history:
        history.store_history(report.to_dict(), args.project,
                              out=args.out, history_dir=args.history_dir)
    elif out_dir:
        (out_dir / "latest.json").write_text(report_mod.render(report, "json"), encoding="utf-8")
    # Byte-identical to the primary artifact: every renderer already terminates its output,
    # so `ra1 report --format html > report.html` and `--out DIR` agree exactly.
    sys.stdout.write(rendered[0])

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


def cmd_gaps(args) -> int:
    """List what the scan could not determine for itself.

    Exits 0 even when gaps exist: an unanswered question is a worklist item, not a failure,
    and wiring it to a non-zero exit would turn missing inputs into a broken build.
    """
    report = analyze(args.project, {"no_github": args.no_github})
    if args.format == "markdown":
        print("\n".join(report_mod._gap_lines(report.gaps)) if report.gaps
              else "No unanswered questions: every input the engine needed was inferable.")
    else:
        print(json.dumps([g.to_dict() for g in report.gaps], indent=2))
    return 0


def cmd_version(args) -> int:
    print(json.dumps(version.version_stamp(), indent=2))
    return 0


def cmd_formats(args) -> int:
    print("\n".join(report_mod.REPORT_FORMATS))
    return 0


def cmd_history_list(args) -> int:
    from readiness import history
    payload, reason = history.list_history(args.project, history_dir=args.history_dir)
    # Unreachable: `history list` has no --require-origin and a local id always resolves.
    # The pragma MUST stay on the `if` line -- coverage only honours it there, and moving it
    # to its own line silently re-enables the branch and breaks the 100%-on-touched gate.
    if payload is None:  # pragma: no cover
        sys.stderr.write(f"ra1 history: {reason}\n")
        return 1
    if args.format == "markdown":
        print(report_mod.render_history_list(payload))
    else:
        print(json.dumps(payload, indent=2))
    return 0


def cmd_history_diff(args) -> int:
    from readiness import history
    old = history.load_snapshot(args.project, args.from_id, history_dir=args.history_dir)
    new = history.load_snapshot(args.project, args.to_id, history_dir=args.history_dir)
    if old is None or new is None:
        sys.stderr.write("ra1 history: could not resolve --from/--to snapshots.\n")
        return 1
    payload = {"from": args.from_id, "to": args.to_id, **history.delta(old, new)}
    if args.format == "markdown":
        print(report_mod.render_history_diff(payload))
    else:
        print(json.dumps(payload, indent=2))
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
    p_report.add_argument("--format", default="json",
                          help="Comma list: " + ",".join(report_mod.REPORT_FORMATS))
    p_report.add_argument("--out", default=None, help="Directory to write report artifacts")
    p_report.add_argument("--no-github", action="store_true", help="Disable T2 GitHub API checks")
    p_report.add_argument("--exec", dest="exec_t3", action="store_true",
                          help="Opt in to T3 execution (sandboxed copy, allowlisted test cmd; "
                               "advisory only)")
    p_report.add_argument("--exec-timeout", type=int, default=120,
                          help="T3 execution timeout in seconds (default 120)")
    p_report.add_argument("--min-level", type=int, default=None,
                          help="Exit non-zero if below this level")
    p_report.add_argument("--fail-on", nargs="*", default=None,
                          help="Exit non-zero if these criterion ids fail")
    p_report.add_argument("--require-origin", action="store_true",
                          help="Fail if the repo has no 'origin' remote (Droid prerequisite)")
    p_report.add_argument("--store-history", action="store_true",
                          help="Write timestamped local history keyed by repository identity")
    p_report.add_argument("--history-dir", default=None,
                          help="History root (default <out>/history, else "
                               "<project>/.agents/readiness/history)")
    p_report.set_defaults(func=cmd_report)

    p_detect = sub.add_parser("detect", help="Print project-type detection")
    p_detect.add_argument("--project", default=".")
    p_detect.set_defaults(func=cmd_detect)

    p_fix = sub.add_parser("fix", help="The Loadout — apply safe remediation scaffolds")
    p_fix.add_argument("--project", default=".")
    p_fix.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    p_fix.add_argument("--force", action="store_true", help="Apply even if the worktree is dirty")
    p_fix.add_argument("--report", default=None, help="Path to a latest.json report")
    p_fix.add_argument("--latest", action="store_true",
                       help="Resolve the latest stored report by repository identity")
    p_fix.add_argument("--history-dir", default=None,
                       help="History root for --latest "
                            "(default <project>/.agents/readiness/history)")
    p_fix.add_argument("--include", nargs="*", default=None,
                       help="Only remediate these criterion ids (authoritative filter)")
    p_fix.add_argument("--exclude", nargs="*", default=None,
                       help="Never remediate these criterion ids (authoritative filter)")
    p_fix.add_argument("--instructions", default=None,
                       help="Focus grammar, e.g. 'prioritize security' or 'do not touch CI'")
    p_fix.set_defaults(func=_cmd_fix)

    p_hist = sub.add_parser("history", help="Local readiness history (list/diff)")
    hsub = p_hist.add_subparsers(dest="history_op")
    h_list = hsub.add_parser("list", help="List stored reports for this repo")
    h_list.add_argument("--project", default=".")
    h_list.add_argument("--history-dir", default=None)
    h_list.add_argument("--format", default="json", help="json or markdown")
    h_list.set_defaults(func=cmd_history_list)
    h_diff = hsub.add_parser("diff", help="Diff two stored reports")
    h_diff.add_argument("--project", default=".")
    h_diff.add_argument("--from", dest="from_id", required=True, help="history id or 'latest'")
    h_diff.add_argument("--to", dest="to_id", default="latest", help="history id or 'latest'")
    h_diff.add_argument("--history-dir", default=None)
    h_diff.add_argument("--format", default="json", help="json or markdown")
    h_diff.set_defaults(func=cmd_history_diff)

    p_gaps = sub.add_parser("gaps", help="List inputs the scan could not determine")
    p_gaps.add_argument("--project", default=".")
    p_gaps.add_argument("--format", default="markdown", help="markdown or json")
    p_gaps.add_argument("--no-github", action="store_true",
                        help="Skip GitHub (T2) collection")
    p_gaps.set_defaults(func=cmd_gaps)

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


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
