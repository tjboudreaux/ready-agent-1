import io
import json
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

from readiness.fix import recipes
from readiness import cli, history
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
        {"id": "loop.pr_artifact_template", "title": "PR Artifact Evidence Template", "status": "fail"},
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
    ".github/pull_request_template.md",
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


def _args(root, apply=False, force=False, report=None):
    return types.SimpleNamespace(project=str(root), apply=apply, force=force, report=report)


class TestResolveScaffold(unittest.TestCase):
    def test_language_aware_and_static(self):
        self.assertEqual(recipes.resolve_scaffold("style.linter_config", ["python"]), ("ruff.toml", "ruff.toml"))
        self.assertEqual(recipes.resolve_scaffold("style.linter_config", ["npm"]), (".eslintrc.json", "eslintrc.json"))
        self.assertEqual(recipes.resolve_scaffold("style.formatter", ["npm"]), (".prettierrc.json", "prettierrc.json"))
        self.assertEqual(recipes.resolve_scaffold("style.formatter", ["python"]), ("ruff.toml", "ruff.toml"))
        self.assertEqual(recipes.resolve_scaffold("security.security_md", [])[0], "SECURITY.md")
        self.assertEqual(recipes.resolve_scaffold("security.gitignore_comprehensive", [])[1], "__gitignore_append__")
        self.assertIsNone(recipes.resolve_scaffold("unknown.criterion", []))

    def test_loop_scaffolds_are_only_safe_four(self):
        expected = {
            "loop.loop_runs_dir": ("loop-runs/README.md", "loop/loop-runs-README.md"),
            "loop.denylist": (".omp/rules/denylist.md", "loop/denylist.md"),
            "loop.signal_schema": ("signals/README.md", "loop/signals-README.md"),
            "loop.pr_artifact_template": (".omp/commands/pr-artifact-template.md", "loop/pr-artifact-template.md"),
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
        self.assertIn("style.type_check", plan["manual"])  # no fix recipe in registry


class TestApplyPlan(unittest.TestCase):
    def test_writes_missing_and_skips_existing(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, REPORT)
        result = recipes.apply_plan(root, plan, write=True)
        self.assertIn("ruff.toml", result["written"])
        self.assertIn(".gitignore", result["written"])
        self.assertIn("line-length", (root / "ruff.toml").read_text())
        gi = (root / ".gitignore").read_text()
        self.assertIn(".env", gi)
        self.assertIn("__pycache__/", gi)
        # idempotent re-run -> everything skipped
        plan2 = recipes.build_plan(root, REPORT)
        result2 = recipes.apply_plan(root, plan2, write=True)
        self.assertEqual(result2["written"], [])
        self.assertIn("ruff.toml", result2["skipped"])

    def test_never_overwrites_existing(self):
        root = make_repo({"ruff.toml": "# my custom config\n"})
        self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, REPORT)
        recipes.apply_plan(root, plan, write=True)
        self.assertEqual((root / "ruff.toml").read_text(), "# my custom config\n")


class TestGitignoreAppend(unittest.TestCase):
    def test_append_to_partial(self):
        root = make_repo({".gitignore": "__pycache__/\n"})  # has artifacts, missing secrets
        self.addCleanup(rmtree, root)
        changed = recipes._apply_gitignore(root / ".gitignore", write=True)
        self.assertTrue(changed)
        self.assertIn(".env", (root / ".gitignore").read_text())

    def test_complete_gitignore_no_change(self):
        root = make_repo({".gitignore": ".env\n__pycache__/\n"})
        self.addCleanup(rmtree, root)
        self.assertFalse(recipes._apply_gitignore(root / ".gitignore", write=True))


class TestWorktreeDirty(unittest.TestCase):
    def test_states(self):
        self.assertFalse(recipes.worktree_dirty("/x", runner=lambda r, a: ""))
        self.assertTrue(recipes.worktree_dirty("/x", runner=lambda r, a: " M file\n"))
        self.assertIsNone(recipes.worktree_dirty("/x", runner=lambda r, a: None))


class TestRunFix(unittest.TestCase):
    def _seed_report(self, root, report=REPORT):
        rp = root / ".agents" / "readiness"
        rp.mkdir(parents=True, exist_ok=True)
        (rp / "latest.json").write_text(json.dumps(report))

    def test_no_report_returns_2(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self.assertEqual(recipes.run_fix(_args(root)), 2)

    def test_dry_run_writes_nothing(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed_report(root)
        self.assertEqual(recipes.run_fix(_args(root, apply=False)), 0)
        self.assertFalse((root / "ruff.toml").exists())

    def test_apply_writes(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed_report(root)
        self.assertEqual(recipes.run_fix(_args(root, apply=True)), 0)
        self.assertTrue((root / "ruff.toml").exists())

    def test_dirty_worktree_refuses(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed_report(root)
        with mock.patch.object(recipes, "worktree_dirty", return_value=True):
            self.assertEqual(recipes.run_fix(_args(root, apply=True, force=False)), 1)
        self.assertFalse((root / "ruff.toml").exists())  # nothing written on refusal

    def test_loop_dry_run_apply_and_safety(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed_report(root, LOOP_REPORT)

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

        code, out = _cli(["fix", "--project", str(root), "--apply"])
        self.assertEqual(code, 0)
        for target in LOOP_TARGETS:
            self.assertIn(f"`{target}`", out)
            self.assertIn("exists → skipped", out)

    def test_loop_existing_targets_not_overwritten(self):
        root = make_repo({"loop-runs/README.md": "# custom\n"})
        self.addCleanup(rmtree, root)
        self._seed_report(root, LOOP_REPORT)
        code, _out = _cli(["fix", "--project", str(root), "--apply"])
        self.assertEqual(code, 0)
        self.assertEqual((root / "loop-runs/README.md").read_text(), "# custom\n")

    def test_loop_dirty_worktree_refuses_without_writes(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed_report(root, LOOP_REPORT)
        with mock.patch.object(recipes, "worktree_dirty", return_value=True):
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
        self.assertIn("x.s", plan["manual"])

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
        self.assertIn("ruff.toml", result["written"])
        self.assertFalse((root / "ruff.toml").exists())

    def test_gitignore_dry_run_reports_change_without_writing(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self.assertTrue(recipes._apply_gitignore(root / ".gitignore", write=False))
        self.assertFalse((root / ".gitignore").exists())

    def test_format_plan_empty_sections(self):
        text = recipes.format_plan({"auto": [], "propose": [], "github": [], "manual": []})
        self.assertIn("- (none)", text)
        self.assertNotIn("## Manual", text)


class TestRunFixLatest(unittest.TestCase):
    def _seed_history(self, root):
        ident = history.repo_identity(str(root))
        report = {"schema_version": "2", "engine_version": "0.3.0", "registry_version": "0.3.0",
                  "detector_version": "0.3.0", "generated_at": "2026-06-20T00:00:00+00:00",
                  "repository": ident, "detection": {"languages": ["python"]},
                  "results": [{"id": "style.linter_config", "status": "fail"}],
                  "score": {"level": 1}}
        history.store_history(report, str(root))

    def test_latest_no_history_errors(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        code, _ = _cli(["fix", "--project", str(root), "--latest"])
        self.assertEqual(code, 2)

    def test_latest_resolves_stored_report(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        self._seed_history(root)
        code, out = _cli(["fix", "--project", str(root), "--latest"])
        self.assertEqual(code, 0)
        self.assertIn("ruff.toml", out)

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
                | {g["id"] for g in plan["github"]} | set(plan["manual"]))

    def test_advisory_scaffold_auto_nonscaffold_excluded(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        ids = self._ids(recipes.build_plan(root, self._report()))
        self.assertIn("loop.denylist", ids)
        self.assertNotIn("loop.rules_index", ids)

    def test_include_is_authoritative(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(), focus={"include": ["style.linter_config"]})
        self.assertEqual(self._ids(plan), {"style.linter_config"})

    def test_include_overrides_advisory_rule(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(), focus={"include": ["loop.rules_index"]})
        self.assertIn("loop.rules_index", self._ids(plan))

    def test_exclude_removes(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(), focus={"exclude": ["style.linter_config"]})
        self.assertNotIn("style.linter_config", self._ids(plan))

    def test_pillar_exclude(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        plan = recipes.build_plan(root, self._report(),
                                  focus={"pillar_exclude": {"Security & Governance"}})
        self.assertNotIn("security.branch_protection", self._ids(plan))

    def test_prioritize_orders_pillar_first(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        report = {"detection": {"languages": ["python"]}, "results": [
            {"id": "style.linter_config", "status": "fail", "gating": True},
            {"id": "security.codeowners", "status": "fail", "gating": True},
        ]}
        plan = recipes.build_plan(root, report, focus={"pillar_prioritize": {"Security & Governance"}})
        self.assertEqual(plan["auto"][0]["id"], "security.codeowners")


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
        ident = history.repo_identity(str(root))
        report = {"schema_version": "2", "engine_version": "0.3.0", "registry_version": "0.3.0",
                  "detector_version": "0.3.0", "generated_at": "2026-06-20T00:00:00+00:00",
                  "repository": ident, "detection": {"languages": ["python"]},
                  "results": [{"id": "style.linter_config", "status": "fail"}],
                  "score": {"level": 1}}
        return ident, report

    def test_instructions_unsupported_note_and_verify(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        _, report = self._seed(root)
        history.store_history(report, str(root))
        code, out = _cli(["fix", "--project", str(root), "--latest", "--instructions", "make it nice"])
        self.assertEqual(code, 0)
        self.assertIn("## Notes", out)
        self.assertIn("not recognized", out)
        self.assertIn("## Verify", out)

    def test_exclude_via_cli(self):
        root = make_repo({}); self.addCleanup(rmtree, root)
        _, report = self._seed(root)
        history.store_history(report, str(root))
        code, out = _cli(["fix", "--project", str(root), "--latest", "--exclude", "style.linter_config"])
        self.assertEqual(code, 0)
        self.assertNotIn("ruff.toml", out)

    def test_custom_store_needs_history_dir(self):
        import io
        from contextlib import redirect_stderr
        root = make_repo({}); self.addCleanup(rmtree, root)
        custom = root / "custom"
        _, report = self._seed(root)
        history.store_history(report, str(root), out=str(custom))
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["fix", "--project", str(root), "--latest"])
        self.assertEqual(code, 2)
        self.assertIn(".agents/readiness/history", err.getvalue())  # names the default path checked
        code2, out = _cli(["fix", "--project", str(root), "--latest",
                           "--history-dir", str(custom / "history")])
        self.assertEqual(code2, 0)
        self.assertIn("ruff.toml", out)


class TestFixSafety(unittest.TestCase):
    def test_fix_never_executes_mutation_commands(self):
        root = make_repo({})
        self.addCleanup(rmtree, root)
        ident = history.repo_identity(str(root))
        report = {"schema_version": "2", "engine_version": "0.3.0", "registry_version": "0.3.0",
                  "detector_version": "0.3.0", "generated_at": "2026-06-20T00:00:00+00:00",
                  "repository": ident, "detection": {"languages": ["python"]},
                  "results": [{"id": "security.branch_protection", "status": "fail"},
                              {"id": "style.linter_config", "status": "fail"}],
                  "score": {"level": 0}}
        history.store_history(report, str(root))
        calls = []
        real = recipes.subprocess.run

        def spy(args, *a, **k):
            calls.append(args)
            return real(args, *a, **k)

        with mock.patch.object(recipes.subprocess, "run", spy):
            code, _ = _cli(["fix", "--project", str(root), "--latest", "--apply"])
        self.assertEqual(code, 0)
        flat = " ".join(" ".join(map(str, c)) for c in calls)
        for forbidden in ("push", "gh ", "-X PUT", "-X POST", "-X PATCH", "pull-request", " pr "):
            self.assertNotIn(forbidden, flat)


if __name__ == "__main__":
    unittest.main()
