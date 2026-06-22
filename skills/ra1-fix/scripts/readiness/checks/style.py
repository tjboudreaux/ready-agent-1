"""Style & Validation checks."""
from __future__ import annotations

from .. import parsers
from ._helpers import adep, aglob, atool, cfg_texts, ev, failed, passed, tool_invoked

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


# --- code-health detectors (Factory-parity Style depth; advisory, T0) ----------------
# Each guards undeserved credit: a capable linter installed is not enough — the rule,
# budget, or scan must be configured or actually wired into CI/pre-commit/scripts.

_LINT_CFG = [".eslintrc*", "eslint.config.*", "ruff.toml", ".ruff.toml", ".pylintrc",
             "pylintrc", ".flake8", "tox.ini", "biome.json", "biome.jsonc",
             "pyproject.toml", "setup.cfg", ".golangci.yml", ".golangci.yaml"]


def _codes_from(table):
    out = set()
    if not isinstance(table, dict):
        return out
    for key in ("select", "extend-select"):
        v = table.get(key)
        if isinstance(v, list):
            out.update(str(c) for c in v)
    return out


def _ruff_select(ctx):
    """Ruff rule-code prefixes selected in pyproject [tool.ruff(.lint)] or ruff.toml."""
    codes = set()
    for base in {ctx.app_static().root, ctx.static.root}:
        py = parsers.load_toml(base / "pyproject.toml") or {}
        ruff = (py.get("tool", {}) or {}).get("ruff", {}) or {}
        codes |= _codes_from(ruff) | _codes_from(ruff.get("lint"))
        for name in ("ruff.toml", ".ruff.toml"):
            rt = parsers.load_toml(base / name) or {}
            codes |= _codes_from(rt) | _codes_from(rt.get("lint"))
    return codes


def naming_convention_rule(ctx):
    blob = "\n".join(cfg_texts(ctx, _LINT_CFG)).lower()
    if any(t in blob for t in ("naming-convention", "filename-case", "pep8-naming", "naming-style")):
        return passed("Naming-convention lint rule configured.", [ev("naming rule")])
    if any(c.startswith("N") for c in _ruff_select(ctx)):
        return passed("Ruff pep8-naming (N) rules enabled.", [ev("ruff N rules")])
    return failed("No enforced naming-convention rule (eslint naming-convention / ruff N / pylint naming).")


def complexity_budget(ctx):
    blob = "\n".join(cfg_texts(ctx, _LINT_CFG)).lower()
    if any(t in blob for t in ("max-complexity", "max_complexity", "cognitive-complexity", '"complexity"')):
        return passed("Cyclomatic-complexity budget configured.", [ev("complexity rule")])
    if any(c.startswith("C9") for c in _ruff_select(ctx)):
        return passed("Ruff mccabe (C90) complexity rules enabled.", [ev("ruff C90 rules")])
    return failed("No complexity budget (eslint complexity / ruff C90 / flake8 max-complexity).")


_DEADCODE = ["knip", "ts-prune", "vulture", "deptry", "unimport", "ts-unused-exports"]
_DEADCODE_CFG = ["knip.json", "knip.jsonc", ".knip.json", ".vulture", "vulture.ini"]


def dead_code_detection(ctx):
    cfg = aglob(ctx, _DEADCODE_CFG)
    tool = adep(ctx, _DEADCODE) or (cfg[0] if cfg else None)
    if not tool:
        return failed("No dead-code detector (knip/ts-prune/vulture/deptry).")
    wiring = tool_invoked(ctx, _DEADCODE)
    if wiring:
        return passed(f"Dead-code detection wired: {tool}.",
                      [ev("dead-code tool", source=str(tool)), ev("invocation", source=wiring)])
    return failed(f"Dead-code detector present ({tool}) but not wired into CI/pre-commit/scripts.")


_DUP = ["jscpd", "@jscpd/core", "pmd"]
_DUP_CFG = [".jscpd.json", "jscpd.json", ".jscpd.config.json"]


def duplicate_code_detection(ctx):
    cfg = aglob(ctx, _DUP_CFG)
    tool = adep(ctx, _DUP) or (cfg[0] if cfg else None)
    if not tool:
        return failed("No duplicate-code detector (jscpd/pmd-cpd/sonar).")
    wiring = tool_invoked(ctx, ["jscpd", "cpd"])
    if wiring:
        return passed(f"Duplicate-code detection wired: {tool}.",
                      [ev("dup-code tool", source=str(tool)), ev("invocation", source=wiring)])
    return failed(f"Duplicate-code detector present ({tool}) but not wired into CI/pre-commit/scripts.")


def large_file_guard(ctx):
    for f in ctx.static.glob([".pre-commit-config.yaml", ".pre-commit-config.yml"]):
        if "check-added-large-files" in (ctx.static.read(f) or ""):
            return passed("Large-file guard via pre-commit check-added-large-files.",
                          [ev("pre-commit large-file hook", source=f)])
    if "filter=lfs" in (ctx.static.read(".gitattributes") or ""):
        return passed("Large binaries tracked via Git LFS.", [ev(".gitattributes lfs", source=".gitattributes")])
    if "max-lines" in "\n".join(cfg_texts(ctx, _LINT_CFG)).lower():
        return passed("Max-lines lint rule configured.", [ev("max-lines rule")])
    wiring = tool_invoked(ctx, ["check-added-large-files", "git-sizer"])
    if wiring:
        return passed("Large-file check wired into CI.", [ev("CI large-file check", source=wiring)])
    return failed("No large-file guard (pre-commit large-files hook / Git LFS / max-lines rule).")


_DEBT_DOCS = ["TECH_DEBT.md", "docs/TECH_DEBT.md", "TECHNICAL_DEBT.md", "docs/tech-debt.md"]


def tech_debt_tracking(ctx):
    if ctx.static.glob(_DEBT_DOCS):
        return passed("Tech-debt register present.", [ev("tech-debt doc")])
    if "no-warning-comments" in "\n".join(cfg_texts(ctx, _LINT_CFG)).lower():
        return passed("TODO/FIXME lint rule configured (no-warning-comments).", [ev("no-warning-comments rule")])
    wiring = tool_invoked(ctx, ["todocheck", "leasot", "todo-to-issue", "no-warning-comments"])
    if wiring:
        return passed("Tech-debt scanner wired into CI.", [ev("CI tech-debt scan", source=wiring)])
    return failed("No tech-debt tracking (debt register / TODO scanner / no-warning-comments rule).")
