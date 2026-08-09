import io
import json
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from readiness import cli, history
from readiness.fix import recipes

from tests._util import make_repo, rmtree

REPORT = {
    "detection": {"languages": ["python"]},
    "results": [
        {"id": "style.linter_config", "title": "Linter Config", "status": "fail"},
        {"id": "security.gitignore_comprehensive", "title": "Gitignore", "status": "fail"},
        {"id": "docs.readme", "title": "README", "status": "fail"},
        {"id": "security.branch_protection", "title": "Branch Protection", "status": "fail"},
        {"id": "style.type_check", "title": "Type Check", "status": "fail"},
        {"id": "docs.skills", "title": "Skills", "status": "pass"},  # not failing -> ignored
    ],
}

LOOP_REPORT = {
    "detection": {"languages": ["python"]},
    "results": [
        {"id": "loop.loop_runs_dir", "title": "Loop Run Log README", "status": "fail"},
        {"id": "loop.rules_index", "title": "Loop Rules Index", "status": "fail"},
        {"id": "loop.denylist", "title": "Loop Denylist", "status": "fail"},
        {"id": "loop.signal_schema", "title": "Signal Schema README", "status": "fail"},
        {"id": "loop.pr_artifact_template", "title": "PR Artifact Evidence Template"
                                                     , "status": "fail"},
        {"id": "loop.skills_present", "title": "OMP Loop Skills", "status": "fail"},
        {"id": "loop.prompt_contracts", "title": "Loop Prompt Contracts", "status": "fail"},
        {"id": "loop.architecture_doc", "title": "Architecture Doc", "status": "fail"},
        {"id": "loop.domain_docs", "title": "Domain README Docs", "status": "fail"},
    ],
}

LOOP_TARGETS = [
    "loop-runs/README.md",
    ".omp/rules/denylist.md",
    "signals/README.md",
    ".omp/commands/pr-artifact-template.md",
]

UNSAFE_LOOP_TARGETS = [
    ".agents/readiness/config.json",
    ".omp/skills",
    "domains",
    ".omp/commands/goal.md",
    ".omp/commands/loop.md",
    "ARCHITECTURE.md",
    "docs/MOBILE.md",
]


def _cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def _args(root, apply=False, report=None, latest=False, **overrides):
    base = dict(project=str(root), apply=apply, report=report, latest=latest,
                reports_dir=None, github=False, host_proxy=False, format="markdown",
                include=None, exclude=None, instructions=None)
    base.update(overrides)
    return types.SimpleNamespace(**base)


class TestResolveScaffold(unittest.TestCase):
    def test_language_aware_and_static(self):
        self.assertEqual(
                         recipes.resolve_scaffold("style.linter_config", ["python"]),
                         ("ruff.toml", "ruff.toml"))
        self.assertEqual(
                         recipes.resolve_scaffold("style.linter_config", ["npm"]),
                         (".eslintrc.json", "eslintrc.json"))
        self.assertEqual(
                         recipes.resolve_scaffold("style.formatter", ["npm"]),
                         (".prettierrc.json", "prettierrc.json"))
        self.assertEqual(
                         recipes.resolve_scaffold("style.formatter", ["python"]),
                         ("ruff.toml", "ruff.toml"))
        self.assertEqual(recipes.resolve_scaffold("security.security_md", [])[0], "SECURITY.md")
        self.assertEqual(
                         recipes.resolve_scaffold("security.gitignore_comprehensive", [])[1],
                         "gitignore.ra1")
        self.assertIsNone(recipes.resolve_scaffold("unknown.criterion", []))

    def test_loop_scaffolds_are_only_safe_four(self):
        expected = {
            "loop.loop_runs_dir": ("loop-runs/README.md", "loop/loop-runs-README.md"),
            "loop.denylist": (".omp/rules/denylist.md", "loop/denylist.md"),
            "loop.signal_schema": ("signals/README.md", "loop/signals-README.md"),
            "loop.pr_artifact_template": (
                                          ".omp/commands/pr-artifact-template.md",
                                          "loop/pr-artifact-template.md"),
        }
        for cid, scaffold in expected.items():
            self.assertEqual(recipes.resolve_scaffold(cid, []), scaffold)
        for cid in [
            "loop.rules_index",
            "loop.skills_present",
            "loop.prompt_contracts",
            "loop.architecture_doc",
            "loop.domain_docs",
            "loop.mobile_doc",
            "loop.smoke_artifacts_cited",
        ]:
            self.assertIsNone(recipes.resolve_scaffold(cid, []))


class TestBuildPlan(unittest.TestCase):
    def test_buckets(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, REPORT)
        auto_ids = {a["id"] for a in plan["auto"]}
        self.assertIn("style.linter_config", auto_ids)
        self.assertIn("security.gitignore_comprehensive", auto_ids)
        self.assertEqual([p["id"] for p in plan["propose"]], ["docs.readme"])
        self.assertEqual([g["id"] for g in plan["github"]], ["security.branch_protection"])
        assert "style.type_check" in {i["id"] for i in plan["manual"]}


class TestApplyPlan(unittest.TestCase):
    def test_writes_missing_and_skips_existing(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, REPORT)
        result = recipes.apply_plan(root, plan, write=True)
        written = {w["target"] for w in result["written"]}
        self.assertIn("ruff.toml", written)
        self.assertIn(".gitignore", written)
        self.assertIn("line-length", (root / "ruff.toml").read_text())
        gi = (root / ".gitignore").read_text()
        self.assertIn("/.ra1/reports/", gi)
        # idempotent re-run -> everything skipped
        plan2 = recipes.build_plan(root, REPORT)
        result2 = recipes.apply_plan(root, plan2, write=True)
        self.assertEqual(result2["written"], [])
        self.assertIn("ruff.toml", {s["target"] for s in result2["skipped"]})

    def test_never_overwrites_existing(self):
        root = make_repo({"ruff.toml": "# my custom config\n"})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, REPORT)
        recipes.apply_plan(root, plan, write=True)
        self.assertEqual((root / "ruff.toml").read_text(), "# my custom config\n")


class TestGitignoreCreateOnly(unittest.TestCase):
    def test_create_when_missing(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = {"auto": [{"target": ".gitignore", "template": "gitignore.ra1",
                          "criterion_ids": ["security.gitignore_comprehensive"],
                          "exists": False}]}
        result = recipes.apply_plan(root, plan, write=True)
        self.assertEqual(len(result["written"]), 1)
        self.assertEqual((root / ".gitignore").read_text().count("/.ra1/reports/"), 1)

    def test_existing_gitignore_is_never_appended(self):
        root = make_repo({".gitignore": "__pycache__/\n"})
        self.addCleanup(rmtree, root)
        plan = {"auto": [{"target": ".gitignore", "template": "gitignore.ra1",
                          "criterion_ids": ["security.gitignore_comprehensive"],
                          "exists": True}]}
        result = recipes.apply_plan(root, plan, write=True)
        self.assertEqual(result["written"], [])
        self.assertEqual((root / ".gitignore").read_text(), "__pycache__/\n")


class TestWorktreeDirty(unittest.TestCase):
    def test_states(self):
        import subprocess
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "i"], check=True)
        self.assertFalse(recipes.worktree_dirty(root))
        (root / "dirty.txt").write_text("x")
        self.assertTrue(recipes.worktree_dirty(root))


class TestRunFix(unittest.TestCase):
    def _git_repo(self, files=None, dirty=False):
        import subprocess
        root = make_repo(files or {"pyproject.toml": '[project]\nname="x"\nversion="0.1.0"\n',
                                   "README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "i"], check=True)
        if dirty:
            (root / "dirty.txt").write_text("x")
        return root

    def test_plan_mode_is_source_less(self):
        root = self._git_repo()
        self.assertEqual(recipes.run_fix(_args(root)), 0)
        self.assertFalse((root / "ruff.toml").exists())

    def test_dry_run_writes_nothing(self):
        root = self._git_repo()
        self.assertEqual(recipes.run_fix(_args(root, apply=False)), 0)
        self.assertFalse((root / "ruff.toml").exists())

    def test_apply_writes(self):
        root = self._git_repo()
        code = recipes.run_fix(_args(root, apply=True, format="json"))
        self.assertEqual(code, 0)
        self.assertTrue((root / "ruff.toml").exists())

    def test_dirty_worktree_refuses(self):
        root = self._git_repo(dirty=True)
        code = recipes.run_fix(_args(root, apply=True))
        self.assertEqual(code, 1)
        self.assertFalse((root / "ruff.toml").exists())  # nothing written on refusal

    LOOP_FILES = {
        ".ra1/config.json": '{"loop_ready": true}',
        "pyproject.toml": '[project]\nname="x"\nversion="0.1.0"\n',
        "README.md": "# x\n",
    }

    def test_loop_dry_run_apply_and_safety(self):
        root = self._git_repo(self.LOOP_FILES)

        code, out = _cli(["fix", "--project", str(root)])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# ra1-fix plan (dry run — no files written)"))
        for target in LOOP_TARGETS:
            self.assertIn(target, out)
            self.assertFalse((root / target).exists())
        for target in UNSAFE_LOOP_TARGETS:
            self.assertNotIn(f"`{target}`", out)

        code, out = _cli(["fix", "--project", str(root), "--apply"])
        self.assertEqual(code, 0)
        for target in LOOP_TARGETS:
            self.assertTrue((root / target).exists(), target)
            self.assertIn(f"`{target}`", out)
        for target in UNSAFE_LOOP_TARGETS:
            self.assertFalse((root / target).exists(), target)

        # the always-clean contract: commit the scaffolds before the idempotent re-run
        import subprocess
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "scaffolds"], check=True)
        code, out = _cli(["fix", "--project", str(root), "--apply", "--format", "json"])
        self.assertEqual(code, 0)
        import json
        contract = json.loads(out)
        self.assertEqual(contract["apply_result"]["written"], [])  # idempotent no-op
        self.assertEqual(contract["verification"]["status"], "passed")

    def test_loop_existing_targets_not_overwritten(self):
        root = self._git_repo({**self.LOOP_FILES, "loop-runs/README.md": "# custom\n"})
        code, _out = _cli(["fix", "--project", str(root), "--apply"])
        self.assertEqual(code, 0)
        self.assertEqual((root / "loop-runs/README.md").read_text(), "# custom\n")

    def test_loop_dirty_worktree_refuses_without_writes(self):
        root = self._git_repo(self.LOOP_FILES, dirty=True)
        code, _out = _cli(["fix", "--project", str(root), "--apply"])
        self.assertEqual(code, 1)
        for target in LOOP_TARGETS:
            self.assertFalse((root / target).exists(), target)


class TestFormatPlan(unittest.TestCase):
    def test_format_contains_sections(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, REPORT)
        text = recipes.format_plan(plan, dry_run=True)
        self.assertIn("Auto-apply", text)
        self.assertIn("Propose", text)
        self.assertIn("GitHub settings", text)



class TestRecipeCoverage(unittest.TestCase):
    def test_load_report_corrupt_json_returns_none(self):
        root = make_repo({"bad.json": "{not json"})
        self.addCleanup(rmtree, root)
        args = _args(root, report=str(root / "bad.json"))
        self.assertIsNone(recipes.load_report(args, root))

    def test_scaffold_kind_unresolved_goes_manual(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, {"results": [{"id": "x.s", "status": "fail"}]},
                                  registry=[{"id": "x.s", "fix": {"kind": "scaffold"}}])
        assert "x.s" in {i["id"] for i in plan["manual"]}

    def test_duplicate_target_deduped(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        report = {"detection": {"languages": ["python"]},
                  "results": [{"id": "style.linter_config", "status": "fail"},
                              {"id": "style.formatter", "status": "fail"}]}
        plan = recipes.build_plan(root, report)
        targets = [a["target"] for a in plan["auto"]]
        self.assertEqual(targets.count("ruff.toml"), 1)

    def test_apply_plan_dry_run_records_without_writing(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, REPORT)
        result = recipes.apply_plan(root, plan, write=False)
        self.assertEqual(result["written"], [])
        self.assertEqual(result["skipped"], [])
        self.assertFalse((root / "ruff.toml").exists())
        self.assertFalse((root / "ruff.toml").exists())

    def test_gitignore_dry_run_records_no_write(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, {"results": [
            {"id": "security.gitignore_comprehensive", "status": "fail", "gating": True}]})
        auto = [i for i in plan["auto"] if i["target"] == ".gitignore"]
        self.assertTrue(auto and not auto[0]["exists"])
        result = recipes.apply_plan(root, plan, write=False)
        self.assertEqual(result["written"], [])
        self.assertFalse((root / ".gitignore").exists())

    def test_format_plan_empty_sections(self):
        text = recipes.format_plan({"auto": [], "propose": [], "github": [], "manual": []})
        self.assertIn("- (none)", text)
        self.assertNotIn("## Manual", text)


class TestFocusControls(unittest.TestCase):
    def _report(self):
        return {"detection": {"languages": ["python"]}, "results": [
            {"id": "style.linter_config", "status": "fail", "gating": True},
            {"id": "docs.readme", "status": "fail", "gating": True},
            {"id": "security.branch_protection", "status": "fail", "gating": True},
            {"id": "loop.denylist", "status": "fail", "gating": False},      # advisory scaffold
            {"id": "loop.rules_index", "status": "fail", "gating": False},   # advisory non-scaffold
        ]}

    def _ids(self, plan):
        return ({a["id"] for a in plan["auto"]} | {p["id"] for p in plan["propose"]}
                | {g["id"] for g in plan["github"]} | {i["id"] for i in plan["manual"]})

    def test_advisory_scaffold_auto_nonscaffold_excluded(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        ids = self._ids(recipes.build_plan(root, self._report()))
        self.assertIn("loop.denylist", ids)
        self.assertNotIn("loop.rules_index", ids)

    def test_include_is_authoritative(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(), focus={"include": ["style.linter_config"]})
        self.assertEqual(self._ids(plan), {"style.linter_config"})

    def test_include_overrides_advisory_rule(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(), focus={"include": ["loop.rules_index"]})
        self.assertIn("loop.rules_index", self._ids(plan))

    def test_exclude_removes(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(), focus={"exclude": ["style.linter_config"]})
        self.assertNotIn("style.linter_config", self._ids(plan))

    def test_pillar_exclude(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(),
                                  focus={"pillar_exclude": {"Security & Governance"}})
        self.assertNotIn("security.branch_protection", self._ids(plan))

    def test_prioritize_orders_pillar_first(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        report = {"detection": {"languages": ["python"]}, "results": [
            {"id": "style.linter_config", "status": "fail", "gating": True},
            {"id": "security.security_md", "status": "fail", "gating": True},
        ]}
        plan = recipes.build_plan(
                                  root,
                                  report,
                                  focus={"pillar_prioritize": {"Security & Governance"}})
        self.assertEqual(plan["auto"][0]["id"], "security.security_md")


class TestInstructionGrammar(unittest.TestCase):
    def setUp(self):
        from readiness import score
        self.pillars = {c["pillar"] for c in score.load_registry()}

    def test_prioritize_and_exclude(self):
        p = recipes.parse_instructions("prioritize security and do not touch docs", self.pillars)
        self.assertIn("Security & Governance", p["pillar_prioritize"])
        self.assertIn("Documentation", p["pillar_exclude"])
        self.assertFalse(p["unsupported"])

    def test_ci_alias(self):
        p = recipes.parse_instructions("do not touch ci", self.pillars)
        self.assertIn("Build System", p["pillar_exclude"])

    def test_unsupported_freeform(self):
        p = recipes.parse_instructions("please make everything perfect somehow", self.pillars)
        self.assertTrue(p["unsupported"])
        self.assertEqual(p["pillar_exclude"], set())

    def test_unknown_pillar_word(self):
        p = recipes.parse_instructions("skip nonsensepillar", self.pillars)
        self.assertTrue(p["unsupported"])

    def test_empty(self):
        self.assertFalse(recipes.parse_instructions(None, self.pillars)["unsupported"])


class TestFixCliFocus(unittest.TestCase):
    def _seed(self, root):
        from readiness.run import AnalyzeOptions, analyze
        report = analyze(str(root), AnalyzeOptions()).to_dict()
        source = history.admit_or_create_current_source(str(root / ".ra1" / "reports"))
        try:
            history.store_history(report, source)
        finally:
            source.close()
        return report

    def _git_repo(self):
        import subprocess
        root = make_repo({"pyproject.toml": '[project]\nname="x"\nversion="0.1.0"\n',
                          "README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "i"], check=True)
        return root

    def test_instructions_unsupported_note_and_verify(self):
        root = self._git_repo()
        self._seed(root)
        code, out = _cli(["fix", "--project", str(root), "--latest", "--instructions"
            , "make it nice"])
        self.assertEqual(code, 0)
        self.assertIn("## Notes", out)
        self.assertIn("not recognized", out)
        self.assertIn("## Verify", out)

    def test_exclude_via_cli(self):
        root = self._git_repo()
        self._seed(root)
        code, out = _cli(["fix", "--project", str(root), "--latest", "--exclude",
                          "style.linter_config", "style.formatter"])
        self.assertEqual(code, 0)
        self.assertNotIn("`ruff.toml`", out)

    def test_custom_store_needs_reports_dir(self):
        import io
        from contextlib import redirect_stderr
        root = self._git_repo()
        custom = root / "custom"
        from readiness.run import AnalyzeOptions, analyze
        report = analyze(str(root), AnalyzeOptions()).to_dict()
        source = history.admit_or_create_current_source(str(custom))
        try:
            history.store_history(report, source)
        finally:
            source.close()
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["fix", "--project", str(root), "--latest"])
        self.assertEqual(code, 2)  # default reports root has no history
        code2, out = _cli(["fix", "--project", str(root), "--latest",
                           "--reports-dir", str(custom)])
        self.assertEqual(code2, 0)
        self.assertIn("ra1-fix plan", out)


class TestRunFixLatest(unittest.TestCase):
    def _git_repo(self):
        import subprocess
        root = make_repo({"pyproject.toml": '[project]\nname="x"\nversion="0.1.0"\n',
                          "README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "i"], check=True)
        return root

    def test_latest_no_history_errors(self):
        root = self._git_repo()
        code, _ = _cli(["fix", "--project", str(root), "--latest"])
        self.assertEqual(code, 2)

    def test_latest_resolves_stored_report(self):
        from readiness.run import AnalyzeOptions, analyze
        root = self._git_repo()
        report = analyze(str(root), AnalyzeOptions()).to_dict()
        source = history.admit_or_create_current_source(str(root / ".ra1" / "reports"))
        try:
            history.store_history(report, source)
        finally:
            source.close()
        code, out = _cli(["fix", "--project", str(root), "--latest"])
        self.assertEqual(code, 0)
        self.assertIn("ruff.toml", out)
        self.assertIn("## Verify", out)


class TestFixSafety(unittest.TestCase):
    def test_fix_never_executes_mutation_commands(self):
        import subprocess
        root = make_repo({"pyproject.toml": '[project]\nname="x"\nversion="0.1.0"\n',
                          "README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "i"], check=True)
        calls = []
        from readiness import process
        real = process.run_bounded_process

        def spy(tool_id, args, **kwargs):
            calls.append((tool_id, args))
            return real(tool_id, args, **kwargs)

        with mock.patch.object(process, "run_bounded_process", spy):
            code, _ = _cli(["fix", "--project", str(root), "--apply"])
        self.assertEqual(code, 0)
        flat = " ".join(" ".join(map(str, a)) for _t, a in calls)
        for forbidden in ("push", "gh api", "-X PUT", "-X POST", "-X PATCH",
                          "pull-request", "label create"):
            self.assertNotIn(forbidden, flat)


def _run_fix(args):
    """run_fix with captured stdout/stderr."""
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = recipes.run_fix(args)
    return code, buf.getvalue(), err.getvalue()


def _schema2_report(ident, ts, *, engine="0.10.0", registry="0.7.0", detector="0.6.0",
                    status="fail", level=2):
    """A minimal valid schema-2 report dict (mirrors the released tuple)."""
    results = [{
        "id": "docs.readme", "title": "README", "pillar": "Documentation", "level": 1,
        "scope": "repository", "gating": True, "status": status, "rationale": "r",
        "evidence": [], "app_path": ".", "fixable": False, "fix_kind": "",
        "passed_apps": 0 if status != "pass" else 1, "evaluated_apps": 1,
    }]
    passed = 1 if status == "pass" else 0
    return {
        "schema_version": "2", "engine_version": engine, "registry_version": registry,
        "detector_version": detector, "commit": "", "branch": "main",
        "github_available": False, "generated_at": ts, "repository": ident,
        "detection": None,
        "score": {"level": level, "level_name": "Documented", "pass_rate": passed,
                  "gating_passed": passed, "gating_total": 1,
                  "levels": [{"level": level, "name": n, "passed": 0, "total": 0,
                              "ratio": 0.0, "achieved": False}
                             for level, n in [(1, "Functional"), (2, "Documented"),
                                          (3, "Standardized"), (4, "Optimized"),
                                          (5, "Autonomous")]],
                  "pillars": {}, "recommendations": []},
        "results": results, "advisory": [],
    }


SCHEMA1_REPORT = {
    "schema_version": "1", "engine_version": "0.2.0", "registry_version": "0.2.0",
    "detector_version": "0.2.0", "project_path": "/untrusted", "commit": "",
    "branch": "", "github_available": False, "detection": None,
    "score": {"level": 0}, "results": [], "advisory": [],
}


def _git_repo_clean(testcase, files=None):
    import subprocess
    root = make_repo(files or {"pyproject.toml": '[project]\nname="x"\nversion="0.1.0"\n',
                               "README.md": "# x\n"})
    testcase.addCleanup(rmtree, root)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "i"], check=True)
    return root


class TestExplanationAndContracts(unittest.TestCase):
    def test_explanation_collects_only_in_range_observation_refs(self):
        result = {
            "id": "x.y", "status": "fail", "rationale": "r",
            "evidence": [{"summary": "s", "tier": "T0", "source": "p"}],
            "decision_trace": {
                "reason_code": "check.fail", "rule_ref": "mod.func",
                "steps": [
                    {"kind": "rule", "evidence_refs": []},
                    {"kind": "observation", "evidence_refs": [0, 5, -1]},
                    {"kind": "evaluation", "evidence_refs": []},
                    {"kind": "conclusion", "evidence_refs": []},
                ],
                "limitations": ["lim"],
            },
        }
        exp = recipes._explanation(result)
        self.assertEqual(exp["reason_code"], "check.fail")
        self.assertEqual(exp["rule_ref"], "mod.func")
        # only the in-range index 0 resolves; 5 and -1 are dropped
        self.assertEqual(exp["evidence_citations"],
                         [{"summary": "s", "tier": "T0", "source": "p"}])
        self.assertEqual(exp["limitations"], ["lim"])

    def test_empty_fix_contract_shape(self):
        contract = recipes.empty_fix_contract("plan")
        self.assertEqual(contract["operation"], "plan")
        self.assertEqual(contract["plan"],
                         {"auto": [], "propose": [], "github": [], "manual": []})
        self.assertEqual(contract["apply_result"], {"written": [], "skipped": []})
        self.assertEqual(contract["verification"]["status"], "not_run")
        self.assertFalse(contract["verification"]["decision_successful"])

    def test_dedupe_append(self):
        errors = []
        recipes._dedupe_append(errors, "a")
        recipes._dedupe_append(errors, "a")  # duplicate is not appended twice
        recipes._dedupe_append(errors, "b")
        self.assertEqual(errors, ["a", "b"])


class TestTargetAndApplyGuards(unittest.TestCase):
    def test_target_nonempty_unreadable_root_treated_as_existing(self):
        from readiness import safe_io
        with mock.patch("readiness.safe_io.acquire_root",
                        side_effect=safe_io.RepositoryInputError("nope")):
            self.assertTrue(recipes._target_nonempty(Path("/nonexistent-ra1-xyz"), "x"))

    def test_apply_plan_unreadable_root_skips_everything(self):
        from readiness import safe_io
        plan = {"auto": [{"target": "ruff.toml", "template": "ruff.toml",
                          "criterion_ids": ["style.linter_config"], "exists": False}]}
        with mock.patch("readiness.safe_io.acquire_root",
                        side_effect=safe_io.RepositoryInputError("nope")):
            result = recipes.apply_plan(Path("/nonexistent-ra1-xyz"), plan, write=True)
        self.assertEqual(result["written"], [])
        self.assertEqual(result["skipped"], [{"target": "ruff.toml",
                                              "criterion_ids": ["style.linter_config"]}])

    def test_apply_plan_missing_template_skips(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = {"auto": [{"target": "ruff.toml", "template": "no/such-template.md",
                          "criterion_ids": ["style.linter_config"], "exists": False}]}
        result = recipes.apply_plan(root, plan, write=True)
        self.assertEqual(result["written"], [])
        self.assertEqual(result["skipped"], [{"target": "ruff.toml",
                                              "criterion_ids": ["style.linter_config"]}])
        self.assertFalse((root / "ruff.toml").exists())

    def test_apply_plan_race_created_target_is_skipped(self):
        # exists=False in the plan, but the target appears before the exclusive create:
        # create-only semantics refuse the overwrite and record a skip.
        root = make_repo({"ruff.toml": "# appeared concurrently\n"})
        self.addCleanup(rmtree, root)
        plan = {"auto": [{"target": "ruff.toml", "template": "ruff.toml",
                          "criterion_ids": ["style.linter_config"], "exists": False}]}
        result = recipes.apply_plan(root, plan, write=True)
        self.assertEqual(result["written"], [])
        self.assertEqual(result["skipped"], [{"target": "ruff.toml",
                                              "criterion_ids": ["style.linter_config"]}])
        self.assertEqual((root / "ruff.toml").read_text(), "# appeared concurrently\n")

    def test_build_plan_exists_cache_hit_branch(self):
        # The exists_cache hit branch is defensive: auto_by_target yields each target
        # once. Replay the first target through a shape-guarded sorted() wrapper so the
        # cache-hit path is exercised without touching engine code.
        root = make_repo({})
        self.addCleanup(rmtree, root)
        report = {"detection": {"languages": ["python"]},
                  "results": [{"id": "style.linter_config", "status": "fail"}]}
        real_sorted = sorted
        calls = []
        real_nonempty = recipes._target_nonempty

        def spy(r, t):
            calls.append(t)
            return real_nonempty(r, t)

        def rigged(iterable, *a, **k):
            out = real_sorted(iterable, *a, **k)
            if (out and isinstance(out[0], tuple) and len(out[0]) == 2
                    and isinstance(out[0][0], str)
                    and isinstance(out[0][1], list) and len(out[0][1]) == 2
                    and isinstance(out[0][1][0], tuple)
                    and isinstance(out[0][1][1], list)):
                return out + out[:1]
            return out

        with mock.patch.object(recipes, "_target_nonempty", spy), \
                mock.patch("builtins.sorted", rigged):
            plan = recipes.build_plan(root, report)
        self.assertEqual(len(calls), 1)  # the replayed target hit the cache
        self.assertEqual(len(plan["auto"]), 2)


class TestWorktreeDirtyEdges(unittest.TestCase):
    def test_provided_collector_is_not_closed(self):
        from readiness.collectors.git import GitCollector
        root = _git_repo_clean(self)
        collector = GitCollector(str(root))
        try:
            self.assertFalse(recipes.worktree_dirty(root, git_collector=collector))
        finally:
            collector.close()
        (root / "dirty.txt").write_text("x")
        dirty_collector = GitCollector(str(root))  # fresh: status observations are cached
        try:
            self.assertTrue(recipes.worktree_dirty(root, git_collector=dirty_collector))
        finally:
            dirty_collector.close()

    def test_non_git_repo_is_indeterminate(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        self.assertIsNone(recipes.worktree_dirty(root))


class TestLoadReportEdges(unittest.TestCase):
    def test_missing_file_returns_none(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        args = _args(root, report=str(root / "no-such-report.json"))
        self.assertIsNone(recipes.load_report(args, root))


class TestRunFixReportSources(unittest.TestCase):
    def _write_source(self, payload):
        import tempfile
        directory = Path(tempfile.mkdtemp(prefix="ar-source-"))
        self.addCleanup(rmtree, directory)
        path = directory / "source.json"
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_report_file_missing_exits_2(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        code, _out, err = _run_fix(_args(root, report=str(root / "missing.json")))
        self.assertEqual(code, 2)
        self.assertIn("not a readable regular file", err)

    def test_report_file_invalid_json_exits_2(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        code, _out, err = _run_fix(_args(root, report=str(self._write_source("{not json"))))
        self.assertEqual(code, 2)
        self.assertIn("not valid bounded JSON", err)

    def test_schema1_plan_only_transparency(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        code, out, _err = _run_fix(_args(root, report=str(self._write_source(SCHEMA1_REPORT))))
        self.assertEqual(code, 0)
        self.assertIn("_Source: schema1 (plan-only)", out)

    def test_schema1_invalid_exits_2(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        code, _out, err = _run_fix(_args(root, report=str(self._write_source(
            {"schema_version": "1"}))))
        self.assertEqual(code, 2)
        self.assertIn("not a valid schema-1 report", err)

    def test_schema1_apply_rejected(self):
        root = _git_repo_clean(self)
        code, _out, err = _run_fix(_args(root, apply=True,
                                         report=str(self._write_source(SCHEMA1_REPORT))))
        self.assertEqual(code, 2)
        self.assertIn("plan-only", err)
        self.assertFalse((root / "ruff.toml").exists())

    def test_schema2_valid_transparency(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        ident = history.repo_identity(str(root))
        source = _schema2_report(ident, "2026-06-20T00:00:00+00:00")
        code, out, _err = _run_fix(_args(root, report=str(self._write_source(source))))
        self.assertEqual(code, 0)
        self.assertIn("_Source: schema2", out)

    def test_schema2_invalid_exits_2(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        code, _out, err = _run_fix(_args(root, report=str(self._write_source(
            {"schema_version": "2", "engine_version": "0.10.0"}))))
        self.assertEqual(code, 2)
        self.assertIn("failed strict validation", err)

    def test_unknown_schema_is_ignored(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        code, out, _err = _run_fix(_args(root, report=str(self._write_source(
            {"schema_version": "9"}))))
        self.assertEqual(code, 0)
        self.assertNotIn("_Source:", out)

    def test_latest_empty_reports_root_exits_2(self):
        root = make_repo({"README.md": "# x\n"})
        self.addCleanup(rmtree, root)
        (root / ".ra1" / "reports").mkdir(parents=True)
        code, _out, err = _run_fix(_args(root, latest=True))
        self.assertEqual(code, 2)
        self.assertIn("no readiness history", err)


class TestRunFixApplyTransparency(unittest.TestCase):
    def _write_source(self, payload):
        import tempfile
        directory = Path(tempfile.mkdtemp(prefix="ar-source-"))
        self.addCleanup(rmtree, directory)
        path = directory / "source.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_schema2_source_is_not_comparable_note(self):
        root = _git_repo_clean(self)
        ident = history.repo_identity(str(root))
        source = _schema2_report(ident, "2026-06-20T00:00:00+00:00")
        code, out, _err = _run_fix(_args(root, apply=True, format="markdown",
                                         report=str(self._write_source(source))))
        self.assertEqual(code, 0)
        self.assertIn("source report not comparable", out)

    def test_stale_schema3_source_note(self):
        import subprocess

        from readiness.run import AnalyzeOptions, analyze
        root = _git_repo_clean(self)
        source = analyze(str(root), AnalyzeOptions()).to_dict()
        srcfile = self._write_source(source)
        (root / "SECURITY.md").write_text("# Security Policy\n\n"
                                          "Report issues to the maintainers.\n")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "security"], check=True)
        code, out, _err = _run_fix(_args(root, apply=True, format="markdown",
                                         report=str(srcfile)))
        self.assertEqual(code, 0)
        self.assertIn("source report is stale", out)

    def test_current_schema3_source_no_note(self):
        from readiness.run import AnalyzeOptions, analyze
        root = _git_repo_clean(self)
        source = analyze(str(root), AnalyzeOptions()).to_dict()
        srcfile = self._write_source(source)
        code, out, _err = _run_fix(_args(root, apply=True, format="markdown",
                                         report=str(srcfile)))
        self.assertEqual(code, 0)
        self.assertNotIn("source report not comparable", out)
        self.assertNotIn("source report is stale", out)


class TestRunFixApplyGuards(unittest.TestCase):
    def _fake_report(self, *, level=1, detection=None, static=True, git=True, github=True):
        report = mock.Mock()
        report.score = types.SimpleNamespace(level=level)
        report.to_dict.return_value = {
            "detection": detection,
            "assessment_provenance": {"invocation": {
                "static": {"collection_complete": static},
                "git": {"collection_complete": git},
                "github": {"requested": False, "collection_complete": github},
            }},
            "results": [],
        }
        return report

    def _guarded(self, root, reports, args=None, delta=None, apply_result=None):
        """run_fix with analyze/delta/apply_plan stubbed at the recipes boundary."""
        args = args or _args(root, apply=True, format="json")
        patches = [mock.patch("readiness.run.analyze", side_effect=reports)]
        if delta is not None:
            patches.append(mock.patch.object(history, "delta", return_value=delta))
        if apply_result is not None:
            patches.append(mock.patch.object(recipes, "apply_plan",
                                             return_value=apply_result))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return _run_fix(args)

    def test_repository_indeterminate_refuses_before_write(self):
        root = _git_repo_clean(self)
        report = self._fake_report(detection={"repository_indeterminate": True})
        code, out, _err = self._guarded(root, [report])
        self.assertEqual(code, 1)
        contract = json.loads(out)
        self.assertIn("repository_indeterminate", contract["verification"]["errors"])
        self.assertEqual(contract["verification"]["status"], "failed")
        self.assertFalse((root / "ruff.toml").exists())

    def test_baseline_static_incomplete_refuses(self):
        root = _git_repo_clean(self)
        code, out, _err = self._guarded(root, [self._fake_report(static=False)])
        self.assertEqual(code, 1)
        self.assertIn("baseline_evidence_incomplete",
                      json.loads(out)["verification"]["errors"])

    def test_baseline_github_incomplete_refuses(self):
        root = _git_repo_clean(self)
        args = _args(root, apply=True, format="json", github=True, host_proxy=True)
        env = {"HTTPS_PROXY": "http://127.0.0.1:9", "GH_TOKEN": "unit-test-token"}
        with mock.patch.dict("os.environ", env):
            code, out, _err = self._guarded(root, [self._fake_report(github=False)],
                                            args=args)
        self.assertEqual(code, 1)
        self.assertIn("baseline_github_incomplete",
                      json.loads(out)["verification"]["errors"])

    def test_invalid_host_proxy_exits_1_before_scan(self):
        root = _git_repo_clean(self)
        args = _args(root, apply=True, format="json", github=True, host_proxy=True)
        with mock.patch.dict("os.environ", {"HTTPS_PROXY": "http://bad\x01proxy",
                                            "GH_TOKEN": "unit-test-token"}):
            code, _out, err = _run_fix(args)
        self.assertEqual(code, 1)
        self.assertIn("invalid host proxy environment", err)

    def test_verified_static_incomplete_refuses(self):
        root = _git_repo_clean(self)
        reports = [self._fake_report(), self._fake_report(static=False)]
        code, out, _err = self._guarded(root, reports)
        self.assertEqual(code, 1)
        self.assertIn("verified_evidence_incomplete",
                      json.loads(out)["verification"]["errors"])

    def test_verified_github_incomplete_refuses(self):
        root = _git_repo_clean(self)
        reports = [self._fake_report(), self._fake_report(github=False)]
        args = _args(root, apply=True, format="json", github=True)
        with mock.patch.dict("os.environ", {"GH_TOKEN": "unit-test-token"}):
            code, out, _err = self._guarded(root, reports, args=args)
        self.assertEqual(code, 1)
        self.assertIn("verified_github_incomplete",
                      json.loads(out)["verification"]["errors"])

    def test_incomparable_delta_refuses(self):
        root = _git_repo_clean(self)
        delta = {"comparable": False, "reason": "unit test"}
        code, out, _err = self._guarded(
            root, [self._fake_report(), self._fake_report()], delta=delta)
        self.assertEqual(code, 1)
        self.assertIn("delta_incomparable:unit test",
                      json.loads(out)["verification"]["errors"])

    def _comparable_delta(self, changes=(), newly_failing=()):
        return {"comparable": True, "reason": "", "newly_passing": [],
                "newly_failing": list(newly_failing), "newly_unknown": [],
                "criteria_changes": list(changes),
                "score_delta": {"level": {"from": 1, "to": 1}}}

    def test_unresolved_written_criteria_fails(self):
        root = _git_repo_clean(self)
        apply_result = {"written": [{"target": "ruff.toml",
                                     "criterion_ids": ["style.linter_config"]}],
                        "skipped": []}
        code, out, _err = self._guarded(
            root, [self._fake_report(), self._fake_report()],
            delta=self._comparable_delta(), apply_result=apply_result)
        self.assertEqual(code, 1)
        verification = json.loads(out)["verification"]
        self.assertIn("written_criteria_unresolved", verification["errors"])
        self.assertEqual(verification["unresolved"],
                         [{"id": "style.linter_config", "status": "unknown",
                           "reason_code": ""}])

    def test_regressions_fail(self):
        root = _git_repo_clean(self)
        delta = self._comparable_delta(
            changes=[{"id": "x.reg", "from": "pass", "to": "fail"},
                     {"id": "x.unk", "from": "pass", "to": "unknown"}],
            newly_failing=["x.reg"])
        code, out, _err = self._guarded(
            root, [self._fake_report(), self._fake_report()], delta=delta)
        self.assertEqual(code, 1)
        verification = json.loads(out)["verification"]
        self.assertIn("regression_detected", verification["errors"])
        self.assertEqual(verification["regressions"],
                         [{"id": "x.reg", "from": "pass", "to": "fail"},
                          {"id": "x.unk", "from": "pass", "to": "unknown"}])

    def test_level_decrease_fails(self):
        root = _git_repo_clean(self)
        reports = [self._fake_report(level=2), self._fake_report(level=1)]
        code, out, _err = self._guarded(root, reports, delta=self._comparable_delta())
        self.assertEqual(code, 1)
        verification = json.loads(out)["verification"]
        self.assertIn("level_decreased", verification["errors"])
        self.assertEqual(verification["level"], {"from": 2, "to": 1})


class TestFailApplyEmit(unittest.TestCase):
    def _delta_payload(self):
        return {"comparable": True, "reason": "", "newly_passing": [],
                "newly_failing": ["z.w"], "newly_unknown": [],
                "criteria_changes": [],
                "score_delta": {"level": {"from": 2, "to": 2}}}

    def test_markdown_renders_delta_unresolved_regressions_and_note(self):
        run = recipes.FixRun()
        run.confirmed_ids = ["a.b"]
        run.unresolved = [{"id": "x.y", "status": "fail", "reason_code": "check.fail"}]
        run.regressions = [{"id": "z.w", "from": "pass", "to": "unknown"}]
        run.delta = self._delta_payload()
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = recipes._fail_apply(types.SimpleNamespace(format="markdown"), run,
                                       "verification failed")
        self.assertEqual(code, 1)
        out = buf.getvalue()
        self.assertIn("Remediation delta", out)
        self.assertIn("Confirmed fixed: a.b.", out)
        self.assertIn("Unresolved written: x.y (fail).", out)
        self.assertIn("Regressions: z.w (pass→unknown).", out)
        self.assertIn("**verification failed.**", out)
        # no pre-existing error: the defensive apply_failed category is appended
        self.assertEqual(run.errors, ["apply_failed"])
        self.assertIn("ra1 fix: verification failed", err.getvalue())

    def test_markdown_without_confirmed_ids(self):
        run = recipes.FixRun()
        run.errors = ["written_criteria_unresolved"]
        run.unresolved = [{"id": "x.y", "status": "fail", "reason_code": "check.fail"}]
        run.delta = self._delta_payload()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = recipes._fail_apply(types.SimpleNamespace(format="markdown"), run,
                                       "verification failed")
        self.assertEqual(code, 1)
        out = buf.getvalue()
        self.assertNotIn("Confirmed fixed", out)
        self.assertIn("Unresolved written: x.y (fail).", out)

    def test_json_contract_without_stderr_note(self):
        run = recipes.FixRun()
        run.errors = ["regression_detected"]
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = recipes._fail_apply(types.SimpleNamespace(format="json"), run,
                                       "verification failed")
        self.assertEqual(code, 1)
        contract = json.loads(buf.getvalue())
        self.assertEqual(contract["verification"]["status"], "failed")
        self.assertEqual(contract["verification"]["errors"], ["regression_detected"])
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
