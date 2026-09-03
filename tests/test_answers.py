"""Focused tests for the one-answer interview engine (engine/readiness/answers.py).

Every answer plan/apply path: canonical gap resolution, stale/invalid/unrecordable/multi
value/range refusals, ignored-policy refusal, clean-baseline apply, honest score decrease,
post-write failure, and the exact `answer_contract` shape on every path.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from readiness import answers
from readiness.model import Gap

import tests._util as U


def _args(gap_id, choice=None, minutes=None, apply=False, project="."):
    return types.SimpleNamespace(project=project, gap_id=gap_id,
                                 choice=[choice] if choice else None,
                                 minutes=minutes, apply=apply, format="json")


class TestResolve(unittest.TestCase):
    def _gap(self, gap_id, input_kind="single_choice", recordable=True,
             choices=None, blocks=()):
        choices = choices or [{"id": "boolean.yes", "label": "Y", "effect": "record"},
                              {"id": "boolean.no", "label": "N", "effect": "record"}]
        return Gap(id=gap_id, kind="config", question="Q", why="W",
                   blocks=list(blocks), recordable=recordable,
                   input_kind=input_kind, choices=choices)

    def test_loop_ready_boolean(self):
        plan, err = answers._resolve({
            "gap": self._gap("config.loop_ready"), "choices": ["boolean.yes"],
            "minutes": None, "app_paths": {}, "commands": {}, "results": []})
        self.assertFalse(err)
        self.assertEqual(plan, {"kind": "config", "path": "loop_ready",
                                "value": True})

    def test_project_type_pin(self):
        plan, err = answers._resolve({
            "gap": self._gap("detect.project_type",
                             choices=[{"id": "service", "label": "Service",
                                       "effect": "record"}]),
            "choices": ["service"], "minutes": None, "app_paths": {}, "commands": {},
            "results": []})
        self.assertEqual(plan["value"], "service")

    def test_contested_multi(self):
        gap = self._gap(
            "detect.project_type.contested", input_kind="multi_choice",
            choices=[{"id": "service", "label": "Service", "effect": "record"},
                     {"id": "frontend", "label": "Frontend", "effect": "record"}])
        plan, err = answers._resolve({
            "gap": gap, "choices": ["service", "frontend"], "minutes": None,
            "app_paths": {}, "commands": {}, "results": []})
        self.assertFalse(err)
        self.assertEqual(plan["value"], sorted(["frontend", "service"]))

    def test_multi_value_rejected_for_single(self):
        _plan, err = answers._resolve({
            "gap": self._gap("config.loop_ready"), "choices": ["boolean.yes", "boolean.no"],
            "minutes": None, "app_paths": {}, "commands": {}, "results": []})
        self.assertEqual(err, "multi_value_rejected")

    def test_app_type_hashed(self):
        app_paths = {"detect.app_type." + answers._sha("apps/web"): "apps/web"}
        plan, err = answers._resolve({
            "gap": self._gap("detect.app_type." + answers._sha("apps/web"),
                             choices=[{"id": "frontend", "label": "F", "effect": "record"}]),
            "choices": ["frontend"], "minutes": None, "app_paths": app_paths,
            "commands": {}, "results": []})
        self.assertFalse(err)
        self.assertEqual(plan["path"], "detect.apps.apps/web")

    def test_stale_app_gap(self):
        # the choice is unknown before the app path is even consulted
        _plan, err = answers._resolve({
            "gap": self._gap("detect.app_type.dead", choices=[
                {"id": "frontend", "label": "F", "effect": "record"}]),
            "choices": ["frontend"], "minutes": None, "app_paths": {}, "commands": {},
            "results": []})
        self.assertEqual(err, "stale_gap")

    def test_verify_command_choice(self):
        commands = {"command.abc": "make check"}
        plan, err = answers._resolve({
            "gap": self._gap("config.acdc.verify_command",
                             choices=[{"id": "command.abc", "label": "make check",
                                       "effect": "record"}]),
            "choices": ["command.abc"], "minutes": None, "app_paths": {},
            "commands": commands, "results": []})
        self.assertEqual(plan, {"kind": "config", "path": "acdc.verify_command",
                                "value": "make check"})

    def test_ci_budget_minutes(self):
        plan, err = answers._resolve({
            "gap": self._gap("config.ci_budget_minutes", input_kind="integer",
                             choices=[]),
            "choices": [], "minutes": 15, "app_paths": {}, "commands": {},
            "results": []})
        self.assertEqual(plan, {"kind": "config", "path": "ci_budget_minutes",
                                "value": 15})

    def test_minutes_out_of_range(self):
        _plan, err = answers._resolve({
            "gap": self._gap("config.ci_budget_minutes", input_kind="integer",
                             choices=[]),
            "choices": [], "minutes": 0, "app_paths": {}, "commands": {},
            "results": []})
        self.assertEqual(err, "value_out_of_range")

    def test_minutes_gap_mismatch(self):
        _plan, err = answers._resolve({
            "gap": self._gap("config.loop_ready"), "choices": [], "minutes": 15,
            "app_paths": {}, "commands": {}, "results": []})
        self.assertEqual(err, "minutes_gap_mismatch")

    def test_stale_choice(self):
        _plan, err = answers._resolve({
            "gap": self._gap("config.loop_ready",
                             choices=[{"id": "boolean.yes", "label": "Y",
                                       "effect": "record"}]),
            "choices": ["boolean.maybe"], "minutes": None, "app_paths": {},
            "commands": {}, "results": []})
        self.assertEqual(err, "stale_choice")

    def test_non_record_effect(self):
        _plan, err = answers._resolve({
            "gap": self._gap("capability.github",
                             choices=[{"id": "github.restore_access", "label": "R",
                                       "effect": "external_action"}]),
            "choices": ["github.restore_access"], "minutes": None, "app_paths": {},
            "commands": {}, "results": []})
        self.assertEqual(err, "choice_not_recordable")

    def test_unrecordable_gap(self):
        _plan, err = answers._resolve({
            "gap": self._gap("config.acdc.verify_command", recordable=False,
                             choices=[]), "choices": [], "minutes": None,
            "app_paths": {}, "commands": {}, "results": []})
        self.assertEqual(err, "choice_required")

    def test_choice_required(self):
        _plan, err = answers._resolve({
            "gap": self._gap("config.loop_ready"), "choices": [], "minutes": None,
            "app_paths": {}, "commands": {}, "results": []})
        self.assertEqual(err, "choice_required")

    def test_non_github_waiver(self):
        gap = self._gap("capability.github",
                        choices=[{"id": "github.non_github_host", "label": "W",
                                  "effect": "record"}], blocks=["security.branch_protection"])
        plan, err = answers._resolve({
            "gap": gap, "choices": ["github.non_github_host"], "minutes": None,
            "app_paths": {}, "commands": {},
            "results": [{"id": "security.branch_protection"}]})
        self.assertFalse(err)
        self.assertEqual(plan, {"kind": "waiver",
                                "ids": ["security.branch_protection"]})


class TestMergeAndPolicy(unittest.TestCase):
    def test_apply_config_merge_preserves_keys(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-ans-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".ra1").mkdir()
        (tmp / ".ra1" / "config.json").write_text('{"existing": 1}', encoding="utf-8")
        auth = __import__("readiness.safe_io", fromlist=["acquire_root"]).acquire_root(tmp)
        _created, value = answers._apply_config(auth, "acdc.verify_command", "make check")
        auth.close()
        blob = json.loads((tmp / ".ra1" / "config.json").read_text())
        self.assertEqual(blob, {"existing": 1, "acdc": {"verify_command": "make check"}})

    def test_apply_waivers_appends_exact_ids(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-ans-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".ra1").mkdir()
        (tmp / ".ra1" / "waivers.json").write_text('[{"id":"a"}]', encoding="utf-8")
        auth = __import__("readiness.safe_io", fromlist=["acquire_root"]).acquire_root(tmp)
        _created, value = answers._apply_waivers(auth, ["b", "a"])
        auth.close()
        blob = json.loads((tmp / ".ra1" / "waivers.json").read_text())
        self.assertEqual(blob, [{"id": "a"}, {"id": "b"}])

    def test_policy_target_unignored(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-ans-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
        (tmp / ".gitignore").write_text(".ra1/config.json\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "i"], check=True)
        self.assertFalse(answers._policy_target_unignored(tmp, ".ra1/config.json"))
        self.assertTrue(answers._policy_target_unignored(tmp, ".ra1/waivers.json"))


class TestRunAnswer(unittest.TestCase):
    def _repo(self, loop_config=False):
        files = {"README.md": "# x\n", "pyproject.toml": '[project]\nname="x"\n'
                                                    'version="0.1.0"\n'}
        if loop_config:
            files[".ra1/config.json"] = '{"loop_ready": false}'
        files["Makefile"] = "check:\n\tpytest\n"
        tmp = U.make_repo(files)
        subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "i"], check=True)
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp

    def _run(self, tmp, gap_id, choice=None, minutes=None, apply=False):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = answers.run_answer(_args(gap_id, choice=choice, minutes=minutes,
                                            apply=apply, project=str(tmp)))
        return code, json.loads(buf.getvalue() or "{}")

    def test_plan_writes_nothing(self):
        tmp = self._repo()
        code, contract = self._run(tmp, "config.loop_ready", choice="boolean.no")
        self.assertEqual(code, 0)
        self.assertEqual(contract["operation"], "plan")
        self.assertEqual(contract["verification"]["status"], "not_run")
        self.assertFalse((tmp / ".ra1" / "config.json").exists())

    def test_apply_loop_ready(self):
        tmp = self._repo()
        code, contract = self._run(tmp, "config.loop_ready", choice="boolean.no",
                                   apply=True)
        self.assertEqual(code, 0, contract)
        self.assertEqual(contract["operation"], "apply")
        self.assertEqual(contract["apply_result"], {"written": True, "created": True})
        self.assertEqual(contract["verification"]["status"], "passed")
        self.assertTrue(contract["verification"]["gap_resolved"])
        self.assertTrue(contract["verification"]["decision_successful"])
        self.assertEqual(json.loads((tmp / ".ra1" / "config.json").read_text()),
                         {"loop_ready": False})

    def test_stale_gap_emits_failed_contract(self):
        tmp = self._repo()
        code, contract = self._run(tmp, "nope.gap", choice="boolean.no")
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["errors"], ["stale_gap"])
        self.assertEqual(contract["verification"]["status"], "failed")

    def test_gap_persistence_makes_second_plan_resolve(self):
        tmp = self._repo()
        code, _ = self._run(tmp, "config.loop_ready", choice="boolean.no", apply=True)
        self.assertEqual(code, 0)
        # a second run no longer offers the gap (explicit false is a decision)
        code2, contract2 = self._run(tmp, "config.loop_ready", choice="boolean.no")
        self.assertNotEqual(code2, 0)

    def test_ci_budget_minutes_apply(self):
        import unittest.mock as mock
        tmp = self._repo()
        gap = Gap(id="config.ci_budget_minutes", kind="config", question="Q",
                  why="W", recordable=True, input_kind="integer", choices=[])
        calls = {"n": 0}

        def _find(scope, gap_id):
            calls["n"] += 1
            # the pre-write scan sees the gap; the post-write rescan does not
            return gap if calls["n"] == 1 else None

        with mock.patch.object(answers, "_find_gap", side_effect=_find):
            code, contract = self._run(tmp, "config.ci_budget_minutes", minutes=30,
                                       apply=True)
        self.assertEqual(code, 0, contract.get("verification"))
        blob = json.loads((tmp / ".ra1" / "config.json").read_text())
        self.assertEqual(blob.get("ci_budget_minutes"), 30)

    def test_minutes_out_of_range_plan(self):
        import unittest.mock as mock
        tmp = self._repo()
        gap = Gap(id="config.ci_budget_minutes", kind="config", question="Q",
                  why="W", recordable=True, input_kind="integer", choices=[])
        with mock.patch.object(answers, "_find_gap", return_value=gap):
            code, contract = self._run(tmp, "config.ci_budget_minutes", minutes=2000)
        self.assertEqual(code, 1)
        self.assertIn("value_out_of_range", contract["verification"]["errors"])


class TestResolveEdgeCases(unittest.TestCase):
    def _gap(self, gap_id, input_kind="single_choice", recordable=True,
             choices=None, blocks=()):
        choices = choices if choices is not None else [
            {"id": "boolean.yes", "label": "Y", "effect": "record"},
            {"id": "boolean.no", "label": "N", "effect": "record"}]
        return Gap(id=gap_id, kind="config", question="Q", why="W",
                   blocks=list(blocks), recordable=recordable,
                   input_kind=input_kind, choices=choices)

    def _resolve(self, gap, choices, minutes=None, app_paths=None, commands=None,
                 results=None):
        return answers._resolve({
            "gap": gap, "choices": choices, "minutes": minutes,
            "app_paths": app_paths or {}, "commands": commands or {},
            "results": results or []})

    def test_minutes_on_non_integer_gap_is_stale(self):
        _plan, err = self._resolve(
            self._gap("config.ci_budget_minutes", input_kind="single_choice"),
            [], minutes=15)
        self.assertEqual(err, "stale_gap")

    def test_unrecordable_gap_with_record_choice(self):
        _plan, err = self._resolve(
            self._gap("config.loop_ready", recordable=False), ["boolean.yes"])
        self.assertEqual(err, "gap_unrecordable")

    def test_project_type_choice_outside_pin_types_is_stale(self):
        _plan, err = self._resolve(
            self._gap("detect.project_type",
                      choices=[{"id": "backend", "label": "B", "effect": "record"}]),
            ["backend"])
        self.assertEqual(err, "stale_choice")

    def test_contested_choice_outside_pin_types_is_stale(self):
        gap = self._gap("detect.project_type.contested", input_kind="multi_choice",
                        choices=[{"id": "service", "label": "S", "effect": "record"},
                                 {"id": "backend", "label": "B", "effect": "record"}])
        _plan, err = self._resolve(gap, ["service", "backend"])
        self.assertEqual(err, "stale_choice")

    def test_verify_command_choice_missing_from_scan_is_stale(self):
        _plan, err = self._resolve(
            self._gap("config.acdc.verify_command",
                      choices=[{"id": "command.abc", "label": "x", "effect": "record"}]),
            ["command.abc"], commands={})
        self.assertEqual(err, "stale_choice")

    def test_ci_budget_rejects_choice_form(self):
        _plan, err = self._resolve(
            self._gap("config.ci_budget_minutes", input_kind="integer",
                      choices=[{"id": "boolean.yes", "label": "Y", "effect": "record"}]),
            ["boolean.yes"])
        self.assertEqual(err, "value_out_of_range")

    def test_unknown_gap_id_is_stale(self):
        _plan, err = self._resolve(self._gap("config.bogus"), ["boolean.yes"])
        self.assertEqual(err, "stale_gap")


class TestApplyConfigNesting(unittest.TestCase):
    def test_merge_reuses_existing_nested_dict(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-ans-nest-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".ra1").mkdir()
        (tmp / ".ra1" / "config.json").write_text('{"acdc": {"keep": 1}}',
                                                  encoding="utf-8")
        auth = answers.safe_io.acquire_root(tmp)
        try:
            answers._apply_config(auth, "acdc.verify_command", "make check")
        finally:
            auth.close()
        blob = json.loads((tmp / ".ra1" / "config.json").read_text())
        self.assertEqual(blob, {"acdc": {"keep": 1, "verify_command": "make check"}})


class TestPolicyTargetUnignoredEdge(unittest.TestCase):
    def test_unreadable_ignore_state_refuses(self):
        # No Git repository: the ignore observation is not present, so the target is
        # treated as unsafe to write (fail closed).
        tmp = U.make_repo({"README.md": "# x"})
        self.addCleanup(lambda: U.rmtree(tmp))
        self.assertFalse(answers._policy_target_unignored(tmp, ".ra1/config.json"))


class TestRunAnswerFailurePaths(unittest.TestCase):
    def _repo(self):
        files = {"README.md": "# x\n", "pyproject.toml": '[project]\nname="x"\n'
                                                    'version="0.1.0"\n',
                 "Makefile": "check:\n\tpytest\n"}
        tmp = U.make_repo(files)
        subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "i"], check=True)
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp

    def _run(self, tmp, gap_id, choice=None, minutes=None, apply=False):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        buf, ebuf = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(ebuf):
            code = answers.run_answer(_args(gap_id, choice=choice, minutes=minutes,
                                            apply=apply, project=str(tmp)))
        return code, json.loads(buf.getvalue() or "{}"), ebuf.getvalue()

    def _safeio_shim(self, **overrides):
        shim = types.SimpleNamespace(
            acquire_root=answers.safe_io.acquire_root,
            merge_rooted_policy_json=answers.safe_io.merge_rooted_policy_json,
            RepositoryInputError=answers.safe_io.RepositoryInputError,
            SafeIoUnsupportedError=answers.safe_io.SafeIoUnsupportedError)
        for key, value in overrides.items():
            setattr(shim, key, value)
        return shim

    def test_apply_status_changes_are_recorded(self):
        tmp = self._repo()
        code, contract, _err = self._run(tmp, "config.loop_ready", choice="boolean.yes",
                                         apply=True)
        self.assertEqual(code, 0, contract.get("verification"))
        changes = contract["verification"]["status_changes"]
        self.assertTrue(changes)
        for change in changes:
            self.assertIn(change["from"], ("skipped", "pass", "fail", "unknown"))
            self.assertNotEqual(change["from"], change["to"])

    def test_ignored_policy_target_refuses_before_write(self):
        tmp = self._repo()
        (tmp / ".gitignore").write_text(".ra1/config.json\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "i2"], check=True)
        code, contract, _err = self._run(tmp, "config.loop_ready", choice="boolean.no")
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["errors"], ["policy_target_ignored"])
        self.assertFalse((tmp / ".ra1" / "config.json").exists())

    def test_dirty_worktree_refuses_apply(self):
        tmp = self._repo()
        (tmp / "README.md").write_text("# dirty\n", encoding="utf-8")
        code, contract, _err = self._run(tmp, "config.loop_ready", choice="boolean.no",
                                         apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["errors"], ["worktree_not_clean"])
        self.assertFalse((tmp / ".ra1" / "config.json").exists())

    def test_incomplete_baseline_refuses_before_write(self):
        import unittest.mock as mock

        from readiness import run as run_mod
        tmp = self._repo()
        real_analyze = run_mod.analyze
        calls = {"n": 0}

        def flip_first(root, options=None, **kw):
            calls["n"] += 1
            report = real_analyze(root, options, **kw)
            if calls["n"] == 1:
                invocation = report.assessment_provenance["invocation"]
                invocation["static"]["collection_complete"] = False
            return report

        with mock.patch.object(run_mod, "analyze", new=flip_first):
            code, contract, _err = self._run(tmp, "config.loop_ready",
                                             choice="boolean.no", apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["errors"],
                         ["baseline_evidence_incomplete"])
        self.assertFalse((tmp / ".ra1" / "config.json").exists())

    def test_root_unavailable_refuses_apply(self):
        import unittest.mock as mock
        tmp = self._repo()
        shim = self._safeio_shim(acquire_root=mock.Mock(side_effect=OSError("boom")))
        with mock.patch.object(answers, "safe_io", shim):
            code, contract, _err = self._run(tmp, "config.loop_ready",
                                             choice="boolean.no", apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["errors"], ["root_unavailable"])

    def test_merge_refusal_retains_no_partial_write(self):
        import unittest.mock as mock
        tmp = self._repo()
        shim = self._safeio_shim(merge_rooted_policy_json=mock.Mock(
            side_effect=answers.safe_io.RepositoryInputError("refused")))
        with mock.patch.object(answers, "safe_io", shim):
            code, contract, _err = self._run(tmp, "config.loop_ready",
                                             choice="boolean.no", apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["errors"], ["policy_merge_refused"])
        self.assertFalse((tmp / ".ra1" / "config.json").exists())

    def test_incomplete_verified_evidence_reports_written_failure(self):
        import unittest.mock as mock

        from readiness import run as run_mod
        tmp = self._repo()
        real_analyze = run_mod.analyze
        calls = {"n": 0}

        def flip_second(root, options=None, **kw):
            calls["n"] += 1
            report = real_analyze(root, options, **kw)
            if calls["n"] == 2:
                invocation = report.assessment_provenance["invocation"]
                invocation["git"]["collection_complete"] = False
            return report

        with mock.patch.object(run_mod, "analyze", new=flip_second):
            code, contract, _err = self._run(tmp, "config.loop_ready",
                                             choice="boolean.no", apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["errors"],
                         ["verified_evidence_incomplete"])
        # The bounded policy edit is retained and disclosed, never rolled back.
        self.assertEqual(contract["apply_result"], {"written": True, "created": True})

    def test_persistent_gap_after_rescan_exits_nonzero(self):
        import unittest.mock as mock
        tmp = self._repo()
        gap = Gap(id="config.loop_ready", kind="config", question="Q", why="W",
                  recordable=True, input_kind="single_choice",
                  choices=[{"id": "boolean.no", "label": "No", "effect": "record"}])
        with mock.patch.object(answers, "_find_gap", return_value=gap):
            code, contract, err = self._run(tmp, "config.loop_ready",
                                            choice="boolean.no", apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(contract["verification"]["status"], "passed")
        self.assertIn("gap persists", err)

    def test_non_github_host_waiver_apply(self):
        tmp = self._repo()
        code, contract, _err = self._run(tmp, "capability.github",
                                         choice="github.non_github_host", apply=True)
        self.assertEqual(code, 0, contract.get("verification"))
        self.assertEqual(contract["target_kind"], "waiver")
        self.assertEqual(contract["target"], ".ra1/waivers.json")
        self.assertEqual(contract["apply_result"], {"written": True, "created": True})
        self.assertEqual(contract["verification"]["status"], "passed")
        waived = contract["verification"]["waived_ids"]
        self.assertIn("security.branch_protection", waived)
        blob = json.loads((tmp / ".ra1" / "waivers.json").read_text())
        self.assertEqual(sorted(w["id"] for w in blob), waived)
        # Engine-authored waivers carry no free-form reason text.
        self.assertTrue(all(set(w) == {"id"} for w in blob))


if __name__ == "__main__":
    unittest.main()