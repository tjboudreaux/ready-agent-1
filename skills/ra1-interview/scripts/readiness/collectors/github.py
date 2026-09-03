"""T2 GitHub evidence via the engine-selected ``gh`` CLI (github.com only).

Construction requires the safe Git authority's sanitized origin projection (exact host
``github.com`` plus a validated owner/repository pair) and a usable
:class:`process.GithubAuthAuthority`; otherwise the collector is ``unavailable`` and no
``gh`` process is ever spawned. Requests run in an engine-owned neutral temporary cwd with
fixed ``--hostname github.com``, explicit relative API endpoints, and only the authority's
minimal environment. Repository data may supply only validated owner/repository, branch,
and PR path components inside fixed endpoint kinds — never host, executable, option, query
key, page bound, authentication, environment, proxy, or whether a request occurs.

Every method returns a lossless :class:`CollectorObservation`:
- ``unavailable``: T2 disabled, engine-selected ``gh`` absent, no usable host-token/
  safe-config authority, or no safe exact ``github.com/owner/repository`` origin identity;
- ``absent``: only an exact HTTP-404 from the fixed branch-protection endpoint (documented
  meaning: branch not protected); every other 404 is ``unreadable``;
- ``unreadable``: invalid identity/dynamic field, 401/403/5xx, other nonzero, transport/
  timeout/output limit, empty success where an object is required, malformed/wrong-shape
  JSON, or any page/cap failure;
- ``present``: validated success, including legitimate empty list endpoints.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .. import parsers, process
from ._observation import CollectorObservation as Obs
from ._observation import absent, present, unavailable, unreadable

MAX_GITHUB_JSON_BYTES = 1_048_576
MAX_GITHUB_TOTAL_BYTES = 8_388_608
MAX_GITHUB_JSON_DEPTH = 64
MAX_GITHUB_JSON_NODES = 100_000
MAX_GITHUB_REQUESTS_PER_SCAN = 64

_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}\Z")
_REF_FORBIDDEN = ("..", "//", "@{", ".lock", "\\")
_HEADER_RE = re.compile(r"[A-Za-z0-9-]{1,64}: [ -~]{0,512}\Z")

_TOPICS_CAP = 100
_WORKFLOWS_CAP = 100
_LABELS_CAP = 100
_REVIEWS_CAP = 100
_RUNS_CAP = 20
_ISSUES_CAP = 50
_MERGED_PR_CAP = 20
_MERGED_PR_PAGES = 3


@dataclass(frozen=True)
class IssueRecord:
    has_labels: bool
    has_milestone: bool
    has_body: bool


@dataclass(frozen=True)
class MergedPrRecord:
    number: int
    merged_at: str
    created_at: str


@dataclass(frozen=True)
class RunRecord:
    started_at: str
    updated_at: str


@dataclass(frozen=True)
class RepoRecord:
    full_name: str
    default_branch: str
    topics: tuple
    secret_scanning: bool
    push_protection: bool


@dataclass(frozen=True)
class ProtectionRecord:
    """The minimal typed branch-protection projection checks consume."""

    required_approving_review_count: int
    require_code_owner_reviews: bool
    status_contexts: tuple
    status_checks: tuple
    allow_force_pushes: bool
    allow_deletions: bool


def _project_protection(data: dict) -> ProtectionRecord | None:
    """Project the API protection object; ``None`` on wrong shape (unreadable).

    Absent/null ``allow_force_pushes``/``allow_deletions`` count as disabled; only an
    explicit ``{"enabled": true}`` counts as enabled.
    """
    reviews = data.get("required_pull_request_reviews")
    if not isinstance(reviews, dict):
        reviews = {}
    count = reviews.get("required_approving_review_count")
    if count is None:
        count = 0
    if type(count) is not int or count < 0:
        return None
    code_owners = reviews.get("require_code_owner_reviews")
    if code_owners is not None and type(code_owners) is not bool:
        return None
    status = data.get("required_status_checks")
    if not isinstance(status, dict):
        status = {}
    contexts = status.get("contexts") or []
    checks = status.get("checks") or []
    if not isinstance(contexts, list) or not isinstance(checks, list):
        return None
    context_names = tuple(c for c in contexts if isinstance(c, str))
    check_names = tuple(c.get("context") for c in checks
                        if isinstance(c, dict) and isinstance(c.get("context"), str))
    force = data.get("allow_force_pushes")
    deletions = data.get("allow_deletions")
    return ProtectionRecord(
        required_approving_review_count=count,
        require_code_owner_reviews=bool(code_owners),
        status_contexts=context_names,
        status_checks=check_names,
        allow_force_pushes=isinstance(force, dict) and force.get("enabled") is True,
        allow_deletions=isinstance(deletions, dict) and deletions.get("enabled") is True,
    )


class GithubCollector:
    def __init__(self, root, *, origin: tuple = (), auth=None, toolchain=None,
                 proxy=None, runner=None):
        """``origin`` is the sanitized ``(host, owner, name)`` projection or ``()``.

        ``runner`` is the private test boundary: ``fn(argv: tuple) -> BoundedProcessResult``
        (legacy ``str``/``None``/``dict`` injections raise ``TypeError``).
        """
        self.root = Path(root)
        self._origin = tuple(origin)
        self._auth = auth
        self._toolchain = toolchain
        self._proxy = proxy
        self._runner = runner
        self._cache: dict = {}
        self._requests = 0
        self._total_bytes = 0
        self._cwd_dir = None
        self._cwd_handle = None
        self.collection_complete = True
        self._identity = self._validate_identity()

    def close(self) -> None:
        if self._cwd_handle is not None:
            import os
            os.close(self._cwd_handle)
            self._cwd_handle = None
        if self._cwd_dir is not None:
            import shutil
            shutil.rmtree(self._cwd_dir, ignore_errors=True)
            self._cwd_dir = None

    # ----- identity / availability ---------------------------------------------------------
    def _validate_identity(self):
        if len(self._origin) != 3:
            return None
        host, owner, repo = self._origin
        if host != "github.com":
            return None
        if not _OWNER_RE.match(owner or ""):
            return None
        if not _REPO_RE.match(repo or "") or repo in (".", ".."):
            return None
        return (owner, repo)

    def availability(self) -> Obs:
        """Construction-level availability: no request is issued."""
        def produce():
            if self._runner is not None:
                return present(True) if self._identity else unavailable(
                    "no safe github.com origin identity")
            if self._identity is None:
                return unavailable("no safe github.com origin identity")
            if self._auth is None:
                return unavailable("no usable github auth authority")
            if self._toolchain is None:
                self._toolchain = process.resolve_toolchain(self.root)
            if self._toolchain.get(process.ToolId.GH) is None:
                return unavailable("engine gh unavailable")
            return present(True)
        return self._observe(("availability",), produce)

    @property
    def available(self) -> bool:
        return self.availability().state == "present"

    @property
    def slug(self) -> str | None:
        if self._identity is None:
            return None
        return f"{self._identity[0]}/{self._identity[1]}"

    # ----- request plumbing ------------------------------------------------------------------
    def _observe(self, key, produce):
        if key not in self._cache:
            obs = produce()
            self._cache[key] = obs
            if obs.state in ("unreadable", "unavailable"):
                self.collection_complete = False
        return self._cache[key]

    def _cwd(self):
        if self._cwd_handle is None:
            import os
            self._cwd_dir = tempfile.mkdtemp(prefix="ra1-gh-cwd-")
            os.chmod(self._cwd_dir, 0o700)
            self._cwd_handle = os.open(self._cwd_dir,
                                       os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return self._cwd_handle

    def _spawn(self, argv: tuple) -> process.BoundedProcessResult:
        if self._runner is not None:
            result = self._runner(tuple(argv))
            if not isinstance(result, process.BoundedProcessResult):
                raise TypeError("injected github runners must return BoundedProcessResult")
            return result
        env = self._auth.env(self._proxy)
        return process.run_bounded_process(
            process.ToolId.GH, argv, toolchain=self._toolchain,
            cwd_handle=self._cwd(), env=env, timeout_seconds=25)

    def _api(self, endpoint: str, *, kind: str):
        """One bounded API call: ``(status, parsed_json)`` or an Obs failure."""
        if self._requests >= MAX_GITHUB_REQUESTS_PER_SCAN:
            return unreadable("github request cap reached")
        argv = ("api", "--hostname", "github.com", "--include", endpoint)
        self._requests += 1
        result = self._spawn(argv)
        if result.state is process.ProcessState.UNSUPPORTED:
            return unavailable("engine gh unavailable")
        if result.state is not process.ProcessState.OK \
                and result.state is not process.ProcessState.NONZERO:
            return unreadable(f"gh process {result.state.value}")
        parsed_env = _parse_envelope(result.stdout)
        if parsed_env is None:
            return unreadable("malformed gh envelope")
        status, body = parsed_env
        if not 200 <= status < 300:
            if status == 404 and kind == "branch_protection":
                return absent()
            return unreadable(f"github status {status}")
        self._total_bytes += len(body.encode("utf-8", "replace"))
        if self._total_bytes > MAX_GITHUB_TOTAL_BYTES:
            return unreadable("github total byte cap reached")
        try:
            data = parsers.strict_load_json(
                body, max_bytes=MAX_GITHUB_JSON_BYTES, max_depth=MAX_GITHUB_JSON_DEPTH,
                max_nodes=MAX_GITHUB_JSON_NODES)
        except parsers.StrictJsonError:
            return unreadable("malformed github json")
        return (status, data)

    def _endpoint(self, suffix: str) -> str | None:
        if self._identity is None:
            return None
        owner, repo = self._identity
        return f"repos/{quote(owner, safe='')}/{quote(repo, safe='')}{suffix}"

    @staticmethod
    def _encode_branch(branch) -> str | None:
        if not isinstance(branch, str) or not branch or len(branch.encode()) > 1024:
            return None
        if not _REF_RE.match(branch):
            return None
        if any(bad in branch for bad in _REF_FORBIDDEN):
            return None
        if branch.startswith("/") or branch.endswith(("/", ".")):
            return None
        return quote(branch, safe="")

    # ----- facts ----------------------------------------------------------------------------
    def repo(self) -> Obs:
        def produce():
            if not self.available:
                return self.availability()
            endpoint = self._endpoint("")
            outcome = self._api(endpoint, kind="repo")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if not isinstance(data, dict):
                return unreadable("repo response not an object")
            full_name = data.get("full_name") or data.get("nameWithOwner") or ""
            if full_name.lower() != (self.slug or "").lower():
                return unreadable("repository identity mismatch")
            saa = data.get("security_and_analysis")
            if not isinstance(saa, dict):
                saa = {}
            topics = data.get("topics")
            record = RepoRecord(
                full_name=full_name,
                default_branch=data.get("default_branch") or "",
                topics=tuple(t for t in (topics or []) if isinstance(t, str)),
                secret_scanning=(saa.get("secret_scanning") or {}).get("status") == "enabled",
                push_protection=(saa.get("secret_scanning_push_protection") or {})
                .get("status") == "enabled",
            )
            return present(record)
        return self._observe(("repo",), produce)

    def default_branch(self) -> Obs:
        def produce():
            repo = self.repo()
            if repo.state != "present":
                return repo
            if not repo.value.default_branch:
                return unreadable("default branch missing")
            if self._encode_branch(repo.value.default_branch) is None:
                return unreadable("default branch invalid")
            return present(repo.value.default_branch)
        return self._observe(("default_branch",), produce)

    def topics(self) -> Obs:
        def produce():
            if not self.available:
                return self.availability()
            endpoint = self._endpoint("/topics")
            outcome = self._api(endpoint, kind="topics")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if isinstance(data, dict) and isinstance(data.get("names"), list):
                return present(tuple(t for t in data["names"][:_TOPICS_CAP]
                                     if isinstance(t, str)))
            repo = self.repo()
            if repo.state == "present":
                return present(repo.value.topics[:_TOPICS_CAP])
            return unreadable("topics response wrong shape")
        return self._observe(("topics",), produce)

    def secret_scanning_enabled(self) -> Obs:
        def produce():
            repo = self.repo()
            if repo.state != "present":
                return repo
            return present(repo.value.secret_scanning or repo.value.push_protection)
        return self._observe(("secret_scanning",), produce)

    def branch_protection_details(self, branch: str | None = None) -> Obs:
        """Raw protection record for the branch; exact-404 404 is the only ``absent``."""
        def produce():
            if not self.available:
                return self.availability()
            target = branch
            if target is None:
                resolved = self.default_branch()
                if resolved.state != "present":
                    return resolved
                target = resolved.value
            encoded = self._encode_branch(target)
            if encoded is None:
                return unreadable("branch invalid")
            endpoint = self._endpoint(f"/branches/{encoded}/protection")
            outcome = self._api(endpoint, kind="branch_protection")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if not isinstance(data, dict):
                return unreadable("protection response not an object")
            record = _project_protection(data)
            if record is None:
                return unreadable("protection response wrong shape")
            return present(record)
        return self._observe(("protection", branch), produce)

    def branch_protected(self, branch: str | None = None) -> Obs:
        def produce():
            details = self.branch_protection_details(branch)
            if details.state == "present":
                return present(True)
            if details.state == "absent":
                return present(False)
            return details
        return self._observe(("protected", branch), produce)

    def workflows(self) -> Obs:
        """Present value is the bounded workflow count."""
        def produce():
            if not self.available:
                return self.availability()
            endpoint = self._endpoint("/actions/workflows?per_page=100")
            outcome = self._api(endpoint, kind="workflows")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if isinstance(data, dict) and isinstance(data.get("workflows"), list):
                return present(len(data["workflows"][:_WORKFLOWS_CAP]))
            return unreadable("workflows response wrong shape")
        return self._observe(("workflows",), produce)

    def recent_runs(self, n: int = _RUNS_CAP) -> Obs:
        def produce():
            if not self.available:
                return self.availability()
            endpoint = self._endpoint("/actions/runs?per_page=20")
            outcome = self._api(endpoint, kind="runs")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if isinstance(data, dict) and isinstance(data.get("workflow_runs"), list):
                records = []
                for run in data["workflow_runs"][:_RUNS_CAP]:
                    if not isinstance(run, dict):
                        return unreadable("run record wrong shape")
                    records.append(RunRecord(
                        started_at=run.get("run_started_at") or run.get("created_at") or "",
                        updated_at=run.get("updated_at") or ""))
                return present(tuple(records))
            return unreadable("runs response wrong shape")
        return self._observe(("runs", n), produce)

    def labels(self) -> Obs:
        def produce():
            if not self.available:
                return self.availability()
            endpoint = self._endpoint("/labels?per_page=100")
            outcome = self._api(endpoint, kind="labels")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if isinstance(data, list):
                names = []
                for label in data[:_LABELS_CAP]:
                    if not isinstance(label, dict) or not isinstance(label.get("name"), str):
                        return unreadable("label record wrong shape")
                    names.append(label["name"])
                return present(tuple(names))
            return unreadable("labels response wrong shape")
        return self._observe(("labels",), produce)

    def open_issues(self, n: int = _ISSUES_CAP) -> Obs:
        """Real issues only (the issues endpoint also returns PRs)."""
        def produce():
            if not self.available:
                return self.availability()
            endpoint = self._endpoint("/issues?state=open&per_page=50")
            outcome = self._api(endpoint, kind="issues")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if isinstance(data, list):
                records = []
                for issue in data[:_ISSUES_CAP]:
                    if not isinstance(issue, dict):
                        return unreadable("issue record wrong shape")
                    if "pull_request" in issue:
                        continue
                    records.append(IssueRecord(
                        has_labels=bool(issue.get("labels")),
                        has_milestone=bool(issue.get("milestone")),
                        has_body=bool((issue.get("body") or "").strip())))
                return present(tuple(records))
            return unreadable("issues response wrong shape")
        return self._observe(("issues", n), produce)

    def recent_merged_prs(self, n: int = _MERGED_PR_CAP) -> Obs:
        """Up to ``n`` recently updated closed PRs that were merged (≤3 bounded pages)."""
        def produce():
            if not self.available:
                return self.availability()
            merged: list = []
            for page in range(1, _MERGED_PR_PAGES + 1):
                endpoint = self._endpoint(
                    f"/pulls?state=closed&sort=updated&direction=desc"
                    f"&per_page=50&page={page}")
                outcome = self._api(endpoint, kind="pulls")
                if isinstance(outcome, Obs):
                    if outcome.state == "present":
                        break
                    if not merged:
                        return outcome
                    return unreadable("partial merged-pr page failure")
                _status, data = outcome
                if not isinstance(data, list):
                    return unreadable("pulls response wrong shape")
                if not data:
                    break
                for pr in data:
                    if not isinstance(pr, dict):
                        return unreadable("pr record wrong shape")
                    merged_at = pr.get("merged_at")
                    if not merged_at:
                        continue
                    number = pr.get("number")
                    if not _valid_pr_number(number):
                        return unreadable("pr number invalid")
                    merged.append(MergedPrRecord(number=number, merged_at=merged_at,
                                                 created_at=pr.get("created_at") or ""))
                    if len(merged) >= n:
                        return present(tuple(merged))
            return present(tuple(merged))
        return self._observe(("merged_prs", n), produce)

    def pr_first_review_iso(self, number: int) -> Obs:
        """Earliest review ``submitted_at`` for a PR (any reviewer, including bots)."""
        def produce():
            if not self.available:
                return self.availability()
            if not _valid_pr_number(number):
                return unreadable("pr number invalid")
            endpoint = self._endpoint(f"/pulls/{number}/reviews?per_page=100")
            outcome = self._api(endpoint, kind="reviews")
            if isinstance(outcome, Obs):
                return outcome
            _status, data = outcome
            if not isinstance(data, list):
                return unreadable("reviews response wrong shape")
            times = []
            for rev in data[:_REVIEWS_CAP]:
                if not isinstance(rev, dict):
                    return unreadable("review record wrong shape")
                submitted = rev.get("submitted_at")
                if submitted:
                    times.append(submitted)
            if not times:
                return absent()
            return present(min(times))
        return self._observe(("reviews", number), produce)


def _valid_pr_number(number) -> bool:
    return type(number) is int and 1 <= number <= 2_147_483_647


def _parse_envelope(text: str):
    """Parse one strict HTTP envelope from ``gh api --include`` stdout.

    Returns ``(status, body)`` or ``None``: one validated status line, bounded ASCII header
    lines, one separator, then the body. Redirects, multiple envelopes, and malformed
    controls are rejected.
    """
    if not isinstance(text, str) or not text:
        return None
    head, sep, body = text.partition("\r\n\r\n")
    if not sep:
        return None
    lines = head.split("\r\n")
    status_line = lines[0]
    if not status_line.startswith("HTTP/"):
        return None
    parts = status_line.split(" ")
    if len(parts) < 2:
        return None
    try:
        status = int(parts[1])
    except ValueError:
        return None
    if not 100 <= status <= 599:
        return None
    for header in lines[1:]:
        if not _HEADER_RE.match(header):
            return None
    if body.startswith("HTTP/"):
        return None  # a second envelope means a redirect chain we do not follow
    return status, body
