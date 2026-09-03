"""The composite action's bounded engine launcher (no eval, env-only inputs).

Builds one quoted argv array from environment inputs and runs the fixed vendored CLI.
Never inherits repository-supplied shell words; every value crosses through env.

``ACTION_MODE=report`` runs the report scan with the configured formats into the private
``out_dir``. ``ACTION_MODE=gate`` re-runs with identical evidence options (same
``GITHUB_ENABLED`` scope) and gates on ``MIN_LEVEL`` — the gate always scores the same
evidence the persisted report was built from.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("true", "yes", "1", "on")


def _cli() -> str:
    return str((pathlib.Path(os.environ["ACTION_PATH"]) / ".." / "engine"
                / "readiness" / "cli.py").resolve())


def main() -> int:
    mode = os.environ.get("ACTION_MODE", "report")
    min_level = os.environ["MIN_LEVEL"]
    if not (min_level.isdigit() and 1 <= int(min_level) <= 4):
        sys.stderr.write(f"min-level must be 1-4, got {min_level!r}\n")
        return 1
    if mode == "gate":
        argv = [_cli(), "report", "--project", os.environ["PROJECT"],
                "--format", "json", "--min-level", min_level]
    else:
        argv = [_cli(), "report", "--project", os.environ["PROJECT"],
                "--format", os.environ["FORMATS"], "--out", os.environ["out_dir"]]
    if _env_bool("GITHUB_ENABLED"):
        argv.append("--github")
    # argv-list form with a fixed executable and no shell: env values become discrete
    # arguments to the vendored CLI, which validates them itself (--format tokens,
    # --min-level range, --project admission). Nothing here is interpreted by a shell.
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args  # noqa: E501
    proc = subprocess.run(["python3", *argv], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or "")
        if mode == "report":
            # A gate failure below min-level is report failure? No: the report scan itself
            # exits 0 unless persistence fails; print its stderr and propagate.
            pass
        return proc.returncode
    return 0


if __name__ == "__main__":  # pragma: no cover - action entrypoint
    raise SystemExit(main())