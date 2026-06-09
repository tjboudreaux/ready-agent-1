"""Dev Environment checks."""
from __future__ import annotations

from ._helpers import aglob, ev, failed, passed


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
