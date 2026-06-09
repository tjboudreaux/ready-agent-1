"""Project-type detection with explicit confidence + a monorepo application inventory.

Design principle from the review: skipping is the easiest way to manufacture a high score,
so when signals are weak or conflicting we return ``unknown`` (low confidence) rather than
guessing a type. Type-dependent criteria then surface as ``unknown`` instead of being
silently skipped.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from .collectors.static import StaticCollector
from .model import App, Detection

WEB_SERVICE_DEPS = {
    "express", "fastapi", "flask", "django", "koa", "hapi", "nestjs", "@nestjs/core",
    "gin-gonic/gin", "actix-web", "rails", "sinatra", "spring-boot", "starlette",
    "uvicorn", "gunicorn", "fastify",
}
FRONTEND_DEPS = {
    "react", "react-dom", "vue", "next", "nuxt", "svelte", "@sveltejs/kit",
    "@angular/core", "vite", "solid-js", "astro",
}
CLI_DEPS = {"click", "typer", "argparse", "commander", "yargs", "cobra", "clap"}
DATA_DEPS = {"airflow", "apache-airflow", "dbt-core", "dagster", "prefect", "luigi", "kedro"}

CONF_HIGH = 0.9
CONF_MED = 0.6
CONF_LOW = 0.3
UNKNOWN_THRESHOLD = 0.5


def _classify(static: StaticCollector) -> Tuple[str, float, List[str]]:
    """Return (deploy_surface/project_type, confidence, signals) for a single app dir."""
    signals: List[str] = []
    deps = static.declared_deps()
    manifests = static.manifests()

    def dep_hit(names):
        return sorted(deps & {n.lower() for n in names})

    # Infra as code
    if static.glob(["*.tf", "**/*.tf", "main.tf"]) or static.exists_any(["Pulumi.yaml", "cloudformation.yaml", "**/*.bicep"]):
        signals.append("IaC files (.tf/Pulumi/CloudFormation) present")
        return "infra", CONF_HIGH, signals

    svc = dep_hit(WEB_SERVICE_DEPS)
    fe = dep_hit(FRONTEND_DEPS)
    data = dep_hit(DATA_DEPS)

    if data:
        signals.append(f"data-pipeline deps: {', '.join(data)}")
        return "data", CONF_HIGH, signals
    if svc:
        signals.append(f"web/service framework deps: {', '.join(svc)}")
        return "service", CONF_HIGH, signals
    if fe:
        signals.append(f"frontend framework deps: {', '.join(fe)}")
        return "frontend", CONF_HIGH, signals

    # CLI: declared entrypoints
    pkg = manifests.get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and pkg.get("bin"):
        signals.append("package.json declares a bin entrypoint")
        return "cli", CONF_MED, signals
    pyproject = manifests.get("pyproject.toml", (None, None))[1]
    if isinstance(pyproject, dict):
        if pyproject.get("project", {}).get("scripts"):
            signals.append("pyproject declares console scripts")
            return "cli", CONF_MED, signals

    # Library: packaged, importable, no app entrypoint / no server dep
    if manifests:
        is_lib = False
        if isinstance(pkg, dict) and (pkg.get("main") or pkg.get("exports") or pkg.get("module")):
            is_lib = True
        if isinstance(pyproject, dict) and (
            pyproject.get("project", {}).get("name") or pyproject.get("tool", {}).get("poetry")
        ):
            is_lib = True
        if static.exists_any(["Cargo.toml", "go.mod", "setup.py", "setup.cfg"]):
            is_lib = True
        if is_lib:
            signals.append("packaged library (manifest, no service/app entrypoint)")
            return "library", CONF_MED, signals
        signals.append("manifest present but type ambiguous")
        return "unknown", CONF_LOW, signals

    signals.append("no recognizable manifest")
    return "unknown", CONF_LOW, signals


def _workspace_dirs(root: Path, static: StaticCollector) -> List[str]:
    """Discover application subdirectories in a monorepo (best-effort, no YAML parsing)."""
    dirs: set = set()
    globs: List[str] = []
    pkg = static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict):
        ws = pkg.get("workspaces")
        if isinstance(ws, list):
            globs.extend(ws)
        elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
            globs.extend(ws["packages"])
    # Tooling that implies a monorepo but where we glob conventional dirs.
    if static.exists_any(["pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json", "go.work"]):
        globs.extend(["packages/*", "apps/*", "services/*"])
    for g in globs:
        g = g.rstrip("/")
        for p in root.glob(g):
            if p.is_dir() and _has_manifest(p):
                dirs.add(p.relative_to(root).as_posix())
    # Cargo workspace members
    cargo = static.manifests().get("Cargo.toml", (None, None))[1]
    if isinstance(cargo, dict) and isinstance(cargo.get("workspace"), dict):
        for member in cargo["workspace"].get("members", []) or []:
            for p in root.glob(member):
                if p.is_dir() and _has_manifest(p):
                    dirs.add(p.relative_to(root).as_posix())
    return sorted(dirs)


def _has_manifest(path: Path) -> bool:
    for name in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml", "build.gradle"):
        if (path / name).exists():
            return True
    return False


def _build_app(root: Path, rel: str) -> App:
    sub = StaticCollector(root / rel if rel != "." else root)
    surface, _conf, _sig = _classify(sub)
    langs = sub.languages()
    test_cmd = _detect_test_cmd(sub)
    prod = "unknown"
    if surface in ("service", "frontend"):
        if sub.exists_any(["Dockerfile", "**/Dockerfile", "Procfile", "fly.toml", "vercel.json", "k8s/**", "helm/**"]):
            prod = True
    return App(
        path=rel,
        languages=langs,
        runtime=surface,
        deploy_surface=surface,
        prod_facing=prod,
        test_cmd=test_cmd,
    )


def _detect_test_cmd(static: StaticCollector) -> str:
    pkg = static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict):
        scripts = pkg.get("scripts", {})
        if isinstance(scripts, dict) and scripts.get("test"):
            return "npm test"
    if static.has_dep("pytest") or static.has_tool_config("pytest"):
        return "pytest"
    if static.exists_any(["go.mod"]):
        return "go test ./..."
    if static.exists_any(["Cargo.toml"]):
        return "cargo test"
    return ""


def detect(root, static: StaticCollector = None) -> Detection:
    root = Path(root)
    static = static or StaticCollector(root)

    ws = _workspace_dirs(root, static)
    has_mono_tooling = bool(static.exists_any(["turbo.json", "nx.json", "pnpm-workspace.yaml", "lerna.json", "go.work"]))
    is_monorepo = len(ws) > 1 or (has_mono_tooling and len(ws) >= 1)

    if is_monorepo:
        apps = [_build_app(root, rel) for rel in ws] or [_build_app(root, ".")]
        languages = sorted({l for a in apps for l in a.languages})
        signals = [f"monorepo: {len(apps)} application(s) discovered"]
        if has_mono_tooling:
            signals.append("monorepo tooling present")
        return Detection(
            project_type="monorepo-root",
            confidence=CONF_HIGH if apps else CONF_LOW,
            signals=signals,
            languages=languages,
            apps=apps,
            is_monorepo=True,
        )

    surface, conf, signals = _classify(static)
    app = _build_app(root, ".")
    project_type = surface if conf >= UNKNOWN_THRESHOLD else "unknown"
    if conf < UNKNOWN_THRESHOLD:
        signals.append("confidence below threshold -> type=unknown (criteria will not be silently skipped)")
    return Detection(
        project_type=project_type,
        confidence=conf,
        signals=signals,
        languages=app.languages,
        apps=[app],
        is_monorepo=False,
    )
