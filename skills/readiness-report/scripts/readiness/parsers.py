"""Stdlib-only, tolerant parsers.

Pure standard library: ``json`` and ``tomllib`` (Python 3.11+) and ``configparser``.
No YAML parser exists in the stdlib, so we never parse YAML semantically — CI *presence*
is a file glob and CI *semantics* come from the GitHub API. JSONC (tsconfig/biome) is
handled by stripping comments + trailing commas before ``json.loads``.
"""
from __future__ import annotations

import configparser
import json
import tomllib
from pathlib import Path
from typing import Optional


def read_text(path) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def load_json(path) -> Optional[object]:
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


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


def load_jsonc(path) -> Optional[object]:
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, ValueError):
        return None


def load_toml(path) -> Optional[dict]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return None


def load_ini(path) -> Optional[configparser.ConfigParser]:
    text = read_text(path)
    if text is None:
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
        return parser
    except configparser.Error:
        return None
