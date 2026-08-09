"""Conservative, indentation-aware parser for `.github/workflows/*.yml|yaml` (pure stdlib).

This is deliberately *not* a YAML parser. It recognizes exactly the workflow structure the
supply-chain-provenance and artifact-publication checks need — workflow/job permission
maps, jobs, job-level reusable-workflow ``uses``, steps, step-level ``uses``/``with``,
literal ``if``, ``continue-on-error``, and job ``with``/``secrets`` — and reports
unsupported constructs that could hide a qualifying signal as *indeterminate* rather than
guessing. Raw values are never emitted: parsed candidates carry only canonical
presence/state fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# --------------------------------------------------------------------------- scalar model
class Unsupported:
    """Marker for YAML constructs this parser deliberately does not support."""

    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<unsupported: {self.reason}>"


def _is_expression(value) -> bool:
    return isinstance(value, str) and value.startswith("${{")


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment outside single/double quotes."""
    out = []
    i, n = 0, len(line)
    quote = ""
    while i < n:
        c = line[i]
        if quote:
            out.append(c)
            if c == quote:
                if quote == "'" and i + 1 < n and line[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "#" and (i == 0 or line[i - 1] in (" ", "\t")):
            break
        out.append(c)
        i += 1
    return "".join(out)


def _parse_scalar(text: str):
    """Parse one scalar token; returns str/bool/None-intent or Unsupported."""
    t = text.strip()
    if not t:
        return ""
    if t.startswith("${{"):
        return t  # expression: dynamic, non-empty wiring
    if t.startswith("|") or t.startswith(">"):
        return Unsupported("block scalar header mishandled")
    if t.startswith(("[", "{")):
        return _parse_flow(t)
    if t.startswith(("&", "*", "!")) or t == "<<":
        return Unsupported("anchor/alias/tag")
    if (t.startswith("'") and not t.endswith("'")) or (t.startswith('"')
                                                       and not t.endswith('"')):
        return Unsupported("unterminated quoted scalar")
    if t.startswith("'") and t.endswith("'") and len(t) >= 2:
        return t[1:-1].replace("''", "'")
    if t.startswith('"') and t.endswith('"') and len(t) >= 2:
        return t[1:-1]
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    return t


def _parse_flow(t: str):
    """Single-line flow list ``[a, b]`` or flow map ``{k: v}`` of plain scalars."""
    if t.startswith("["):
        if not t.endswith("]"):
            return Unsupported("multiline flow list")
        inner = t[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_flow(inner)]
    if t.startswith("{"):
        if not t.endswith("}"):
            return Unsupported("multiline flow map")
        inner = t[1:-1].strip()
        if not inner:
            return {}
        out = {}
        for part in _split_flow(inner):
            if ":" not in part:
                return Unsupported("flow map entry without colon")
            key, value = part.split(":", 1)
            out[key.strip()] = _parse_scalar(value)
        return out
    return Unsupported("flow construct")


def _split_flow(inner: str) -> list[str]:
    parts = []
    depth = 0
    quote = ""
    current = []
    for c in inner:
        if quote:
            current.append(c)
            if c == quote:
                quote = ""
            continue
        if c in ("'", '"'):
            quote = c
            current.append(c)
            continue
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(c)
    if current:
        parts.append("".join(current).strip())
    return parts


# --------------------------------------------------------------------------- block parser
@dataclass
class _Line:
    indent: int
    text: str
    no: int


def _lex(text: str):
    lines = []
    for no, raw in enumerate(text.splitlines(), 1):
        if raw.strip() in ("---", "..."):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            return None  # tab indentation is unsupported
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append(_Line(indent, stripped.strip(), no))
    return lines


def parse(text: str):
    """Parse a workflow document into ordered dict/list/scalar structure.

    Returns ``(node, unsupported_reason)``; ``unsupported_reason`` is "" when the document
    parsed cleanly. A structurally unparseable document returns ``(None, reason)``.
    """
    lines = _lex(text)
    if not lines:
        return None, "empty workflow"
    node, index, unsupported = _parse_block(lines, 0, lines[0].indent)
    if index < len(lines):
        return None, f"trailing content at line {lines[index].no}"
    return node, unsupported


def _parse_block(lines: list[_Line], index: int, indent: int):
    unsupported = ""
    if index >= len(lines):
        return None, index, unsupported
    if lines[index].text.startswith("- ") or lines[index].text == "-":
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_sequence(lines: list[_Line], index: int, indent: int):
    items = []
    unsupported = ""
    while index < len(lines) and lines[index].indent == indent \
            and (lines[index].text.startswith("- ") or lines[index].text == "-"):
        line = lines[index]
        rest = line.text[2:].strip() if line.text.startswith("- ") else ""
        index += 1
        if not rest:
            # Nested block on following deeper-indented lines, or null item.
            if index < len(lines) and lines[index].indent > indent:
                node, index, sub = _parse_block(lines, index, lines[index].indent)
                unsupported = unsupported or sub
                items.append(node)
            else:
                items.append(None)
            continue
        if ":" in rest and not rest.startswith(('"', "'", "[", "{")):
            key, _, value = rest.partition(":")
            key = key.strip()
            value = value.strip()
            mapping = {}
            if value in ("|", "|-", "|+", ">", ">-", ">+"):
                # Block scalar inside a sequence item: consume deeper-indented lines.
                literal = value.startswith("|")
                body_lines = []
                while index < len(lines) and lines[index].indent > indent:
                    body_lines.append(" " * (lines[index].indent - indent - 2)
                                      + lines[index].text)
                    index += 1
                mapping[key] = "\n".join(body_lines) if literal else " ".join(body_lines)
            elif value:
                parsed_value = _parse_scalar(value)
                if isinstance(parsed_value, Unsupported):
                    unsupported = unsupported or parsed_value.reason
                mapping[key] = parsed_value
            elif index < len(lines) and lines[index].indent > indent:
                node, index, sub = _parse_block(lines, index, lines[index].indent)
                unsupported = unsupported or sub
                mapping[key] = node
            else:
                mapping[key] = None
            # Additional mapping keys inside this sequence item.
            while index < len(lines) and lines[index].indent > indent:
                sub_indent = lines[index].indent
                before = index
                _node, index, sub = _parse_mapping(lines, index, sub_indent,
                                                   into=mapping)
                unsupported = unsupported or sub
                if index == before:
                    unsupported = unsupported or f"unparseable line {lines[index].no}"
                    index += 1  # guarantee progress on pathological input
            items.append(mapping)
            continue
        parsed = _parse_scalar(rest)
        if isinstance(parsed, Unsupported):
            unsupported = unsupported or parsed.reason
            items.append(parsed)
        else:
            items.append(parsed)
    return items, index, unsupported


def _parse_mapping(lines: list[_Line], index: int, indent: int, into=None):
    mapping = {} if into is None else into
    unsupported = ""
    while index < len(lines) and lines[index].indent == indent \
            and not lines[index].text.startswith("- ") \
            and lines[index].text != "-":
        line = lines[index]
        if ":" not in line.text:
            return mapping, index, (unsupported or f"not a mapping line {line.no}")
        key, _, value = line.text.partition(":")
        key = key.strip()
        value = value.strip()
        index += 1
        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            # Block scalar: consume deeper-indented lines verbatim.
            literal = value.startswith("|")
            body_lines = []
            while index < len(lines) and lines[index].indent > indent:
                body_lines.append(" " * (lines[index].indent - indent - 2)
                                  + lines[index].text)
                index += 1
            mapping[key] = "\n".join(body_lines) if literal else " ".join(body_lines)
            continue
        if value:
            parsed = _parse_scalar(value)
            if isinstance(parsed, Unsupported):
                unsupported = unsupported or parsed.reason
            mapping[key] = parsed
            continue
        # Nested block or empty value.
        if index < len(lines) and lines[index].indent > indent:
            node, index, sub = _parse_block(lines, index, lines[index].indent)
            unsupported = unsupported or sub
            mapping[key] = node
        else:
            mapping[key] = None
    return mapping, index, unsupported


# --------------------------------------------------------------------------- workflow view
@dataclass(frozen=True)
class StepView:
    ordinal: int
    uses: str            # "" when a run step
    run: str             # "" when a uses step
    with_map: tuple      # tuple[(key, str)] with map keys only (values canonicalized)
    if_state: str        # "enabled" | "literal_false" | "dynamic"
    continue_on_error: str  # "true" | "false" | "dynamic" | "absent"


@dataclass(frozen=True)
class JobView:
    name: str
    ordinal: int
    uses: str            # reusable-workflow call, else ""
    with_map: tuple
    secrets_map: tuple
    if_state: str
    continue_on_error: str
    permissions: tuple | None  # None = inherit workflow permissions
    steps: tuple


@dataclass(frozen=True)
class WorkflowView:
    permissions: tuple | None
    jobs: tuple
    unsupported: str = ""


def _if_state(value) -> str:
    if value is False:
        return "literal_false"
    if value is None:
        return "enabled"
    if isinstance(value, str):
        if value.strip().lower() == "false":
            return "literal_false"
        return "dynamic" if value.strip() else "enabled"
    return "dynamic"


def _coe_state(value) -> str:
    if value is True:
        return "true"
    if value is False or value is None:
        return "absent" if value is None else "false"
    if isinstance(value, str):
        low = value.strip().lower()
        if low == "true":
            return "true"
        if low == "false":
            return "false"
        return "dynamic"
    return "dynamic"


def _canonical_with(value) -> tuple:
    """With/secrets map -> tuple[(key, state)]; state is
    empty|true|false|set|dynamic (literal booleans preserved)."""
    if not isinstance(value, dict):
        return ()
    out = []
    for key, item in value.items():
        if item is None or item == "":
            state = "empty"
        elif item is True:
            state = "true"
        elif item is False:
            state = "false"
        elif isinstance(item, str) and _is_expression(item):
            inner = item.strip()
            state = "dynamic" if inner != "${{ }}" else "empty"
        elif isinstance(item, str):
            state = "set"
        elif isinstance(item, (int, float)):
            state = "set"
        else:
            state = "dynamic"
        out.append((str(key), state))
    return tuple(out)


def _permissions_map(value):
    """Permissions map -> tuple[(key, level)] or None when the key is absent."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return ()
    return tuple((str(k), str(v).lower() if v is not None else "") for k, v in value.items())


def view(node, unsupported: str) -> WorkflowView | None:
    """Project the parsed document into the typed workflow view (never raw values)."""
    if not isinstance(node, dict):
        return None
    jobs_node = node.get("jobs")
    if not isinstance(jobs_node, dict):
        return WorkflowView(permissions=_permissions_map(node.get("permissions")),
                            jobs=(), unsupported=unsupported)
    jobs = []
    for ordinal, (job_name, job) in enumerate(jobs_node.items()):
        if not isinstance(job, dict):
            continue
        steps = []
        raw_steps = job.get("steps")
        if isinstance(raw_steps, list):
            for step_ordinal, step in enumerate(raw_steps):
                if not isinstance(step, dict):
                    continue
                steps.append(StepView(
                    ordinal=step_ordinal,
                    uses=step.get("uses") if isinstance(step.get("uses"), str) else "",
                    run=step.get("run") if isinstance(step.get("run"), str) else "",
                    with_map=_canonical_with(step.get("with")),
                    if_state=_if_state(step.get("if")),
                    continue_on_error=_coe_state(step.get("continue-on-error")),
                ))
        jobs.append(JobView(
            name=str(job_name),
            ordinal=ordinal,
            uses=job.get("uses") if isinstance(job.get("uses"), str) else "",
            with_map=_canonical_with(job.get("with")),
            secrets_map=_canonical_with(job.get("secrets")),
            if_state=_if_state(job.get("if")),
            continue_on_error=_coe_state(job.get("continue-on-error")),
            permissions=_permissions_map(job.get("permissions"))
            if "permissions" in job else None,
            steps=tuple(steps),
        ))
    return WorkflowView(permissions=_permissions_map(node.get("permissions")),
                        jobs=tuple(jobs), unsupported=unsupported)


def effective_permissions(workflow: WorkflowView, job: JobView) -> tuple:
    """Job permissions replace workflow permissions; absent job map inherits."""
    if job.permissions is not None:
        return job.permissions
    return workflow.permissions or ()


def parse_workflow(text: str) -> WorkflowView | None:
    """One call: text -> typed view (None when structurally unparseable)."""
    node, unsupported = parse(text)
    if node is None:
        return None
    return view(node, unsupported)


# --------------------------------------------------------------------------- provenance

_PUBLICATION_ACTIONS = frozenset({
    "googleapis/release-please-action", "changesets/action",
    "goreleaser/goreleaser-action", "softprops/action-gh-release",
    "ncipollo/release-action", "pypa/gh-action-pypi-publish",
    "JS-DevTools/npm-publish", "actions/create-release", "helm/chart-releaser-action",
})
_PUBLISH_PREFIXES = (
    ("npm", "publish"), ("pnpm", "publish"), ("yarn", "publish"),
    ("python", "-m", "twine", "upload"), ("python3", "-m", "twine", "upload"),
    ("twine", "upload"), ("cargo", "publish"), ("dotnet", "nuget", "push"),
    ("mvn", "deploy"), ("gradle", "publish"), ("./gradlew", "publish"),
    ("docker", "push"), ("podman", "push"), ("gh", "release", "create"),
    ("gh", "release", "upload"), ("goreleaser", "release"),
)
_RELEASE_CONFIG_GLOBS = [
    ".releaserc", ".releaserc.json", ".releaserc.yml", ".releaserc.yaml",
    "release.config.js", "release.config.cjs", "release.config.mjs",
    ".goreleaser.yml", ".goreleaser.yaml", ".changeset/config.json",
]
_RELEASE_DEPS = ("semantic-release", "release-please", "@changesets/cli",
                 "standard-version")
_SHELL_OPERATORS = ("&&", "||", ";", "|", ">", "<", "$(", "`", "\n")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S+$")

def _uses_ref(uses: str) -> tuple[str, str]:
    """(action path, ref) from a uses string; ref may be ""."""
    if "@" not in uses:
        return uses, ""
    path, _, ref = uses.rpartition("@")
    return path, ref


def _with_value(with_map: tuple, key: str) -> str:
    for k, state in with_map:
        if k == key:
            return state
    return "absent"


def _publish_command_tokens(run: str) -> list[str] | None:
    """Leading command tokens of a bounded, comment-stripped logical run command.

    Returns None when the command cannot be safely classified (shell operators present).
    """
    for line in run.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if any(op in stripped for op in ("&&", "||", ";", "|", "$(", "`", ">", "<")):
            return None
        tokens = stripped.split()
        while tokens and _ENV_ASSIGN_RE.match(tokens[0]):
            tokens = tokens[1:]
        if len(tokens) > 24:
            return None
        return tokens
    return []


def _run_is_publish(run: str) -> bool | None:
    """True when a run scalar is a recognized publish command; None when unclassifiable."""
    tokens = _publish_command_tokens(run)
    if tokens is None:
        return None
    if not tokens:
        return False
    for prefix in _PUBLISH_PREFIXES:
        if tuple(tokens[: len(prefix)]) == prefix:
            return True
    return False


def artifact_publication_intent(ctx) -> str:
    """The shared three-state publication signal: ``present`` | ``absent`` | ``indeterminate``.

    Only the closed §5.0 signal set qualifies; generic manifests, project types, build
    commands, workflow filenames, prose, release-like job names, container builds without
    push, and transient upload-artifact usage never do.
    """
    from .. import safe_io as _safe_io
    indeterminate = False
    # 1. Release configuration or dependency already recognized by release_automation.
    try:
        if ctx.static.exists_any(_RELEASE_CONFIG_GLOBS):
            return "present"
        if ctx.app_static().has_dep(list(_RELEASE_DEPS)) or \
                (ctx.app.path != "." and ctx.static.has_dep(list(_RELEASE_DEPS))):
            return "present"
    except _safe_io.RepositoryInputError:
        return "indeterminate"
    # 2/3/4. Workflow wiring.
    obs = ctx.static.glob_repo_files([".github/workflows/*.yml", ".github/workflows/*.yaml"])
    if obs.state is not _safe_io.RepoDiscoveryState.OK:
        return "indeterminate"
    for path in obs.paths:
        read = ctx.static.read_repo_file(path)
        if read.state is not _safe_io.RepoReadState.OK:
            indeterminate = True
            continue
        workflow = parse_workflow(read.text)
        if workflow is None:
            indeterminate = True
            continue
        for job in workflow.jobs:
            if job.if_state == "literal_false":
                continue
            job_uses_path, job_uses_ref = _uses_ref(job.uses)
            if job_uses_path in _PUBLICATION_ACTIONS and job_uses_ref:
                return "present"
            for step in job.steps:
                if step.if_state == "literal_false":
                    continue
                uses_path, uses_ref = _uses_ref(step.uses)
                if uses_path in _PUBLICATION_ACTIONS and uses_ref:
                    return "present"
                if uses_path == "docker/build-push-action":
                    push = _with_value(step.with_map, "push")
                    # Literal true or a non-empty expression qualifies; literal false,
                    # empty, and absent (the action's default) never do.
                    if push in ("true", "dynamic"):
                        return "present"
                if step.run:
                    verdict = _run_is_publish(step.run)
                    if verdict is True:
                        return "present"
                    if verdict is None:
                        indeterminate = True
    return "indeterminate" if indeterminate else "absent"


# --------------------------------------------------------------------------- provenance candidates
@dataclass(frozen=True)
class ProvenanceCandidate:
    workflow_path: str
    job_ordinal: int
    step_ordinal: int        # -1 for job-level reusable-workflow calls
    kind: str                # attest_action | attest_legacy_action | slsa_generic | slsa_container
    state: str               # complete | incomplete | indeterminate | excluded
    missing: tuple = ()      # canonical missing-control categories


_ATTEST_CURRENT = "actions/attest"
_ATTEST_LEGACY = ("actions/attest-build-provenance", "actions/attest-sbom")
_SLSA_GENERIC = ("slsa-framework/slsa-github-generator/.github/workflows/"
                 "generator_generic_slsa3.yml")
_SLSA_CONTAINER = ("slsa-framework/slsa-github-generator/.github/workflows/"
                   "generator_container_slsa3.yml")
_SEMVER_REF_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _perm_levels(permissions: tuple) -> dict:
    return {k: v for k, v in permissions}


def _evaluate_attest_step(workflow, job, step, path: str):
    """One attestation-step candidate (§5.7 path 1)."""
    uses_path, uses_ref = _uses_ref(step.uses)
    kind = "attest_action" if uses_path == _ATTEST_CURRENT else "attest_legacy_action"
    missing = []
    if not uses_ref:
        missing.append("empty action ref")
    subject_path = _with_value(step.with_map, "subject-path")
    subject_name = _with_value(step.with_map, "subject-name")
    subject_digest = _with_value(step.with_map, "subject-digest")
    sbom_path = _with_value(step.with_map, "sbom-path")
    has_subject = subject_path in ("set", "dynamic") or (
        subject_name in ("set", "dynamic") and subject_digest in ("set", "dynamic"))
    if not has_subject:
        missing.append("subject inputs")
    if uses_path == "actions/attest-sbom" and sbom_path not in ("set", "dynamic"):
        missing.append("sbom-path")
    perms = _perm_levels(effective_permissions(workflow, job))
    for required, level in (("contents", "read"), ("id-token", "write"),
                            ("attestations", "write")):
        if perms.get(required) != level:
            missing.append(f"permission {required}: {level}")
    if step.continue_on_error == "true" or job.continue_on_error == "true":
        return ProvenanceCandidate(path, job.ordinal, step.ordinal, kind, "indeterminate")
    if missing:
        return ProvenanceCandidate(path, job.ordinal, step.ordinal, kind, "incomplete",
                                   tuple(missing))
    return ProvenanceCandidate(path, job.ordinal, step.ordinal, kind, "complete")


def _evaluate_slsa_job(workflow, job, path: str, kind: str):
    """One reusable-workflow SLSA candidate (§5.7 paths 2/3)."""
    uses_path, uses_ref = _uses_ref(job.uses)
    if not _SEMVER_REF_RE.match(uses_ref):
        return ProvenanceCandidate(path, job.ordinal, -1, kind, "incomplete",
                                   ("non-semver reusable-workflow ref",))
    perms = _perm_levels(effective_permissions(workflow, job))
    missing = []
    for required, level in (("actions", "read"), ("id-token", "write")):
        if perms.get(required) != level:
            missing.append(f"permission {required}: {level}")
    with_states = dict(job.with_map)
    secret_states = dict(job.secrets_map)
    if kind == "slsa_generic":
        upload = with_states.get("upload-assets", "absent")
        if upload == "true" and perms.get("contents") != "write":
            missing.append("permission contents: write")
        elif upload == "dynamic" and perms.get("contents") != "write":
            return ProvenanceCandidate(path, job.ordinal, -1, kind, "indeterminate")
        if with_states.get("base64-subjects") not in ("set", "dynamic") and \
                with_states.get("base64-subjects-as-file") not in ("set", "dynamic"):
            missing.append("subjects input")
    else:  # slsa_container
        if perms.get("packages") != "write":
            missing.append("permission packages: write")
        if with_states.get("digest") not in ("set", "dynamic"):
            missing.append("digest input")
        for required_input in ("image", "registry-username", "registry-password"):
            if with_states.get(required_input) in ("set", "dynamic"):
                continue
            if secret_states.get(required_input) in ("set", "dynamic"):
                continue
            missing.append(f"input/secret {required_input}")
    if job.continue_on_error == "true":
        return ProvenanceCandidate(path, job.ordinal, -1, kind, "indeterminate")
    if missing:
        return ProvenanceCandidate(path, job.ordinal, -1, kind, "incomplete",
                                   tuple(missing))
    return ProvenanceCandidate(path, job.ordinal, -1, kind, "complete")


def provenance_candidates(ctx) -> tuple[str, list[ProvenanceCandidate]]:
    """(state, candidates): ``ok`` | ``indeterminate`` plus the bounded candidate list.

    Discovers workflow paths in ascending POSIX order and evaluates every
    provenance-looking step/job under MAX_CANDIDATES_PER_CRITERION; the 257th candidate
    is an overflow (``overflow`` state), never a truncated pass.
    """
    from .. import safe_io as _safe_io
    obs = ctx.static.glob_repo_files([".github/workflows/*.yml", ".github/workflows/*.yaml"])
    if obs.state is not _safe_io.RepoDiscoveryState.OK:
        return "overflow" if obs.state is _safe_io.RepoDiscoveryState.OVERFLOW \
            else "indeterminate", []
    candidates: list[ProvenanceCandidate] = []
    state = "ok"
    for path in obs.paths:
        read = ctx.static.read_repo_file(path)
        if read.state is not _safe_io.RepoReadState.OK:
            state = "indeterminate"
            continue
        workflow = parse_workflow(read.text)
        if workflow is None:
            state = "indeterminate"
            continue
        if workflow.unsupported:
            # Provenance-relevant unsupported syntax could hide a qualifying signal.
            state = "indeterminate"
        for job in workflow.jobs:
            if job.if_state == "literal_false":
                continue
            uses_path, _ref = _uses_ref(job.uses)
            if uses_path == _SLSA_GENERIC or uses_path == _SLSA_CONTAINER:
                kind = "slsa_generic" if uses_path == _SLSA_GENERIC else "slsa_container"
                candidates.append(_evaluate_slsa_job(workflow, job, path, kind))
                continue
            for step in job.steps:
                if step.if_state == "literal_false":
                    continue
                step_path, _sref = _uses_ref(step.uses)
                if step_path == _ATTEST_CURRENT or step_path in _ATTEST_LEGACY:
                    candidates.append(_evaluate_attest_step(workflow, job, step, path))
            if len(candidates) > 256:
                return "overflow", []
        if len(candidates) > 256:
            return "overflow", []
    order = {"attest_action": 0, "attest_legacy_action": 1, "slsa_generic": 2,
             "slsa_container": 3}
    candidates.sort(key=lambda c: (c.workflow_path, c.job_ordinal, c.step_ordinal,
                                   order[c.kind]))
    return state, candidates
