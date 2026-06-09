"""Shared test helpers: build throwaway fixture repos and fake collector runners."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


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


def fake_runner(responses: dict):
    """Return a runner(args)->str|None backed by a {tuple(args): stdout} dict."""
    def run(args):
        return responses.get(tuple(args))
    return run
