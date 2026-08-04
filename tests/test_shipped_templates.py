"""Pin policy for the workflow templates we install into other repositories.

The zizmor gate on ``templates/ci/`` runs with ``unpinned-uses: ref-pin``, because those
templates deliberately ship readable refs rather than digests -- handing a consumer a hash
they will never refresh is worse than a tag Dependabot can bump for them.

But ``ref-pin`` is looser than that intent: zizmor's policy vocabulary is only ``any`` /
``ref-pin`` / ``hash-pin``, and ``ref-pin`` accepts a mutable branch (``@main``) and a short
SHA just as happily as ``@v5``. Verified against zizmor 1.29.0. So the "readable *version
tag*" half of the policy has no enforcement over there, and this test supplies it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>\S+)", re.MULTILINE)
# A version tag: v1, v5, v1.2, v1.2.3. Not a branch, not a SHA, not a floating ref.
_VERSION_TAG_RE = re.compile(r"^v\d+(?:\.\d+)*$")


def _uses_refs():
    """Yield (path, full_ref) for every ``uses:`` in a shipped template."""
    for path in sorted(TEMPLATES.rglob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for m in _USES_RE.finditer(text):
            yield path.relative_to(TEMPLATES), m.group("ref")


class TestShippedTemplatePins(unittest.TestCase):
    def test_templates_with_uses_are_discovered(self):
        # Guard against the walk silently finding nothing and passing vacuously.
        refs = list(_uses_refs())
        self.assertTrue(refs, "no `uses:` found under templates/ -- did the walk break?")
        self.assertIn("ci/readiness.yml", {str(p) for p, _ in refs})

    def test_every_shipped_uses_pins_a_version_tag(self):
        offenders = []
        for path, ref in _uses_refs():
            if "@" not in ref:
                offenders.append(f"{path}: '{ref}' has no ref at all")
                continue
            _, _, pin = ref.rpartition("@")
            if not _VERSION_TAG_RE.match(pin):
                offenders.append(f"{path}: '{ref}' pins '{pin}', not a vN[.N[.N]] tag")
        self.assertEqual(
            offenders, [],
            "shipped templates must pin a version tag (zizmor's ref-pin would accept "
            "@main or a short SHA):\n  " + "\n  ".join(offenders),
        )

    def test_detector_rejects_branches_shas_and_bare_refs(self):
        # The check above is only worth having if it would actually fail.
        good = ["v1", "v5", "v1.2", "v1.2.3"]
        bad = ["main", "master", "HEAD", "8f4b7c2", "3d3c42e5aac5ba805825da76410c181273ba90b1",
               "latest", "v", "release/1", "1.2.3"]
        for pin in good:
            self.assertRegex(pin, _VERSION_TAG_RE, f"{pin!r} should be accepted")
        for pin in bad:
            self.assertNotRegex(pin, _VERSION_TAG_RE, f"{pin!r} should be rejected")

    def test_uses_regex_extracts_refs_from_realistic_yaml(self):
        sample = (
            "jobs:\n  j:\n    steps:\n"
            "      - uses: actions/checkout@v5\n"
            "        with:\n          persist-credentials: false\n"
            "      - uses: owner/repo/sub@v1 # trailing comment\n"
            "      - name: not a uses line\n        run: echo uses: nope\n"
        )
        found = [m.group("ref") for m in _USES_RE.finditer(sample)]
        self.assertEqual(found, ["actions/checkout@v5", "owner/repo/sub@v1"])


if __name__ == "__main__":
    unittest.main()
