"""Stdlib-only, tolerant parsers.

Pure standard library: ``json`` and ``tomllib`` (Python 3.11+) and ``configparser``.
No YAML parser exists in the stdlib, so we never parse YAML semantically — CI *presence*
is a file glob and CI *semantics* come from the GitHub API. JSONC (tsconfig/biome) is
handled by stripping comments + trailing commas before ``json.loads``.

Repository-controlled content always arrives here as *bounded text/bytes* already read
through the safe-I/O collector boundary: these entrypoints parse data, never paths.
``read_engine_text`` remains for explicit engine-owned files (registry, templates).
"""
from __future__ import annotations

import configparser
import json
import tomllib
from pathlib import Path

MAX_ENGINE_FILE_BYTES = 2_097_152


def read_engine_text(path, *, max_bytes: int = MAX_ENGINE_FILE_BYTES) -> str | None:
    """Bounded read of an explicit engine-owned file (registry/templates), never
    repository input."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError:
        return None
    if len(data) > max_bytes:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_text(path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def loads_json(text: str) -> object | None:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def load_json(path) -> object | None:
    return loads_json(read_engine_text(path))


def load_jsonc(path) -> object | None:
    text = read_engine_text(path)
    if text is None:
        return None
    try:
        return json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, ValueError):
        return None


def loads_jsonc(text: str) -> object | None:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, ValueError):
        return None


def loads_toml(text: str) -> dict | None:
    if not isinstance(text, str):
        return None
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None


def load_toml(path) -> dict | None:
    return loads_toml(read_engine_text(path))


def loads_ini(text: str) -> configparser.ConfigParser | None:
    if not isinstance(text, str):
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
        return parser
    except configparser.Error:
        return None


def load_ini(path) -> configparser.ConfigParser | None:
    return loads_ini(read_engine_text(path))


# --------------------------------------------------------------------------- strict JSON
class StrictJsonError(ValueError):
    """Bounded strict-JSON rejection (duplicate keys, non-finite numbers, caps, root shape)."""


MAX_STRICT_JSON_BYTES = 8_388_608
MAX_STRICT_JSON_DEPTH = 64
MAX_STRICT_JSON_NODES = 100_000
MAX_STRICT_JSON_STRING_BYTES = 1_048_576


def strict_load_json(data, *, max_bytes: int = MAX_STRICT_JSON_BYTES,
                     max_depth: int = MAX_STRICT_JSON_DEPTH,
                     max_nodes: int = MAX_STRICT_JSON_NODES,
                     max_string_bytes: int = MAX_STRICT_JSON_STRING_BYTES,
                     require_object: bool = False) -> object:
    """Bounded, strict JSON loader for imported reports and API bodies.

    Rejects duplicate object keys, non-finite numbers, excessive depth/node/string/total
    sizes, and (with ``require_object``) a non-object root. Raises StrictJsonError; never
    repairs or coerces.
    """
    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        raise StrictJsonError("json input must be str or bytes")
    if len(raw) > max_bytes:
        raise StrictJsonError("json input exceeds byte cap")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError("json input is not utf-8") from exc

    def _no_constant(value):
        raise StrictJsonError(f"non-finite number: {value}")

    def _no_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise StrictJsonError(f"duplicate key: {key!r}")
            out[key] = value
        return out

    try:
        value = json.loads(text, parse_constant=_no_constant,
                           object_pairs_hook=_no_duplicates)
    except StrictJsonError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise StrictJsonError("malformed json") from exc
    nodes = _count_nodes(value, 1, max_depth, max_nodes, max_string_bytes)
    if nodes > max_nodes:
        raise StrictJsonError("json exceeds node cap")
    if require_object and not isinstance(value, dict):
        raise StrictJsonError("json root must be an object")
    return value


def _count_nodes(value, depth: int, max_depth: int, max_nodes: int,
                 max_string_bytes: int) -> int:
    if depth > max_depth:
        raise StrictJsonError("json exceeds depth cap")
    if isinstance(value, str) and len(value.encode("utf-8")) > max_string_bytes:
        raise StrictJsonError("json string exceeds byte cap")
    count = 1
    if isinstance(value, dict):
        for key, item in value.items():
            count += _count_nodes(key, depth + 1, max_depth, max_nodes, max_string_bytes)
            count += _count_nodes(item, depth + 1, max_depth, max_nodes, max_string_bytes)
            if count > max_nodes:
                raise StrictJsonError("json exceeds node cap")
    elif isinstance(value, list):
        for item in value:
            count += _count_nodes(item, depth + 1, max_depth, max_nodes, max_string_bytes)
            if count > max_nodes:
                raise StrictJsonError("json exceeds node cap")
    return count


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, respecting string literals.

    A small character state machine so we never strip a ``//`` that lives inside a string.
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    quote = ""
    escaped = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                in_str = False
            i += 1
            continue
        # not in string
        if c in ('"', "'"):
            in_str = True
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in ("\n", "\r"):
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    stripped = "".join(out)
    # Remove trailing commas before } or ] (outside strings — comments already gone).
    result = []
    i, n = 0, len(stripped)
    in_str = False
    quote = ""
    escaped = False
    while i < n:
        c = stripped[i]
        if in_str:
            result.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                in_str = False
            i += 1
            continue
        if c in ('"', "'"):
            in_str = True
            quote = c
            result.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and stripped[j] in " \t\r\n":
                j += 1
            if j < n and stripped[j] in "}]":
                i += 1  # drop the comma
                continue
        result.append(c)
        i += 1
    return "".join(result)


