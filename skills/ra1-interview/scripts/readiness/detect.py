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


def _candidate(value: str, confidence: float, signal: str) -> dict:
    return {"type": value, "confidence": confidence, "signal": signal}


def classify_candidates(static: StaticCollector) -> list[dict]:
    """Every type this directory could be, strongest first.

    The list order IS the decision priority: :func:`_classify` takes the head. The tail is
    what the old code threw away — a directory with both Django and Next.js resolved to
    `service` and never said the frontend signal existed, so frontend-only criteria were
    skipped with no trace of the ambiguity. The gaps layer reads the tail to ask about it.
    """
    deps = static.declared_deps()
    manifests = static.manifests()
    out: list[dict] = []

    def dep_hit(names):
        return sorted(deps & {n.lower() for n in names})

    if static.glob(["*.tf", "**/*.tf", "main.tf"]) or static.exists_any(
            ["Pulumi.yaml", "cloudformation.yaml", "**/*.bicep"]):
        out.append(_candidate("infra", CONF_HIGH,
                              "IaC files (.tf/Pulumi/CloudFormation) present"))

    data, svc, fe = dep_hit(DATA_DEPS), dep_hit(WEB_SERVICE_DEPS), dep_hit(FRONTEND_DEPS)
    if data:
        out.append(_candidate("data", CONF_HIGH, f"data-pipeline deps: {', '.join(data)}"))
    if svc:
        out.append(_candidate("service", CONF_HIGH,
                              f"web/service framework deps: {', '.join(svc)}"))
    if fe:
        out.append(_candidate("frontend", CONF_HIGH,
                              f"frontend framework deps: {', '.join(fe)}"))

    pkg = manifests.get("package.json", (None, None))[1]
    if isinstance(pkg, dict) and pkg.get("bin"):
        out.append(_candidate("cli", CONF_MED, "package.json declares a bin entrypoint"))
    pyproject = manifests.get("pyproject.toml", (None, None))[1]
    if isinstance(pyproject, dict) and pyproject.get("project", {}).get("scripts"):
        out.append(_candidate("cli", CONF_MED, "pyproject declares console scripts"))

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
            out.append(_candidate("library", CONF_MED,
                                  "packaged library (manifest, no service/app entrypoint)"))
        if not out:
            out.append(_candidate("unknown", CONF_LOW, "manifest present but type ambiguous"))
    elif not out:
        out.append(_candidate("unknown", CONF_LOW, "no recognizable manifest"))
    return out


def _classify(static: StaticCollector) -> tuple[str, float, list[str]]:
    """Return (deploy_surface/project_type, confidence, signals) for a single app dir.

    The head of :func:`classify_candidates`. Only the winning signal is reported here so
    the detection output a score was computed from is unchanged by the candidate list.
    """
    top = classify_candidates(static)[0]
    return top["type"], top["confidence"], [top["signal"]]


def _workspace_dirs(root: Path, static: StaticCollector) -> list[str]:
    """Discover application subdirectories in a monorepo (best-effort, no YAML parsing)."""
    dirs: set = set()
    globs: list[str] = []
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
    for name in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml",
                 "pom.xml", "build.gradle"):
        if (path / name).exists():
            return True
    return False


# Directories that are never independently deployable apps even with a manifest.
_IGNORED_APP_PREFIXES = ("examples/", "example/", "vendor/", "third_party/", "third-party/",
                         "node_modules/", "testdata/", "fixtures/", "samples/", "docs/",
                         "test/", "tests/")


def _ignored_app_dir(rel: str) -> bool:
    return (rel.strip("/").lower() + "/").startswith(_IGNORED_APP_PREFIXES)


def _go_cmd_apps(root: Path) -> list[str]:
    """Go convention: each ``cmd/<name>`` with a ``.go`` file is an independent binary."""
    if not (root / "go.mod").exists():
        return []
    cmd = root / "cmd"
    if not cmd.is_dir():
        return []
    return [p.relative_to(root).as_posix() for p in sorted(cmd.iterdir())
            if p.is_dir() and any(p.glob("*.go"))]


# pom.xml is attacker-supplied (it comes from the scanned repository) and real module
# lists are tiny, so cap the read well below anything legitimate.
_POM_MAX_BYTES = 1 << 20


def _pom_tree(raw: bytes):
    """Parse an untrusted pom, refusing any DTD. Returns None if unusable.

    stdlib ElementTree expands internal entities, so a document with a DTD can trigger
    entity-expansion DoS ("billion laughs" / quadratic blowup). defusedxml is not an option
    -- engine/ is pure stdlib by contract -- so reject the doctype at the parser level.

    Scanning the bytes for ``<!DOCTYPE`` instead would be bypassable: a UTF-16 document
    contains no such ASCII substring, yet ElementTree honours the BOM/encoding declaration
    and would expand it anyway. Hooking the parser is encoding-agnostic.
    """
    import xml.etree.ElementTree as ET

    class _NoDoctype(ET.TreeBuilder):
        def doctype(self, name, pubid, system):
            raise ET.ParseError("doctype declarations are not accepted")

    try:
        # B314 asks for exactly the mitigation _NoDoctype provides: every DTD is rejected
        # at the parser level, which is encoding-agnostic (see docstring).
        parser = ET.XMLParser(target=_NoDoctype())  # nosec B314
        parser.feed(raw)
        return parser.close()
    except (ET.ParseError, ValueError, UnicodeDecodeError):
        return None


def _maven_modules(root: Path) -> list[str]:
    pom = root / "pom.xml"
    if not pom.exists():
        return []
    try:
        with pom.open("rb") as fh:
            # Bounded read: read_bytes() would allocate the entire attacker-sized file
            # before any cap could reject it. Reading one byte past the cap is enough to
            # detect oversize without ever holding it in memory.
            raw = fh.read(_POM_MAX_BYTES + 1)
    except OSError:
        return []
    if len(raw) > _POM_MAX_BYTES:
        return []
    tree = _pom_tree(raw)
    if tree is None:
        return []
    out = []
    root_abs = root.resolve()
    for el in tree.iter():
        if el.tag.split("}")[-1] != "module" or not el.text:
            continue
        rel = el.text.strip()
        if not rel:
            continue
        target = (root / rel)
        if not target.is_dir():
            continue
        # A module of "../../.." would otherwise make every downstream collector read
        # outside the scanned project and quote it back in the report. is_dir() already
        # succeeded above, so resolve() has an existing directory to work on.
        resolved = target.resolve()
        if resolved != root_abs and root_abs not in resolved.parents:
            continue
        out.append(rel)
    return out


def _gradle_modules(root: Path, static: StaticCollector) -> list[str]:
    text = ((static.read("settings.gradle") or "") + "\n"
            + (static.read("settings.gradle.kts") or ""))
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
    candidates = classify_candidates(sub)
    surface, conf = candidates[0]["type"], candidates[0]["confidence"]
    # A Go cmd/* binary has no manifest of its own; classify it from the module's deps.
    if (surface == "unknown" and rel != "." and (root / "go.mod").exists()
            and list((root / rel).glob("*.go"))):
        surface = _go_root_surface(root_static or StaticCollector(root))
        conf = CONF_MED
        candidates = [_candidate(surface, CONF_MED,
                                 "go cmd/ binary classified from module dependencies")]
    langs = sub.languages() or (["go"] if list((root / rel).glob("*.go")) else [])
    test_cmd = _detect_test_cmd(sub)
    prod = "unknown"
    if surface in ("service", "frontend"):
        if sub.exists_any(["Dockerfile", "**/Dockerfile", "Procfile", "fly.toml",
                           "vercel.json", "k8s/**", "helm/**"]):
            prod = True
    return App(
        path=rel,
        languages=langs,
        runtime=surface,
        deploy_surface=surface,
        prod_facing=prod,
        test_cmd=test_cmd,
        type_confidence=conf,
        type_candidates=candidates,
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
    has_mono_tooling = bool(static.exists_any(
        ["turbo.json", "nx.json", "pnpm-workspace.yaml", "lerna.json", "go.work"]))
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
                signals.append(
                    f"ignored invalid type pin '{pinned}' for app '{rel}' in {PIN_SOURCE}")
            apps.append(app)
        languages = sorted({lang for a in apps for lang in a.languages})
        signals.insert(0, f"monorepo: {len(apps)} application(s) discovered")
        if has_mono_tooling:
            signals.append("monorepo tooling present")
        if root_pin is not None:
            signals.append(
                f"root project_type pin ignored for monorepo (use detect.apps in {PIN_SOURCE})")
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
        signals.append(
            "confidence below threshold -> type=unknown (criteria will not be silently skipped)")
    return Detection(
        project_type=project_type,
        confidence=conf,
        signals=signals,
        languages=app.languages,
        apps=[app],
        is_monorepo=False,
        opt_in=opt_in,
        # What the scanner considered, pin or no pin: the gaps layer needs the inference to
        # explain a contested classification, and a pin is recorded in `signals` above.
        candidates=app.type_candidates,
    )
