"""Dev Environment checks."""
from __future__ import annotations

import json

from ._helpers import acdc_config, aglob, check_needles, ev, failed, passed, skipped


def devcontainer(ctx):
    files = ctx.static.glob([".devcontainer/devcontainer.json", ".devcontainer.json",
                             ".devcontainer/*/devcontainer.json"])
    if files:
        return passed(f"Devcontainer config: {files[0]}", [ev("devcontainer", source=files[0])])
    return failed("No devcontainer configuration.")


def env_template(ctx):
    files = aglob(ctx, [".env.example", ".env.template", ".env.sample", "env.example", ".env.dist"])
    if files:
        return passed(f"Env template: {files[0]}", [ev("env template", source=files[0])])
    return failed("No environment template (.env.example).")


# --- Factory-parity dev-environment hygiene (advisory; T0) ---------------------------

_LOCAL_SVC_FILES = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
                    "docker-compose.*.yml", "Procfile", "Procfile.dev", "Tiltfile",
                    "skaffold.yaml", "skaffold.yml"]


def local_services(ctx):
    files = ctx.static.glob(_LOCAL_SVC_FILES)
    if files:
        return passed(f"Local services defined: {files[0]}", [ev("local services", source=files[0])])
    return failed("No local services definition (docker-compose/Procfile/Tiltfile/skaffold).")


_DB_SCHEMA_FILES = ["migrations/**", "**/migrations/**", "prisma/schema.prisma", "alembic/**",
                    "db/migrate/**", "drizzle/**", "**/schema.rb", "ent/schema/**", "**/*.sql"]


def database_schema(ctx):
    files = aglob(ctx, _DB_SCHEMA_FILES)
    if files:
        return passed(f"Database schema/migrations present: {files[0]}", [ev("db schema", source=files[0])])
    return failed("No database schema or migrations (migrations/prisma/alembic/*.sql).")


def devcontainer_runnable(ctx):
    """T3 (advisory): the devcontainer image builds on an isolated copy.

    Skips unless the user opted in (``--exec``) and a devcontainer config exists.
    """
    ex = ctx.exec
    if ex is None or not ex.enabled:
        return skipped("T3 execution disabled (opt in with --exec); CI status (T2) substitutes.")
    if not ctx.static.glob([".devcontainer/devcontainer.json", ".devcontainer.json",
                            ".devcontainer/*/devcontainer.json"]):
        return skipped("No devcontainer configuration to build.")
    res = ex.run_build_cmd("devcontainer build", ctx.app.path)  # allowlisted by construction
    if res["timed_out"]:
        return failed(f"devcontainer build timed out after {ex.timeout}s on an isolated copy.")
    if res["returncode"] == 0:
        return passed("Devcontainer builds on an isolated copy.", [ev("T3 devcontainer build", tier="T3")])
    return failed(f"devcontainer build exited {res['returncode']} on an isolated copy.")


def _hook_runs_check(value):
    if isinstance(value, dict):
        command = value.get("command")
        if isinstance(command, str):
            low = command.lower()
            if check_needles(command) or "sonar" in low or "ra1" in low:
                return True
        return any(_hook_runs_check(item) for item in value.values())
    if isinstance(value, list):
        return any(_hook_runs_check(item) for item in value)
    return False


def agent_hooks(ctx):
    configured = acdc_config(ctx).get("hook_files")
    patterns = [item for item in configured if isinstance(item, str)] if isinstance(configured, list) else []
    for path in ctx.static.glob(patterns):
        text = ctx.static.read(path) or ""
        low = text.lower()
        if check_needles(text) or "sonar" in low or "ra1" in low:
            return passed(
                f"Maintainer-declared post-edit hook: {path} (acdc.hook_files).",
                [
                    ev("agent hook (config-declared)", source=path),
                    ev("acdc.hook_files", source=".agents/readiness/config.json"),
                ],
            )

    raw = ctx.static.read(".claude/settings.json")
    if raw:
        try:
            settings = json.loads(raw)
        except json.JSONDecodeError:
            settings = None
        hooks = settings.get("hooks") if isinstance(settings, dict) else None
        if isinstance(hooks, dict):
            for hook_key in ("PostToolUse", "Stop"):
                if hook_key in hooks and _hook_runs_check(hooks[hook_key]):
                    return passed(
                        f"Post-edit verification hook runs a check command: .claude/settings.json ({hook_key}).",
                        [ev("agent hook", source=".claude/settings.json")],
                    )

    return failed(
        "No machine-enforced post-edit verification hook (e.g. Claude Code PostToolUse/Stop hook running a check command, or files declared in acdc.hook_files); instruction files are advisory to the agent — hooks make inner-loop verification mechanical (AC/DC Verify stage)."
    )
