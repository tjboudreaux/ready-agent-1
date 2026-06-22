"""Repository identity, local report history, and deterministic deltas.

Factory/Droid's `/readiness-report` is keyed to a git repo with an `origin` remote and persists
reports for dashboard history. RA1 keeps working on arbitrary local repos, so this module:

- resolves a *repository identity* — an `origin`-derived identity when a remote exists, or a
  local-path identity otherwise (never both, never a raw token or absolute path on disk);
- stores immutable, timestamped report snapshots plus a canonical `latest.json` and an ordered
  per-identity index;
- computes a deterministic delta between two reports, but only when the version contract matches.

Pure standard library. Secrets policy: the raw origin URL and the raw absolute project path are
used only transiently to derive a redaction/hash and are never returned or serialized.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# The stale-state contract: a delta is only meaningful when these match.
_CONTRACT_KEYS = ("schema_version", "engine_version", "registry_version")


# --------------------------------------------------------------------------- identity
def _default_git_runner(project, args):  # pragma: no cover - subprocess boundary
    try:
        p = subprocess.run(["git", "-C", str(project), *args],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _origin_url(project, git_runner):
    out = (git_runner or _default_git_runner)(project, ["remote", "get-url", "origin"])
    return out.strip() if out and out.strip() else None


def redact_origin_url(url):
    """Strip any ``user[:token]@`` credentials from a remote URL for safe serialization."""
    return re.sub(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^/@]*@", r"\1", url.strip())


def parse_origin(url):
    """Best-effort ``(host, owner, name)`` from an https/ssh/scp-style git remote URL."""
    u = url.strip()
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://([^/]+)(?:/(.*))?$", u)
    if m:
        netloc, path = m.group(1), m.group(2) or ""
        host = netloc.split("@")[-1]
    else:
        m2 = re.match(r"^(?:[^@/]+@)?([^:/]+):(.+)$", u)  # scp-like git@host:owner/repo
        if not m2:
            return ("", "", "")
        host, path = m2.group(1), m2.group(2)
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return (host, "", "")
    return (host, "/".join(parts[:-1]), parts[-1])


def _hash(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def repo_identity(project, *, require_origin=False, git_runner=None):
    """Resolve a serializable repository identity, or ``None`` when origin is required but absent.

    Origin identity wins when an `origin` remote exists; otherwise a local-path identity is used
    unless ``require_origin`` is set. The raw origin URL and raw absolute path never escape.
    """
    project = Path(project).resolve()
    raw = _origin_url(project, git_runner)
    if raw:
        host, owner, name = parse_origin(raw)
        return {
            "identity_kind": "origin",
            "redacted_origin_url": redact_origin_url(raw),
            "host": host,
            "owner": owner,
            "name": name,
            "identity_hash": _hash("origin", host, owner, name),
        }
    if require_origin:
        return None
    project_path_hash = _hash(str(project))
    return {
        "identity_kind": "local_path",
        "name": project.name,
        "project_path_hash": project_path_hash,
        "identity_hash": _hash("local_path", project_path_hash),
    }


# --------------------------------------------------------------------------- storage paths
def primary_out(project, out=None):
    """The directory that holds ``report.<ext>`` and the canonical ``latest.json``."""
    return Path(out) if out else Path(project) / ".agents" / "readiness"


def history_root(project, out=None, history_dir=None):
    """The history root: explicit ``--history-dir`` wins, else ``<out>/history``, else default."""
    if history_dir:
        return Path(history_dir)
    if out:
        return Path(out) / "history"
    return Path(project) / ".agents" / "readiness" / "history"


def _safe_ts(ts):
    return re.sub(r"[^0-9A-Za-z]", "-", ts) or "unknown"


def _unique_snapshot(bucket, safe_ts):
    candidate = bucket / (safe_ts + ".json")
    n = 1
    while candidate.exists():
        candidate = bucket / f"{safe_ts}-{n}.json"
        n += 1
    return candidate


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _index_entry(report_dict, filename):
    score = report_dict.get("score") or {}
    return {
        "timestamp": report_dict.get("generated_at", ""),
        "file": filename,
        "level": score.get("level"),
        "pass_rate": score.get("pass_rate"),
        "gating_passed": score.get("gating_passed"),
        "gating_total": score.get("gating_total"),
        "registry_version": report_dict.get("registry_version"),
        "detector_version": report_dict.get("detector_version"),
        "commit": report_dict.get("commit", ""),
    }


def load_index(index_path):
    try:
        data = json.loads(Path(index_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def store_history(report_dict, project, *, out=None, history_dir=None):
    """Write an immutable snapshot, refresh ``latest.json``, and append the ordered index.

    Requires the report to carry a repository identity. Returns the written paths.
    """
    identity = report_dict.get("repository")
    if not identity or not identity.get("identity_hash"):
        raise ValueError("cannot store history without a repository identity")
    ih = identity["identity_hash"]
    ts = report_dict.get("generated_at") or now_iso()
    bucket = history_root(project, out, history_dir) / ih
    bucket.mkdir(parents=True, exist_ok=True)

    snapshot = _unique_snapshot(bucket, _safe_ts(ts))
    payload = json.dumps(report_dict, indent=2)
    snapshot.write_text(payload, encoding="utf-8")

    index_path = bucket / "index.json"
    index = load_index(index_path)
    index.append(_index_entry(report_dict, snapshot.name))
    index.sort(key=lambda e: (e.get("timestamp") or "", e.get("file") or ""))
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    pout = primary_out(project, out)
    pout.mkdir(parents=True, exist_ok=True)
    latest = pout / "latest.json"
    latest.write_text(payload, encoding="utf-8")
    return {"snapshot": snapshot, "index": index_path, "latest": latest}


def resolve_latest(project, *, history_dir=None, require_origin=False, git_runner=None):
    """Resolve the latest stored report for this repo's identity.

    Returns ``(report_dict, "")`` on success or ``(None, reason)`` when nothing resolvable is
    found (no identity, no history, empty/corrupt index, or a stale schema-1 snapshot).
    """
    identity = repo_identity(project, require_origin=require_origin, git_runner=git_runner)
    if identity is None:
        return None, "no repository identity (origin remote required)"
    bucket = history_root(project, out=None, history_dir=history_dir) / identity["identity_hash"]
    index = load_index(bucket / "index.json")
    if not index:
        return None, f"no readiness history for this repository at {bucket}"
    entry = index[-1]
    snap = bucket / entry.get("file", "")
    try:
        report = json.loads(snap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, f"history snapshot unreadable: {snap}"
    if str(report.get("schema_version")) != "2":
        return None, "stored report predates schema 2; rerun `ra1 report --store-history`"
    return report, ""


def list_history(project, *, history_dir=None, require_origin=False, git_runner=None):
    """Ordered history index for this repo's identity (API-shaped, no schema translation needed)."""
    identity = repo_identity(project, require_origin=require_origin, git_runner=git_runner)
    if identity is None:
        return None, "no repository identity (origin remote required)"
    bucket = history_root(project, out=None, history_dir=history_dir) / identity["identity_hash"]
    entries = [{**e, "id": _stem(e.get("file", ""))} for e in load_index(bucket / "index.json")]
    return {"repository": identity, "entries": entries}, ""


def load_snapshot(project, snapshot_id, *, history_dir=None, require_origin=False, git_runner=None):
    """Load a stored report by its history id (file stem) or the literal ``latest``; else None."""
    identity = repo_identity(project, require_origin=require_origin, git_runner=git_runner)
    if identity is None:
        return None
    bucket = history_root(project, out=None, history_dir=history_dir) / identity["identity_hash"]
    index = load_index(bucket / "index.json")
    if snapshot_id == "latest":
        target = index[-1] if index else None
    else:
        target = next((e for e in index if _stem(e.get("file", "")) == snapshot_id), None)
    if not target:
        return None
    try:
        return json.loads((bucket / target.get("file", "")).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _stem(filename):
    return filename[:-5] if filename.endswith(".json") else filename


# --------------------------------------------------------------------------- delta
def delta(old, new):
    """Deterministic delta between two report dicts.

    ``comparable`` is False when the schema/engine/registry contract differs. When comparable but
    the detector version changed, ``detector_changed`` is True and callers must suppress N/M and
    application deltas (a detector change can move denominators with no real repository change).
    """
    mismatched = [k for k in _CONTRACT_KEYS if old.get(k) != new.get(k)]
    if mismatched:
        return {"comparable": False, "reason": "version mismatch: " + ", ".join(mismatched)}

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
        "detector_changed": old.get("detector_version") != new.get("detector_version"),
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


def _num_delta(old_score, new_score, key):
    return {"from": old_score.get(key), "to": new_score.get(key)}
