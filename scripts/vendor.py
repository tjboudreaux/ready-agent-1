#!/usr/bin/env python3
"""Vendor the engine + templates into each self-contained skill.

`gh skill` / skills.sh install a single skill *directory*, not the whole repo — so each skill
must carry its own engine and templates. The canonical source is engine/readiness + templates;
this syncs byte-identical copies into skills/<name>/scripts/readiness and skills/<name>/templates,
and stamps a manifest.json. CI runs `vendor.py --check` to fail on drift.

All reads/writes are confined through the safe-I/O boundary: canonical sources and
destinations are admitted roots, copies are bounded regular-file-only, and `--check`
treats missing/different/extra/link/special generated entries as drift. Write mode
prevalidates the whole canonical inventory before the first mutation and removes only
stale *generated* files — never skill-owned files (SKILL.md, manifest.json, reference/).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from readiness import safe_io, version  # noqa: E402

SKILLS = ["ra1-report", "ra1-fix", "ra1-interview"]
_SKIP_PARTS = {"__pycache__"}
TEMPLATE_ALLOWLIST = [
    "AGENTS.md",
    "acdc/workflow.md",
    "acdc/guide-skill.md",
    "acdc/verify-skill.md",
    "acdc/solve-skill.md",
    "SECURITY.md",
    "CODEOWNERS",
    "env.example",
    "devcontainer.json",
    "dependabot.yml",
    "precommit-config.yaml",
    "prettierrc.json",
    "eslintrc.json",
    "ruff.toml",
    "pull_request_template.md",
    "gitignore.ra1",
    "ci/readiness.yml",
    "ISSUE_TEMPLATE/bug_report.md",
    "loop/loop-runs-README.md",
    "loop/denylist.md",
    "loop/signals-README.md",
    "loop/pr-artifact-template.md",
]

ENGINE_GLOB = ["**"]


def _engine_inventory(eng_auth) -> list[str]:
    """Canonical engine files (relative paths), excluding caches and bytecode."""
    obs = safe_io.discover_rooted_regular(
        eng_auth, ENGINE_GLOB, max_matches=safe_io.MAX_DISCOVERY_MATCHES)
    if obs.state is not safe_io.RepoDiscoveryState.OK:
        raise safe_io.RepositoryInputError(
            f"engine inventory failed ({obs.state.value}:{obs.reason_code})")
    return [p for p in obs.paths
            if not (set(p.split("/")) & _SKIP_PARTS) and not p.endswith(".pyc")]


def _manifest_content() -> str:
    manifest = dict(version.version_stamp())
    manifest["vendored"] = "engine/readiness + templates"
    return json.dumps(manifest, indent=2) + "\n"


def _read_canonical(auth, rel: str) -> bytes:
    obs = safe_io.read_rooted_regular(auth, rel,
                                      max_bytes=safe_io.MAX_SAFE_COPY_FILE_BYTES)
    if obs.state is not safe_io.RepoReadState.OK:
        raise safe_io.RepositoryInputError(
            f"canonical source unreadable ({obs.state.value}): {rel}")
    return obs.data


def _read_generated(auth, rel: str) -> bytes | None:
    obs = safe_io.read_rooted_regular(auth, rel,
                                      max_bytes=safe_io.MAX_SAFE_COPY_FILE_BYTES)
    if obs.state is safe_io.RepoReadState.MISSING:
        return None
    if obs.state is not safe_io.RepoReadState.OK:
        raise safe_io.RepositoryInputError(
            f"generated entry unsafe ({obs.state.value}): {rel}")
    return obs.data


def _generated_inventory(auth) -> list[str]:
    obs = safe_io.discover_rooted_regular(
        auth, ENGINE_GLOB, max_matches=safe_io.MAX_DISCOVERY_MATCHES)
    if obs.state is not safe_io.RepoDiscoveryState.OK:
        raise safe_io.RepositoryInputError(
            f"generated inventory unsafe ({obs.state.value}:{obs.reason_code})")
    return [p for p in obs.paths
            if not (set(p.split("/")) & _SKIP_PARTS) and not p.endswith(".pyc")]


def _sync_tree(src_auth, dst_auth, canonical: dict, dst_root_rel: str, drift: list,
               write: bool) -> None:
    """Sync one generated tree to the exact canonical inventory."""
    current = _generated_inventory(dst_auth)
    for rel, content in canonical.items():
        existing = _read_generated(dst_auth, rel)
        if existing is None:
            if write:
                safe_io.create_rooted_exclusive(dst_auth, rel, content, mode=0o644)
            else:
                drift.append(f"{dst_root_rel}/{rel}")
        elif existing != content:
            if write:
                safe_io.atomic_replace_rooted(dst_auth, rel, content)
            else:
                drift.append(f"{dst_root_rel}/{rel}")
    for rel in current:
        if rel not in canonical:
            if write:
                safe_io.unlink_rooted(dst_auth, rel)
            else:
                drift.append(f"{dst_root_rel}/{rel}")


def vendor(repo_root, write=True):
    """Sync (write=True) or check (write=False, returns list of drifted dst paths)."""
    repo_root = Path(repo_root)
    drift = []
    eng_auth = safe_io.acquire_root(repo_root / "engine" / "readiness")
    tpl_auth = safe_io.acquire_root(repo_root / "templates")
    try:
        engine_files = _engine_inventory(eng_auth)
        # Prevalidate the complete canonical inventory before the first mutation.
        engine_canonical = {rel: _read_canonical(eng_auth, rel)
                            for rel in engine_files}
        template_canonical = {rel: _read_canonical(tpl_auth, rel)
                              for rel in TEMPLATE_ALLOWLIST}
        manifest = _manifest_content()
        for skill in SKILLS:
            base = repo_root / "skills" / skill
            scripts_rel = "scripts/readiness"
            templates_rel = "templates"
            if write:
                for rel_dir in (scripts_rel, templates_rel):
                    target = base / rel_dir
                    if not target.exists():
                        parent_auth = safe_io.acquire_root(base)
                        try:
                            for component in rel_dir.split("/"):
                                safe_io.ensure_rooted_directory(
                                    parent_auth, component, mode=0o755)
                                nxt = safe_io.open_subroot(parent_auth, component)
                                parent_auth.close()
                                parent_auth = nxt
                            parent_auth.close()
                        except Exception:
                            parent_auth.close()
                            raise
            for rel_dir, canonical in ((scripts_rel, engine_canonical),
                                       (templates_rel, template_canonical)):
                dst_auth = safe_io.acquire_root(base / rel_dir)
                try:
                    _sync_tree(eng_auth if rel_dir == scripts_rel else tpl_auth,
                               dst_auth, canonical, f"skills/{skill}/{rel_dir}", drift,
                               write)
                finally:
                    dst_auth.close()
            manifest_path = base / "manifest.json"
            existing = manifest_path.read_text(encoding="utf-8") \
                if manifest_path.exists() else None
            if existing != manifest:
                if write:
                    manifest_auth = safe_io.acquire_root(base)
                    try:
                        safe_io.atomic_replace_rooted(manifest_auth, "manifest.json",
                                                      manifest.encode("utf-8"))
                    finally:
                        manifest_auth.close()
                else:
                    drift.append(f"skills/{skill}/manifest.json")
    finally:
        eng_auth.close()
        tpl_auth.close()
    return drift


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    check = "--check" in argv
    drift = vendor(ROOT, write=not check)
    if check:
        if drift:
            sys.stderr.write(
                "VENDOR DRIFT (run scripts/vendor.py to sync):\n" + "\n".join(drift) + "\n"
            )
            return 1
        print("vendored skills are in sync")
        return 0
    print(f"vendored engine + templates into: {', '.join(SKILLS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
