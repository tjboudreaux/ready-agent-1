"""T0 static evidence: file existence, globs, and semantic config/manifest parsing."""
from __future__ import annotations

import re
from pathlib import Path

from .. import parsers

# Directories never worth walking.
_IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build",
    "__pycache__", ".mypy_cache", ".tox",
}

_MANIFEST_FILES = {
    "package.json": "npm",
    "pyproject.toml": "python",
    # The dominant Python dependency declaration outside packaging metadata. Without it a
    # Flask or Django app whose only manifest is requirements.txt reports no manifest, no
    # language, and no dependencies, so it classifies as `unknown` and every type-dependent
    # criterion goes unknown with it.
    "requirements.txt": "python",
    "setup.cfg": "python",
    "setup.py": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "Gemfile": "ruby",
    "composer.json": "php",
    "Package.swift": "swift",
}

# Requirement-file names whose dependencies count as declared. Dev requirements matter: a
# repo can declare pytest or ruff only there, and the checks look those up by name.
_REQUIREMENT_GLOBS = ["requirements.txt", "requirements-*.txt", "requirements_*.txt",
                      "requirements/*.txt"]

_LOCKFILES = [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "uv.lock", "Pipfile.lock",
    "go.sum", "Cargo.lock", "composer.lock", "Gemfile.lock",
]


class StaticCollector:
    def __init__(self, root):
        self.root = Path(root)
        self._cache: dict = {}

    # ----- generic file helpers ---------------------------------------------------------
    def glob(self, patterns) -> list:
        """Return sorted relative posix paths matching any glob pattern (supports ``**``)."""
        if isinstance(patterns, str):
            patterns = [patterns]
        found = set()
        for pat in patterns:
            for p in self.root.glob(pat):
                rel = p.relative_to(self.root)
                if any(part in _IGNORE_DIRS for part in rel.parts):
                    continue
                found.add(rel.as_posix())
        return sorted(found)

    def exists_any(self, patterns) -> str | None:
        hits = self.glob(patterns)
        return hits[0] if hits else None

    def read(self, relpath) -> str | None:
        return parsers.read_text(self.root / relpath)

    # ----- manifests & dependencies -----------------------------------------------------
    def manifests(self) -> dict:
        """{filename: (kind, parsed)} for manifest files present at the root."""
        if "manifests" in self._cache:
            return self._cache["manifests"]
        out = {}
        for fname, kind in _MANIFEST_FILES.items():
            path = self.root / fname
            if not path.exists():
                continue
            if fname.endswith(".json"):
                parsed = parsers.load_json(path)
            elif fname.endswith(".toml"):
                parsed = parsers.load_toml(path)
            elif fname == "setup.cfg":
                parsed = parsers.load_ini(path)
            else:
                parsed = parsers.read_text(path)
            out[fname] = (kind, parsed)
        self._cache["manifests"] = out
        return out

    def languages(self) -> list:
        langs = {kind for kind, _ in self.manifests().values()}
        return sorted(langs)

    def declared_deps(self) -> set:
        """Best-effort union of declared dependency names, lowercased."""
        if "deps" in self._cache:
            return self._cache["deps"]
        deps: set = set()
        for fname, (_kind, parsed) in self.manifests().items():
            if fname == "package.json" and isinstance(parsed, dict):
                for key in ("dependencies", "devDependencies", "peerDependencies",
                            "optionalDependencies"):
                    section = parsed.get(key)
                    if isinstance(section, dict):
                        deps.update(k.lower() for k in section)
            elif fname == "pyproject.toml" and isinstance(parsed, dict):
                proj = parsed.get("project", {})
                for d in proj.get("dependencies", []) or []:
                    deps.add(_pkg_name(d))
                for group in (proj.get("optional-dependencies", {}) or {}).values():
                    for d in group:
                        deps.add(_pkg_name(d))
                poetry = parsed.get("tool", {}).get("poetry", {})
                for section in ("dependencies", "dev-dependencies"):
                    for k in (poetry.get(section, {}) or {}):
                        deps.add(k.lower())
                # ruff/black/mypy etc. configured under [tool.*] count as "present tool config"
                for tool in (parsed.get("tool", {}) or {}):
                    deps.add(("tool:" + tool).lower())
            elif fname == "Cargo.toml" and isinstance(parsed, dict):
                for section in ("dependencies", "dev-dependencies"):
                    for k in (parsed.get(section, {}) or {}):
                        deps.add(k.lower())
            elif fname == "go.mod" and isinstance(parsed, str):
                for m in re.finditer(r"^\s*([\w./\-]+)\s+v[\w.\-+]+", parsed, re.MULTILINE):
                    path = m.group(1).lower()
                    deps.add(path)
                    segs = path.split("/")
                    if len(segs) >= 2:
                        deps.add("/".join(segs[-2:]))
            elif fname == "Gemfile" and isinstance(parsed, str):
                for m in re.finditer(r"""^\s*gem\s+["']([^"']+)["']""", parsed, re.MULTILINE):
                    deps.add(m.group(1).lower())
        deps |= self._requirement_deps()
        self._cache["deps"] = deps
        return deps

    def _requirement_deps(self) -> set:
        """Dependency names from every requirements file at this root.

        Reads the files directly rather than through `manifests()` so dev requirements count
        too: a repo may declare pytest or ruff only in requirements-dev.txt, and the checks
        look those up by name.
        """
        out: set = set()
        for rel in self.glob(_REQUIREMENT_GLOBS):
            for raw in (parsers.read_text(self.root / rel) or "").splitlines():
                name = _requirement_name(raw)
                if name:
                    out.add(name)
        return out

    def has_dep(self, names) -> str | None:
        if isinstance(names, str):
            names = [names]
        declared = self.declared_deps()
        for n in names:
            if n.lower() in declared:
                return n
        return None

    def has_tool_config(self, name) -> bool:
        """True if a [tool.<name>] table exists in pyproject (e.g. ruff/black/mypy)."""
        return ("tool:" + name).lower() in self.declared_deps()

    def lockfiles(self) -> list:
        return [f for f in _LOCKFILES if (self.root / f).exists()]

    def gitignore_patterns(self) -> list:
        text = self.read(".gitignore") or ""
        return [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    def within(self, subpath) -> StaticCollector:
        """A collector scoped to an application subdirectory ('.' returns self)."""
        if subpath in (".", "", None):
            return self
        return StaticCollector(self.root / subpath)


def _pkg_name(requirement: str) -> str:
    """'requests>=2,<3 ; extra==x' -> 'requests' (lowercased)."""
    token = requirement.strip()
    for sep in (" ", ";", "[", "=", "<", ">", "!", "~", "("):
        idx = token.find(sep)
        if idx > 0:
            token = token[:idx]
    return token.strip().lower()



def _requirement_name(line: str) -> str:
    """A distribution name from one requirements.txt line, or "" when the line declares none.

    Handles what real files carry: comments, inline comments, pins and ranges, extras, env
    markers, and editable/URL/flag lines. `-r base.txt`, `-e .`, `--index-url ...` and bare
    URLs name no distribution, so they yield "" rather than a junk dependency.
    """
    token = line.split("#", 1)[0].strip()
    if not token or token.startswith("-"):
        return ""
    # A direct reference (`name @ https://…`) keeps its name; a bare URL has none.
    if "@" in token:
        head = token.split("@", 1)[0].strip()
        return _pkg_name(head) if head else ""
    if "://" in token:
        return ""
    return _pkg_name(token)