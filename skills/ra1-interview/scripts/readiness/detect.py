"""Project-type detection with explicit confidence + a monorepo application inventory.

Design principle from the review: skipping is the easiest way to manufacture a high score,
so when signals are weak or conflicting we return ``unknown`` (low confidence) rather than
guessing a type. Type-dependent criteria then surface as ``unknown`` instead of being
silently skipped.
"""
from __future__ import annotations

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

PIN_SOURCE = ".ra1/config.json"
WAIVERS_SOURCE = ".ra1/waivers.json"
LEGACY_POLICY_FILES = (".agents/readiness/config.json", ".agents/readiness/waivers.json")
VALID_PIN_TYPES = {"library", "service", "frontend", "cli", "data", "infra"}


def read_policy_json(static: StaticCollector, relpath: str):
    """Read one ``.ra1`` policy file through the safe boundary.

    Returns ``("ok", data)`` | ``("missing", None)`` | ``("invalid", None)``. Missing keeps
    absence semantics; malformed/unsafe/unreadable/oversize is ``invalid`` and the caller
    marks the global repository-indeterminate state (never a partial read).
    """
    from . import safe_io
    obs = static.read_repo_file(relpath, max_bytes=safe_io.MAX_CONFIG_BYTES)
    if obs.state is safe_io.RepoReadState.MISSING:
        return ("missing", None)
    if obs.state is not safe_io.RepoReadState.OK:
        return ("invalid", None)
    from . import parsers
    try:
        data = parsers.strict_load_json(obs.text, max_bytes=safe_io.MAX_CONFIG_BYTES)
    except parsers.StrictJsonError:
        return ("invalid", None)
    return ("ok", data)


def _static_for(root, static: StaticCollector | None) -> StaticCollector:
    if static is not None:
        return static
    if hasattr(root, "read_repo_file"):
        return root
    return StaticCollector(root)


def legacy_policy_present(static: StaticCollector) -> bool:
    """True when a legacy ``.agents/readiness`` policy file exists (blocks scoring)."""
    from . import safe_io
    obs = static.exists_observation(list(LEGACY_POLICY_FILES))
    return obs.state is safe_io.PresenceState.PRESENT


def load_readiness_config(root, options=None) -> dict:
    """Read ``.ra1/config.json`` as the readiness config root.

    An explicit injected ``readiness_config`` dependency beats the on-disk file. Missing,
    malformed, unreadable, or non-object config returns ``{}`` (the malformed case is
    surfaced separately through the repository-indeterminate state).
    """
    options = options or {}
    deps = options.get("_deps") or {}
    if deps.get("readiness_config") is not None:
        data = deps["readiness_config"]
        return data if isinstance(data, dict) else {}
    static = _static_for(root, None)
    state, data = read_policy_json(static, PIN_SOURCE)
    if state != "ok" or not isinstance(data, dict):
        return {}
    return data


def load_detect_config(root, options=None) -> dict:
    """Read the nested ``detect`` block of readiness config (user pins).

    ``detect_config`` is the injected dependency channel for detection pins, while
    top-level readiness options continue to come from ``load_readiness_config``.
    """
    options = options or {}
    deps = options.get("_deps") or {}
    if deps.get("detect_config") is not None:
        data = deps["detect_config"]
    else:
        data = load_readiness_config(root, options)
    if not isinstance(data, dict):
        return {}
    detect_cfg = data.get("detect")
    return detect_cfg if isinstance(detect_cfg, dict) else {}


def _pin_app(app: App, pinned) -> None:
    """Apply a type pin: one surface, or several for a fullstack directory.

    `deploy_surface` keeps the first declared surface so display and prod-facing heuristics
    stay single-valued; `surfaces` carries the full set that applicability is judged against.
    """
    surfaces = _pin_surfaces(pinned)
    app.runtime = surfaces[0]
    app.deploy_surface = surfaces[0]
    app.surfaces = surfaces if len(surfaces) > 1 else []


def _pin_surfaces(pinned) -> list:
    """Valid surfaces from a pin value (string or list), in declared order, deduplicated."""
    values = [pinned] if isinstance(pinned, str) else list(pinned or [])
    out = []
    for value in values:
        if value in VALID_PIN_TYPES and value not in out:
            out.append(value)
    return out


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


_APP_MANIFEST_NAMES = ("package.json", "pyproject.toml", "go.mod", "Cargo.toml",
                       "pom.xml", "build.gradle")


def _validated_workspace_patterns(raw_globs) -> list[str]:
    """Validate repository-derived workspace globs against the config-pattern grammar.

    Workspace patterns are the one repository-supplied discovery input; invalid grammar or
    overflow is repository-derived invalidity and raises (the caller surfaces the global
    indeterminate state rather than silently narrowing discovery).
    """
    from . import safe_io
    globs = []
    for raw in raw_globs:
        if not isinstance(raw, str):
            raise safe_io.RepositoryInputError("workspace pattern must be a string")
        g = raw.rstrip("/")
        if not g:
            continue
        if safe_io.validate_discovery_pattern(g) is not None:
            raise safe_io.RepositoryInputError(f"invalid workspace pattern: {g!r}")
        if g not in globs:
            globs.append(g)
    if len(globs) > safe_io.MAX_CONFIG_PATTERNS:
        raise safe_io.RepositoryInputError("workspace pattern cap exceeded")
    return globs


def _workspace_dirs(root: Path, static: StaticCollector) -> list[str]:
    """Discover application subdirectories in a monorepo (best-effort, no YAML parsing)."""
    dirs: set = set()
    raw_globs: list[str] = []
    pkg = static.manifests().get("package.json", (None, None))[1]
    if isinstance(pkg, dict):
        ws = pkg.get("workspaces")
        if isinstance(ws, list):
            raw_globs.extend(ws)
        elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
            raw_globs.extend(ws["packages"])
    # Tooling that implies a monorepo but where we glob conventional dirs.
    if static.exists_any(["pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json", "go.work"]):
        raw_globs.extend(["packages/*", "apps/*", "services/*"])
    # Cargo workspace members
    cargo = static.manifests().get("Cargo.toml", (None, None))[1]
    if isinstance(cargo, dict) and isinstance(cargo.get("workspace"), dict):
        raw_globs.extend(cargo["workspace"].get("members", []) or [])
    globs = _validated_workspace_patterns(raw_globs)
    if globs:
        patterns = [f"{g}/{name}" for g in globs for name in _APP_MANIFEST_NAMES]
        for path in static.glob(patterns):
            dirs.add(path.rsplit("/", 1)[0])
    dirs |= set(_go_cmd_apps(root, static))
    dirs |= set(_maven_modules(root, static))
    dirs |= set(_gradle_modules(root, static))
    return sorted(d for d in dirs if not _ignored_app_dir(d))


# Directories that are never independently deployable apps even with a manifest.
_IGNORED_APP_PREFIXES = ("examples/", "example/", "vendor/", "third_party/", "third-party/",
                         "node_modules/", "testdata/", "fixtures/", "samples/", "docs/",
                         "test/", "tests/")


def _ignored_app_dir(rel: str) -> bool:
    return (rel.strip("/").lower() + "/").startswith(_IGNORED_APP_PREFIXES)


def _go_cmd_apps(root: Path, static: StaticCollector) -> list[str]:
    """Go convention: each ``cmd/<name>`` with a ``.go`` file is an independent binary."""
    if not static.exists_any(["go.mod"]):
        return []
    hits = static.glob(["cmd/*/*.go"])
    return sorted({"/".join(h.split("/")[:2]) for h in hits})


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


def _maven_modules(root: Path, static: StaticCollector) -> list[str]:
    from . import safe_io
    obs = static.read_repo_file("pom.xml", max_bytes=_POM_MAX_BYTES)
    if obs.state is safe_io.RepoReadState.MISSING:
        return []
    if obs.state is not safe_io.RepoReadState.OK:
        raise safe_io.RepositoryInputError("pom.xml unreadable")
    tree = _pom_tree(obs.text.encode("utf-8"))
    if tree is None:
        raise safe_io.RepositoryInputError("pom.xml malformed")
    out = []
    for el in tree.iter():
        if el.tag.split("}")[-1] != "module" or not el.text:
            continue
        rel = el.text.strip()
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            continue
        # A module of "../../.." can never make a collector read outside the scanned
        # project: the directory is opened fd-relative beneath the admitted root, which
        # rejects traversal, links, and escape attempts.
        try:
            sub = safe_io.open_subroot(static.authority, rel)
            sub.close()
        except (OSError, safe_io.RepositoryInputError):
            continue
        out.append(rel)
    return out


def _gradle_modules(root: Path, static: StaticCollector) -> list[str]:
    from . import safe_io
    text = ((static.read("settings.gradle") or "") + "\n"
            + (static.read("settings.gradle.kts") or ""))
    out = []
    for m in re.finditer(r"include[\s(]+([^)\n]+)", text):
        for tok in re.findall(r"""["']([^"']+)["']""", m.group(1)):
            rel = tok.lstrip(":").replace(":", "/")
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                continue
            try:
                sub = safe_io.open_subroot(static.authority, rel)
                sub.close()
            except (OSError, safe_io.RepositoryInputError):
                continue
            out.append(rel)
    return out


def _go_root_surface(static: StaticCollector) -> str:
    deps = static.declared_deps()
    return "service" if deps & {n.lower() for n in WEB_SERVICE_DEPS} else "cli"


def _build_app(root: Path, rel: str, root_static: StaticCollector = None) -> App:
    if root_static is not None:
        sub = root_static if rel == "." else root_static.within(rel)
    else:
        sub = StaticCollector(root if rel == "." else root / rel)
    candidates = classify_candidates(sub)
    surface, conf = candidates[0]["type"], candidates[0]["confidence"]
    has_go_mod = (root_static or sub).exists_any(["go.mod"])
    has_go_files = bool(sub.glob(["*.go"]))
    # A Go cmd/* binary has no manifest of its own; classify it from the module's deps.
    if surface == "unknown" and rel != "." and has_go_mod and has_go_files:
        surface = _go_root_surface(root_static or StaticCollector(root))
        conf = CONF_MED
        candidates = [_candidate(surface, CONF_MED,
                                 "go cmd/ binary classified from module dependencies")]
    langs = sub.languages() or (["go"] if has_go_files else [])
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


def _indeterminate_detection(reason: str) -> Detection:
    """The global degraded-input detection: authored unknown values, blocking denominator."""
    if reason == "input.legacy_policy_path":
        signal = ("legacy .agents/readiness policy file present; move it to .ra1/ before "
                  "scoring")
    else:
        signal = ("repository configuration or manifest input could not be read safely; "
                  "classification unavailable")
    return Detection(
        project_type="unknown",
        confidence=0.0,
        signals=[signal],
        languages=[],
        apps=[App(path=".")],
        is_monorepo=False,
        opt_in={"loop_ready": False},
        repository_indeterminate=True,
        indeterminate_reason=reason,
    )


def detect(root, static: StaticCollector = None, options=None) -> Detection:
    root = Path(root)
    static = static or StaticCollector(root)
    from . import safe_io
    try:
        return _detect_inner(root, static, options)
    except safe_io.RepositoryInputError:
        return _indeterminate_detection("input.repository_indeterminate")


def _detect_inner(root, static: StaticCollector, options=None) -> Detection:
    if legacy_policy_present(static):
        return _indeterminate_detection("input.legacy_policy_path")
    config_state, _raw_config = read_policy_json(static, PIN_SOURCE)
    if config_state == "invalid":
        return _indeterminate_detection("input.repository_indeterminate")

    readiness_cfg = load_readiness_config(static, options)
    cfg = load_detect_config(static, options)
    opt_in = {"loop_ready": readiness_cfg.get("loop_ready") is True}
    root_pin = cfg.get("project_type")
    # A directory can serve several surfaces; `surfaces` is the richer input and wins.
    surfaces_pin = _pin_surfaces(cfg.get("surfaces")) if cfg.get("surfaces") else []
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
            valid = _pin_surfaces(pinned)
            if valid:
                _pin_app(app, valid)
                signals.append(
                    f"app '{rel}' surfaces pinned to {valid} via {PIN_SOURCE}")
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
    if surfaces_pin:
        surface, conf = surfaces_pin[0], CONF_HIGH
        _pin_app(app, surfaces_pin)
        signals.append(f"surfaces pinned to {surfaces_pin} via {PIN_SOURCE}")
    elif root_pin in VALID_PIN_TYPES:
        surface, conf = root_pin, CONF_HIGH
        _pin_app(app, root_pin)
        signals.append(f"project_type pinned to '{root_pin}' via {PIN_SOURCE}")
    elif root_pin is not None:
        signals.append(f"ignored invalid project_type pin '{root_pin}' in {PIN_SOURCE}")
    if cfg.get("surfaces") and not surfaces_pin:
        signals.append(f"ignored invalid surfaces pin '{cfg['surfaces']}' in {PIN_SOURCE}")
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
        # Declared multi-surface sets reach repository-scope applicability through this, so
        # the order a developer wrote them in cannot change a score.
        surfaces=list(app.surfaces),
    )
