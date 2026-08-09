"""Shared test helpers: build throwaway fixture repos and fake collector runners.

The 0.11.0 collector contract: every injected runner returns
:class:`readiness.process.BoundedProcessResult` — legacy ``str``/``None``/``dict``
injections raise ``TypeError`` at the private test boundary. ``bpr`` builds results,
``fake_runner`` maps argv tuples to results, and ``gh_runner`` maps API endpoints to
bounded ``gh api --include`` envelopes.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from readiness.process import BoundedProcessResult, ProcessState
from readiness.run import AnalyzeDependencies, AnalyzeOptions


def make_repo(files: dict) -> Path:
    """Create a temp directory containing ``files`` (relpath -> text). Caller cleans up."""
    root = Path(tempfile.mkdtemp(prefix="ar-test-"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def rmtree(path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def bpr(stdout="", returncode=0) -> BoundedProcessResult:
    """A bounded process result: exit 0 -> ok, anything else -> nonzero."""
    if returncode == 0:
        return BoundedProcessResult(ProcessState.OK, returncode=0, stdout=stdout)
    return BoundedProcessResult(ProcessState.NONZERO, returncode=returncode,
                                stdout=stdout)


def fake_runner(responses: dict):
    """Return a runner(args)->BoundedProcessResult backed by {tuple(args): stdout}.

    A value may be a stdout string (exit 0) or a ``(stdout, returncode)`` pair. Unmapped
    argv exits 128 (the legacy ``None`` behavior: command not faked).
    """
    def run(args):
        value = responses.get(tuple(args))
        if value is None:
            return bpr("", 128)
        if isinstance(value, tuple):
            return bpr(value[0], value[1])
        return bpr(value, 0)
    return run


def envelope(body: str, status: int = 200) -> str:
    """One strict HTTP envelope as ``gh api --include`` prints it."""
    reason = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
              500: "Internal Server Error"}.get(status, "Status")
    return f"HTTP/2 {status} {reason}\r\ncontent-type: application/json\r\n\r\n{body}"


def gh_runner(responses: dict):
    """Map ``{endpoint: json-text-or-(json-text, status)}`` to the fixed T2 argv shape.

    Unmapped endpoints answer 404 (an exact 404 matters only for the branch-protection
    endpoint; every other 404 is unreadable by contract).
    """
    def run(args):
        args = tuple(args)
        if args[:4] == ("api", "--hostname", "github.com", "--include"):
            endpoint = args[4]
            if endpoint in responses:
                value = responses[endpoint]
                if isinstance(value, tuple):
                    return bpr(envelope(value[0], value[1]), 0)
                return bpr(envelope(value, 200), 0)
        return bpr(envelope("{}", 404), 0)
    return run


def options(**kw) -> AnalyzeOptions:
    return AnalyzeOptions(**kw)


def deps(**kw) -> AnalyzeDependencies:
    return AnalyzeDependencies(**kw)


def github_deps(slug=("github.com", "o", "r"), gh_responses=None, **kw):
    """Dependencies for a GitHub-enabled scan: sanitized origin + endpoint fakes."""
    return AnalyzeDependencies(github_origin=slug,
                               github_runner=gh_runner(gh_responses or {}), **kw)
