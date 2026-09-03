"""Branch-completion tests for ``checks/_workflow_policy.py`` and ``checks/_agent_policy.py``.

``tests/test_checks.py`` and ``tests/test_new_criteria.py`` exercise the happy paths
through the security checks; this file drives the two policy modules directly (and through
stubbed collector observations for the error/refusal branches) so every parser, intent,
candidate-evaluation, permission, and CODEOWNERS branch is covered.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from readiness import safe_io
from readiness.checks import _agent_policy as ap
from readiness.checks import _workflow_policy as wp
from readiness.collectors.git import GitCollector
from readiness.collectors.github import GithubCollector
from readiness.collectors.static import StaticCollector
from readiness.context import Context
from readiness.detect import detect

from tests._util import fake_runner, make_repo, rmtree


# --------------------------------------------------------------------------- stubs
def _read_ok(text: str) -> safe_io.RepoFileObservation:
    return safe_io.RepoFileObservation(safe_io.RepoReadState.OK, text=text)


def _read_err() -> safe_io.RepoFileObservation:
    return safe_io.RepoFileObservation(safe_io.RepoReadState.UNREADABLE,
                                       reason_code="io_error")


def _glob_ok(*paths: str) -> safe_io.RepoDiscoveryObservation:
    return safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OK,
                                            paths=tuple(paths))


def _glob_unreadable() -> safe_io.RepoDiscoveryObservation:
    return safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.UNREADABLE,
                                            reason_code="io_error")


def _glob_overflow() -> safe_io.RepoDiscoveryObservation:
    return safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OVERFLOW,
                                            reason_code="match_overflow")


class _FakeStatic:
    """Minimal static-collector stub for the two ctx-driven workflow helpers."""

    def __init__(self, *, exists_raises=False, app_dep=None, root_dep=None,
                 glob_obs=None, reads=None):
        self._exists_raises = exists_raises
        self._app_dep = app_dep
        self._root_dep = root_dep
        self._glob_obs = glob_obs if glob_obs is not None else _glob_ok()
        self._reads = dict(reads or {})

    def exists_any(self, patterns):
        if self._exists_raises:
            raise safe_io.RepositoryInputError("existence indeterminate (io_error)")
        return None

    def has_dep(self, names):
        return self._app_dep

    def root_has_dep(self, names):
        return self._root_dep

    def glob_repo_files(self, patterns, **kw):
        return self._glob_obs

    def read_repo_file(self, path, **kw):
        return self._reads.get(path, _read_ok(""))


def _intent_ctx(static: _FakeStatic, *, app_path=".", app_static=None):
    """Ctx stand-in for ``artifact_publication_intent`` (static/app_static/app only)."""
    return SimpleNamespace(static=static,
                           app=SimpleNamespace(path=app_path),
                           app_static=lambda: app_static or static)


def _cand_ctx(static: _FakeStatic):
    """Ctx stand-in for ``provenance_candidates`` (static only)."""
    return SimpleNamespace(static=static)


def _wf_ctx(workflow_text: str, *, name=".github/workflows/wf.yml", **static_kw):
    reads = {name: _read_ok(workflow_text)}
    return _cand_ctx(_FakeStatic(glob_obs=_glob_ok(name), reads=reads, **static_kw))


def _repo_ctx(files):
    root = make_repo(files)
    static = StaticCollector(root)
    det = detect(root, static)
    ctx = Context(root=root, detection=det, static=static,
                  git=GitCollector(root, runner=fake_runner({})),
                  github=GithubCollector(root), app=det.apps[0], options={})
    return root, ctx


# --------------------------------------------------------------------------- scalar lexer
class TestStripComment(unittest.TestCase):
    def test_doubled_single_quote_escape_inside_quotes(self):
        self.assertEqual(wp._strip_comment("'it''s' # note"), "'it''s' ")

    def test_hash_inside_double_quotes_kept(self):
        self.assertEqual(wp._strip_comment('run: "a # b"'), 'run: "a # b"')


class TestParseScalar(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(wp._parse_scalar("   "), "")

    def test_block_scalar_header_mishandled(self):
        self.assertIsInstance(wp._parse_scalar("|literal"), wp.Unsupported)
        self.assertIsInstance(wp._parse_scalar(">folded"), wp.Unsupported)

    def test_unterminated_quote(self):
        self.assertIsInstance(wp._parse_scalar("'abc"), wp.Unsupported)
        self.assertIsInstance(wp._parse_scalar('"abc'), wp.Unsupported)

    def test_single_quote_doubling(self):
        self.assertEqual(wp._parse_scalar("'a''b'"), "a'b")

    def test_true_words(self):
        self.assertIs(wp._parse_scalar("yes"), True)
        self.assertIs(wp._parse_scalar("on"), True)

    def test_null_words(self):
        self.assertIsNone(wp._parse_scalar("~"))
        self.assertIsNone(wp._parse_scalar("null"))


class TestParseFlow(unittest.TestCase):
    def test_multiline_flow_list_unsupported(self):
        self.assertIsInstance(wp._parse_flow("[a, b"), wp.Unsupported)

    def test_flow_map(self):
        self.assertEqual(wp._parse_flow("{a: 1, b: x}"), {"a": "1", "b": "x"})

    def test_empty_flow_map(self):
        self.assertEqual(wp._parse_flow("{}"), {})

    def test_multiline_flow_map_unsupported(self):
        self.assertIsInstance(wp._parse_flow("{a: 1"), wp.Unsupported)

    def test_flow_map_entry_without_colon(self):
        self.assertIsInstance(wp._parse_flow("{a, b}"), wp.Unsupported)

    def test_non_flow_construct(self):
        self.assertIsInstance(wp._parse_flow("plain"), wp.Unsupported)

    def test_empty_element(self):
        self.assertEqual(wp._parse_flow("[a,,b]"), ["a", "", "b"])

    def test_trailing_comma_dropped(self):
        self.assertEqual(wp._parse_flow("[a,]"), ["a"])

    def test_nested_flow_depth(self):
        self.assertEqual(wp._parse_flow("[[a], b]"), [["a"], "b"])

    def test_quoted_comma_not_a_separator(self):
        self.assertEqual(wp._split_flow("'a,b', c"), ["'a,b'", "c"])


# --------------------------------------------------------------------------- block parser
class TestLex(unittest.TestCase):
    def test_document_markers_skipped(self):
        lines = wp._lex("---\nkey: value\n...\n")
        self.assertEqual([line.text for line in lines], ["key: value"])

    def test_tab_indentation_unsupported(self):
        self.assertIsNone(wp._lex("\tkey: value"))

    def test_empty_document(self):
        node, reason = wp.parse("# only a comment\n")
        self.assertIsNone(node)
        self.assertEqual(reason, "empty workflow")

    def test_parse_block_past_end(self):
        node, index, unsupported = wp._parse_block([], 0, 0)
        self.assertIsNone(node)
        self.assertEqual(unsupported, "")


class TestParseSequence(unittest.TestCase):
    def test_nested_and_null_items(self):
        node, unsupported = wp.parse("items:\n  -\n    a: b\n  -\n  - x\n")
        self.assertEqual(unsupported, "")
        self.assertEqual(node, {"items": [{"a": "b"}, None, "x"]})

    def test_block_scalars_in_sequence_items(self):
        node, unsupported = wp.parse(
            "steps:\n  - run: |\n      echo a\n      echo b\n"
            "  - run: >\n      echo c\n      echo d\n")
        self.assertEqual(unsupported, "")
        # Sequence-item block scalars retain the indent relative to the "- " marker.
        self.assertEqual(node["steps"][0]["run"], "  echo a\n  echo b")
        self.assertEqual(node["steps"][1]["run"], "  echo c   echo d")

    def test_unsupported_scalar_value_in_item(self):
        node, unsupported = wp.parse("steps:\n  - run: &anch echo hi\n")
        self.assertIn("anchor", unsupported)
        self.assertIsInstance(node["steps"][0]["run"], wp.Unsupported)

    def test_nested_block_value_in_item(self):
        node, unsupported = wp.parse(
            "steps:\n  - with:\n      subject-path: dist/*\n")
        self.assertEqual(unsupported, "")
        self.assertEqual(node["steps"][0]["with"], {"subject-path": "dist/*"})

    def test_empty_value_in_item(self):
        node, unsupported = wp.parse("steps:\n  - name:\n  - uses: x\n")
        self.assertIsNone(node["steps"][0]["name"])

    def test_unparseable_deeper_line_guarantees_progress(self):
        node, unsupported = wp.parse("steps:\n  - uses: x\n    - bogus\n")
        self.assertIn("unparseable line", unsupported)
        self.assertEqual(node["steps"][0]["uses"], "x")

    def test_unsupported_plain_scalar_item(self):
        node, unsupported = wp.parse("items:\n  - &anch\n")
        self.assertIn("anchor", unsupported)
        self.assertIsInstance(node["items"][0], wp.Unsupported)


class TestParseMapping(unittest.TestCase):
    def test_literal_and_folded_block_scalars(self):
        node, unsupported = wp.parse("a: |\n  line1\n  line2\nb: >\n  x\n  y\n")
        self.assertEqual(unsupported, "")
        self.assertEqual(node["a"], "line1\nline2")
        self.assertEqual(node["b"], "x y")

    def test_line_without_colon_is_trailing_content(self):
        node, reason = wp.parse("a: b\nnocolon\n")
        self.assertIsNone(node)
        self.assertIn("trailing content", reason)


# --------------------------------------------------------------------------- workflow view
class TestViewHelpers(unittest.TestCase):
    def test_if_state(self):
        self.assertEqual(wp._if_state(False), "literal_false")
        self.assertIs(wp._if_state(None), "enabled")
        self.assertEqual(wp._if_state(" FALSE "), "literal_false")
        self.assertEqual(wp._if_state("${{ x }}"), "dynamic")
        self.assertEqual(wp._if_state(""), "enabled")
        self.assertEqual(wp._if_state(3), "dynamic")

    def test_continue_on_error_state(self):
        self.assertEqual(wp._coe_state(True), "true")
        self.assertEqual(wp._coe_state(False), "false")
        self.assertEqual(wp._coe_state(None), "absent")
        self.assertEqual(wp._coe_state("true"), "true")
        self.assertEqual(wp._coe_state("FALSE"), "false")
        self.assertEqual(wp._coe_state("${{ x }}"), "dynamic")
        self.assertEqual(wp._coe_state(3), "dynamic")

    def test_canonical_with_states(self):
        self.assertEqual(wp._canonical_with(None), ())
        self.assertEqual(dict(wp._canonical_with({
            "none": None, "empty": "", "yes": True, "no": False,
            "expr": "${{ x }}", "blank_expr": "${{ }}", "text": "v",
            "num": 5, "coll": ["x"],
        })), {
            "none": "empty", "empty": "empty", "yes": "true", "no": "false",
            "expr": "dynamic", "blank_expr": "empty", "text": "set",
            "num": "set", "coll": "dynamic",
        })

    def test_permissions_map(self):
        self.assertIsNone(wp._permissions_map(None))
        self.assertEqual(wp._permissions_map("read-all"), ())
        self.assertEqual(dict(wp._permissions_map({"contents": "Read", "x": None})),
                         {"contents": "read", "x": ""})

    def test_view_non_dict_node(self):
        self.assertIsNone(wp.view(["a"], ""))
        self.assertIsNone(wp.parse_workflow("- a\n- b\n"))

    def test_view_non_dict_jobs(self):
        view = wp.view({"jobs": "nope"}, "")
        self.assertEqual(view.jobs, ())

    def test_view_non_dict_job_and_step(self):
        view = wp.parse_workflow(
            "jobs:\n  bad: scalar\n  ok:\n    steps:\n      - plainstring\n")
        self.assertEqual(len(view.jobs), 1)
        self.assertEqual(view.jobs[0].name, "ok")
        self.assertEqual(view.jobs[0].steps, ())

    def test_effective_permissions_inherit_and_empty(self):
        wf = wp.parse_workflow("permissions:\n  contents: read\n"
                               "jobs:\n  a:\n    steps: []\n")
        self.assertEqual(dict(wp.effective_permissions(wf, wf.jobs[0])),
                         {"contents": "read"})
        bare = wp.parse_workflow("jobs:\n  a:\n    steps: []\n")
        self.assertEqual(wp.effective_permissions(bare, bare.jobs[0]), ())


# --------------------------------------------------------------------------- publish tokens
class TestPublishTokens(unittest.TestCase):
    def test_comment_and_blank_lines_skipped(self):
        self.assertEqual(wp._publish_command_tokens("# note\n\nnpm publish"),
                         ["npm", "publish"])

    def test_overlong_command_unclassifiable(self):
        self.assertIsNone(wp._publish_command_tokens("cmd " + "x " * 30))

    def test_comment_only_command_has_no_tokens(self):
        self.assertEqual(wp._publish_command_tokens("# only\n"), [])
        self.assertIs(wp._run_is_publish("# only\n"), False)


# --------------------------------------------------------------------------- publication intent
class TestArtifactPublicationIntent(unittest.TestCase):
    def test_indeterminate_when_existence_check_raises(self):
        ctx = _intent_ctx(_FakeStatic(exists_raises=True))
        self.assertEqual(wp.artifact_publication_intent(ctx), "indeterminate")

    def test_present_via_app_dependency(self):
        ctx = _intent_ctx(_FakeStatic(app_dep="release-please"))
        self.assertEqual(wp.artifact_publication_intent(ctx), "present")

    def test_present_via_root_dependency_for_non_root_app(self):
        root_static = _FakeStatic()
        root_static.has_dep = lambda names: "semantic-release"
        ctx = SimpleNamespace(static=root_static, app=SimpleNamespace(path="pkg"),
                              app_static=lambda: _FakeStatic(app_dep=None))
        self.assertEqual(wp.artifact_publication_intent(ctx), "present")

    def test_indeterminate_when_workflow_discovery_degraded(self):
        ctx = _intent_ctx(_FakeStatic(glob_obs=_glob_unreadable()))
        self.assertEqual(wp.artifact_publication_intent(ctx), "indeterminate")

    def test_indeterminate_when_workflow_unreadable(self):
        static = _FakeStatic(glob_obs=_glob_ok(".github/workflows/w.yml"),
                             reads={".github/workflows/w.yml": _read_err()})
        self.assertEqual(wp.artifact_publication_intent(_intent_ctx(static)),
                         "indeterminate")

    def test_indeterminate_when_workflow_unparseable(self):
        static = _FakeStatic(glob_obs=_glob_ok(".github/workflows/w.yml"),
                             reads={".github/workflows/w.yml": _read_ok("\tjobs: {}")})
        self.assertEqual(wp.artifact_publication_intent(_intent_ctx(static)),
                         "indeterminate")

    def test_literal_false_job_is_skipped(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    if: false\n"
                "    uses: googleapis/release-please-action@v4\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "absent")

    def test_job_level_publication_action(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    uses: changesets/action@v1\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "present")

    def test_job_level_publication_action_without_ref_does_not_count(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    uses: changesets/action\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "absent")

    def test_step_level_publication_action(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    steps:\n"
                "      - uses: softprops/action-gh-release@v2\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "present")

    def test_step_level_publication_action_without_ref_does_not_count(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    steps:\n"
                "      - uses: softprops/action-gh-release\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "absent")

    def test_literal_false_step_is_skipped(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    steps:\n      - if: false\n"
                "        uses: softprops/action-gh-release@v2\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "absent")

    def _docker(self, with_block: str) -> str:
        text = ("jobs:\n  x:\n    steps:\n"
                "      - uses: docker/build-push-action@v6\n" + with_block)
        ctx = _intent_ctx(_FakeStatic(glob_obs=_glob_ok("w.yml"),
                                      reads={"w.yml": _read_ok(text)}))
        return wp.artifact_publication_intent(ctx)

    def test_docker_push_literal_true(self):
        self.assertEqual(self._docker("        with:\n          push: true\n"),
                         "present")

    def test_docker_push_dynamic_expression(self):
        self.assertEqual(self._docker(
            "        with:\n          push: ${{ github.event.inputs.push }}\n"),
            "present")

    def test_docker_push_literal_false(self):
        self.assertEqual(self._docker("        with:\n          push: false\n"),
                         "absent")

    def test_docker_push_absent_default(self):
        self.assertEqual(self._docker(""), "absent")

    def test_run_publish_command(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    steps:\n      - run: npm publish --access public\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "present")

    def test_run_non_publish_command(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    steps:\n      - run: npm test\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "absent")

    def test_run_unclassifiable_command_is_indeterminate(self):
        ctx = _intent_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"),
            reads={"w.yml": _read_ok(
                "jobs:\n  x:\n    steps:\n"
                "      - run: npm test && npm publish\n")}))
        self.assertEqual(wp.artifact_publication_intent(ctx), "indeterminate")


# --------------------------------------------------------------------------- provenance
_ATTEST_PERMS = ("    permissions:\n      contents: read\n      id-token: write\n"
                 "      attestations: write\n")


class TestProvenanceCandidates(unittest.TestCase):
    def _run(self, workflow_text: str, *, name=".github/workflows/wf.yml"):
        return wp.provenance_candidates(_wf_ctx(workflow_text, name=name))

    def test_current_attest_action_complete(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest@v1\n"
            "        with:\n          subject-path: dist/*\n")
        self.assertEqual(state, "ok")
        self.assertEqual([(c.kind, c.state) for c in candidates],
                         [("attest_action", "complete")])

    def test_empty_action_ref_is_incomplete(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest\n"
            "        with:\n          subject-path: dist/*\n")
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("empty action ref", candidates[0].missing)

    def test_subject_name_digest_pair_qualifies(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest-build-provenance@v2\n"
            "        with:\n          subject-name: app\n"
            "          subject-digest: ${{ needs.x.outputs.digest }}\n")
        self.assertEqual(candidates[0].state, "complete")
        self.assertEqual(candidates[0].kind, "attest_legacy_action")

    def test_subject_name_without_digest_is_incomplete(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest-build-provenance@v2\n"
            "        with:\n          subject-name: app\n")
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("subject inputs", candidates[0].missing)

    def test_sbom_action_requires_sbom_path(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest-sbom@v1\n"
            "        with:\n          subject-path: dist/*\n")
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("sbom-path", candidates[0].missing)

    def test_sbom_action_complete_with_sbom_path(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest-sbom@v1\n"
            "        with:\n          subject-path: dist/*\n"
            "          sbom-path: sbom.json\n")
        self.assertEqual(candidates[0].state, "complete")

    def test_step_continue_on_error_is_indeterminate(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest@v1\n"
            "        continue-on-error: true\n"
            "        with:\n          subject-path: dist/*\n")
        self.assertEqual(candidates[0].state, "indeterminate")

    def test_job_continue_on_error_is_indeterminate(self):
        state, candidates = self._run(
            "jobs:\n  a:\n    continue-on-error: true\n" + _ATTEST_PERMS +
            "    steps:\n      - uses: actions/attest@v1\n"
            "        with:\n          subject-path: dist/*\n")
        self.assertEqual(candidates[0].state, "indeterminate")

    def test_literal_false_step_is_excluded(self):
        state, candidates = self._run(
            "jobs:\n  a:\n" + _ATTEST_PERMS +
            "    steps:\n      - if: false\n        uses: actions/attest@v1\n")
        self.assertEqual((state, candidates), ("ok", []))

    def test_candidates_sorted_in_total_order(self):
        state, candidates = wp.provenance_candidates(_cand_ctx(_FakeStatic(
            glob_obs=_glob_ok("a.yml", "b.yml"),
            reads={
                "a.yml": _read_ok("jobs:\n  a:\n" + _ATTEST_PERMS +
                                  "    steps:\n      - uses: actions/attest@v1\n"
                                  "        with:\n          subject-path: x\n"),
                "b.yml": _read_ok("jobs:\n  b:\n" + _ATTEST_PERMS +
                                  "    steps:\n      - uses: actions/attest@v1\n"
                                  "        with:\n          subject-path: x\n"),
            })))
        self.assertEqual([c.workflow_path for c in candidates], ["a.yml", "b.yml"])

    def test_unsupported_yaml_marks_state_indeterminate(self):
        state, candidates = self._run("jobs:\n  a: &anchor\n  b: *anchor\n")
        self.assertEqual(state, "indeterminate")
        self.assertEqual(candidates, [])

    def test_unreadable_workflow_is_indeterminate(self):
        ctx = _cand_ctx(_FakeStatic(
            glob_obs=_glob_ok("w.yml"), reads={"w.yml": _read_err()}))
        self.assertEqual(wp.provenance_candidates(ctx), ("indeterminate", []))

    def test_discovery_overflow(self):
        ctx = _cand_ctx(_FakeStatic(glob_obs=_glob_overflow()))
        self.assertEqual(wp.provenance_candidates(ctx), ("overflow", []))

    def test_discovery_unreadable_is_indeterminate(self):
        ctx = _cand_ctx(_FakeStatic(glob_obs=_glob_unreadable()))
        self.assertEqual(wp.provenance_candidates(ctx), ("indeterminate", []))

    def test_step_overflow_returns_overflow_never_truncated_pass(self):
        steps = "".join("      - uses: actions/attest@v1\n" for _ in range(257))
        state, candidates = self._run("jobs:\n  a:\n    steps:\n" + steps)
        self.assertEqual((state, candidates), ("overflow", []))

    def test_job_overflow_returns_overflow(self):
        jobs = "".join(
            f"  j{n}:\n    uses: slsa-framework/slsa-github-generator/"
            f".github/workflows/generator_generic_slsa3.yml@v2.1.0\n"
            for n in range(257))
        state, candidates = self._run("jobs:\n" + jobs)
        self.assertEqual((state, candidates), ("overflow", []))


_SLSA_GENERIC_USES = ("slsa-framework/slsa-github-generator/.github/workflows/"
                      "generator_generic_slsa3.yml")
_SLSA_CONTAINER_USES = ("slsa-framework/slsa-github-generator/.github/workflows/"
                        "generator_container_slsa3.yml")


class TestSlsaCandidates(unittest.TestCase):
    def _run(self, workflow_text: str):
        return wp.provenance_candidates(_wf_ctx(workflow_text))

    def _generic(self, perms: str, extra: str = "") -> str:
        return (f"jobs:\n  gen:\n    uses: {_SLSA_GENERIC_USES}@v2.1.0\n"
                f"    permissions:\n{perms}    with:\n"
                "      base64-subjects: ${{ needs.build.outputs.digests }}\n"
                f"{extra}")

    def test_generic_complete_with_subjects_as_file(self):
        state, candidates = self._run(
            f"jobs:\n  gen:\n    uses: {_SLSA_GENERIC_USES}@v2.1.0\n"
            "    permissions:\n      actions: read\n      id-token: write\n"
            "    with:\n      base64-subjects-as-file: digests.txt\n")
        self.assertEqual(candidates[0].state, "complete")

    def test_non_semver_ref_is_incomplete(self):
        state, candidates = self._run(
            f"jobs:\n  gen:\n    uses: {_SLSA_GENERIC_USES}@main\n"
            "    permissions:\n      actions: read\n      id-token: write\n"
            "    with:\n      base64-subjects: x\n")
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("non-semver reusable-workflow ref", candidates[0].missing)

    def test_missing_id_token_permission(self):
        state, candidates = self._run(
            self._generic("      actions: read\n"))
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("permission id-token: write", candidates[0].missing)

    def test_missing_subjects_input(self):
        state, candidates = self._run(
            f"jobs:\n  gen:\n    uses: {_SLSA_GENERIC_USES}@v2.1.0\n"
            "    permissions:\n      actions: read\n      id-token: write\n")
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("subjects input", candidates[0].missing)

    def test_upload_assets_true_requires_contents_write(self):
        state, candidates = self._run(
            self._generic("      actions: read\n      id-token: write\n",
                          "      upload-assets: true\n"))
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("permission contents: write", candidates[0].missing)

    def test_upload_assets_true_with_contents_write_passes(self):
        state, candidates = self._run(
            self._generic("      actions: read\n      id-token: write\n"
                          "      contents: write\n",
                          "      upload-assets: true\n"))
        self.assertEqual(candidates[0].state, "complete")

    def test_upload_assets_dynamic_without_contents_write_is_indeterminate(self):
        state, candidates = self._run(
            self._generic("      actions: read\n      id-token: write\n",
                          "      upload-assets: ${{ inputs.up }}\n"))
        self.assertEqual(candidates[0].state, "indeterminate")

    def test_upload_assets_dynamic_with_contents_write_proceeds(self):
        state, candidates = self._run(
            self._generic("      actions: read\n      id-token: write\n"
                          "      contents: write\n",
                          "      upload-assets: ${{ inputs.up }}\n"))
        self.assertEqual(candidates[0].state, "complete")

    def test_job_continue_on_error_is_indeterminate(self):
        state, candidates = self._run(
            self._generic("      actions: read\n      id-token: write\n")
            .replace("    with:", "    continue-on-error: true\n    with:"))
        self.assertEqual(candidates[0].state, "indeterminate")

    def _container(self, perms: str, with_block: str, secrets_block: str = "") -> str:
        secrets = f"    secrets:\n{secrets_block}" if secrets_block else ""
        return (f"jobs:\n  gen:\n    uses: {_SLSA_CONTAINER_USES}@v2.1.0\n"
                f"    permissions:\n{perms}    with:\n{with_block}{secrets}")

    def test_container_complete(self):
        state, candidates = self._run(self._container(
            "      actions: read\n      id-token: write\n      packages: write\n",
            "      digest: ${{ needs.x.outputs.digest }}\n      image: app\n",
            "      registry-username: u\n      registry-password: p\n"))
        self.assertEqual(candidates[0].state, "complete")
        self.assertEqual(candidates[0].kind, "slsa_container")

    def test_container_missing_packages_permission(self):
        state, candidates = self._run(self._container(
            "      actions: read\n      id-token: write\n",
            "      digest: d\n      image: app\n      registry-username: u\n"
            "      registry-password: p\n"))
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("permission packages: write", candidates[0].missing)

    def test_container_missing_digest(self):
        state, candidates = self._run(self._container(
            "      actions: read\n      id-token: write\n      packages: write\n",
            "      image: app\n      registry-username: u\n      registry-password: p\n"))
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("digest input", candidates[0].missing)

    def test_container_missing_named_secret(self):
        state, candidates = self._run(self._container(
            "      actions: read\n      id-token: write\n      packages: write\n",
            "      digest: d\n      image: app\n",
            "      registry-username: u\n"))
        self.assertEqual(candidates[0].state, "incomplete")
        self.assertIn("input/secret registry-password", candidates[0].missing)


# -------------------------------------------------------------------- agent policy: Claude settings
class TestEvaluateClaudeSettings(unittest.TestCase):
    def test_permissions_not_an_object(self):
        report = ap.evaluate_claude_settings("p", {"permissions": "x"})
        self.assertEqual((report.state, report.categories),
                         ("malformed", ("permissions not an object",)))

    def test_non_string_mode_is_unsupported(self):
        report = ap.evaluate_claude_settings("p", {"permissions": {"defaultMode": 5}})
        self.assertEqual(report.state, "unsupported_mode")

    def test_unknown_mode_is_unsupported(self):
        report = ap.evaluate_claude_settings(
            "p", {"permissions": {"defaultMode": "yolo"}})
        self.assertEqual((report.state, report.categories),
                         ("unsupported_mode", ("unknown default mode",)))

    def test_non_string_allow_rule_is_malformed(self):
        report = ap.evaluate_claude_settings("p", {"permissions": {"allow": [42]}})
        self.assertEqual((report.state, report.categories),
                         ("malformed", ("non-string rule",)))

    def test_dangerous_allow_rule(self):
        report = ap.evaluate_claude_settings(
            "p", {"permissions": {"allow": ["Bash(rm -rf /tmp/x)"]}})
        self.assertEqual(report.state, "dangerous_allow")
        self.assertIn("dangerous allow rule", report.categories)

    def test_broad_and_dangerous_rules_deduplicated(self):
        report = ap.evaluate_claude_settings(
            "p", {"permissions": {"allow": ["Bash(*)", "Bash(git push origin main)"]}})
        self.assertEqual(report.state, "dangerous_allow")
        self.assertEqual(report.categories,
                         ("broad pre-approved mutation/command rule",
                          "dangerous allow rule"))

    def test_accept_edits_missing_consequence_guards(self):
        report = ap.evaluate_claude_settings("p", {"permissions": {
            "defaultMode": "acceptEdits",
            "deny": ["Read(.env*)", "Read(*.pem)", "Read(.ssh/**)"],
        }})
        self.assertEqual(report.state, "consequence_guards_incomplete")
        self.assertEqual(set(report.categories),
                         {"destructive file actions",
                          "consequential push/merge/deploy/release/publish",
                          "protected-control mutation"})

    def test_accept_edits_with_full_guards_is_safe(self):
        report = ap.evaluate_claude_settings("p", {"permissions": {
            "defaultMode": "acceptEdits",
            "deny": ["Read(.env*)", "Read(*.pem)", "Read(.ssh/**)"],
            "ask": ["Bash(rm -rf *)", "Bash(git push)",
                    "Edit(.github/workflows/*)", 7],
        }})
        self.assertEqual(report.state, "safe")

    def test_is_broad_allow_scoping(self):
        self.assertFalse(ap._is_broad_allow("Bash(npm test)"))
        self.assertTrue(ap._is_broad_allow("Bash(*)"))
        self.assertTrue(ap._is_broad_allow("Bash(npm *)"))
        self.assertTrue(ap._is_broad_allow("Bash"))
        self.assertFalse(ap._is_broad_allow("WebFetch"))


# --------------------------------------------------------------------------- agent policy: generic
class TestEvaluateGenericPolicy(unittest.TestCase):
    def test_text_only_document(self):
        report = ap.evaluate_generic_policy("p", None,
                                            "Never push to main without approval.")
        self.assertEqual(report.state, "safe")

    def test_permissive_prose_fails(self):
        report = ap.evaluate_generic_policy("p", None, "Agents allow all operations.")
        self.assertEqual((report.state, report.categories),
                         ("dangerous_allow", ("permissive prose",)))

    def test_json_without_policy_statements_fails(self):
        report = ap.evaluate_generic_policy("p", {"note": "nothing here"}, "")
        self.assertEqual((report.state, report.categories),
                         ("malformed", ("no parseable policy statements",)))

    def test_deny_without_targets_fails(self):
        report = ap.evaluate_generic_policy("p", None, "Never give up.")
        self.assertEqual(report.state, "malformed")

    def test_json_dumps_bounded_returns_empty_on_unserializable(self):
        self.assertEqual(ap.json_dumps_bounded({"a": object()}), "")
        self.assertEqual(ap.json_dumps_bounded({"a": 1}), '{"a": 1}')


# ------------------------------------------------------------------------- agent policy: CODEOWNERS
class TestCodeownersParsing(unittest.TestCase):
    def test_comments_and_blank_lines_ignored(self):
        rules = ap.parse_codeowners("# comment\n\n* @team\n")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].owners, ("@team",))

    def test_invalid_owner_tokens_filtered(self):
        rules = ap.parse_codeowners("docs/** not-an-owner @ok\n")
        self.assertEqual(rules[0].owners, ("@ok",))
        self.assertFalse(ap._valid_owner("no-at-sign"))
        self.assertFalse(ap._valid_owner("@your-team"))

    def test_unsupported_patterns(self):
        self.assertFalse(ap._pattern_supported(""))
        self.assertFalse(ap._pattern_supported("!x"))
        self.assertFalse(ap._pattern_supported("\\#x"))
        self.assertFalse(ap._pattern_supported("a[bc]"))
        self.assertFalse(ap._pattern_supported("a***b"))
        self.assertTrue(ap._pattern_supported("docs/**"))

    def test_matching_forms(self):
        self.assertTrue(ap.codeowners_matches("/docs/**", "docs/a.md"))
        self.assertTrue(ap.codeowners_matches("**/foo.md", "a/b/foo.md"))
        self.assertTrue(ap.codeowners_matches("docs/", "docs/a.md"))
        self.assertFalse(ap.codeowners_matches("docs/", "src/a.md"))
        self.assertTrue(ap.codeowners_matches("doc?", "docs"))
        self.assertTrue(ap.codeowners_matches("docs/x", "docs/x"))
        self.assertFalse(ap.codeowners_matches("docs/x", "docs/y"))


class TestOwnershipForTargets(unittest.TestCase):
    def test_unsupported_range_pattern_marks_nothing(self):
        rules = [ap.CodeownersRule("docs/[ab]", ("@x",), False, 0)]
        self.assertEqual(ap.ownership_for_targets(rules, ["docs/a"]),
                         {"docs/a": "uncovered"})

    def test_unsupported_empty_body_marks_nothing(self):
        rules = ap.parse_codeowners("!\n")
        self.assertEqual(ap.ownership_for_targets(rules, ["AGENTS.md"]),
                         {"AGENTS.md": "uncovered"})

    def test_unsupported_escaped_comment_body_evaluated(self):
        rules = ap.parse_codeowners("\\#foo @x\n")
        self.assertEqual(ap.ownership_for_targets(rules, ["foo"]),
                         {"foo": "uncovered"})

    def test_negated_pattern_marks_uncertain(self):
        rules = ap.parse_codeowners("!AGENTS.md\n")
        self.assertEqual(ap.ownership_for_targets(rules, ["AGENTS.md"]),
                         {"AGENTS.md": "uncertain"})

    def test_later_supported_rule_overrides_uncertainty(self):
        rules = ap.parse_codeowners("!AGENTS.md\n* @team\n")
        self.assertEqual(ap.ownership_for_targets(rules, ["AGENTS.md"]),
                         {"AGENTS.md": "owned"})

    def test_supported_match_without_owners_is_unowned(self):
        rules = ap.parse_codeowners("docs/**\n")
        self.assertEqual(ap.ownership_for_targets(rules, ["docs/a.md"]),
                         {"docs/a.md": "unowned"})


# --------------------------------------------------------------------- agent policy: path discovery
class TestControlPathDiscovery(unittest.TestCase):
    def test_agent_control_paths_include_instructions_rules_and_skills(self):
        root, ctx = _repo_ctx({
            ".github/instructions/a.instructions.md": "# A\n",
            ".cursor/rules/b.mdc": "# B\n",
            "AGENTS.md": "# A\n",
        })
        self.addCleanup(rmtree, root)
        paths = ap.agent_control_paths(ctx)
        self.assertIn(".github/instructions/a.instructions.md", paths)
        self.assertIn(".cursor/rules/b.mdc", paths)
        self.assertIn("AGENTS.md", paths)
        self.assertEqual(paths, sorted(paths))

    def test_select_codeowners_absent(self):
        root, ctx = _repo_ctx({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        self.assertIsNone(ap.select_codeowners(ctx))


if __name__ == "__main__":
    unittest.main()
