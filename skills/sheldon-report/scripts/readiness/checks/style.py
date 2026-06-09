"""Style & Validation checks."""
from __future__ import annotations

from .. import parsers
from ._helpers import adep, aglob, atool, ev, failed, passed

_TYPED_LANGS = ("go", "rust", "java", "swift")


def linter_config(ctx):
    files = aglob(ctx, [".eslintrc*", "eslint.config.*", "ruff.toml", ".ruff.toml",
                        "biome.json", "biome.jsonc", ".flake8", ".pylintrc", "pylintrc",
                        ".golangci.yml", ".golangci.yaml"])
    if files:
        return passed(f"Linter config present: {files[0]}", [ev(f"linter config {files[0]}", source=files[0])])
    if atool(ctx, "ruff") or atool(ctx, "flake8") or atool(ctx, "pylint"):
        return passed("Linter configured via pyproject [tool.*].", [ev("pyproject linter tool config")])
    dep = adep(ctx, ["eslint", "ruff", "flake8", "pylint", "biome", "golangci-lint"])
    if dep:
        return passed(f"Linter dependency declared: {dep}", [ev(f"dependency {dep}")])
    return failed("No linter config (eslint/ruff/flake8/pylint/biome).")


def formatter(ctx):
    files = aglob(ctx, [".prettierrc*", "prettier.config.*", ".clang-format", "rustfmt.toml", ".rustfmt.toml"])
    if files:
        return passed(f"Formatter config: {files[0]}", [ev("formatter config", source=files[0])])
    if atool(ctx, "black") or atool(ctx, "ruff"):
        return passed("Formatter via pyproject [tool.black]/[tool.ruff].")
    dep = adep(ctx, ["prettier", "black"])
    if dep:
        return passed(f"Formatter dependency: {dep}")
    if any(l in ctx.app.languages for l in ("go", "rust")):
        return passed("Standard formatter (gofmt/rustfmt) available for language.")
    return failed("No formatter (prettier/black/ruff-format).")


def type_check(ctx):
    if any(l in ctx.app.languages for l in _TYPED_LANGS):
        return passed("Statically-typed language.")
    files = aglob(ctx, ["tsconfig.json", "tsconfig.*.json", "mypy.ini", ".mypy.ini", "pyrightconfig.json"])
    if files:
        return passed(f"Type checker config: {files[0]}", [ev("type config", source=files[0])])
    if atool(ctx, "mypy") or atool(ctx, "pyright"):
        return passed("Type checker via pyproject [tool.mypy]/[tool.pyright].")
    dep = adep(ctx, ["typescript", "mypy", "pyright", "pyre-check"])
    if dep:
        return passed(f"Type checker dependency: {dep}")
    return failed("No type checker (tsconfig/mypy/pyright).")


def _read_first(ctx, name):
    for base in (ctx.app_static(), ctx.static):
        hit = base.glob([name])
        if hit:
            return base.root / hit[0], hit[0]
    return None, None


def strict_typing(ctx):
    if any(l in ctx.app.languages for l in _TYPED_LANGS):
        return passed("Statically-typed language (strict by default).")
    path, rel = _read_first(ctx, "tsconfig.json")
    if path is not None:
        cfg = parsers.load_jsonc(path) or {}
        if (cfg.get("compilerOptions", {}) or {}).get("strict") is True:
            return passed("tsconfig has strict:true.", [ev("tsconfig strict", source=rel)])
        return failed("tsconfig present but strict mode not enabled.")
    if atool(ctx, "mypy"):
        py = parsers.load_toml(ctx.app_static().root / "pyproject.toml") or {}
        if (py.get("tool", {}).get("mypy", {}) or {}).get("strict") is True:
            return passed("mypy strict enabled.")
        return failed("mypy present but strict not enabled.")
    return failed("No strict typing configuration.")


def precommit_hooks(ctx):
    files = ctx.static.glob([".pre-commit-config.yaml", ".pre-commit-config.yml",
                             "lefthook.yml", "lefthook.yaml", ".husky/*"])
    if files:
        return passed(f"Pre-commit hooks: {files[0]}", [ev("pre-commit hooks", source=files[0])])
    pkg = ctx.static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and "lint-staged" in pkg:
        return passed("lint-staged configured in package.json.")
    return failed("No pre-commit hooks (.pre-commit-config/husky/lefthook).")
