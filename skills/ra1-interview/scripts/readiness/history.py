"""Repository identity, local report history, and deterministic deltas.

- resolves a *repository identity* — an `origin`-derived identity when a remote exists, or a
  local-path identity otherwise (never both, never a raw token or absolute path on disk);
- stores immutable, timestamped report snapshots plus a canonical `latest.json` and an ordered
  per-identity index under ``.ra1/reports`` (legacy ``.agents/readiness`` storage is reachable
  only through explicit typed legacy sources);
- computes a deterministic delta only when the full version/evidence-scope contract matches.

Authority contract: every history root is an explicitly admitted, retained
:class:`safe_io.RootAuthority` carried by an immutable :class:`HistorySource`. No reader
guesses a mode from contents, opens a parent of an authorized root, or touches the
filesystem by repository-derived path. The only creation flow is the report writer's
authorized nearest-existing-parent directory creation.

Pure standard library. Secrets policy: the raw origin URL and raw absolute project path are
used only transiently to derive a redaction/hash and are never returned or serialized.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import model, parsers, safe_io, version

# The stale-state contract: a delta is only meaningful when these match.
_CONTRACT_KEYS = ("schema_version", "engine_version", "registry_version",
                  "detector_version")

DEFAULT_REPORTS_DIR = ".ra1/reports"
MAX_HISTORY_INDEX_BYTES = 8_388_608
MAX_HISTORY_INDEX_ENTRIES = 10_000
MAX_OUTPUT_ROOT_CREATE_DEPTH = 8
_INDEX_FILE_RE = re.compile(r"[0-9A-Za-z-]+(?:-[0-9]+)?\.json\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT_ID_RE = re.compile(r"[0-9A-Za-z-]{1,128}\Z")


class HistoryLimitError(RuntimeError):
    """The 10,000-entry history cap; the caller emits the exact persistence diagnostic."""


# --------------------------------------------------------------------------- identity
def _hash(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def repo_identity(project, *, git_collector=None, require_origin=False, git_runner=None):
    """Resolve a serializable repository identity, or ``None`` when origin is required but absent.

    Origin identity wins when an `origin` remote exists; a present *malformed* origin fails
    resolution and never falls back to local identity. The raw origin URL and raw absolute
    path never escape; ``identity_hash`` derives from the complete normalized identity
    before display minimization.
    """
    collector = git_collector
    if collector is None:
        from .collectors.git import GitCollector
        collector = GitCollector(project or ".", runner=git_runner)
    if collector.origin_malformed():
        return None
    origin = collector.origin_identity()
    if origin:
        host, owner, name = origin
        return {
            "identity_kind": "origin",
            "host": host,
            "owner": owner,
            "name": name,
            "identity_hash": _hash("origin", host, owner, name),
        }
    if require_origin:
        return None
    physical = os.path.realpath(os.fspath(project or "."))
    return {
        "identity_kind": "local_path",
        "name": Path(physical).name,
        "identity_hash": _hash("local_path", physical),
    }


# --------------------------------------------------------------------------- typed sources
@dataclass
class HistorySource:
    """An immutable typed history root: mode plus its explicitly admitted authority.

    No function guesses mode from contents; no reader opens a parent outside the
    authorized root. ``close()`` releases the retained handle.
    """

    mode: str                              # "current" | "legacy"
    root_authority: safe_io.RootAuthority

    def __post_init__(self):
        if self.mode not in ("current", "legacy"):
            raise ValueError(f"invalid history source mode: {self.mode!r}")
        if not isinstance(self.root_authority, safe_io.RootAuthority):
            raise ValueError("history source requires an admitted root authority")

    def close(self) -> None:
        self.root_authority.close()


def admit_history_source(mode: str, path) -> HistorySource | None:
    """Admit an explicitly supplied history root. ``None`` when the root does not exist."""
    try:
        auth = safe_io.acquire_root(path)
    except (OSError, safe_io.RepositoryInputError, safe_io.SafeIoUnsupportedError):
        return None
    return HistorySource(mode, auth)


def admit_or_create_root(path) -> safe_io.RootAuthority:
    """Admit the output root, creating only missing trailing components beneath the nearest
    retained existing ancestor (the report writer's authorized creation flow)."""
    try:
        return safe_io.acquire_root(path)
    except FileNotFoundError:
        pass
    physical = os.path.abspath(os.fspath(path))
    components = [c for c in physical.split("/") if c]
    auth = None
    missing: list = []
    for i in range(len(components), 0, -1):
        candidate = "/" + "/".join(components[:i])
        try:
            auth = safe_io.acquire_root(candidate)
            missing = components[i:]
            break
        except FileNotFoundError:
            continue
    if auth is None:
        raise safe_io.RepositoryInputError("no existing ancestor for output root")
    if len(missing) > MAX_OUTPUT_ROOT_CREATE_DEPTH:
        auth.close()
        raise safe_io.RepositoryInputError("output root creation depth cap")
    try:
        for name in missing:
            safe_io.ensure_rooted_directory(auth, name, mode=0o755)
            nxt = safe_io.open_subroot(auth, name)
            auth.close()
            auth = nxt
        return auth
    except Exception:
        auth.close()
        raise


def admit_or_create_current_source(path) -> HistorySource:
    """The report writer's current source: admitted output root (created when missing)."""
    return HistorySource("current", admit_or_create_root(path))


def current_source_path(project, out=None) -> Path:
    """The path of the current-mode root: selected ``--out`` or ``<project>/.ra1/reports``."""
    return Path(out) if out else Path(project) / DEFAULT_REPORTS_DIR


def _open_bucket(source: HistorySource, identity_hash: str):
    """Open the per-identity bucket fd-relative; ``None`` when absent."""
    rel = f"history/{identity_hash}" if source.mode == "current" else identity_hash
    try:
        return safe_io.open_subroot(source.root_authority, rel)
    except (OSError, safe_io.RepositoryInputError):
        return None


def _safe_ts(ts):
    return re.sub(r"[^0-9A-Za-z]", "-", ts) or "unknown"


def now_iso():
    return datetime.now(UTC).isoformat()


def _index_entry(report_dict, filename, payload: bytes):
    score = report_dict.get("score") or {}
    return {
        "timestamp": report_dict.get("generated_at", ""),
        "file": filename,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "schema_version": report_dict.get("schema_version", ""),
        "engine_version": report_dict.get("engine_version", ""),
        "registry_version": report_dict.get("registry_version", ""),
        "detector_version": report_dict.get("detector_version", ""),
        "level": score.get("level"),
        "pass_rate": score.get("pass_rate"),
        "gating_passed": score.get("gating_passed"),
        "gating_total": score.get("gating_total"),
        "commit": report_dict.get("commit", ""),
    }


def _unique_snapshot(bucket_names: set, safe_ts: str) -> str:
    candidate = safe_ts + ".json"
    n = 1
    while candidate in bucket_names:
        candidate = f"{safe_ts}-{n}.json"
        n += 1
    return candidate


# --------------------------------------------------------------------------- index handling
# One valid entry costs ~27 strict-loader nodes (13 keys x 2 + container nodes), so the
# generic 100k node budget would cap a valid index at ~3.7k entries and surface as
# "history index unreadable" before the authored 10k HistoryLimitError. The index read
# gets an entry-proportional budget so the authored cap always fires first.
_INDEX_MAX_NODES = MAX_HISTORY_INDEX_ENTRIES * 32


def _load_index(bucket_auth: safe_io.RootAuthority):
    """Load a current-format digest index; ``None`` when absent, ``"invalid"`` when malformed."""
    obs = safe_io.read_rooted_regular(bucket_auth, "index.json",
                                      max_bytes=MAX_HISTORY_INDEX_BYTES)
    if obs.state is safe_io.RepoReadState.MISSING:
        return None
    if obs.state is not safe_io.RepoReadState.OK:
        return "invalid"
    try:
        data = parsers.strict_load_json(obs.data, max_bytes=MAX_HISTORY_INDEX_BYTES,
                                        max_nodes=_INDEX_MAX_NODES,
                                        require_object=True)
    except parsers.StrictJsonError:
        return "invalid"
    if data.get("version") != "2" or not isinstance(data.get("entries"), list):
        return "invalid"
    for entry in data["entries"]:
        if not _valid_index_entry(entry):
            return "invalid"
    files = [e["file"] for e in data["entries"]]
    if len(files) != len(set(files)):
        return "invalid"
    entries = sorted(data["entries"], key=lambda e: (e["timestamp"], e["file"]))
    return entries


def _valid_index_entry(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    if set(entry.keys()) != {"timestamp", "file", "bytes", "sha256", "schema_version",
                             "engine_version", "registry_version", "detector_version",
                             "level", "pass_rate", "gating_passed", "gating_total",
                             "commit"}:
        return False
    if not isinstance(entry["timestamp"], str) or not entry["timestamp"]:
        return False
    if not _INDEX_FILE_RE.match(entry["file"] or ""):
        return False
    if type(entry["bytes"]) is not int or not 1 <= entry["bytes"] <= MAX_HISTORY_INDEX_BYTES:
        return False
    if not _SHA256_RE.match(entry["sha256"] or ""):
        return False
    if entry["level"] is not None and type(entry["level"]) is not int:
        return False
    if entry["pass_rate"] is not None and (not isinstance(entry["pass_rate"], (int, float))
                                           or isinstance(entry["pass_rate"], bool)):
        return False
    for key in ("gating_passed", "gating_total"):
        if entry[key] is not None and type(entry[key]) is not int:
            return False
    for key in ("schema_version", "engine_version", "registry_version",
                "detector_version"):
        if not isinstance(entry[key], str) or len(entry[key]) > 32:
            return False
    commit = entry["commit"]
    if commit and not re.fullmatch(r"[0-9a-f]{4,64}", commit):
        return False
    return True


def _load_legacy_index(bucket_auth: safe_io.RootAuthority):
    """Load an old top-level-list index; ``None`` when absent, ``"invalid"`` when malformed."""
    obs = safe_io.read_rooted_regular(bucket_auth, "index.json",
                                      max_bytes=MAX_HISTORY_INDEX_BYTES)
    if obs.state is safe_io.RepoReadState.MISSING:
        return None
    if obs.state is not safe_io.RepoReadState.OK:
        return "invalid"
    try:
        data = parsers.strict_load_json(obs.data, max_bytes=MAX_HISTORY_INDEX_BYTES,
                                        max_nodes=_INDEX_MAX_NODES)
    except parsers.StrictJsonError:
        return "invalid"
    if not isinstance(data, list):
        return "invalid"
    entries = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) \
                or not _INDEX_FILE_RE.match(entry["file"]):
            return "invalid"
        entries.append(entry)
    return sorted(entries, key=lambda e: (e.get("timestamp") or "", e.get("file") or ""))


# --------------------------------------------------------------------------- storage
def store_history(report_dict, source: HistorySource, *, write_latest: bool = True):
    """Write an immutable snapshot, refresh ``latest.json``, and append the ordered index.

    Requires a current-mode source and a report carrying a repository identity. Refuses at
    the MAX_HISTORY_INDEX_ENTRIES cap (never prunes or overwrites an old snapshot).
    Returns manifest entries ``(relpath, bytes)`` for the persistence protocol.
    """
    if source.mode != "current":
        raise ValueError("history storage requires a current source")
    identity = report_dict.get("repository")
    if not identity or not identity.get("identity_hash"):
        raise ValueError("cannot store history without a repository identity")
    ih = identity["identity_hash"]
    safe_io.ensure_rooted_directory(source.root_authority, f"history/{ih}", mode=0o755)
    bucket = safe_io.open_subroot(source.root_authority, f"history/{ih}")
    try:
        entries = _load_index(bucket) or []
        if entries == "invalid":
            raise ValueError("history index unreadable")
        if len(entries) >= MAX_HISTORY_INDEX_ENTRIES:
            raise HistoryLimitError(
                "history limit reached; archive or remove old snapshots before persisting")
        ts = report_dict.get("generated_at") or now_iso()
        payload = json.dumps(report_dict, indent=2).encode("utf-8")
        filename = _unique_snapshot({e["file"] for e in entries}, _safe_ts(ts))
        if not safe_io.create_rooted_exclusive(bucket, filename, payload):
            raise ValueError(f"history snapshot already exists: {filename}")
        entries.append(_index_entry(report_dict, filename, payload))
        entries.sort(key=lambda e: (e["timestamp"], e["file"]))
        index_payload = json.dumps({"version": "2", "entries": entries},
                                   indent=2).encode("utf-8")
        safe_io.atomic_replace_rooted(bucket, "index.json", index_payload)
    finally:
        bucket.close()
    manifest = [(f"history/{ih}/{filename}", payload),
                (f"history/{ih}/index.json", index_payload)]
    if write_latest:
        safe_io.atomic_replace_rooted(source.root_authority, "latest.json", payload)
        manifest.append(("latest.json", payload))
    write_commit_manifest(source.root_authority,
                          history_manifest(source.root_authority)
                          + ([("latest.json", payload)] if write_latest else []))
    return manifest


# --------------------------------------------------------------------------- reading
def _identity_for(project, git_collector, require_origin=False):
    from .collectors.git import GitCollector
    collector = git_collector or GitCollector(project or ".")
    return collector, repo_identity(project, git_collector=collector,
                                    require_origin=require_origin)


def _read_snapshot(bucket_auth: safe_io.RootAuthority, filename: str):
    obs = safe_io.read_rooted_regular(bucket_auth, filename,
                                      max_bytes=MAX_HISTORY_INDEX_BYTES)
    if obs.state is not safe_io.RepoReadState.OK:
        return None
    try:
        return parsers.strict_load_json(obs.data, max_bytes=MAX_HISTORY_INDEX_BYTES,
                                        require_object=True)
    except parsers.StrictJsonError:
        return None


def _reader_lock(source: HistorySource):
    """Take the nonblocking shared reader lock for a current source (legacy exempt).

    Returns ``""`` with the lock held, or an authored reason when busy/incomplete.
    """
    if source.mode != "current":
        return ""
    if not safe_io.lock_directory(source.root_authority.fd, exclusive=False):
        return "persistence busy; retry after the active writer finishes"
    state = validate_generation(source.root_authority)
    if state:
        safe_io.unlock_directory(source.root_authority.fd)
        return state
    return ""


def _reader_unlock(source: HistorySource) -> None:
    if source.mode == "current":
        safe_io.unlock_directory(source.root_authority.fd)


def list_history(source: HistorySource, project, *, git_collector=None):
    """Ordered history index for this repo's identity (current or explicit legacy mode)."""
    busy = _reader_lock(source)
    if busy:
        return None, busy
    try:
        return _list_history_unlocked(source, project, git_collector=git_collector)
    finally:
        _reader_unlock(source)


def _list_history_unlocked(source: HistorySource, project, *, git_collector=None):
    _collector, identity = _identity_for(project, git_collector)
    if identity is None:
        return None, "no repository identity (origin remote required)"
    bucket = _open_bucket(source, identity["identity_hash"])
    if bucket is None:
        return None, "no readiness history for this repository"
    try:
        if source.mode == "current":
            entries = _load_index(bucket)
            commit_status = "committed"
        else:
            entries = _load_legacy_index(bucket)
            commit_status = "legacy_uncommitted"
        if entries == "invalid":
            return None, "history index unreadable"
        entries = entries or []
        out = [{**e, "id": _stem(e.get("file", "")), "commit_status": commit_status}
               for e in entries]
        return {"repository": identity, "entries": out}, ""
    finally:
        bucket.close()


def load_snapshot(source: HistorySource, project, snapshot_id: str, *, git_collector=None):
    """Load a stored report by history id (file stem) or the literal ``latest``."""
    if snapshot_id != "latest" and not _SNAPSHOT_ID_RE.match(snapshot_id or ""):
        return None
    busy = _reader_lock(source)
    if busy:
        return None
    try:
        return _load_snapshot_unlocked(source, project, snapshot_id,
                                       git_collector=git_collector)
    finally:
        _reader_unlock(source)


def _load_snapshot_unlocked(source: HistorySource, project, snapshot_id: str,
                            *, git_collector=None):
    _collector, identity = _identity_for(project, git_collector)
    if identity is None:
        return None
    bucket = _open_bucket(source, identity["identity_hash"])
    if bucket is None:
        return None
    try:
        if source.mode == "current":
            entries = _load_index(bucket)
        else:
            entries = _load_legacy_index(bucket)
        if entries == "invalid":
            return None
        entries = entries or []
        if snapshot_id == "latest":
            target = entries[-1] if entries else None
        else:
            target = next((e for e in entries
                           if _stem(e.get("file", "")) == snapshot_id), None)
        if not target:
            return None
        return _read_snapshot(bucket, target["file"])
    finally:
        bucket.close()


def resolve_latest(source: HistorySource, project, *, git_collector=None,
                   require_origin=False):
    """Resolve the latest stored current-schema report for this repo's identity.

    Only a current-mode source resolves; schema-v2 legacy history remains listable/loadable
    by explicit ID but never resolves through ``--latest``.
    """
    if source.mode != "current":
        return None, "latest resolves only from a current reports root"
    busy = _reader_lock(source)
    if busy:
        return None, busy
    try:
        return _resolve_latest_unlocked(source, project, git_collector,
                                        require_origin)
    finally:
        _reader_unlock(source)


def _resolve_latest_unlocked(source: HistorySource, project, git_collector,
                             require_origin):
    _collector, identity = _identity_for(project, git_collector, require_origin)
    if identity is None:
        return None, "no repository identity (origin remote required)"
    bucket = _open_bucket(source, identity["identity_hash"])
    if bucket is None:
        return None, "no readiness history for this repository"
    try:
        entries = _load_index(bucket)
        if entries == "invalid":
            return None, "history index unreadable"
        if not entries:
            return None, "no readiness history for this repository"
        report = _read_snapshot(bucket, entries[-1]["file"])
        if report is None:
            return None, "history snapshot unreadable"
        schema = str(report.get("schema_version"))
        if schema != version.SCHEMA_VERSION:
            return None, (f"stored report schema {schema}; current schema "
                          f"{version.SCHEMA_VERSION}; rerun")
        return report, ""
    finally:
        bucket.close()


def _stem(filename):
    return filename[:-5] if filename.endswith(".json") else filename


# --------------------------------------------------------------------------- delta
def delta(old, new, *, git_collector=None):
    """Deterministic delta between two strictly validated report dicts.

    ``comparable`` is False with an authored reason when validation fails, the version
    contract differs, or (schema 3) the identity/input/evidence-scope/ancestry contract is
    unmet. Schema2 comparison requires equal schema, engine, registry, and detector
    versions; schema 2↔3 is explicitly incomparable.
    """
    if not isinstance(old, dict) or not isinstance(new, dict):
        return {"comparable": False, "reason": "invalid old report"}
    old_schema = str(old.get("schema_version"))
    new_schema = str(new.get("schema_version"))
    if old_schema not in ("2", "3") or model.validate_imported_report(old, old_schema):
        return {"comparable": False, "reason": "invalid old report"}
    if new_schema not in ("2", "3") or model.validate_imported_report(new, new_schema):
        return {"comparable": False, "reason": "invalid new report"}
    if old_schema != new_schema:
        return {"comparable": False, "reason": "version mismatch: schema_version"}
    mismatched = [k for k in _CONTRACT_KEYS[1:] if old.get(k) != new.get(k)]
    if mismatched:
        return {"comparable": False, "reason": "version mismatch: " + ", ".join(mismatched)}
    if old_schema == "3":
        reason = _schema3_scope_reason(old, new, git_collector)
        if reason:
            return {"comparable": False, "reason": reason}

    old_status = {r["id"]: r["status"] for r in old.get("results", [])}
    new_status = {r["id"]: r["status"] for r in new.get("results", [])}
    changes = []
    for cid in sorted(set(old_status) | set(new_status)):
        before, after = old_status.get(cid), new_status.get(cid)
        if before != after:
            changes.append({"id": cid, "from": before, "to": after})

    old_score, new_score = (old.get("score") or {}), (new.get("score") or {})
    return {
        "comparable": True,
        "score_delta": {
            "level": _num_delta(old_score, new_score, "level"),
            "gating_passed": _num_delta(old_score, new_score, "gating_passed"),
            "gating_total": _num_delta(old_score, new_score, "gating_total"),
        },
        "criteria_changes": changes,
        "newly_passing": [c["id"] for c in changes if c["to"] == "pass"],
        "newly_failing": [c["id"] for c in changes if c["to"] == "fail"],
        "newly_unknown": [c["id"] for c in changes if c["to"] == "unknown"],
    }


def _schema3_scope_reason(old, new, git_collector) -> str:
    """The ordered schema-3 comparability preconditions (§4.7). "" when comparable."""
    # 1. repository identity
    old_repo, new_repo = old.get("repository"), new.get("repository")
    for repo in (old_repo, new_repo):
        if not isinstance(repo, dict) or not repo.get("identity_kind") \
                or not repo.get("identity_hash"):
            return "repository identity unavailable"
    if (old_repo["identity_kind"] != new_repo["identity_kind"]
            or old_repo["identity_hash"] != new_repo["identity_hash"]):
        return "repository identity mismatch"

    def invocation(report):
        return ((report.get("assessment_provenance") or {}).get("invocation") or {})

    old_inv, new_inv = invocation(old), invocation(new)

    # 2. canonical inputs
    if ((old_inv.get("inputs") or {}).get("profile") != "repository"
            or (new_inv.get("inputs") or {}).get("profile") != "repository"):
        return "assessment inputs noncanonical"

    # 3. assessment order (equal allowed)
    old_dt = _parse_iso(old.get("generated_at"))
    new_dt = _parse_iso(new.get("generated_at"))
    if old_dt is None or new_dt is None or old_dt > new_dt:
        return "assessment order invalid"

    # 4. static completeness
    if not (old_inv.get("static") or {}).get("collection_complete") \
            or not (new_inv.get("static") or {}).get("collection_complete"):
        return "evidence scope incomplete: static"

    # 5/6. git metadata class + completeness
    def git_class(report):
        profile = ((invocation(report).get("git") or {}).get("metadata_profile") or "")
        return "absent" if profile == "absent" else "repository"
    if git_class(old) != git_class(new):
        return "evidence scope mismatch: git presence"
    if not (old_inv.get("git") or {}).get("collection_complete") \
            or not (new_inv.get("git") or {}).get("collection_complete"):
        return "evidence scope incomplete: git"

    # 7. github scope
    old_gh, new_gh = old_inv.get("github") or {}, new_inv.get("github") or {}
    if bool(old_gh.get("requested")) != bool(new_gh.get("requested")):
        return "evidence scope mismatch: github requested"
    if old_gh.get("requested") and not (old_gh.get("collection_complete")
                                        and new_gh.get("collection_complete")):
        return "evidence scope incomplete: github"

    # 8. execution scope
    old_ex, new_ex = old_inv.get("execution") or {}, new_inv.get("execution") or {}
    if bool(old_ex.get("requested")) != bool(new_ex.get("requested")):
        return "evidence scope mismatch: execution requested"
    if old_ex.get("requested"):
        if old_ex.get("timeout_seconds") != new_ex.get("timeout_seconds"):
            return "evidence scope mismatch: execution timeout"
        if not (old_ex.get("successful") and new_ex.get("successful")):
            return "evidence scope incomplete: execution"

    # 9. commit ancestry through the retained safe current-project Git authority
    old_commit = ((old.get("assessment_provenance") or {}).get("subject") or {}) \
        .get("commit") or old.get("commit") or ""
    new_commit = ((new.get("assessment_provenance") or {}).get("subject") or {}) \
        .get("commit") or new.get("commit") or ""
    if not _valid_object_id(old_commit) or not _valid_object_id(new_commit) \
            or len(old_commit) != len(new_commit):
        return "commit ancestry not proven"
    if git_collector is None:
        return "commit ancestry not proven"
    obs = git_collector.is_ancestor(old_commit, new_commit)
    if obs.state != "present" or obs.value is not True:
        return "commit ancestry not proven"
    return ""


def _valid_object_id(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",
                                                        value))


def _parse_iso(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _num_delta(old_score, new_score, key):
    return {"from": old_score.get(key), "to": new_score.get(key)}


# --------------------------------------------------------------------------- commit manifest
MAX_PERSIST_MANIFEST_BYTES = 262_144
_PERSIST_GENERATED_RE = re.compile(
    r"(?:report\.(?:json|md|html|xml|sarif|txt)|latest\.json|\.commit\.json\Z|"
    r"\.[^/]*\.ra1-tmp\Z)")


def _manifest_payload(files) -> bytes:
    return (json.dumps({
        "version": "1",
        "files": [{"path": path, "bytes": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()} for path, data in files],
    }, indent=2) + "\n").encode("utf-8")


def validate_generation(out_auth) -> str:
    """Validate the existing generated state under a persistence candidate root.

    Returns "" for a valid first generation or a complete committed generation, else an
    authored refusal category. Never adopts a partial/mixed generation silently.
    """
    manifest_obs = safe_io.read_rooted_regular(out_auth, ".commit.json",
                                               max_bytes=MAX_PERSIST_MANIFEST_BYTES)
    if manifest_obs.state is safe_io.RepoReadState.MISSING:
        inventory = safe_io.discover_rooted_regular(out_auth, ["report.*", "latest.json",
                                                               "history/**", ".commit.json",
                                                               ".*.ra1-tmp"])
        if inventory.state is safe_io.RepoDiscoveryState.OK and not inventory.paths:
            return ""  # a missing or empty output directory is a valid first generation
        return "persistence.incomplete"
    if manifest_obs.state is not safe_io.RepoReadState.OK:
        return "persistence.incomplete"
    try:
        manifest = parsers.strict_load_json(manifest_obs.data,
                                            max_bytes=MAX_PERSIST_MANIFEST_BYTES,
                                            require_object=True)
    except parsers.StrictJsonError:
        return "persistence.incomplete"
    if manifest.get("version") != "1" or not isinstance(manifest.get("files"), list):
        return "persistence.incomplete"
    declared = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return "persistence.incomplete"
        declared.add(entry["path"])
        obs = safe_io.read_rooted_regular(out_auth, entry["path"],
                                          max_bytes=MAX_HISTORY_INDEX_BYTES)
        if obs.state is not safe_io.RepoReadState.OK:
            return "persistence.incomplete"
        if hashlib.sha256(obs.data).hexdigest() != entry.get("sha256"):
            return "persistence.incomplete"
    inventory = safe_io.discover_rooted_regular(out_auth, ["report.*", "latest.json",
                                                           "history/**", ".commit.json",
                                                           ".*.ra1-tmp"])
    if inventory.state is not safe_io.RepoDiscoveryState.OK:
        return "persistence.incomplete"
    for path in inventory.paths:
        if path == ".commit.json":
            continue  # the manifest never declares itself
        if path not in declared:
            return "persistence.incomplete"
    return ""


def write_commit_manifest(out_auth, files) -> None:
    """The sole logical commit point: atomic manifest replacement, last."""
    payload = _manifest_payload(files)
    safe_io.atomic_replace_rooted(out_auth, ".commit.json", payload)



def history_manifest(out_auth) -> list:
    """The complete closed history inventory as manifest entries ``(relpath, bytes)``.

    Every digest-indexed immutable snapshot plus the index itself — a new manifest always
    carries the full history, never only the latest write.
    """
    buckets = safe_io.discover_rooted_regular(out_auth, ["history/*/index.json"])
    if buckets.state is not safe_io.RepoDiscoveryState.OK:
        raise safe_io.RepositoryInputError("history inventory unreadable")
    files = []
    for index_path in buckets.paths:
        bucket_rel = index_path.rsplit("/", 1)[0]
        bucket = safe_io.open_subroot(out_auth, bucket_rel)
        try:
            entries = _load_index(bucket) or []
            if entries == "invalid":
                raise safe_io.RepositoryInputError("history index unreadable")
            for entry in entries:
                obs = safe_io.read_rooted_regular(bucket, entry["file"],
                                                  max_bytes=MAX_HISTORY_INDEX_BYTES)
                if obs.state is not safe_io.RepoReadState.OK:
                    raise safe_io.RepositoryInputError("history snapshot unreadable")
                files.append((f"{bucket_rel}/{entry['file']}", obs.data))
            index_obs = safe_io.read_rooted_regular(bucket, "index.json",
                                                    max_bytes=MAX_HISTORY_INDEX_BYTES)
            if index_obs.state is not safe_io.RepoReadState.OK:
                raise safe_io.RepositoryInputError("history index unreadable")
            files.append((index_path, index_obs.data))
        finally:
            bucket.close()
    return files



def plan_history_write(report_dict, out_auth):
    """Read-only preflight for one history write: index validity, cap, and every payload.

    Raises HistoryLimitError/ValueError *before any final-name mutation* so a refusal
    leaves the existing generation byte-identical. Returns
    ``(bucket_rel, filename, snapshot_payload, index_payload)``.
    """
    identity = report_dict.get("repository")
    if not identity or not identity.get("identity_hash"):
        raise ValueError("cannot store history without a repository identity")
    ih = identity["identity_hash"]
    bucket_rel = f"history/{ih}"
    entries = []
    try:
        bucket = safe_io.open_subroot(out_auth, bucket_rel)
    except (OSError, safe_io.RepositoryInputError):
        bucket = None
    if bucket is not None:
        try:
            loaded = _load_index(bucket)
            if loaded == "invalid":
                raise ValueError("history index unreadable")
            entries = loaded or []
        finally:
            bucket.close()
    if len(entries) >= MAX_HISTORY_INDEX_ENTRIES:
        raise HistoryLimitError(
            "history limit reached; archive or remove old snapshots before persisting")
    ts = report_dict.get("generated_at") or now_iso()
    payload = json.dumps(report_dict, indent=2).encode("utf-8")
    filename = _unique_snapshot({e["file"] for e in entries}, _safe_ts(ts))
    new_entries = sorted(entries + [_index_entry(report_dict, filename, payload)],
                         key=lambda e: (e["timestamp"], e["file"]))
    index_payload = json.dumps({"version": "2", "entries": new_entries},
                               indent=2).encode("utf-8")
    return (bucket_rel, filename, payload, index_payload)


def commit_history_write(out_auth, plan) -> None:
    """Commit one preflighted history write (snapshot create, then index replace)."""
    bucket_rel, filename, payload, index_payload = plan
    safe_io.ensure_rooted_directory(out_auth, bucket_rel, mode=0o755)
    bucket = safe_io.open_subroot(out_auth, bucket_rel)
    try:
        if not safe_io.create_rooted_exclusive(bucket, filename, payload):
            raise ValueError(f"history snapshot already exists: {filename}")
        safe_io.atomic_replace_rooted(bucket, "index.json", index_payload)
    finally:
        bucket.close()
