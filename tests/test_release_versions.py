"""scripts/check_release_versions.py: current-tree matrix validation and publication mode.

Every test runs against a real temporary mirror tree (pyproject, plugin metadata, SKILL.md
frontmatter, manifests, vendored engine stamps) with ``check_release_versions.REPO`` pointed
at it; publication tests use real throwaway git repositories with a path remote.
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import readiness  # noqa: F401 — ensures engine is importable
from readiness import version as engine_version

from tests._util import rmtree

REPO = Path(readiness.__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import check_release_versions as crv  # noqa: E402

SKILLS = ("ra1-report", "ra1-fix", "ra1-interview")


def _prev_minor(v: str) -> str:
    major, minor, _patch = v.split(".")
    return f"{major}.{int(minor) - 1}.0"


def _matrix(*, package=None, engine=None, registry=None, detector=None,
            schema_baseline="2", schema_selected=None, plugin="0.3.0",
            plugin_baseline="0.2.0", tag=None, base_commit="0" * 40, highest="",
            schema_version="1"):
    """A matrix consistent with the live engine constants unless overridden."""
    package = package or engine_version.ENGINE_VERSION
    engine = engine or engine_version.ENGINE_VERSION
    registry = registry or engine_version.REGISTRY_VERSION
    detector = detector or engine_version.DETECTOR_VERSION
    schema_selected = schema_selected or engine_version.SCHEMA_VERSION

    def group(selected, baseline):
        return {"selected": selected, "baseline": baseline}

    pkg = group(package, _prev_minor(package))
    eng = group(engine, _prev_minor(engine))
    reg = group(registry, _prev_minor(registry))
    det = group(detector, detector)  # detector never advances
    return {
        "schema_version": schema_version,
        "release_tag": tag if tag is not None else "v" + package,
        "publication_source": {
            "repository": "https://github.com/tjboudreaux/ready-agent-1",
            "branch": "origin/main",
            "selection_base_commit": base_commit,
            "highest_eligible_release_tag": highest,
        },
        "baseline": {
            "package": pkg["baseline"],
            "engine": eng["baseline"],
            "registry": reg["baseline"],
            "detector": det["baseline"],
            "report_schema": schema_baseline,
            "skills": {s: pkg["baseline"] for s in SKILLS},
            "claude_plugin": plugin_baseline,
        },
        "selected": {
            "package": pkg["selected"],
            "engine": eng["selected"],
            "registry": reg["selected"],
            "detector": det["selected"],
            "report_schema": schema_selected,
            "skills": {s: pkg["selected"] for s in SKILLS},
            "claude_plugin": plugin,
        },
    }


def _write_mirror(root: Path, matrix: dict, *, vendored=True, skill_version_line=True):
    """Materialize the tree validate_matrix reads, consistent with ``matrix``."""
    sel = matrix["selected"]
    (root / "release").mkdir(parents=True, exist_ok=True)
    (root / "release" / "versions.json").write_text(json.dumps(matrix), encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "ready-agent-1"\nversion = "{sel["package"]}"\n',
        encoding="utf-8")
    (root / "uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.11"\n\n[[package]]\nname = "agent-readiness"\n'
        f'version = "{sel["package"]}"\nsource = {{ virtual = "." }}\n',
        encoding="utf-8")
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "ready-agent-1", "version": sel["claude_plugin"]}),
        encoding="utf-8")
    for skill in SKILLS:
        base = root / "skills" / skill
        base.mkdir(parents=True, exist_ok=True)
        if skill_version_line:
            skill_text = (f"---\nname: {skill}\nmetadata:\n"
                          f"  version: {sel['skills'][skill]}\n---\n")
        else:
            skill_text = f"---\nname: {skill}\n---\n"
        (base / "SKILL.md").write_text(skill_text, encoding="utf-8")
        (base / "manifest.json").write_text(json.dumps({
            "engine_version": sel["engine"],
            "registry_version": sel["registry"],
            "detector_version": sel["detector"],
        }), encoding="utf-8")
        if vendored:
            vdir = base / "scripts" / "readiness"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "version.py").write_text(
                f'ENGINE_VERSION = "{sel["engine"]}"\n'
                f'REGISTRY_VERSION = "{sel["registry"]}"\n'
                f'DETECTOR_VERSION = "{sel["detector"]}"\n',
                encoding="utf-8")


class _RepoPatchTest(unittest.TestCase):
    """setUp: a temp mirror of the happy-path tree with crv.REPO pointed at it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ar-relver-"))
        self.addCleanup(rmtree, self.root)
        self.matrix = _matrix()
        _write_mirror(self.root, self.matrix)
        self._old_repo = crv.REPO
        crv.REPO = self.root
        self.addCleanup(self._restore)

    def _restore(self):
        crv.REPO = self._old_repo

    def rewrite_matrix(self, matrix):
        (self.root / "release" / "versions.json").write_text(
            json.dumps(matrix), encoding="utf-8")


class TestLoadMatrix(_RepoPatchTest):
    def test_happy_tree_is_valid(self):
        self.assertEqual(crv.validate_matrix(), [])

    def test_missing_matrix_file_is_an_error(self):
        (self.root / "release" / "versions.json").unlink()
        errors = crv.validate_matrix()
        self.assertEqual(len(errors), 1)

    def test_malformed_matrix_json_is_an_error(self):
        (self.root / "release" / "versions.json").write_text("{not json",
                                                             encoding="utf-8")
        errors = crv.validate_matrix()
        self.assertEqual(len(errors), 1)

    def test_top_keys_missing(self):
        matrix = _matrix()
        del matrix["baseline"]
        self.rewrite_matrix(matrix)
        self.assertEqual(crv.validate_matrix(),
                         [f"release/versions.json keys not exact: {sorted(matrix)}"])

    def test_top_keys_extra(self):
        matrix = {**_matrix(), "surprise": {}}
        self.rewrite_matrix(matrix)
        errors = crv.validate_matrix()
        self.assertEqual(len(errors), 1)
        self.assertIn("keys not exact", errors[0])


class TestValidateMatrix(_RepoPatchTest):
    def test_bad_schema_version(self):
        self.rewrite_matrix(_matrix(schema_version="2"))
        self.assertIn("versions schema must be '1'", crv.validate_matrix())

    def test_non_string_component_returns_early(self):
        matrix = _matrix()
        matrix["baseline"]["package"] = 1
        self.rewrite_matrix(matrix)
        self.assertEqual(crv.validate_matrix(), ["package must be strings"])

    def test_package_not_stable_semver(self):
        # A two-component version fails the stable-SemVer grammar; the advance check
        # skips it rather than crashing.
        matrix = _matrix()
        matrix["baseline"]["package"] = "0.10"
        self.rewrite_matrix(matrix)
        self.assertIn("package versions must be stable SemVer", crv.validate_matrix())

    def test_engine_not_stable_semver(self):
        matrix = _matrix()
        matrix["baseline"]["engine"] = "0.10.0.1"
        self.rewrite_matrix(matrix)
        self.assertIn("engine versions must be stable SemVer", crv.validate_matrix())

    def test_registry_not_stable_semver(self):
        matrix = _matrix()
        matrix["baseline"]["registry"] = "0.7"
        self.rewrite_matrix(matrix)
        self.assertIn("registry versions must be stable SemVer", crv.validate_matrix())

    def test_advance_must_be_exactly_one_step(self):
        two_minors = _matrix(package="0.12.0", tag="v0.12.0")
        two_minors["baseline"]["package"] = "0.10.0"
        self.rewrite_matrix(two_minors)
        _write_mirror(self.root, two_minors)
        self.assertIn("package must advance exactly one minor (patch 0) or one patch",
                      crv.validate_matrix())

    def test_same_minor_patch_advance_is_allowed(self):
        package = engine_version.ENGINE_VERSION
        base = package.split(".")
        same = f"{base[0]}.{base[1]}.{int(base[2]) + 1}"
        matrix = _matrix(package=same, tag=f"v{same}")
        matrix["baseline"]["package"] = package
        matrix["baseline"]["skills"] = {s: package for s in SKILLS}
        self.rewrite_matrix(matrix)
        _write_mirror(self.root, matrix)
        self.assertEqual(crv.validate_matrix(), [])

    def test_detector_must_not_change(self):
        matrix = _matrix()
        matrix["selected"]["detector"] = "0.7.0"
        self.rewrite_matrix(matrix)
        self.assertIn("detector must be unchanged", crv.validate_matrix())

    def test_schema_may_remain_3(self):
        matrix = _matrix(schema_baseline="3")
        self.rewrite_matrix(matrix)
        self.assertEqual(crv.validate_matrix(), [])

    def test_schema_invalid_transition(self):
        matrix = _matrix(schema_selected="2", schema_baseline="2")
        self.rewrite_matrix(matrix)
        errors = crv.validate_matrix()
        self.assertIn("report_schema must transition 2 -> 3 or remain 3", errors)

    def test_release_tag_mismatch(self):
        matrix = _matrix(tag="v9.9.9")
        self.rewrite_matrix(matrix)
        self.assertIn("release_tag must equal 'v' + selected.package",
                      crv.validate_matrix())

    def test_engine_version_py_mismatch(self):
        matrix = _matrix(engine="0.12.0")
        self.rewrite_matrix(matrix)
        _write_mirror(self.root, matrix)
        errors = crv.validate_matrix()
        self.assertTrue(any("ENGINE" in e and "!= selected" in e for e in errors))

    def test_registry_version_py_mismatch(self):
        matrix = _matrix(registry="0.9.0")
        self.rewrite_matrix(matrix)
        _write_mirror(self.root, matrix)
        self.assertIn("registry mismatch in engine/readiness/version.py",
                      crv.validate_matrix())

    def test_detector_version_py_mismatch(self):
        matrix = _matrix(detector="9.9.9")
        self.rewrite_matrix(matrix)
        _write_mirror(self.root, matrix)
        self.assertIn("detector mismatch in engine/readiness/version.py",
                      crv.validate_matrix())

    def test_schema_version_py_mismatch(self):
        matrix = _matrix(schema_selected="2", schema_baseline="2")
        self.rewrite_matrix(matrix)
        errors = crv.validate_matrix()
        self.assertIn("schema mismatch in engine/readiness/version.py", errors)

    def test_pyproject_mismatch(self):
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "ready-agent-1"\nversion = "0.0.1"\n', encoding="utf-8")
        self.assertIn("pyproject version != selected package", crv.validate_matrix())

    def test_skill_metadata_version_mismatch(self):
        skill_md = self.root / "skills" / "ra1-fix" / "SKILL.md"
        skill_md.write_text("---\nname: ra1-fix\nmetadata:\n  version: 0.9.0\n---\n",
                            encoding="utf-8")
        self.assertIn("skills/ra1-fix metadata version != selected",
                      crv.validate_matrix())

    def test_skill_metadata_version_line_absent(self):
        skill_md = self.root / "skills" / "ra1-report" / "SKILL.md"
        skill_md.write_text("---\nname: ra1-report\n---\n", encoding="utf-8")
        self.assertIn("skills/ra1-report metadata version != selected",
                      crv.validate_matrix())

    def test_skill_manifest_tuple_mismatch(self):
        manifest = self.root / "skills" / "ra1-interview" / "manifest.json"
        manifest.write_text(json.dumps({
            "engine_version": "0.0.0",
            "registry_version": self.matrix["selected"]["registry"],
            "detector_version": self.matrix["selected"]["detector"],
        }), encoding="utf-8")
        self.assertIn("skills/ra1-interview manifest engine tuple != selected",
                      crv.validate_matrix())

    def test_plugin_version_mismatch(self):
        (self.root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "ready-agent-1", "version": "0.2.0"}), encoding="utf-8")
        self.assertIn("plugin version != selected claude_plugin", crv.validate_matrix())

    def test_vendored_engine_missing(self):
        for skill in SKILLS:
            rmtree(self.root / "skills" / skill / "scripts")
        errors = crv.validate_matrix()
        self.assertEqual(sorted(errors),
                         sorted([f"vendored engine missing in {s}" for s in SKILLS]))

    def test_vendored_engine_tuple_mismatch(self):
        (self.root / "skills" / "ra1-report" / "scripts" / "readiness"
         / "version.py").write_text('ENGINE_VERSION = "0.0.0"\n', encoding="utf-8")
        self.assertIn("vendored engine tuple != selected in ra1-report",
                      crv.validate_matrix())

    def test_pyproject_unreadable(self):
        (self.root / "pyproject.toml").unlink()
        errors = crv.validate_matrix()
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("pyproject.toml unreadable: "))

    def test_uv_lock_version_mismatch(self):
        (self.root / "uv.lock").write_text(
            '[[package]]\nname = "agent-readiness"\nversion = "0.6.0"\n', encoding="utf-8")
        self.assertIn("uv.lock package version != selected package", crv.validate_matrix())

    def test_uv_lock_unreadable(self):
        (self.root / "uv.lock").unlink()
        errors = crv.validate_matrix()
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("uv.lock unreadable: "))

    def test_uv_lock_without_package_entry(self):
        (self.root / "uv.lock").write_text(
            '[[package]]\nname = "ruff"\nversion = "0.16.1"\n', encoding="utf-8")
        self.assertEqual(crv.validate_matrix(),
                         ["uv.lock unreadable: no [[package]] entry for agent-readiness"])

    def test_skill_md_unreadable(self):
        (self.root / "skills" / "ra1-fix" / "SKILL.md").unlink()
        errors = crv.validate_matrix()
        self.assertTrue(any(e.startswith("skills/ra1-fix/SKILL.md unreadable: ")
                            for e in errors))

    def test_manifest_unreadable(self):
        (self.root / "skills" / "ra1-report" / "manifest.json").write_text(
            "{not json", encoding="utf-8")
        errors = crv.validate_matrix()
        self.assertTrue(any(e.startswith("skills/ra1-report/manifest.json unreadable: ")
                            for e in errors))

    def test_plugin_unreadable(self):
        (self.root / ".claude-plugin" / "plugin.json").unlink()
        errors = crv.validate_matrix()
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("plugin.json unreadable: "))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _make_git_pair(tmp: Path, name: str, *, tags=("v0.1.0", "v0.2.0-rc1")):
    """A work repo whose path ``origin`` carries one commit plus the given tags."""
    origin = tmp / f"{name}-origin"
    work = tmp / f"{name}-work"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "test@example.invalid")
    _git(origin, "config", "user.name", "Test")
    (origin / "f.txt").write_text("x\n", encoding="utf-8")
    _git(origin, "add", "f.txt")
    _git(origin, "commit", "-qm", "base")
    base_commit = _git(origin, "rev-parse", "HEAD").stdout.decode().strip()
    for tag in tags:
        _git(origin, "tag", tag)
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "remote", "add", "origin", str(origin))
    return work, base_commit


class _GitMirrorTest(unittest.TestCase):
    """A work repo with a path ``origin`` carrying one stable and one prerelease tag."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ar-relver-git-"))
        self.addCleanup(rmtree, self.tmp)
        self.work, self.base_commit = _make_git_pair(self.tmp, "pair")

    def write_matrix(self, root=None, **kw):
        root = root or self.work
        kw.setdefault("base_commit", self.base_commit)
        matrix = _matrix(**kw)
        (root / "release").mkdir(parents=True, exist_ok=True)
        (root / "release" / "versions.json").write_text(
            json.dumps(matrix), encoding="utf-8")
        return matrix


class TestCheckPublished(_GitMirrorTest):
    def setUp(self):
        super().setUp()
        self._old_repo = crv.REPO
        crv.REPO = self.work
        self.addCleanup(self._restore)

    def _restore(self):
        crv.REPO = self._old_repo

    def test_happy_publication(self):
        # origin carries stable v0.1.0 (ancestor) + prerelease v0.2.0-rc1 (skipped).
        self.write_matrix(highest="v0.1.0")
        self.assertEqual(crv.check_published(), [])

    def test_no_stable_tags_with_unset_recorded_is_ok(self):
        work, base = _make_git_pair(self.tmp, "tagless", tags=())
        self.write_matrix(root=work, base_commit=base, highest="")
        crv.REPO = work
        self.assertEqual(crv.check_published(), [])

    def test_no_stable_tags_but_recorded_is_an_error(self):
        work, base = _make_git_pair(self.tmp, "tagless2", tags=())
        self.write_matrix(root=work, base_commit=base, highest="v0.1.0")
        crv.REPO = work
        self.assertIn("recorded highest eligible tag, but no stable eligible tags exist",
                      crv.check_published())

    def test_recorded_highest_mismatch(self):
        self.write_matrix(highest="v0.0.1")
        self.assertIn("recorded highest eligible tag v0.0.1 != fetched v0.1.0",
                      crv.check_published())

    def test_recorded_highest_unset_with_tags_is_an_error(self):
        self.write_matrix(highest="")
        self.assertIn("recorded highest eligible tag <unset> != fetched v0.1.0",
                      crv.check_published())

    def test_invalid_matrix_returns_before_git(self):
        self.write_matrix(highest="v0.1.0")
        (self.work / "release" / "versions.json").write_text("{}",
                                                             encoding="utf-8")
        errors = crv.check_published()
        self.assertEqual(len(errors), 1)
        self.assertIn("keys not exact", errors[0])

    def test_fetch_failure(self):
        bare = self.tmp / "not-a-repo"
        bare.mkdir()
        (bare / "release").mkdir()
        (bare / "release" / "versions.json").write_text(json.dumps(_matrix()),
                                                        encoding="utf-8")
        crv.REPO = bare
        errors = crv.check_published()
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("git fetch failed:"))

    def test_selection_base_missing(self):
        self.write_matrix(base_commit="f" * 40, highest="v0.1.0")
        self.assertIn(f"selection base not present: {'f' * 40}",
                      crv.check_published())

    def test_tag_not_ancestor_of_origin_main(self):
        _git(self.work, "config", "user.email", "test@example.invalid")
        _git(self.work, "config", "user.name", "Test")
        (self.work / "local.txt").write_text("local\n", encoding="utf-8")
        _git(self.work, "add", "local.txt")
        _git(self.work, "commit", "-qm", "local only")
        _git(self.work, "tag", "v9.9.9")
        self.write_matrix(highest="v9.9.9")
        self.assertIn("eligible tag not ancestor of origin/main: v9.9.9",
                      crv.check_published())

    def test_release_tag_ok(self):
        _git(self.work, "fetch", "--quiet", "origin", "main")
        _git(self.work, "tag", "v" + engine_version.ENGINE_VERSION, "FETCH_HEAD")
        self.write_matrix(highest="v0.1.0")
        self.assertEqual(
            crv.check_published(tag="v" + engine_version.ENGINE_VERSION), [])

    def test_release_tag_mismatch(self):
        self.write_matrix(highest="v0.1.0")
        errors = crv.check_published(tag="v0.0.9")
        self.assertTrue(any(e.startswith("tag v0.0.9 != v") for e in errors))

    def test_release_tag_not_found(self):
        self.write_matrix(highest="v0.1.0")
        errors = crv.check_published(tag="v" + engine_version.ENGINE_VERSION)
        self.assertIn(f"tag not found: v{engine_version.ENGINE_VERSION}", errors)

    def test_candidate_tag_excluded_from_highest_comparison(self):
        # Release flows create the candidate tag before --check-published runs: the
        # candidate is fetched but must not corrupt the recorded-vs-fetched comparison.
        candidate = "v" + engine_version.ENGINE_VERSION
        _git(self.work, "fetch", "--quiet", "origin", "main")
        _git(self.work, "tag", candidate, "FETCH_HEAD")
        self.write_matrix(highest="v0.1.0")
        self.assertEqual(crv.check_published(tag=candidate), [])

    def test_candidate_tag_still_ancestry_checked(self):
        # The candidate is excluded only from the highest comparison, never from the
        # ancestry proof.
        candidate = "v" + engine_version.ENGINE_VERSION
        _git(self.work, "config", "user.email", "test@example.invalid")
        _git(self.work, "config", "user.name", "Test")
        (self.work / "local.txt").write_text("local\n", encoding="utf-8")
        _git(self.work, "add", "local.txt")
        _git(self.work, "commit", "-qm", "local only")
        _git(self.work, "tag", candidate)
        self.write_matrix(highest="v0.1.0")
        self.assertIn(f"eligible tag not ancestor of origin/main: {candidate}",
                      crv.check_published(tag=candidate))


class TestMain(_RepoPatchTest):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = crv.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_success(self):
        rc, out, _err = self._run([])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "release matrix is consistent\n")

    def test_failure_reports_errors(self):
        (self.root / "release" / "versions.json").unlink()
        rc, _out, err = self._run([])
        self.assertEqual(rc, 1)
        self.assertTrue(err.startswith("RELEASE MATRIX MISMATCH:\n"))

    def test_argv_none_uses_sys_argv(self):
        old_argv = sys.argv[:]
        try:
            sys.argv = ["check_release_versions.py"]
            self.assertEqual(crv.main(), 0)
        finally:
            sys.argv = old_argv

    def test_tag_flag_without_value_is_ignored(self):
        rc, _out, _err = self._run(["--tag"])
        self.assertEqual(rc, 0)

    def test_tag_flag_without_published_still_validates_tree(self):
        rc, out, _err = self._run(["--tag", "v0.0.0"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "release matrix is consistent\n")


class TestMainPublished(_GitMirrorTest):
    def setUp(self):
        super().setUp()
        _write_mirror(self.work, self.write_matrix(highest="v0.1.0"))
        self._old_repo = crv.REPO
        crv.REPO = self.work
        self.addCleanup(self._restore)

    def _restore(self):
        crv.REPO = self._old_repo

    def test_check_published_happy(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = crv.main(["--check-published"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(),
                         "release matrix is consistent (published)\n")

    def test_check_published_failure_exits_1(self):
        self.write_matrix(base_commit="f" * 40, highest="v0.1.0")
        err = io.StringIO()
        with redirect_stderr(err):
            rc = crv.main(["--check-published"])
        self.assertEqual(rc, 1)
        self.assertIn("selection base not present", err.getvalue())

    def test_check_published_with_tag(self):
        _git(self.work, "fetch", "--quiet", "origin", "main")
        _git(self.work, "tag", "v" + engine_version.ENGINE_VERSION, "FETCH_HEAD")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = crv.main(["--check-published", "--tag",
                           "v" + engine_version.ENGINE_VERSION])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
