#!/usr/bin/env python3
"""agent-readiness CLI — `readiness <command>`.

Run directly:  python3 <skill>/scripts/readiness/cli.py report --project .
The script adds its package parent to sys.path so `import readiness...` works whether it
lives in engine/ or is vendored into a skill's scripts/ directory.

Supported platforms are Linux and macOS hosts with the full POSIX directory-fd/no-follow
capability set. Operational commands (``detect``, ``report``, ``history``, ``fix``,
``gaps``, ``answer``) fail closed with the exact ``safe_io_unsupported`` diagnostic before
any repository access or subprocess; ``--help``, ``version``, ``formats``, and ``banner``
remain available everywhere.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from readiness import report as report_mod  # noqa: E402
from readiness import safe_io, version  # noqa: E402
from readiness.run import AnalyzeDependencies, AnalyzeOptions, analyze  # noqa: E402

_SUN = ["  ▟█████▙ ", " ▐███████▌", "  ╲╲╲┃╱╱╱ "]

_OPERATIONAL = {"detect", "report", "history", "fix", "gaps", "answer"}


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
    return tokens or ["markdown"]


def _parse_exec_timeout(value: str) -> int:
    from readiness.collectors.exec import normalize_exec_timeout
    try:
        return normalize_exec_timeout(int(value, 10))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(
            "exec timeout must be an integer in 1..3600") from None


def _capture_authorities(args) -> tuple:
    """Capture the private host authorities exactly once, before root admission.

    Returns ``(host_proxy, github_auth, error)``; an invalid proxy environment is an exact
    authored diagnostic, never a silent fallback.
    """
    from readiness import process
    try:
        proxy = process.capture_host_proxy_authority(
            bool(getattr(args, "host_proxy", False)), __import__("os").environ)
    except process.HostProxyError:
        return None, None, "ra1: invalid host proxy environment"
    github_auth = None
    if getattr(args, "github", False):
        github_auth = process.capture_github_auth_authority(__import__("os").environ)
    return proxy, github_auth, ""


def _make_deps(args, github_auth=None, proxy=None) -> AnalyzeDependencies:
    return AnalyzeDependencies(
        host_proxy_authority=proxy,
        github_auth_authority=github_auth,
    )


def cmd_report(args) -> int:
    if args.host_proxy and not args.github:
        sys.stderr.write("ra1 report: --host-proxy requires --github\n")
        return 2
    try:
        formats = _parse_report_formats(args.format)
    except ValueError as exc:
        sys.stderr.write(f"ra1 report: {exc}\n")
        return 2
    proxy, github_auth, error = _capture_authorities(args)
    if error:
        sys.stderr.write(error + "\n")
        return 1

    from readiness import history
    identity = history.repo_identity(args.project, require_origin=args.require_origin)
    if args.require_origin and identity is None:
        sys.stderr.write("ra1 report: no 'origin' remote found; --require-origin needs one.\n")
        return 1
    try:
        options = AnalyzeOptions(github=bool(args.github), exec=bool(args.exec_t3),
                                 exec_timeout=args.exec_timeout)
    except ValueError as exc:
        sys.stderr.write(f"ra1 report: {exc}\n")
        return 2
    report = analyze(args.project, options, deps=_make_deps(args, github_auth, proxy))
    from readiness.model import PublicReportValidationError
    try:
        rendered = [report_mod.render(report, fmt, detail=args.detail) for fmt in formats]
    except PublicReportValidationError:
        sys.stderr.write("ra1 report: invalid canonical report\n")
        return 1
    report_dict = report.to_dict()

    out_dir = Path(args.out) if args.out else None
    persistence_error = ""
    if out_dir or args.store_history:
        persistence_error = _persist(args, report_dict, rendered, formats, out_dir)
    # Byte-identical to the primary artifact: every renderer already terminates its output,
    # so `ra1 report --format html > report.html` and `--out DIR` agree exactly.
    sys.stdout.write(rendered[0])
    if persistence_error:
        sys.stderr.write(persistence_error + "\n")
        return 1
    # Requested evidence that arrived incomplete is rendered and persisted, then reported.
    if args.github and not report.assessment_provenance["invocation"]["github"][
            "collection_complete"]:
        sys.stderr.write("ra1 report: requested GitHub evidence was incomplete\n")
        return 1
    if args.exec_t3 and not report.assessment_provenance["invocation"]["execution"][
            "successful"]:
        sys.stderr.write("ra1 report: requested execution evidence was unsuccessful\n")
        return 1
    return _gate(report, args)


def _persist(args, report_dict, rendered, formats, out_dir) -> str:
    """Bounded persistence under the output root: exclusive writer lock, complete-generation
    validation, read-only preflight of every fallible check, staged in-memory payloads, and
    the manifest-last logical commit point. A preflight refusal creates/replaces nothing.
    """
    from readiness import history
    project = Path(args.project)
    target = out_dir if out_dir else project / history.DEFAULT_REPORTS_DIR
    # In-repository persistence requires the authoritative ignore proof.
    if _within(project, target):
        proof_error = _ignore_proof(args.project)
        if proof_error:
            return proof_error
    try:
        out_auth = history.admit_or_create_root(target)
    except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
        return "ra1 report: output root could not be admitted"
    try:
        if not safe_io.lock_directory(out_auth.fd, exclusive=True):
            return "ra1 report: persistence busy; retry after the active writer finishes"
        try:
            state = history.validate_generation(out_auth)
            if state:
                return (f"ra1 report: {state}; remove or repair the generated output "
                        "before persisting")
            # --- preflight (zero mutations): every fallible check happens here.
            plan = None
            if args.store_history:
                try:
                    plan = history.plan_history_write(report_dict, out_auth)
                except history.HistoryLimitError:
                    return ("ra1 report: history limit reached; archive or remove old "
                            "snapshots before persisting")
                except ValueError:
                    return "ra1 report: history index unreadable"
            # --- stage every payload in memory.
            staged = []
            json_bytes = None
            for fmt, text in zip(formats, rendered, strict=True):
                name = f"report.{report_mod.format_extension(fmt)}"
                data = text.encode("utf-8")
                if name == "report.json":
                    json_bytes = data
                staged.append((name, data))
            if json_bytes is None:
                json_bytes = json.dumps(report_dict, indent=2).encode("utf-8")
            stale_formats = [name for name in
                             ("report.json", "report.md", "report.html", "report.xml",
                              "report.sarif", "report.txt")
                             if name not in {n for n, _d in staged}]
            # --- commit in authored order; the manifest is replaced last.
            if plan is not None:
                history.commit_history_write(out_auth, plan)
            for name, data in staged:
                safe_io.atomic_replace_rooted(out_auth, name, data)
            for stale in stale_formats:
                safe_io.unlink_rooted(out_auth, stale)
            manifest_files = list(staged)
            if plan is not None:
                safe_io.atomic_replace_rooted(out_auth, "latest.json", json_bytes)
                manifest_files.extend(history.history_manifest(out_auth))
                manifest_files.append(("latest.json", json_bytes))
            history.write_commit_manifest(out_auth, manifest_files)
        finally:
            safe_io.unlock_directory(out_auth.fd)
    except (OSError, safe_io.RepositoryInputError) as exc:
        return f"ra1 report: persistence failed ({type(exc).__name__})"
    finally:
        out_auth.close()
    return ""


def _within(project: Path, target: Path) -> bool:
    import os
    try:
        physical_project = os.path.realpath(project)
        physical_target = os.path.realpath(target)
    except OSError:
        return False
    return physical_target == physical_project \
        or physical_target.startswith(physical_project.rstrip("/") + "/")


def _ignore_proof(project) -> str:
    """Authoritative root-.gitignore proof for the exact .ra1 policy/generated split."""
    from readiness.collectors.git import GitCollector
    collector = GitCollector(project)
    obs = collector.check_ignore((".ra1/reports/.ra1-ignore-probe", ".ra1/config.json",
                                  ".ra1/waivers.json"))
    collector.close()
    if obs.state != "present":
        return ("ra1 report: .ra1/reports is not safely isolated from versioned .ra1 "
                "policy; ignore only .ra1/reports/")
    matched = {path: (source, pattern)
               for source, _lineno, pattern, path in obs.value}
    report_probe = matched.get(".ra1/reports/.ra1-ignore-probe")
    if not report_probe or report_probe[0] != ".gitignore" \
            or report_probe[1].startswith("!"):
        return ("ra1 report: .ra1/reports is not safely isolated from versioned .ra1 "
                "policy; ignore only .ra1/reports/")
    for policy in (".ra1/config.json", ".ra1/waivers.json"):
        if policy in matched:
            return ("ra1 report: .ra1/reports is not safely isolated from versioned .ra1 "
                    "policy; ignore only .ra1/reports/")
    return ""


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
    if args.host_proxy and not args.github:
        sys.stderr.write("ra1 gaps: --host-proxy requires --github\n")
        return 2
    proxy, github_auth, error = _capture_authorities(args)
    if error:
        sys.stderr.write(error + "\n")
        return 1
    options = AnalyzeOptions(github=bool(args.github))
    report = analyze(args.project, options, deps=_make_deps(args, github_auth, proxy))
    if args.format == "markdown":
        print("\n".join(report_mod._gap_lines(report.gaps)) if report.gaps
              else "No unanswered questions: every input the engine needed was inferable.")
    else:
        print(json.dumps([g.to_dict() for g in report.gaps], indent=2))
    if args.github and not report.assessment_provenance["invocation"]["github"][
            "collection_complete"]:
        sys.stderr.write("ra1 gaps: requested GitHub evidence was incomplete\n")
        return 1
    return 0


def cmd_version(args) -> int:
    print(json.dumps(version.version_stamp(), indent=2))
    return 0


def cmd_formats(args) -> int:
    print("\n".join(report_mod.REPORT_FORMATS))
    return 0


def _history_source(args) -> tuple:
    """Resolve the typed history source from --mode/--root. Returns (source, error)."""
    from readiness import history
    mode = getattr(args, "mode", "current")
    root = getattr(args, "root", None)
    if mode == "current":
        path = root or str(Path(args.project) / history.DEFAULT_REPORTS_DIR)
        source = history.admit_history_source("current", path)
    else:
        if not root:
            return None, "ra1 history: legacy mode requires an explicit --root"
        source = history.admit_history_source("legacy", root)
    if source is None:
        return None, "ra1 history: history root does not exist or is unsafe"
    return source, ""


def cmd_history_list(args) -> int:
    from readiness import history
    source, error = _history_source(args)
    if source is None:
        sys.stderr.write(error + "\n")
        return 2
    try:
        payload, reason = history.list_history(source, args.project)
    finally:
        source.close()
    if payload is None:  # pragma: no cover - a local id always resolves
        sys.stderr.write(f"ra1 history: {reason}\n")
        return 1
    if args.format == "markdown":
        print(report_mod.render_history_list(payload))
    else:
        print(json.dumps(payload, indent=2))
    return 0


def cmd_history_diff(args) -> int:
    from readiness import history
    from_source, error = _history_source_pair(args, "from")
    if error:
        sys.stderr.write(error + "\n")
        return 2
    to_source, error = _history_source_pair(args, "to")
    if error:
        from_source.close()
        sys.stderr.write(error + "\n")
        return 2
    try:
        old = history.load_snapshot(from_source, args.project, args.from_id)
        new = history.load_snapshot(to_source, args.project, args.to_id)
    finally:
        from_source.close()
        to_source.close()
    if old is None or new is None:
        sys.stderr.write("ra1 history: could not resolve --from/--to snapshots.\n")
        return 1
    from readiness.collectors.git import GitCollector
    collector = GitCollector(args.project)
    payload = {"from": args.from_id, "to": args.to_id,
               **history.delta(old, new, git_collector=collector)}
    collector.close()
    if args.format == "markdown":
        print(report_mod.render_history_diff(payload))
    else:
        print(json.dumps(payload, indent=2))
    return 0


def _history_source_pair(args, side: str) -> tuple:
    """Side-specific source for diff: quartet form or shared --mode/--root."""
    from readiness import history
    specific_mode = getattr(args, f"{side}_mode", None)
    specific_root = getattr(args, f"{side}_root", None)
    common_mode = getattr(args, "mode", None)
    common_root = getattr(args, "root", None)
    if specific_mode or specific_root:
        if not (specific_mode and specific_root):
            return None, (f"ra1 history: --{side}-mode and --{side}-root are "
                          "all-or-none, and mutually exclusive with --mode/--root")
        if common_mode or common_root:
            return None, ("ra1 history: side-specific sources are mutually exclusive "
                          "with --mode/--root")
        source = history.admit_history_source(specific_mode, specific_root)
    else:
        mode = common_mode or "current"
        if mode == "legacy" and not common_root:
            return None, "ra1 history: legacy mode requires an explicit --root"
        path = common_root or str(Path(args.project) / history.DEFAULT_REPORTS_DIR)
        source = history.admit_history_source(mode, path)
    if source is None:
        return None, "ra1 history: history root does not exist or is unsafe"
    return source, ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ra1",
        description="Ready Agent 1 — is your codebase ready for the agents? Score readiness, "
                    "cite every check, clear the gates.",
        epilog="Clear the defined gates through Level 4; Level 5 Autonomous is reserved.",
    )
    sub = parser.add_subparsers(dest="command")

    p_report = sub.add_parser("report", help="Readiness scan — score the repo (clear the gates)")
    p_report.add_argument("--project", default=".", help="Path to the repo (default: cwd)")
    p_report.add_argument("--format", default="markdown",
                          help="Comma list: " + ",".join(report_mod.REPORT_FORMATS)
                               + " (default markdown)")
    p_report.add_argument("--detail", default="actionable", choices=("actionable", "all"),
                          help="Markdown/HTML trace expansion (default actionable)")
    p_report.add_argument("--out", default=None, help="Directory to write report artifacts")
    p_report.add_argument("--github", action="store_true",
                          help="Opt in to T2 GitHub.com API checks (offline by default)")
    p_report.add_argument("--host-proxy", action="store_true",
                          help="Forward captured host proxy env to gh (requires --github)")
    p_report.add_argument("--exec", dest="exec_t3", action="store_true",
                          help="Opt in to T3 execution (isolated copy, allowlisted test cmd; "
                               "advisory only)")
    p_report.add_argument("--exec-timeout", type=_parse_exec_timeout, default=120,
                          help="T3 execution timeout in seconds (1..3600; default 120)")
    p_report.add_argument("--min-level", type=int, default=None,
                          help="Exit non-zero if below this level")
    p_report.add_argument("--fail-on", nargs="*", default=None,
                          help="Exit non-zero if these criterion ids fail")
    p_report.add_argument("--require-origin", action="store_true",
                          help="Fail if the repo has no 'origin' remote (Droid prerequisite)")
    p_report.add_argument("--store-history", action="store_true",
                          help="Write timestamped local history keyed by repository identity")
    p_report.set_defaults(func=cmd_report)

    p_detect = sub.add_parser("detect", help="Print project-type detection")
    p_detect.add_argument("--project", default=".")
    p_detect.set_defaults(func=cmd_detect)

    p_fix = sub.add_parser("fix", help="The Loadout — apply safe remediation scaffolds")
    p_fix.add_argument("--project", default=".")
    p_fix.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    p_fix.add_argument("--github", action="store_true",
                       help="Opt in to T2 verification (only with --apply)")
    p_fix.add_argument("--host-proxy", action="store_true",
                       help="Forward captured host proxy env (requires --apply --github)")
    p_fix.add_argument("--report", default=None, help="Path to a report JSON for transparency")
    p_fix.add_argument("--latest", action="store_true",
                       help="Resolve the latest stored current-schema report by identity")
    p_fix.add_argument("--reports-dir", default=None,
                       help="Reports root for --latest (default <project>/.ra1/reports)")
    p_fix.add_argument("--format", default="markdown", help="markdown or json")
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
    h_list.add_argument("--mode", default="current", choices=("current", "legacy"))
    h_list.add_argument("--root", default=None,
                        help="Reports root (current) or explicit legacy history root")
    h_list.add_argument("--format", default="json", help="json or markdown")
    h_list.set_defaults(func=cmd_history_list)
    h_diff = hsub.add_parser("diff", help="Diff two stored reports")
    h_diff.add_argument("--project", default=".")
    h_diff.add_argument("--from", dest="from_id", required=True, help="history id or 'latest'")
    h_diff.add_argument("--to", dest="to_id", default="latest", help="history id or 'latest'")
    h_diff.add_argument("--mode", default=None, choices=("current", "legacy"))
    h_diff.add_argument("--root", default=None)
    h_diff.add_argument("--from-mode", default=None, choices=("current", "legacy"))
    h_diff.add_argument("--from-root", default=None)
    h_diff.add_argument("--to-mode", default=None, choices=("current", "legacy"))
    h_diff.add_argument("--to-root", default=None)
    h_diff.add_argument("--format", default="json", help="json or markdown")
    h_diff.set_defaults(func=cmd_history_diff)

    p_gaps = sub.add_parser("gaps", help="List inputs the scan could not determine")
    p_gaps.add_argument("--project", default=".")
    p_gaps.add_argument("--format", default="markdown", help="markdown or json")
    p_gaps.add_argument("--github", action="store_true",
                        help="Opt in to T2 GitHub.com API checks (offline by default)")
    p_gaps.add_argument("--host-proxy", action="store_true",
                        help="Forward captured host proxy env to gh (requires --github)")
    p_gaps.set_defaults(func=cmd_gaps)

    p_answer = sub.add_parser("answer", help="Record one typed interview answer")
    p_answer.add_argument("--project", default=".")
    p_answer.add_argument("--gap-id", required=True,
                          help="Canonical gap id from `ra1 gaps --format json`")
    p_answer.add_argument("--choice", action="append", default=None,
                          help="Canonical choice id (repeatable only for multi-enum gaps)")
    p_answer.add_argument("--minutes", type=int, default=None,
                          help="CI budget minutes (1..1440; config.ci_budget_minutes only)")
    p_answer.add_argument("--apply", action="store_true",
                          help="Record the answer (default is plan-only)")
    p_answer.add_argument("--format", default="json", help="json (answer contract)")
    p_answer.set_defaults(func=_cmd_answer)

    sub.add_parser("version", help="Print version stamps").set_defaults(func=cmd_version)
    sub.add_parser("formats", help="List supported report formats").set_defaults(func=cmd_formats)
    sub.add_parser("banner", help="Print the Ready Agent 1 banner").set_defaults(func=cmd_banner)
    return parser


def _cmd_fix(args) -> int:
    if args.host_proxy and not (args.apply and args.github):
        sys.stderr.write("ra1 fix: --host-proxy requires --apply --github\n")
        return 2
    if args.github and not args.apply:
        sys.stderr.write("ra1 fix: --github is valid only with --apply\n")
        return 2
    from readiness.fix import recipes
    return recipes.run_fix(args)


def _cmd_answer(args) -> int:
    if args.minutes is not None and args.choice:
        sys.stderr.write("ra1 answer: --minutes and --choice are mutually exclusive\n")
        return 2
    if args.minutes is not None and not 1 <= args.minutes <= 1440:
        sys.stderr.write("ra1 answer: --minutes must be in 1..1440\n")
        return 2
    if args.format != "json":
        sys.stderr.write("ra1 answer: unsupported format (json only)\n")
        return 2
    from readiness import answers
    return answers.run_answer(args)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        print(render_banner(sys.stdout.isatty()))
        return 0
    if getattr(args, "command", None) in _OPERATIONAL and not safe_io.safe_io_supported():
        sys.stderr.write(safe_io.SAFE_IO_UNSUPPORTED_MESSAGE + "\n")
        return 1
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
