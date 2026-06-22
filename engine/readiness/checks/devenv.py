"""Dev Environment checks."""
from __future__ import annotations

from ._helpers import aglob, ev, failed, passed, skipped


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
