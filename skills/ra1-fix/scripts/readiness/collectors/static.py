"""T0 static evidence: the canonical repository read/discovery/cache boundary.

Every repository-controlled byte enters the engine through this collector, which maps the
safe-I/O primitives' typed observations and caches them. It never opens, globs, resolves,
or descends paths itself. Legacy helper semantics:

- ``read``/``glob``/``exists_any``/``manifests`` keep their shapes for existing checks;
  a repository-derived unsafe/unreadable/oversize/overflow state raises
  :class:`safe_io.RepositoryInputError`, which scoring maps to a blocking ``unknown``
  (never a partial pass). Programmer misuse (bad engine pattern, bad limit) raises the
  same typed error.
- New checks should consume the observation APIs (``read_repo_file``,
  ``glob_repo_files``, ``exists_observation``) and map states explicitly.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import parsers, safe_io

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
    def __init__(self, root, *, _authority: safe_io.RootAuthority | None = None):
        self.root = Path(root)
        self._authority = _authority
        self._cache: dict = {}
        # Stays True only while every T0 observation resolved as validated present|absent.
        self.collection_complete = True

    # ----- authority --------------------------------------------------------------------
    @property
    def authority(self) -> safe_io.RootAuthority:
        if self._authority is None:
            self._authority = safe_io.acquire_root(self.root)
        return self._authority

    def close(self) -> None:
        if self._authority is not None:
            self._authority.close()
            self._authority = None

    # ----- observation APIs --------------------------------------------------------------
    def read_repo_file(self, relpath, *,
                       max_bytes: int = safe_io.MAX_REPO_TEXT_BYTES
                       ) -> safe_io.RepoFileObservation:
        """One bounded repository text read, UTF-8 mapped exactly once."""
        key = ("read", str(relpath), max_bytes)
        if key not in self._cache:
            obs = safe_io.read_rooted_regular(self.authority, str(relpath),
                                              max_bytes=max_bytes)
            self._cache[key] = self._map_text(obs)
        obs = self._cache[key]
        if obs.state not in (safe_io.RepoReadState.OK, safe_io.RepoReadState.MISSING):
            self.collection_complete = False
        return obs

    @staticmethod
    def _map_text(obs: safe_io.RootedBytesObservation) -> safe_io.RepoFileObservation:
        if obs.state is not safe_io.RepoReadState.OK:
            return safe_io.RepoFileObservation(obs.state, reason_code=obs.reason_code)
        try:
            return safe_io.RepoFileObservation(safe_io.RepoReadState.OK,
                                               text=obs.data.decode("utf-8"))
        except UnicodeDecodeError:
            return safe_io.RepoFileObservation(safe_io.RepoReadState.UNREADABLE,
                                               reason_code="decode_error")

    def glob_repo_files(self, patterns, *,
                        max_matches: int = safe_io.MAX_CANDIDATES_PER_CRITERION
                        ) -> safe_io.RepoDiscoveryObservation:
        """Bounded no-follow discovery beneath the root for engine-constant patterns."""
        if isinstance(patterns, str):
            patterns = [patterns]
        patterns = [str(p) for p in patterns]
        key = ("glob", tuple(sorted(patterns)), max_matches)
        if key not in self._cache:
            self._cache[key] = safe_io.discover_rooted_regular(
                self.authority, patterns, max_matches=max_matches)
        obs = self._cache[key]
        if obs.state is not safe_io.RepoDiscoveryState.OK:
            self.collection_complete = False
        return obs

    def exists_observation(self, patterns) -> safe_io.PresenceObservation:
        """Three-state existence: never degrade unsafe state to ``absent``."""
        if isinstance(patterns, str):
            patterns = [patterns]
        patterns = [str(p) for p in patterns]
        key = ("exists", tuple(sorted(patterns)))
        if key not in self._cache:
            self._cache[key] = safe_io.exists_rooted(self.authority, patterns)
        obs = self._cache[key]
        if obs.state is safe_io.PresenceState.INDETERMINATE:
            self.collection_complete = False
        return obs

    # ----- legacy delegates (raise RepositoryInputError on degraded repository state) ----
    @staticmethod
    def _require_ok(obs, what: str):
        if obs.state is safe_io.RepoReadState.OK or obs.state is safe_io.RepoDiscoveryState.OK:
            return obs
        raise safe_io.RepositoryInputError(
            f"{what} unavailable ({obs.state.value}:{obs.reason_code})")

    def glob(self, patterns) -> list:
        """Sorted relative posix paths matching any engine-constant glob pattern."""
        obs = self.glob_repo_files(patterns,
                                   max_matches=safe_io.MAX_DISCOVERY_MATCHES)
        return list(self._require_ok(obs, "discovery").paths)

    def exists_any(self, patterns) -> str | None:
        obs = self.exists_observation(patterns)
        if obs.state is safe_io.PresenceState.PRESENT:
            return obs.path
        if obs.state is safe_io.PresenceState.ABSENT:
            return None
        raise safe_io.RepositoryInputError(
            f"existence indeterminate ({obs.reason_code})")

    def read(self, relpath) -> str | None:
        obs = self.read_repo_file(relpath)
        if obs.state is safe_io.RepoReadState.OK:
            return obs.text
        if obs.state is safe_io.RepoReadState.MISSING:
            return None
        raise safe_io.RepositoryInputError(
            f"read unavailable ({obs.state.value}:{obs.reason_code})")

    # ----- manifests & dependencies -----------------------------------------------------
    def manifests(self) -> dict:
        """{filename: (kind, parsed)} for manifest files present at the root."""
        if "manifests" in self._cache:
            return self._cache["manifests"]
        out = {}
        for fname, kind in _MANIFEST_FILES.items():
            obs = self.read_repo_file(fname)
            if obs.state is safe_io.RepoReadState.MISSING:
                continue
            if obs.state is not safe_io.RepoReadState.OK:
                raise safe_io.RepositoryInputError(
                    f"manifest unreadable ({obs.state.value}:{obs.reason_code})")
            text = obs.text
            if fname.endswith(".json"):
                parsed = parsers.loads_json(text)
            elif fname.endswith(".toml"):
                parsed = parsers.loads_toml(text)
            elif fname == "setup.cfg":
                parsed = parsers.loads_ini(text)
            else:
                parsed = text
            if parsed is None:
                # A malformed manifest can change detection and applicability: the global
                # repository-indeterminate state, never a partial read.
                raise safe_io.RepositoryInputError(f"manifest malformed: {fname}")
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
            for raw in (self.read(rel) or "").splitlines():
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
        out = []
        for name in _LOCKFILES:
            obs = self.exists_observation([name])
            if obs.state is safe_io.PresenceState.PRESENT:
                out.append(name)
            elif obs.state is safe_io.PresenceState.INDETERMINATE:
                raise safe_io.RepositoryInputError(
                    f"lockfile indeterminate ({obs.reason_code})")
        return out

    def gitignore_patterns(self) -> list:
        text = self.read(".gitignore") or ""
        return [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    def within(self, subpath) -> StaticCollector:
        """A collector scoped to an application subdirectory ('.' returns self).

        The subdirectory is opened fd-relative beneath this collector's admitted root —
        repository-derived app paths can never escape the scan root, become absolute, or
        ride a symlink into a new authorized root.
        """
        if subpath in (".", "", None):
            return self
        return StaticCollector(self.root / subpath,
                               _authority=safe_io.open_subroot(self.authority,
                                                               str(subpath)))


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
