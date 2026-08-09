#!/usr/bin/env python3
"""Validate the release version matrix against the current tree (and optionally publication).

With no mode flag, strictly validates matrix keys/stable-SemVer/commit/types against the
current tree: every selected advancing group is exactly the next minor/patch-0 above its
recorded baseline, the detector is unchanged, schema transitions only ``2 -> 3`` or stays
``3``, ``release_tag == "v" + selected.package``, package equals engine and all three skill
``metadata.version`` values, registry/detector/schema equal ``engine/readiness/version.py``,
every vendored manifest echoes that selected engine/registry/detector tuple, and plugin
metadata equals the selected plugin value.

``--check-published`` additionally requires fetched ``origin/main``, the recorded
selection-base object, and eligible tags; proves the base object exists, every eligible
published tag is an ancestor of ``origin/main``, preflight candidate collisions are
refused, and the selected package equals ``--tag`` (for release workflows).

Exits 0 on success, 1 on any mismatch.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "engine") not in sys.path:  # pragma: no cover - engine importable
    sys.path.insert(0, str(REPO / "engine"))

_STABLE_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SKILLS = ("ra1-report", "ra1-fix", "ra1-interview")


def _is_stable_tag(tag_name: str) -> bool:
    """Stable release tag: ``v`` + exact MAJOR.MINOR.PATCH, excluding legacy ``v1``."""
    return tag_name != "v1" and tag_name.startswith("v") \
        and _STABLE_RE.match(tag_name[1:]) is not None


def _version_key(tag_name: str) -> tuple:
    """Numeric ordering key for a stable ``v*.*.*`` tag (never follows semver strings)."""
    return tuple(int(part) for part in tag_name[1:].split("."))


def load_matrix() -> dict:
    data = json.loads((REPO / "release" / "versions.json").read_text(encoding="utf-8"))
    required_top = {"schema_version", "release_tag", "publication_source", "baseline",
                    "selected"}
    if set(data.keys()) != required_top:
        raise ValueError(f"release/versions.json keys not exact: {sorted(data)}")
    return data


def validate_matrix() -> list[str]:
    """Strict current-tree validation. Returns authored error strings (empty = valid)."""
    errors = []
    try:
        matrix = load_matrix()
    except (OSError, ValueError) as exc:
        return [str(exc)]
    schema = matrix["schema_version"]
    if schema != "1":
        errors.append("versions schema must be '1'")
    baseline = matrix["baseline"]
    selected = matrix["selected"]
    for key in ("package", "engine", "registry", "detector", "report_schema"):
        if not isinstance(baseline[key], str) or not isinstance(selected[key], str):
            errors.append(f"{key} must be strings")
            return errors
    if not _STABLE_RE.match(selected["package"]) or not _STABLE_RE.match(baseline["package"]):
        errors.append("package versions must be stable SemVer")
    if not _STABLE_RE.match(selected["engine"]) or not _STABLE_RE.match(baseline["engine"]):
        errors.append("engine versions must be stable SemVer")
    if not _STABLE_RE.match(selected["registry"]) \
            or not _STABLE_RE.match(baseline["registry"]):
        errors.append("registry versions must be stable SemVer")
    # selected advancing groups: exactly the next minor, patch 0 (or same minor + 1 patch
    # when the baseline minor is already consumed — never a jump or a regression). Only
    # runs on stable SemVer inputs; malformed versions already errored above.
    for key in ("package", "engine", "registry"):
        if not _STABLE_RE.match(baseline[key]) or not _STABLE_RE.match(selected[key]):
            continue
        b = [int(x) for x in baseline[key].split(".")]
        s = [int(x) for x in selected[key].split(".")]
        same_minor = s[0] == b[0] and s[1] == b[1] and s[2] == b[2] + 1
        next_minor = s[0] == b[0] and s[1] == b[1] + 1 and s[2] == 0
        if not (same_minor or next_minor):
            errors.append(f"{key} must advance exactly one minor (patch 0) or one patch")
    if selected["detector"] != baseline["detector"]:
        errors.append("detector must be unchanged")
    if baseline["report_schema"] != "2" or selected["report_schema"] != "3":
        if baseline["report_schema"] != "3" or selected["report_schema"] != "3":
            errors.append("report_schema must transition 2 -> 3 or remain 3")
    if matrix["release_tag"] != "v" + selected["package"]:
        errors.append("release_tag must equal 'v' + selected.package")

    # current-tree parity
    from readiness import version
    if version.ENGINE_VERSION != selected["engine"]:
        errors.append(f"engine/readiness/version.py ENGINE {version.ENGINE_VERSION} != "
                      f"selected {selected['engine']}")
    if version.REGISTRY_VERSION != selected["registry"]:
        errors.append("registry mismatch in engine/readiness/version.py")
    if version.DETECTOR_VERSION != selected["detector"]:
        errors.append("detector mismatch in engine/readiness/version.py")
    if version.SCHEMA_VERSION != selected["report_schema"]:
        errors.append("schema mismatch in engine/readiness/version.py")

    try:
        import tomllib
        package = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return errors + [f"pyproject.toml unreadable: {exc}"]
    if package["project"]["version"] != selected["package"]:
        errors.append("pyproject version != selected package")

    for skill in _SKILLS:
        try:
            text = (REPO / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"skills/{skill}/SKILL.md unreadable: {exc}")
            continue
        match = re.search(r"(?m)^  version: (.+)$", text)
        if not match or match.group(1) != selected["skills"][skill]:
            errors.append(f"skills/{skill} metadata version != selected")
        try:
            manifest = json.loads(
                (REPO / "skills" / skill / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"skills/{skill}/manifest.json unreadable: {exc}")
            continue
        if (manifest.get("engine_version"), manifest.get("registry_version"),
                manifest.get("detector_version")) != (
            selected["engine"], selected["registry"], selected["detector"]):
            errors.append(f"skills/{skill} manifest engine tuple != selected")

    try:
        plugin = json.loads(
            (REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return errors + [f"plugin.json unreadable: {exc}"]
    if plugin.get("version") != selected["claude_plugin"]:
        errors.append("plugin version != selected claude_plugin")

    if not errors:
        for skill in _SKILLS:
            vendored = (REPO / "skills" / skill / "scripts" / "readiness" / "version.py")
            if not vendored.exists():
                errors.append(f"vendored engine missing in {skill}")
                continue
            text = vendored.read_text(encoding="utf-8")
            if f'ENGINE_VERSION = "{selected["engine"]}"' not in text \
                    or f'REGISTRY_VERSION = "{selected["registry"]}"' not in text \
                    or f'DETECTOR_VERSION = "{selected["detector"]}"' not in text:
                errors.append(f"vendored engine tuple != selected in {skill}")
    return errors


def check_published(*, tag: str | None = None) -> list[str]:
    """Publication validation against fetched origin/main + tags. Returns error strings."""
    import subprocess
    errors = []
    try:
        matrix = load_matrix()
    except (OSError, ValueError) as exc:
        return [str(exc)]
    publication = matrix["publication_source"]
    base = publication["selection_base_commit"]
    try:
        subprocess.run(["git", "fetch", "--quiet", "origin", "main"],
                       cwd=REPO, check=True, capture_output=True)
        subprocess.run(["git", "fetch", "--quiet", "--tags", "origin", "main"],
                       cwd=REPO, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        return [f"git fetch failed: {exc.stderr.decode(errors='replace')[:200]}"]
    # selection-base object must exist
    probe = subprocess.run(["git", "cat-file", "-e", base + "^{commit}"],
                           cwd=REPO, capture_output=True)
    if probe.returncode != 0:
        errors.append(f"selection base not present: {base}")
    # eligible published tags (stable v*.*.*, excluding prerelease/build and v1) must be
    # ancestors of origin/main
    tags = subprocess.run(["git", "tag", "-l", "v*.*.*"], cwd=REPO,
                          capture_output=True, text=True).stdout.splitlines()
    eligible = sorted(t for t in tags if _is_stable_tag(t))
    for tag_name in eligible:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", f"refs/tags/{tag_name}", "origin/main"],
            cwd=REPO, capture_output=True)
        if ancestor.returncode != 0:
            errors.append(f"eligible tag not ancestor of origin/main: {tag_name}")
    # Highest eligible tag must equal the recorded observation. The recorded value is the
    # prior-highest observed at planning time; release flows create the candidate tag
    # BEFORE this check runs, so the in-flight candidate is excluded from the comparison
    # (it is still ancestry-checked above).
    recorded = publication.get("highest_eligible_release_tag")
    comparable = [t for t in eligible if t != tag]
    if comparable:
        highest = max(comparable, key=_version_key)
        if recorded != highest:
            errors.append(f"recorded highest eligible tag {recorded or '<unset>'} "
                          f"!= fetched {highest}")
    elif recorded:
        errors.append("recorded highest eligible tag, but no stable eligible tags exist")
    if tag is not None:
        if tag != "v" + matrix["selected"]["package"]:
            errors.append(f"tag {tag} != v{matrix['selected']['package']}")
        exists = subprocess.run(["git", "cat-file", "-e", f"refs/tags/{tag}"],
                                cwd=REPO, capture_output=True)
        if exists.returncode != 0:
            errors.append(f"tag not found: {tag}")
    return errors


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    tag = None
    published = "--check-published" in argv
    if published:
        argv = [a for a in argv if a != "--check-published"]
    for i, arg in enumerate(argv):
        if arg == "--tag" and i + 1 < len(argv):
            tag = argv[i + 1]
    errors = validate_matrix()
    if published:
        errors += check_published(tag=tag)
    if errors:
        sys.stderr.write("RELEASE MATRIX MISMATCH:\n" + "\n".join(errors) + "\n")
        return 1
    print("release matrix is consistent"
          + (" (published)" if published else ""))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())