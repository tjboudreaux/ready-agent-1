"""Project-type detection with explicit confidence + a monorepo application inventory.

Design principle from the review: skipping is the easiest way to manufacture a high score,
so when signals are weak or conflicting we return ``unknown`` (low confidence) rather than
guessing a type. Type-dependent criteria then surface as ``unknown`` instead of being
silently skipped.
"""
from __future__ import annotations

import json
import re
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

PIN_SOURCE = ".agents/readiness/config.json"
VALID_PIN_TYPES = {"library", "service", "frontend", "cli", "data", "infra"}


def load_readiness_config(root, options=None) -> dict:
    """Read ``.agents/readiness/config.json`` as the readiness config root.

    An explicit ``options["readiness_config"]`` beats the on-disk file. Missing,
    malformed, unreadable, or non-object config returns ``{}``.
    """
    options = options or {}
    if options.get("readiness_config") is not None:
        data = options["readiness_config"]
    else:
        cf = Path(root) / ".agents" / "readiness" / "config.json"
        if not cf.exists():
            return {}
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return data if isinstance(data, dict) else {}


def load_detect_config(root, options=None) -> dict:
    """Read the nested ``detect`` block of readiness config (user pins).

    ``options["detect_config"]`` preserves the legacy override path for detection
    pins, while top-level readiness options continue to come from
    ``load_readiness_config``.
    """
    options = options or {}
    if options.get("detect_config") is not None:
        data = options["detect_config"]
    else:
        data = load_readiness_config(root, options)
    if not isinstance(data, dict):
        return {}
    detect_cfg = data.get("detect")
    return detect_cfg if isinstance(detect_cfg, dict) else {}


def _pin_app(app: App, pinned: str) -> None:
    app.runtime = pinned
    app.deploy_surface = pinned


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
    dirs |= set(_go_cmd_apps(root))
    dirs |= set(_maven_modules(root))
    dirs |= set(_gradle_modules(root, static))
    return sorted(d for d in dirs if not _ignored_app_dir(d))


def _has_manifest(path: Path) -> bool:
    for name in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml", "build.gradle"):
        if (path / name).exists():
            return True
    return False


# Directories that are never independently deployable apps even with a manifest.
_IGNORED_APP_PREFIXES = ("examples/", "example/", "vendor/", "third_party/", "third-party/",
                         "node_modules/", "testdata/", "fixtures/", "samples/", "docs/",
                         "test/", "tests/")


def _ignored_app_dir(rel: str) -> bool:
    return (rel.strip("/").lower() + "/").startswith(_IGNORED_APP_PREFIXES)


def _go_cmd_apps(root: Path) -> List[str]:
    """Go convention: each ``cmd/<name>`` with a ``.go`` file is an independent binary."""
    if not (root / "go.mod").exists():
        return []
    cmd = root / "cmd"
    if not cmd.is_dir():
        return []
    return [p.relative_to(root).as_posix() for p in sorted(cmd.iterdir())
            if p.is_dir() and any(p.glob("*.go"))]


def _maven_modules(root: Path) -> List[str]:
    pom = root / "pom.xml"
    if not pom.exists():
        return []
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(pom)
    except (ET.ParseError, OSError):
        return []
    out = []
    for el in tree.iter():
        if el.tag.split("}")[-1] == "module" and el.text and (root / el.text.strip()).is_dir():
            out.append(el.text.strip())
    return out


def _gradle_modules(root: Path, static: StaticCollector) -> List[str]:
    text = (static.read("settings.gradle") or "") + "\n" + (static.read("settings.gradle.kts") or "")
    out = []
    for m in re.finditer(r"include[\s(]+([^)\n]+)", text):
        for tok in re.findall(r"""["']([^"']+)["']""", m.group(1)):
            rel = tok.lstrip(":").replace(":", "/")
            if rel and (root / rel).is_dir():
                out.append(rel)
    return out


def _go_root_surface(static: StaticCollector) -> str:
    deps = static.declared_deps()
    return "service" if deps & {n.lower() for n in WEB_SERVICE_DEPS} else "cli"


def _build_app(root: Path, rel: str, root_static: StaticCollector = None) -> App:
    sub = StaticCollector(root / rel if rel != "." else root)
    surface, _conf, _sig = _classify(sub)
    # A Go cmd/* binary has no manifest of its own; classify it from the module's deps.
    if surface == "unknown" and rel != "." and (root / "go.mod").exists() and list((root / rel).glob("*.go")):
        surface = _go_root_surface(root_static or StaticCollector(root))
    langs = sub.languages() or (["go"] if list((root / rel).glob("*.go")) else [])
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


def detect(root, static: StaticCollector = None, options=None) -> Detection:
    root = Path(root)
    static = static or StaticCollector(root)

    readiness_cfg = load_readiness_config(root, options)
    cfg = load_detect_config(root, options)
    opt_in = {"loop_ready": readiness_cfg.get("loop_ready") is True}
    root_pin = cfg.get("project_type")
    app_pins = cfg.get("apps") if isinstance(cfg.get("apps"), dict) else {}

    ws = _workspace_dirs(root, static)
    has_mono_tooling = bool(static.exists_any(["turbo.json", "nx.json", "pnpm-workspace.yaml", "lerna.json", "go.work"]))
    is_monorepo = len(ws) > 1 or (has_mono_tooling and len(ws) >= 1)

    if is_monorepo:
        signals = []
        apps = []
        for rel in ws or ["."]:
            app = _build_app(root, rel, static)
            pinned = app_pins.get(rel)
            if pinned in VALID_PIN_TYPES:
                _pin_app(app, pinned)
                signals.append(f"app '{rel}' type pinned to '{pinned}' via {PIN_SOURCE}")
            elif pinned is not None:
                signals.append(f"ignored invalid type pin '{pinned}' for app '{rel}' in {PIN_SOURCE}")
            apps.append(app)
        languages = sorted({l for a in apps for l in a.languages})
        signals.insert(0, f"monorepo: {len(apps)} application(s) discovered")
        if has_mono_tooling:
            signals.append("monorepo tooling present")
        if root_pin is not None:
            signals.append(f"root project_type pin ignored for monorepo (use detect.apps in {PIN_SOURCE})")
        return Detection(
            project_type="monorepo-root",
            confidence=CONF_HIGH if apps else CONF_LOW,
            signals=signals,
            languages=languages,
            apps=apps,
            is_monorepo=True,
            opt_in=opt_in,
        )

    surface, conf, signals = _classify(static)
    app = _build_app(root, ".", static)
    if app.languages:
        signals.append("languages: " + ", ".join(app.languages))
    if root_pin in VALID_PIN_TYPES:
        surface, conf = root_pin, CONF_HIGH
        _pin_app(app, root_pin)
        signals.append(f"project_type pinned to '{root_pin}' via {PIN_SOURCE}")
    elif root_pin is not None:
        signals.append(f"ignored invalid project_type pin '{root_pin}' in {PIN_SOURCE}")
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
        opt_in=opt_in,
    )
