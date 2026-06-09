#!/usr/bin/env python3
"""Vendor the engine + templates into each self-contained skill.

`gh skill` / skills.sh install a single skill *directory*, not the whole repo — so each skill
must carry its own engine and templates. The canonical source is engine/readiness + templates;
this syncs byte-identical copies into skills/<name>/scripts/readiness and skills/<name>/templates,
and stamps a manifest.json. CI runs `vendor.py --check` to fail on drift.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from readiness import version  # noqa: E402

SKILLS = ["readiness-report", "readiness-fix"]
_SKIP_PARTS = {"__pycache__"}


def _files(src: Path):
    for p in sorted(src.rglob("*")):
        if p.is_file() and not (set(p.parts) & _SKIP_PARTS) and p.suffix != ".pyc":
            yield p


def _plan(repo_root: Path):
    eng = repo_root / "engine" / "readiness"
    tpl = repo_root / "templates"
    pairs = []
    for skill in SKILLS:
        base = repo_root / "skills" / skill
        for f in _files(eng):
            pairs.append((f, base / "scripts" / "readiness" / f.relative_to(eng)))
        for f in _files(tpl):
            pairs.append((f, base / "templates" / f.relative_to(tpl)))
    return pairs


def vendor(repo_root, write=True):
    """Sync (write=True) or check (write=False, returns list of drifted dst paths)."""
    repo_root = Path(repo_root)
    drift = []
    for src, dst in _plan(repo_root):
        content = src.read_bytes()
        if write:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(content)
        elif not dst.exists() or dst.read_bytes() != content:
            drift.append(str(dst.relative_to(repo_root)))
    if write:
        for skill in SKILLS:
            _write_manifest(repo_root / "skills" / skill)
    return drift


def _write_manifest(skill_dir: Path):
    manifest = dict(version.version_stamp())
    manifest["vendored"] = "engine/readiness + templates"
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    check = "--check" in argv
    drift = vendor(ROOT, write=not check)
    if check:
        if drift:
            sys.stderr.write("VENDOR DRIFT (run scripts/vendor.py to sync):\n" + "\n".join(drift) + "\n")
            return 1
        print("vendored skills are in sync")
        return 0
    print(f"vendored engine + templates into: {', '.join(SKILLS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
