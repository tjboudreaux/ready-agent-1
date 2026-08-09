"""Focused tests for the safe-I/O authority (engine/readiness/safe_io.py).

Covers the capability probe, observation invariants and reason-code allowlists, root
admission, bounded reads (oversize/decode/unsafe), no-follow discovery with caps, write
targets/exclusive creates/atomic replaces, the policy merge authority, Git authority
admission (primary + linked worktree) and refusal modes, and ysanc: every refusal makes
zero writes.
"""
from __future__ import annotations

import errno
import fcntl
import os
import stat
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from readiness import safe_io

import tests._util as U


class TestObservations(unittest.TestCase):
    def test_read_observation_invariants(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RootedBytesObservation(safe_io.RepoReadState.OK, data=b"",
                                           reason_code="nope")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RootedBytesObservation(safe_io.RepoReadState.MISSING, data=b"x")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RootedBytesObservation(safe_io.RepoReadState.UNREADABLE)
        ok = safe_io.RootedBytesObservation(safe_io.RepoReadState.OK, data=b"abc")
        self.assertEqual(ok.data, b"abc")
        missing = safe_io.RootedBytesObservation(safe_io.RepoReadState.MISSING,
                                                 reason_code="not_found")
        self.assertEqual(missing.state, safe_io.RepoReadState.MISSING)

    def test_file_observation_invariants(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoFileObservation(safe_io.RepoReadState.UNSAFE_PATH,
                                        reason_code="not_found")
        bad = safe_io.RepoFileObservation(safe_io.RepoReadState.OVERSIZE,
                                          reason_code="too_large")
        self.assertEqual(bad.reason_code, "too_large")

    def test_discovery_observation_invariants(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OK,
                                             paths=("b", "a"))
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OVERFLOW,
                                             reason_code="match_overflow",
                                             paths=("x",))
        ok = safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OK,
                                              paths=("a", "b"))
        self.assertEqual(ok.paths, ("a", "b"))

    def test_presence_observation_invariants(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.PresenceObservation(safe_io.PresenceState.PRESENT)
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.PresenceObservation(safe_io.PresenceState.INDETERMINATE)
        p = safe_io.PresenceObservation(safe_io.PresenceState.PRESENT, path="x")
        self.assertEqual(p.path, "x")
        a = safe_io.PresenceObservation(safe_io.PresenceState.ABSENT)
        self.assertEqual(a.state, safe_io.PresenceState.ABSENT)


class TestProbe(unittest.TestCase):
    def test_probe_is_supported_on_this_host(self):
        self.assertTrue(safe_io.safe_io_supported())

    def test_force_unsupported_fails_closed(self):
        safe_io._force_probe_unsupported()
        try:
            with self.assertRaises(safe_io.SafeIoUnsupportedError):
                safe_io._require_supported()
        finally:
            safe_io._reset_probe()
        self.assertTrue(safe_io.safe_io_supported())


class TestReads(unittest.TestCase):
    def _root(self, files):
        tmp = U.make_repo(files)
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_read_regular_and_missing(self):
        tmp, auth = self._root({"a.txt": "hello"})
        ok = safe_io.read_rooted_regular(auth, "a.txt")
        self.assertEqual(ok.state, safe_io.RepoReadState.OK)
        self.assertEqual(ok.data, b"hello")
        missing = safe_io.read_rooted_regular(auth, "gone.txt")
        self.assertEqual(missing.state, safe_io.RepoReadState.MISSING)
        auth.close()

    def test_oversize_read(self):
        tmp, auth = self._root({"big.txt": "x" * 200})
        obs = safe_io.read_rooted_regular(auth, "big.txt", max_bytes=100)
        self.assertEqual(obs.state, safe_io.RepoReadState.OVERSIZE)
        self.assertEqual(obs.data, b"")
        auth.close()

    def test_traversal_and_absolute_rejected(self):
        tmp, auth = self._root({})
        for bad in ("../escape", "/etc/passwd", "a/../../b", "~/.ssh/id_rsa",
                    "C:\\windows", "with\\backslash"):
            with self.subTest(path=bad):
                obs = safe_io.read_rooted_regular(auth, bad)
                self.assertIn(obs.state,
                              (safe_io.RepoReadState.UNSAFE_PATH,
                               safe_io.RepoReadState.MISSING))
                self.assertEqual(obs.data, b"")
        auth.close()

    def test_symlink_final_rejected(self):
        tmp, auth = self._root({"real.txt": "secret"})
        os.symlink(os.path.join(tmp, "real.txt"), os.path.join(tmp, "link.txt"))
        obs = safe_io.read_rooted_regular(auth, "link.txt")
        self.assertEqual(obs.state, safe_io.RepoReadState.UNSAFE_PATH)
        self.assertEqual(obs.reason_code, "symlink")
        auth.close()

    def test_hardlink_rejected(self):
        tmp, auth = self._root({"real.txt": "x"})
        os.link(os.path.join(tmp, "real.txt"), os.path.join(tmp, "hard.txt"))
        obs = safe_io.read_rooted_regular(auth, "real.txt")
        self.assertEqual(obs.state, safe_io.RepoReadState.UNSAFE_PATH)
        self.assertEqual(obs.reason_code, "hardlink")
        auth.close()

    def test_directory_target_rejected(self):
        tmp, auth = self._root({"sub/x.txt": "x"})
        obs = safe_io.read_rooted_regular(auth, "sub")
        self.assertEqual(obs.state, safe_io.RepoReadState.UNSAFE_PATH)
        auth.close()

    def test_fifo_target_returns_refusal_not_hang(self):
        # Regression: os.open(O_RDONLY) on a repo-controlled FIFO blocks until a writer
        # appears; every read open must be O_NONBLOCK and refuse the special file on the
        # open descriptor, never hanging the engine.
        tmp, auth = self._root({"real.txt": "secret"})
        os.mkfifo(os.path.join(tmp, "trap.fifo"))
        obs = safe_io.read_rooted_regular(auth, "trap.fifo")
        self.assertEqual(obs.state, safe_io.RepoReadState.UNSAFE_PATH)
        self.assertEqual(obs.reason_code, "special_file")
        auth.close()

    def test_fifo_swap_refused_by_copy_and_authority(self):
        tmp, auth = self._root({"x.txt": "x"})
        os.unlink(os.path.join(tmp, "x.txt"))
        os.mkfifo(os.path.join(tmp, "x.txt"))  # swap after listing, before open
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.safe_copy_tree(auth, auth)
        auth.close()
        tmp2, auth2 = self._root({})
        os.mkfifo(os.path.join(tmp2, ".git"))
        refusal = safe_io.acquire_git_authority(auth2)
        self.assertIsInstance(refusal, safe_io.GitAuthorityRefusal)
        auth2.close()


class TestDiscovery(unittest.TestCase):
    def _root(self, files):
        tmp = U.make_repo(files)
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_discovery_patterns(self):
        tmp, auth = self._root({
            "a.md": "x", "b/c.md": "y", "b/d.txt": "z", "nested/deep/e.py": "w",
        })
        obs = safe_io.discover_rooted_regular(auth, ["**/*.md"])
        self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OK)
        self.assertEqual(obs.paths, ("a.md", "b/c.md"))
        obs2 = safe_io.discover_rooted_regular(auth, ["b/*.txt"])
        self.assertEqual(obs2.paths, ("b/d.txt",))
        obs3 = safe_io.discover_rooted_regular(auth, ["nested/**"])
        self.assertIn("nested/deep/e.py", obs3.paths)
        auth.close()

    def test_discovery_skips_fixed_ignores(self):
        tmp, auth = self._root({"node_modules/x.js": "x", "src.js": "y"})
        obs = safe_io.discover_rooted_regular(auth, ["**/*.js"])
        self.assertEqual(obs.paths, ("src.js",))
        auth.close()

    def test_discovery_symlink_dir_not_descended(self):
        tmp, auth = self._root({"inside/x.py": "x"})
        os.symlink(os.path.join(tmp, "inside"), os.path.join(tmp, "link"))
        obs = safe_io.discover_rooted_regular(auth, ["**/*.py"])
        self.assertEqual(obs.paths, ("inside/x.py",))
        auth.close()

    def test_discovery_match_overflow(self):
        tmp, auth = self._root({f"f{i}.py": "x" for i in range(30)})
        obs = safe_io.discover_rooted_regular(auth, ["*.py"], max_matches=10)
        self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OVERFLOW)
        self.assertEqual(obs.reason_code, "match_overflow")
        auth.close()

    def test_discovery_entry_overflow(self):
        tmp, auth = self._root({f"d{i}.txt": "x" for i in range(30)})
        obs = safe_io.discover_rooted_regular(auth, ["*.txt"], max_entries=5)
        self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OVERFLOW)
        self.assertEqual(obs.reason_code, "entry_overflow")
        auth.close()

    def test_exists_three_state(self):
        tmp, auth = self._root({"present.md": "x"})
        self.assertEqual(safe_io.exists_rooted(auth, ["present.md"]).state,
                         safe_io.PresenceState.PRESENT)
        self.assertEqual(safe_io.exists_rooted(auth, ["absent.md"]).state,
                         safe_io.PresenceState.ABSENT)
        auth.close()


class TestWrites(unittest.TestCase):
    def _root(self, files=None):
        tmp = U.make_repo(files or {})
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_create_exclusive_and_skip_existing(self):
        tmp, auth = self._root({})
        created = safe_io.create_rooted_exclusive(auth, "new.txt", b"x")
        self.assertTrue(created)
        again = safe_io.create_rooted_exclusive(auth, "new.txt", b"y")
        self.assertFalse(again)
        self.assertEqual(safe_io.read_rooted_regular(auth, "new.txt").data, b"x")
        auth.close()

    def test_atomic_replace(self):
        tmp, auth = self._root({"f.txt": "old"})
        safe_io.atomic_replace_rooted(auth, "f.txt", b"new")
        self.assertEqual(safe_io.read_rooted_regular(auth, "f.txt").data, b"new")
        auth.close()

    def test_unlink_rooted(self):
        tmp, auth = self._root({"f.txt": "x"})
        self.assertTrue(safe_io.unlink_rooted(auth, "f.txt"))
        self.assertFalse(safe_io.unlink_rooted(auth, "f.txt"))
        auth.close()

    def test_validate_write_target_rejects_unsafe(self):
        tmp, auth = self._root({})
        for bad in ("../x", "/x", "a/../../b"):
            with self.subTest(path=bad):
                with self.assertRaises(safe_io.RepositoryInputError):
                    safe_io.validate_write_target(auth, bad)
        auth.close()

    def test_ensure_rooted_directory(self):
        tmp, auth = self._root({})
        safe_io.ensure_rooted_directory(auth, "a/b/c", mode=0o755)
        st = os.stat(os.path.join(tmp, "a", "b", "c"))
        self.assertTrue(stat.S_ISDIR(st.st_mode))
        auth.close()

    def test_merge_policy_json(self):
        tmp, auth = self._root({})
        safe_io.ensure_rooted_directory(auth, ".ra1")
        created, value = safe_io.merge_rooted_policy_json(
            auth, ".ra1/config.json", lambda parsed: {"loop_ready": True})
        self.assertTrue(created)
        self.assertEqual(value, {"loop_ready": True})
        created2, value2 = safe_io.merge_rooted_policy_json(
            auth, ".ra1/config.json",
            lambda parsed: {**parsed, "ci_budget_minutes": 10})
        self.assertFalse(created2)
        self.assertEqual(value2["ci_budget_minutes"], 10)
        blob = safe_io.read_rooted_regular(auth, ".ra1/config.json").data
        import json
        self.assertEqual(json.loads(blob), {"loop_ready": True, "ci_budget_minutes": 10})
        auth.close()

    def test_merge_policy_refuses_other_targets(self):
        tmp, auth = self._root({})
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.merge_rooted_policy_json(auth, ".ra1/other.json",
                                             lambda p: p)
        auth.close()


class TestGitAuthority(unittest.TestCase):
    def _git_repo(self, commit=True):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-gitauth-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / "README.md").write_text("# x\n")
        subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(tmp), "remote", "add", "origin",
                        "https://github.com/o/r.git"], check=True)
        if commit:
            subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
            subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "i"], check=True)
        return tmp

    def test_primary_authority(self):
        tmp = self._git_repo()
        auth = safe_io.acquire_root(tmp)
        authority = safe_io.acquire_git_authority(auth)
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        self.assertEqual(authority.metadata_profile, "primary")
        self.assertEqual(authority.origin, ("github.com", "o", "r"))
        self.assertFalse(authority.origin_malformed)
        authority.close()
        auth.close()

    def test_no_git(self):
        tmp = U.make_repo({"f.txt": "x"})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        refusal = safe_io.acquire_git_authority(auth)
        self.assertEqual(refusal.reason, "no_git")
        auth.close()

    def test_linked_worktree_authority(self):
        tmp = self._git_repo()
        linked = str(Path(tempfile.mkdtemp(prefix="ra1-gitwt-")) / "wt")
        self.addCleanup(lambda: U.rmtree(linked))
        subprocess.run(["git", "-C", str(tmp), "worktree", "add", "-qb", "wtb", linked],
                       check=True)
        lauth = safe_io.acquire_root(linked)
        authority = safe_io.acquire_git_authority(lauth)
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        self.assertEqual(authority.metadata_profile, "linked_worktree")
        authority.close()
        lauth.close()
        auth2 = safe_io.acquire_root(tmp)
        safe_io.acquire_git_authority(auth2).close()
        auth2.close()

    def test_malformed_origin(self):
        tmp = self._git_repo()
        subprocess.run(["git", "-C", str(tmp), "remote", "set-url", "origin",
                        "::::bad"], check=True)
        auth = safe_io.acquire_root(tmp)
        authority = safe_io.acquire_git_authority(auth)
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        self.assertTrue(authority.origin_malformed)
        authority.close()
        auth.close()

    def test_gitfile_unsupported_topology(self):
        tmp = self._git_repo()
        gitfile = Path(tmp) / ".git"
        subprocess.run(["rm", "-rf", str(gitfile)], check=True)
        gitfile.write_text("gitdir: /nonexistent/no\n", encoding="utf-8")
        auth = safe_io.acquire_root(tmp)
        refusal = safe_io.acquire_git_authority(auth)
        self.assertIn(refusal.reason, ("unsupported_topology", "unsafe_metadata"))
        auth.close()


class TestObservationGuardsExtra(unittest.TestCase):
    def test_bytes_observation_remaining_guards(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RootedBytesObservation("ok", data=b"")  # state not an enum
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RootedBytesObservation(safe_io.RepoReadState.OK, data="str")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RootedBytesObservation(safe_io.RepoReadState.MISSING,
                                           reason_code="bogus")

    def test_file_observation_remaining_guards(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoFileObservation("ok")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoFileObservation(safe_io.RepoReadState.OK, reason_code="x")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoFileObservation(safe_io.RepoReadState.OK, text=b"bytes")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoFileObservation(safe_io.RepoReadState.MISSING, text="x")

    def test_discovery_observation_remaining_guards(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation("ok")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OK,
                                             reason_code="x")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OK,
                                             paths=("a", ""))
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OK, paths=(1,))
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OVERFLOW)

    def test_presence_observation_remaining_guards(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.PresenceObservation("present", path="x")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.PresenceObservation(safe_io.PresenceState.PRESENT, path="x",
                                        reason_code="x")
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.PresenceObservation(safe_io.PresenceState.ABSENT, path="x")


class TestRootAuthorityExtra(unittest.TestCase):
    def test_context_manager(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        with safe_io.acquire_root(tmp) as auth:
            self.assertIsNotNone(auth.fd)
        self.assertIsNone(auth.fd)

    def test_close_twice_is_safe(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        auth.close()
        auth.close()

    def test_relative_root_raises_oserror(self):
        # realpath() absolutizes against the cwd, so a relative root walks to ENOENT.
        with self.assertRaises(OSError):
            safe_io.acquire_root("some/relative/dir")

    def test_file_root_raises_oserror(self):
        tmp = U.make_repo({"f.txt": "x"})
        self.addCleanup(lambda: U.rmtree(tmp))
        with self.assertRaises(OSError):
            safe_io.acquire_root(tmp / "f.txt")

    def test_missing_root_raises_oserror(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        with self.assertRaises(OSError):
            safe_io.acquire_root(tmp / "nope")

    def test_open_subroot(self):
        tmp = U.make_repo({"sub/f.txt": "x"})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        sub = safe_io.open_subroot(auth, "sub")
        try:
            self.assertEqual(
                safe_io.read_rooted_regular(sub, "f.txt").data, b"x")
        finally:
            sub.close()
            auth.close()


class TestWalkGuards(unittest.TestCase):
    def test_invalid_relpaths_raise(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        try:
            for bad in ("", "../x", 123):
                with self.subTest(path=bad):
                    with self.assertRaises(safe_io.RepositoryInputError):
                        safe_io._walk(auth, bad)
        finally:
            auth.close()


class TestReadClassification(unittest.TestCase):
    def test_intermediate_symlink_never_descended(self):
        tmp = U.make_repo({"real/x.txt": "data"})
        self.addCleanup(lambda: U.rmtree(tmp))
        os.symlink(os.path.join(tmp, "real"), os.path.join(tmp, "link"))
        auth = safe_io.acquire_root(tmp)
        try:
            obs = safe_io.read_rooted_regular(auth, "link/x.txt")
            # Linux: ELOOP -> unsafe_path/symlink; Darwin: ENOTDIR -> missing.
            # Either way the link is never followed and no payload is returned.
            self.assertIn(obs.state, (safe_io.RepoReadState.UNSAFE_PATH,
                                      safe_io.RepoReadState.MISSING))
            self.assertEqual(obs.data, b"")
        finally:
            auth.close()

    def test_permission_denied_intermediate_is_unreadable(self):
        tmp = U.make_repo({"sub/x.txt": "data"})
        sub = os.path.join(tmp, "sub")
        self.addCleanup(lambda: U.rmtree(tmp))
        self.addCleanup(lambda: os.chmod(sub, 0o755))
        os.chmod(sub, 0o000)
        auth = safe_io.acquire_root(tmp)
        try:
            obs = safe_io.read_rooted_regular(auth, "sub/x.txt")
            self.assertEqual(obs.state, safe_io.RepoReadState.UNREADABLE)
            self.assertEqual(obs.reason_code, "permission_denied")
        finally:
            auth.close()

    def test_max_bytes_validation(self):
        tmp = U.make_repo({"f.txt": "x"})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        try:
            for bad in (0, -1, True, "100"):
                with self.subTest(max_bytes=bad):
                    with self.assertRaises(safe_io.RepositoryInputError):
                        safe_io.read_rooted_regular(auth, "f.txt", max_bytes=bad)
        finally:
            auth.close()


class TestReadExplicitRegular(unittest.TestCase):
    def test_ok_missing_and_oversize(self):
        tmp = U.make_repo({"r.json": "{}", "big.json": "x" * 200})
        self.addCleanup(lambda: U.rmtree(tmp))
        ok = safe_io.read_explicit_regular(tmp / "r.json")
        self.assertEqual(ok.state, safe_io.RepoReadState.OK)
        self.assertEqual(ok.data, b"{}")
        missing = safe_io.read_explicit_regular(tmp / "gone.json")
        self.assertEqual(missing.state, safe_io.RepoReadState.MISSING)
        oversize = safe_io.read_explicit_regular(tmp / "big.json", max_bytes=100)
        self.assertEqual(oversize.state, safe_io.RepoReadState.OVERSIZE)

    def test_max_bytes_validation(self):
        for bad in (0, True, "100"):
            with self.subTest(max_bytes=bad):
                with self.assertRaises(safe_io.RepositoryInputError):
                    safe_io.read_explicit_regular("/tmp/x", max_bytes=bad)

    def test_nul_path_is_unsafe(self):
        obs = safe_io.read_explicit_regular("bad\x00path")
        self.assertEqual(obs.state, safe_io.RepoReadState.UNSAFE_PATH)
        self.assertEqual(obs.reason_code, "invalid_path")

    def test_missing_parent_maps_to_missing(self):
        obs = safe_io.read_explicit_regular("/nonexistent-ra1-dir/f.json")
        self.assertEqual(obs.state, safe_io.RepoReadState.MISSING)


class TestDiscoveryPatterns(unittest.TestCase):
    def test_valid_patterns(self):
        for pat in ("a/b.txt", "**/*.py", "*.md", "x?y.txt", "**", "a/**/b", "a/**"):
            with self.subTest(pattern=pat):
                self.assertIsNone(safe_io.validate_discovery_pattern(pat))

    def test_invalid_patterns(self):
        for pat in (123, "", "x" * 600, "a\x00b", "/abs", "!neg", "a\\b", "C:/x",
                    "//unc", "[a]", "{a}", "(a", "?a", "+a", "a//b", "a/./b",
                    "a/../b", "a**b"):
            with self.subTest(pattern=pat):
                self.assertEqual(safe_io.validate_discovery_pattern(pat),
                                 "invalid_pattern")

    def test_compile_engine_patterns_rejects_bad(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.compile_engine_patterns(["../bad"])
        compiled = safe_io.compile_engine_patterns(["*.py"])
        self.assertEqual(len(compiled), 1)

    def test_double_star_alone_matches_everything(self):
        tmp = U.make_repo({"a.py": "x", "sub/b.py": "y"})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        try:
            obs = safe_io.discover_rooted_regular(auth, ["**"])
            self.assertEqual(obs.paths, ("a.py", "sub/b.py"))
        finally:
            auth.close()


class TestDiscoveryExtra(unittest.TestCase):
    def _root(self, files):
        tmp = U.make_repo(files)
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_cap_validation(self):
        tmp, auth = self._root({"a.py": "x"})
        try:
            for kw in ({"max_entries": 0}, {"max_matches": True},
                       {"max_path_bytes": -1}, {"max_depth": "x"}):
                with self.subTest(kw=kw):
                    with self.assertRaises(safe_io.RepositoryInputError):
                        safe_io.discover_rooted_regular(auth, ["*.py"], **kw)
        finally:
            auth.close()

    def test_depth_overflow(self):
        tmp, auth = self._root({"a/b/c.txt": "x"})
        try:
            obs = safe_io.discover_rooted_regular(auth, ["**/*.txt"], max_depth=1)
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OVERFLOW)
            self.assertEqual(obs.reason_code, "depth_overflow")
        finally:
            auth.close()

    def test_permission_denied_walk_dir_is_unreadable(self):
        tmp, auth = self._root({"sub/x.txt": "x", "top.txt": "y"})
        sub = os.path.join(tmp, "sub")
        self.addCleanup(lambda: os.chmod(sub, 0o755))
        os.chmod(sub, 0o000)
        try:
            obs = safe_io.discover_rooted_regular(auth, ["**/*.txt"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.UNREADABLE)
            self.assertEqual(obs.reason_code, "permission_denied")
        finally:
            auth.close()

    def test_hardlinked_match_is_unsafe(self):
        tmp, auth = self._root({"real.py": "x"})
        os.link(os.path.join(tmp, "real.py"), os.path.join(tmp, "copy.py"))
        try:
            obs = safe_io.discover_rooted_regular(auth, ["*.py"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.UNSAFE_PATH)
            self.assertEqual(obs.reason_code, "hardlink")
        finally:
            auth.close()

    def test_broken_root_handle_is_unreadable(self):
        bogus = safe_io.RootAuthority(1 << 22, "/x")  # never a valid fd
        obs = safe_io.discover_rooted_regular(bogus, ["*.py"])
        self.assertEqual(obs.state, safe_io.RepoDiscoveryState.UNREADABLE)
        self.assertEqual(obs.reason_code, "io_error")

    def test_exists_indeterminate_on_overflow(self):
        tmp, auth = self._root({f"f{i}.py": "x" for i in range(10)})
        try:
            obs = safe_io.exists_rooted(auth, ["*.py"], max_entries=3)
            self.assertEqual(obs.state, safe_io.PresenceState.INDETERMINATE)
            # exists_rooted caps matches at 1, so the second match overflows first.
            self.assertEqual(obs.reason_code, "overflow:match_overflow")
        finally:
            auth.close()


class TestWriteTargetsExtra(unittest.TestCase):
    def _root(self, files=None):
        tmp = U.make_repo(files or {})
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_symlinked_parent_never_written_through(self):
        tmp, auth = self._root({"real/x.txt": "x"})
        os.symlink(os.path.join(tmp, "real"), os.path.join(tmp, "link"))
        try:
            # Linux: validate_write_target raises on the ELOOP parent; Darwin: the
            # parent walk reports ENOTDIR and the create fails at mkdir(EEXIST).
            with self.assertRaises((OSError, safe_io.RepositoryInputError)):
                safe_io.create_rooted_exclusive(auth, "link/y.txt", b"x")
            self.assertFalse(os.path.exists(os.path.join(tmp, "real", "y.txt")))
        finally:
            auth.close()

    def test_directory_target_rejected(self):
        tmp, auth = self._root({"sub/x": "x"})
        try:
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io.validate_write_target(auth, "sub")
        finally:
            auth.close()

    def test_ensure_invalid_path(self):
        tmp, auth = self._root()
        try:
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io.ensure_rooted_directory(auth, "")
        finally:
            auth.close()

    def test_ensure_under_unwritable_root_raises(self):
        tmp, auth = self._root()
        self.addCleanup(lambda: os.chmod(tmp, 0o755))
        os.chmod(tmp, 0o500)
        try:
            with self.assertRaises(OSError):
                safe_io.ensure_rooted_directory(auth, "newdir")
        finally:
            auth.close()

    def test_ensure_component_is_file_raises(self):
        tmp, auth = self._root({"f": "x"})
        try:
            with self.assertRaises(OSError):
                safe_io.ensure_rooted_directory(auth, "f/g")
        finally:
            auth.close()

    def test_create_exclusive_creates_nested_parents(self):
        tmp, auth = self._root()
        try:
            self.assertTrue(
                safe_io.create_rooted_exclusive(auth, "a/b/c.txt", b"x"))
            self.assertEqual((tmp / "a" / "b" / "c.txt").read_bytes(), b"x")
        finally:
            auth.close()

    def test_walk_create_parents_invalid(self):
        tmp, auth = self._root()
        try:
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io._walk_create_parents(auth, "x//y")
        finally:
            auth.close()

    def test_atomic_replace_creates_nested_new_file(self):
        tmp, auth = self._root()
        try:
            safe_io.atomic_replace_rooted(auth, "new/dir/f.txt", b"v")
            self.assertEqual((tmp / "new" / "dir" / "f.txt").read_bytes(), b"v")
        finally:
            auth.close()

    def test_unlink_directory_rejected(self):
        tmp, auth = self._root({"sub/x": "x"})
        try:
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io.unlink_rooted(auth, "sub")
        finally:
            auth.close()


class TestDirectoryLocks(unittest.TestCase):
    def test_lock_contention_and_release(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        fd1 = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
        fd2 = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.assertTrue(safe_io.lock_directory(fd1, exclusive=True))
            self.assertFalse(safe_io.lock_directory(fd2, exclusive=True))
            self.assertFalse(safe_io.lock_directory(fd2, exclusive=False))
            safe_io.unlock_directory(fd1)
            self.assertTrue(safe_io.lock_directory(fd2, exclusive=True))
            safe_io.unlock_directory(fd2)
        finally:
            os.close(fd1)
            os.close(fd2)


class TestPolicyMergeExtra(unittest.TestCase):
    def _root(self, files=None):
        tmp = U.make_repo(files or {})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        safe_io.ensure_rooted_directory(auth, ".ra1")
        return tmp, auth

    def test_invalid_json_rejected(self):
        tmp, auth = self._root({".ra1/config.json": "{not json"})
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "not valid JSON"):
                safe_io.merge_rooted_policy_json(auth, ".ra1/config.json",
                                                 lambda p: p)
        finally:
            auth.close()

    def test_directory_policy_target_unreadable(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".ra1" / "config.json").mkdir(parents=True)
        auth = safe_io.acquire_root(tmp)
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError, "unreadable"):
                safe_io.merge_rooted_policy_json(auth, ".ra1/config.json",
                                                 lambda p: p)
        finally:
            auth.close()

    def test_hardlinked_policy_target_unreadable(self):
        tmp, auth = self._root({".ra1/other.json": "{}"})
        os.link(os.path.join(tmp, ".ra1", "other.json"),
                os.path.join(tmp, ".ra1", "config.json"))
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError, "unreadable"):
                safe_io.merge_rooted_policy_json(auth, ".ra1/config.json",
                                                 lambda p: p)
        finally:
            auth.close()

    def test_oversize_policy_target_unreadable(self):
        big = " " * (safe_io.MAX_CONFIG_BYTES + 16)
        tmp, auth = self._root({".ra1/waivers.json": big})
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError, "unreadable"):
                safe_io.merge_rooted_policy_json(auth, ".ra1/waivers.json",
                                                 lambda p: p)
        finally:
            auth.close()


class TestSafeCopyTree(unittest.TestCase):
    def _pair(self, files):
        src = U.make_repo(files)
        dst = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(src))
        self.addCleanup(lambda: U.rmtree(dst))
        return src, dst, safe_io.acquire_root(src), safe_io.acquire_root(dst)

    def test_copies_tree_with_modes_and_exclusions(self):
        src, dst, sa, da = self._pair({
            "a.txt": "hello",
            "sub/b.sh": "#!/bin/sh\n",
            ".git/x": "ignored",
            ".agents/y": "ignored",
            ".ra1/reports/r.json": "ignored",
            ".ra1/config.json": "{}",
        })
        os.chmod(os.path.join(src, "sub", "b.sh"), 0o755)
        try:
            safe_io.safe_copy_tree(sa, da)
            self.assertEqual((dst / "a.txt").read_bytes(), b"hello")
            mode = stat.S_IMODE(os.stat(dst / "sub" / "b.sh").st_mode)
            self.assertEqual(mode, 0o755)
            self.assertFalse((dst / ".git").exists())
            self.assertFalse((dst / ".agents").exists())
            self.assertFalse((dst / ".ra1" / "reports").exists())
            self.assertEqual((dst / ".ra1" / "config.json").read_bytes(), b"{}")
        finally:
            sa.close()
            da.close()

    def test_symlink_member_rejected(self):
        src, dst, sa, da = self._pair({"real.txt": "x"})
        os.symlink(os.path.join(src, "real.txt"), os.path.join(src, "link.txt"))
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "unsafe copy member"):
                safe_io.safe_copy_tree(sa, da)
        finally:
            sa.close()
            da.close()

    def test_hardlink_member_rejected(self):
        src, dst, sa, da = self._pair({"real.txt": "x"})
        os.link(os.path.join(src, "real.txt"), os.path.join(src, "hard.txt"))
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "unsafe copy member"):
                safe_io.safe_copy_tree(sa, da)
        finally:
            sa.close()
            da.close()

    def test_entry_cap(self):
        src, dst, sa, da = self._pair({f"f{i}.txt": "x" for i in range(5)})
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError, "entry cap"):
                safe_io.safe_copy_tree(sa, da, max_entries=2)
        finally:
            sa.close()
            da.close()

    def test_depth_cap(self):
        src, dst, sa, da = self._pair({"a/b/c.txt": "x"})
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError, "depth cap"):
                safe_io.safe_copy_tree(sa, da, max_depth=1)
        finally:
            sa.close()
            da.close()

    def test_file_bytes_cap(self):
        src, dst, sa, da = self._pair({"big.txt": "x" * 100})
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "file size cap"):
                safe_io.safe_copy_tree(sa, da, max_file_bytes=10)
        finally:
            sa.close()
            da.close()

    def test_total_bytes_cap(self):
        src, dst, sa, da = self._pair({"a.txt": "x" * 50, "b.txt": "y" * 50})
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "total size cap"):
                safe_io.safe_copy_tree(sa, da, max_total_bytes=60)
        finally:
            sa.close()
            da.close()


class TestWalkCandidate(unittest.TestCase):
    def test_success_dir_file_and_relative(self):
        tmp = U.make_repo({"f.txt": "x"})
        self.addCleanup(lambda: U.rmtree(tmp))
        physical = os.path.realpath(tmp)  # macOS /var is a symlink: walk the physical path
        fd = safe_io._walk_candidate(None, physical, expect="dir")
        os.close(fd)
        fd = safe_io._walk_candidate(None, os.path.join(physical, "f.txt"),
                                     expect="file")
        os.close(fd)
        auth = safe_io.acquire_root(tmp)
        try:
            fd = safe_io._walk_candidate(auth.fd, "f.txt", expect="file")
            os.close(fd)
        finally:
            auth.close()

    def test_rejections(self):
        tmp = U.make_repo({"f.txt": "x"})
        self.addCleanup(lambda: U.rmtree(tmp))
        physical = os.path.realpath(tmp)
        auth = safe_io.acquire_root(tmp)
        try:
            for bad, expect in (("", "dir"), ("n\x00l", "dir"), ("/", "dir"),
                                (physical + "/../x", "dir"),
                                (physical + "/..", "dir")):
                with self.subTest(path=bad):
                    with self.assertRaises(safe_io.RepositoryInputError):
                        safe_io._walk_candidate(None, bad, expect=expect)
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io._walk_candidate(None, "relative/path", expect="dir")
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io._walk_candidate(auth.fd, "../b", expect="dir")
            with self.assertRaises(OSError):  # O_DIRECTORY on a file: ENOTDIR
                safe_io._walk_candidate(None, os.path.join(physical, "f.txt"),
                                        expect="dir")
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io._walk_candidate(None, physical, expect="file")
        finally:
            auth.close()


class TestGitAuthorityRefusals(unittest.TestCase):
    def _git_repo(self, config_extra=""):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-gitr-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / "README.md").write_text("# x\n")
        subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"],
                       check=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp), "commit", "-qm", "i"], check=True)
        if config_extra:  # hostile config is appended only after git is done reading it
            with (tmp / ".git" / "config").open("a", encoding="utf-8") as fh:
                fh.write(config_extra)
        return tmp

    def _refusal_reason(self, tmp):
        auth = safe_io.acquire_root(tmp)
        try:
            result = safe_io.acquire_git_authority(auth)
            self.assertIsInstance(result, safe_io.GitAuthorityRefusal)
            return result.reason
        finally:
            auth.close()

    def test_git_fifo_is_unsafe_metadata(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        os.mkfifo(os.path.join(tmp, ".git"))
        self.assertEqual(self._refusal_reason(tmp), "unsafe_metadata")

    def test_group_writable_git_dir_is_unsafe(self):
        tmp = self._git_repo()
        os.chmod(tmp / ".git", 0o775)
        self.assertEqual(self._refusal_reason(tmp), "unsafe_metadata")

    def test_config_include_rejected(self):
        tmp = self._git_repo(config_extra="[include]\n\tpath = /tmp/x\n")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_missing_rejected(self):
        tmp = self._git_repo()
        (tmp / ".git" / "config").unlink()
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_non_utf8_rejected(self):
        tmp = self._git_repo()
        (tmp / ".git" / "config").write_bytes(b"\xff\xfe\x00binary")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_malformed_rejected(self):
        tmp = self._git_repo()
        (tmp / ".git" / "config").write_text("[unclosed\n", encoding="utf-8")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_promisor_rejected(self):
        tmp = self._git_repo(
            config_extra='[remote "origin"]\n\tpromisor = true\n')
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_core_worktree_rejected(self):
        tmp = self._git_repo()
        (tmp / ".git" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n\tworktree = /tmp/x\n",
            encoding="utf-8")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_bare_rejected(self):
        tmp = self._git_repo()
        (tmp / ".git" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n\tbare = true\n",
            encoding="utf-8")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_disallowed_extension_rejected(self):
        tmp = self._git_repo(config_extra="[extensions]\n\tworktreeconfig = true\n")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_config_bad_objectformat_rejected(self):
        tmp = self._git_repo(config_extra="[extensions]\n\tobjectformat = md5\n")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_alternates_marker_rejected(self):
        tmp = self._git_repo()
        info = tmp / ".git" / "objects" / "info"
        info.mkdir(exist_ok=True)
        (info / "alternates").write_text("/elsewhere\n", encoding="utf-8")
        self.assertEqual(self._refusal_reason(tmp), "unsupported_topology")

    def test_hardlinked_metadata_maps_overflow(self):
        tmp = self._git_repo()
        os.link(tmp / ".git" / "HEAD", tmp / ".git" / "packed-refs")
        self.assertEqual(self._refusal_reason(tmp), "overflow")

    def test_sha256_repo_admitted(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-git256-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / "f.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(tmp), "init", "-q",
                        "--object-format=sha256"], check=True)
        auth = safe_io.acquire_root(tmp)
        try:
            authority = safe_io.acquire_git_authority(auth)
            self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
            self.assertEqual(authority.object_format, "sha256")
            authority.close()
        finally:
            auth.close()


class TestAdmitLinkedRefusals(unittest.TestCase):
    def _gitfile_repo(self, content: bytes):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".git").write_bytes(content)
        return tmp

    def _reason(self, tmp):
        auth = safe_io.acquire_root(tmp)
        try:
            result = safe_io.acquire_git_authority(auth)
            self.assertIsInstance(result, safe_io.GitAuthorityRefusal)
            return result.reason
        finally:
            auth.close()

    def test_gitfile_group_writable(self):
        tmp = self._gitfile_repo(b"gitdir: /tmp/x/wt\n")
        os.chmod(tmp / ".git", 0o664)
        self.assertEqual(self._reason(tmp), "unsafe_metadata")

    def test_gitfile_oversize(self):
        tmp = self._gitfile_repo(b"gitdir: /" + b"x" * 5000)
        self.assertEqual(self._reason(tmp), "unsafe_metadata")

    def test_gitfile_non_utf8(self):
        tmp = self._gitfile_repo(b"gitdir: /x\xff\xfe\n")
        self.assertEqual(self._reason(tmp), "unsafe_metadata")

    def test_gitfile_garbage(self):
        tmp = self._gitfile_repo(b"not a gitdir\n")
        self.assertEqual(self._reason(tmp), "unsupported_topology")

    def test_gitfile_multiline(self):
        tmp = self._gitfile_repo(b"gitdir: /a/wt\ngitdir: /b/wt\n")
        self.assertEqual(self._reason(tmp), "unsupported_topology")

    def test_gitfile_empty_target(self):
        tmp = self._gitfile_repo(b"gitdir: \n")
        self.assertEqual(self._reason(tmp), "unsafe_metadata")

    def test_gitfile_control_in_target(self):
        tmp = self._gitfile_repo(b"gitdir: /x\ty/wt\n")
        self.assertEqual(self._reason(tmp), "unsafe_metadata")

    def test_gitfile_bad_worktree_id(self):
        tmp = self._gitfile_repo(b"gitdir: /tmp/x/my wt\n")
        self.assertEqual(self._reason(tmp), "unsupported_topology")

    def test_gitfile_nonexistent_target(self):
        tmp = self._gitfile_repo(b"gitdir: /nonexistent-ra1/x/wt\n")
        self.assertEqual(self._reason(tmp), "unsafe_metadata")


class TestLinkedWorktreeRefusals(unittest.TestCase):
    def _linked(self):
        main = Path(tempfile.mkdtemp(prefix="ra1-lw-main-"))
        self.addCleanup(lambda: U.rmtree(main))
        (main / "README.md").write_text("# x\n")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "T"], ["add", "."],
                     ["commit", "-qm", "i"]):
            subprocess.run(["git", "-C", str(main), *args], check=True)
        linked = str(Path(tempfile.mkdtemp(prefix="ra1-lw-")) / "wt")
        self.addCleanup(lambda: U.rmtree(linked))
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-qb", "wtb",
                        linked], check=True)
        meta = next((main / ".git" / "worktrees").iterdir())
        return main, Path(linked), meta

    def _reason(self, root):
        auth = safe_io.acquire_root(root)
        try:
            result = safe_io.acquire_git_authority(auth)
            self.assertIsInstance(result, safe_io.GitAuthorityRefusal)
            return result.reason
        finally:
            auth.close()

    def test_commondir_mismatch(self):
        main, linked, meta = self._linked()
        (meta / "commondir").write_text("/elsewhere\n", encoding="utf-8")
        self.assertEqual(self._reason(linked), "unsupported_topology")

    def test_commondir_missing(self):
        main, linked, meta = self._linked()
        (meta / "commondir").unlink()
        self.assertEqual(self._reason(linked), "unsupported_topology")

    def test_backref_missing(self):
        main, linked, meta = self._linked()
        (meta / "gitdir").unlink()
        self.assertEqual(self._reason(linked), "unsupported_topology")

    def test_backref_mismatch(self):
        main, linked, meta = self._linked()
        (meta / "gitdir").write_text(str(main / "README.md") + "\n",
                                     encoding="utf-8")
        self.assertEqual(self._reason(linked), "unsupported_topology")

    def test_common_dir_group_writable(self):
        main, linked, meta = self._linked()
        os.chmod(main / ".git", 0o775)
        self.addCleanup(lambda: os.chmod(main / ".git", 0o755))
        self.assertEqual(self._reason(linked), "unsupported_topology")


class TestProjectGitConfigDirect(unittest.TestCase):
    def _meta(self, files):
        tmp = U.make_repo(files)
        self.addCleanup(lambda: U.rmtree(tmp))
        return safe_io.acquire_root(tmp)

    def test_missing_config(self):
        meta = self._meta({})
        try:
            result = safe_io._project_git_config(meta)
            self.assertIsInstance(result, safe_io.GitAuthorityRefusal)
            self.assertEqual(result.reason, "unsupported_topology")
        finally:
            meta.close()

    def test_filemode_false_and_origin(self):
        meta = self._meta({"config": (
            "[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n"
            "[remote \"origin\"]\n\turl = https://github.com/o/r.git\n")})
        try:
            projection = safe_io._project_git_config(meta)
            self.assertEqual(projection["origin"], ("github.com", "o", "r"))
            self.assertFalse(projection["filemode"])
            self.assertFalse(projection["origin_malformed"])
        finally:
            meta.close()

    def test_empty_origin_is_malformed(self):
        meta = self._meta({"config": '[remote "origin"]\n\turl =\n'})
        try:
            projection = safe_io._project_git_config(meta)
            self.assertEqual(projection["origin"], ())
            self.assertTrue(projection["origin_malformed"])
        finally:
            meta.close()

    def test_refstorage_extension_allowed(self):
        meta = self._meta({"config": (
            "[core]\n\trepositoryformatversion = 1\n"
            "[extensions]\n\tobjectformat = sha256\n\trefstorage = reftable\n")})
        try:
            projection = safe_io._project_git_config(meta)
            self.assertEqual(projection["object_format"], "sha256")
            self.assertEqual(projection["refstorage"], "reftable")
        finally:
            meta.close()


class TestParseOriginIdentity(unittest.TestCase):
    def test_forms(self):
        cases = {
            "https://github.com/o/r.git": ("github.com", "o", "r"),
            "https://github.com/o/r": ("github.com", "o", "r"),
            "git@github.com:o/r.git": ("github.com", "o", "r"),
            "git@github.com:org/team/r.git": ("github.com", "org/team", "r"),
            "ssh://git@github.com/o/r.git": ("github.com", "o", "r"),
            "git@host-without-colon": (),
            "::::bad": (),
            "https:///o/r": (),
            "https://github.com": (),
            "https://github.com/o/%ff.git": (),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(safe_io._parse_origin_identity(url), expected)


class TestGitSnapshotViews(unittest.TestCase):
    def _repo_with_view_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-view-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".gitignore").write_text("*.log\n")
        (tmp / "src").mkdir()
        (tmp / "src" / "f.py").write_text("x = 1\n")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "T"], ["add", "."],
                     ["commit", "-qm", "i"]):
            subprocess.run(["git", "-C", str(tmp), *args], check=True)
        auth = safe_io.acquire_root(tmp)
        self.addCleanup(auth.close)
        authority = safe_io.acquire_git_authority(auth)
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        self.addCleanup(authority.close)
        return tmp, authority

    def test_close_with_defaults(self):
        authority = safe_io.GitSnapshotAuthority(snapshot_path="/x")
        authority.close()  # no state, no tempdir: pure no-op

    def test_views_require_open_authority(self):
        authority = safe_io.GitSnapshotAuthority(snapshot_path="/x")
        with self.assertRaises(TypeError):
            authority.ensure_gitignore_view()
        with self.assertRaises(TypeError):
            authority.ensure_full_view()

    def test_gitignore_then_full_view(self):
        tmp, authority = self._repo_with_view_files()
        snap = Path(authority.snapshot_path)
        authority.ensure_gitignore_view()
        self.assertTrue((snap / ".gitignore").is_file())
        self.assertFalse((snap / "src" / "f.py").exists())
        authority.ensure_gitignore_view()  # already built: no-op
        authority.ensure_full_view()
        self.assertEqual((snap / "src" / "f.py").read_text(encoding="utf-8"),
                         "x = 1\n")
        authority.ensure_full_view()  # already built: no-op

    def test_view_symlink_member_refused(self):
        tmp, authority = self._repo_with_view_files()
        os.symlink(os.path.join(tmp, ".gitignore"), os.path.join(tmp, "link"))
        with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                    "unsafe worktree view member"):
            authority.ensure_full_view()

    def test_view_hardlink_member_refused(self):
        tmp, authority = self._repo_with_view_files()
        os.link(tmp / ".gitignore", tmp / "hard")
        with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                    "multiply-linked"):
            authority.ensure_full_view()


class TestFlattenCaps(unittest.TestCase):
    def _git_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-cap-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / "f.txt").write_text("x\n")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "T"], ["add", "."],
                     ["commit", "-qm", "i"]):
            subprocess.run(["git", "-C", str(tmp), *args], check=True)
        return tmp

    def _reason_under(self, tmp, **patches):
        auth = safe_io.acquire_root(tmp)
        try:
            with mock.patch.multiple(safe_io, **patches):
                result = safe_io.acquire_git_authority(auth)
            self.assertIsInstance(result, safe_io.GitAuthorityRefusal)
            return result.reason
        finally:
            auth.close()

    def test_entry_cap(self):
        tmp = self._git_repo()
        self.assertEqual(
            self._reason_under(tmp, MAX_GIT_SNAPSHOT_ENTRIES=1), "overflow")

    def test_depth_cap(self):
        tmp = self._git_repo()
        self.assertEqual(
            self._reason_under(tmp, MAX_GIT_SNAPSHOT_DEPTH=1), "overflow")

    def test_file_bytes_cap(self):
        tmp = self._git_repo()
        self.assertEqual(
            self._reason_under(tmp, MAX_GIT_FILE_BYTES=1), "overflow")

    def test_total_bytes_cap(self):
        tmp = self._git_repo()
        self.assertEqual(
            self._reason_under(tmp, MAX_GIT_SNAPSHOT_BYTES=1), "overflow")


class TestObservationGuardsMore(unittest.TestCase):
    def test_discovery_duplicate_paths_rejected(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.RepoDiscoveryObservation(safe_io.RepoDiscoveryState.OK,
                                             paths=("a", "a"))

    def test_absent_presence_reason_rejected(self):
        with self.assertRaises(safe_io.RepositoryInputError):
            safe_io.PresenceObservation(safe_io.PresenceState.ABSENT,
                                        reason_code="x")

    def test_valid_ok_file_observation(self):
        ok = safe_io.RepoFileObservation(safe_io.RepoReadState.OK, text="body")
        self.assertEqual(ok.text, "body")


class TestProbeRefusals(unittest.TestCase):
    """Every defensive _probe refusal branch, via scoped patching."""

    def test_non_posix(self):
        with mock.patch.object(os, "name", "nt"):
            self.assertFalse(safe_io._probe())

    def test_missing_os_primitives(self):
        saved = os.O_DIRECTORY
        del os.O_DIRECTORY
        try:
            self.assertFalse(safe_io._probe())
        finally:
            os.O_DIRECTORY = saved

    def test_missing_flock(self):
        saved = fcntl.flock
        del fcntl.flock
        try:
            self.assertFalse(safe_io._probe())
        finally:
            fcntl.flock = saved

    def test_probe_stat_not_regular(self):
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "f" and k.get("follow_symlinks") is False:
                return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755,
                                             st_nlink=1)
            return real_stat(name, *a, **k)

        with mock.patch.object(os, "stat", fake_stat):
            self.assertFalse(safe_io._probe())

    def test_probe_symlink_lstat_not_link(self):
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "sym" and k.get("follow_symlinks") is False:
                return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                                             st_nlink=1)
            return real_stat(name, *a, **k)

        with mock.patch.object(os, "stat", fake_stat):
            self.assertFalse(safe_io._probe())

    def test_probe_nofollow_open_succeeds(self):
        leaked = []
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "sym" and flags & os.O_NOFOLLOW:
                fd = real_open("f", os.O_RDONLY, dir_fd=k.get("dir_fd"))
                leaked.append(fd)
                return fd
            return real_open(name, flags, *a, **k)

        try:
            with mock.patch.object(os, "open", fake_open):
                self.assertFalse(safe_io._probe())
        finally:
            for fd in leaked:
                os.close(fd)

    def test_probe_nofollow_wrong_errno(self):
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "sym" and flags & os.O_NOFOLLOW:
                raise PermissionError(errno.EACCES, "denied")
            return real_open(name, flags, *a, **k)

        with mock.patch.object(os, "open", fake_open):
            self.assertFalse(safe_io._probe())

    def test_probe_listdir_empty(self):
        real_listdir = os.listdir

        def fake_listdir(p="."):
            if isinstance(p, int):
                return []
            return real_listdir(p)

        with mock.patch.object(os, "listdir", fake_listdir):
            self.assertFalse(safe_io._probe())

    def test_probe_fstat_not_regular(self):
        def fake_fstat(fd):
            return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)

        with mock.patch.object(os, "fstat", fake_fstat):
            self.assertFalse(safe_io._probe())

    def test_probe_oserror_returns_false(self):
        def fake_mkdir(*a, **k):
            raise OSError(errno.EACCES, "denied")

        with mock.patch.object(os, "mkdir", fake_mkdir):
            self.assertFalse(safe_io._probe())


class TestAcquireRootGuards(unittest.TestCase):
    def test_nonabsolute_realpath_rejected(self):
        with mock.patch.object(os.path, "realpath", return_value="rel/x"):
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io.acquire_root("whatever")

    def test_root_fstat_not_dir(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        with mock.patch.object(stat, "S_ISDIR", lambda m: False):
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io.acquire_root(tmp)


class TestWalkEdges(unittest.TestCase):
    def _root(self, files):
        tmp = U.make_repo(files)
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_enotdir_lstat_failure_reraises(self):
        tmp, auth = self._root({"blk": "x"})
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "blk" and k.get("follow_symlinks") is False:
                raise PermissionError(errno.EACCES, "denied")
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                obs = safe_io.read_rooted_regular(auth, "blk/x.txt")
            self.assertEqual(obs.state, safe_io.RepoReadState.MISSING)
        finally:
            auth.close()

    def test_enotdir_plain_file_reraises(self):
        tmp, auth = self._root({"blk": "x"})
        try:
            obs = safe_io.read_rooted_regular(auth, "blk/x.txt")
            self.assertEqual(obs.state, safe_io.RepoReadState.MISSING)
            self.assertEqual(obs.reason_code, "not_found")
        finally:
            auth.close()

    def test_walk_input_error_classified(self):
        tmp, auth = self._root({"a.txt": "x"})
        try:
            with mock.patch.object(safe_io, "_lexically_valid", return_value=True):
                obs = safe_io.read_rooted_regular(auth, "a//b")
            self.assertEqual(obs.state, safe_io.RepoReadState.UNSAFE_PATH)
            self.assertEqual(obs.reason_code, "invalid_path")
        finally:
            auth.close()

    def test_stale_identity_mid_read(self):
        tmp, auth = self._root({"a.txt": "hello"})
        try:
            with mock.patch.object(safe_io, "_stat_signature",
                                   side_effect=[("s1",), ("s2",)]):
                obs = safe_io.read_rooted_regular(auth, "a.txt")
            self.assertEqual(obs.state, safe_io.RepoReadState.UNREADABLE)
            self.assertEqual(obs.reason_code, "stale_identity")
        finally:
            auth.close()

    def test_classify_oserror_fallback(self):
        obs = safe_io._classify_oserror(OSError(errno.EIO, "io"))
        self.assertEqual(obs.state, safe_io.RepoReadState.UNREADABLE)
        self.assertEqual(obs.reason_code, "io_error")


class TestDiscoveryEdges2(unittest.TestCase):
    def _root(self, files):
        tmp = U.make_repo(files)
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_segment_regex_question_mark(self):
        self.assertEqual(safe_io._segment_regex("a?b"), "a[^/]b")

    def test_string_pattern_accepted(self):
        tmp, auth = self._root({"a.txt": "x", "b.md": "y"})
        try:
            obs = safe_io.discover_rooted_regular(auth, "*.txt")
            self.assertEqual(obs.paths, ("a.txt",))
        finally:
            auth.close()

    def test_listdir_permission_denied(self):
        tmp, auth = self._root({"a.txt": "x"})
        real_listdir = os.listdir

        def fake_listdir(p="."):
            if isinstance(p, int):
                raise PermissionError(errno.EACCES, "denied")
            return real_listdir(p)

        try:
            with mock.patch.object(os, "listdir", fake_listdir):
                obs = safe_io.discover_rooted_regular(auth, ["*.txt"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.UNREADABLE)
            self.assertEqual(obs.reason_code, "permission_denied")
        finally:
            auth.close()

    def test_malformed_name_refused(self):
        tmp, auth = self._root({"a.txt": "x"})
        real_listdir = os.listdir

        def fake_listdir(p="."):
            if isinstance(p, int):
                return ["bad/name"]
            return real_listdir(p)

        try:
            with mock.patch.object(os, "listdir", fake_listdir):
                obs = safe_io.discover_rooted_regular(auth, ["*.txt"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.UNSAFE_PATH)
            self.assertEqual(obs.reason_code, "special_file")
        finally:
            auth.close()

    def test_path_bytes_overflow(self):
        tmp, auth = self._root({"ab.txt": "x"})
        try:
            obs = safe_io.discover_rooted_regular(auth, ["*.txt"], max_path_bytes=2)
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OVERFLOW)
            self.assertEqual(obs.reason_code, "path_bytes_overflow")
        finally:
            auth.close()

    def test_vanished_member_skipped(self):
        tmp, auth = self._root({"a.txt": "x", "b.txt": "y"})
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "a.txt" and k.get("follow_symlinks") is False:
                raise FileNotFoundError(errno.ENOENT, "gone")
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                obs = safe_io.discover_rooted_regular(auth, ["*.txt"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OK)
            self.assertEqual(obs.paths, ("b.txt",))
        finally:
            auth.close()

    def test_member_stat_permission_denied(self):
        tmp, auth = self._root({"a.txt": "x"})
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "a.txt" and k.get("follow_symlinks") is False:
                raise PermissionError(errno.EACCES, "denied")
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                obs = safe_io.discover_rooted_regular(auth, ["*.txt"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.UNREADABLE)
            self.assertEqual(obs.reason_code, "permission_denied")
        finally:
            auth.close()

    def test_child_dir_open_vanished(self):
        tmp, auth = self._root({"d/x.txt": "x", "keep.txt": "y"})
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "d" and flags & os.O_DIRECTORY:
                raise FileNotFoundError(errno.ENOENT, "gone")
            return real_open(name, flags, *a, **k)

        try:
            with mock.patch.object(os, "open", fake_open):
                obs = safe_io.discover_rooted_regular(auth, ["**/*.txt"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OK)
            self.assertEqual(obs.paths, ("keep.txt",))
        finally:
            auth.close()

    def test_child_dir_open_eloop(self):
        tmp, auth = self._root({"d/x.txt": "x", "keep.txt": "y"})
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "d" and flags & os.O_DIRECTORY:
                raise OSError(errno.ELOOP, "loop", name)
            return real_open(name, flags, *a, **k)

        try:
            with mock.patch.object(os, "open", fake_open):
                obs = safe_io.discover_rooted_regular(auth, ["**/*.txt"])
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.UNSAFE_PATH)
            self.assertEqual(obs.reason_code, "symlink")
        finally:
            auth.close()

    def test_repository_input_error_propagates(self):
        tmp, auth = self._root({"x.txt": "x"})
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "x.txt" and k.get("follow_symlinks") is False:
                raise safe_io.RepositoryInputError("boom")
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                with self.assertRaises(safe_io.RepositoryInputError):
                    safe_io.discover_rooted_regular(auth, ["*.txt"])
        finally:
            auth.close()

    def test_leftover_stack_fds_closed(self):
        tmp, auth = self._root({"a/x.txt": "x", "b/sub/y.txt": "y"})
        try:
            obs = safe_io.discover_rooted_regular(auth, ["**/*.txt"], max_depth=1)
            self.assertEqual(obs.state, safe_io.RepoDiscoveryState.OVERFLOW)
            self.assertEqual(obs.reason_code, "depth_overflow")
        finally:
            auth.close()

    def test_leftover_close_failure_swallowed(self):
        tmp, auth = self._root({"a/x.txt": "x", "b/sub/y.txt": "y"})
        state = {}
        real_open = os.open
        real_close = os.close

        def fake_open(name, flags, *a, **k):
            fd = real_open(name, flags, *a, **k)
            if name == "a" and flags & os.O_DIRECTORY:
                state["fd"] = fd
            return fd

        def fake_close(fd):
            if fd == state.get("fd"):
                raise OSError(errno.EBADF, "bad")
            real_close(fd)

        try:
            with mock.patch.object(os, "open", fake_open):
                with mock.patch.object(os, "close", fake_close):
                    obs = safe_io.discover_rooted_regular(auth, ["**/*.txt"],
                                                          max_depth=1)
            self.assertEqual(obs.reason_code, "depth_overflow")
        finally:
            if "fd" in state:
                os.close(state["fd"])  # patch exited: the real close
            auth.close()


class TestWriteTargetEdges2(unittest.TestCase):
    def _root(self, files=None):
        tmp = U.make_repo(files or {})
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_existing_parent_walks_through(self):
        tmp, auth = self._root({"sub/keep.txt": "x"})
        try:
            parts = safe_io.validate_write_target(auth, "sub/new.txt")
            self.assertEqual(parts, ("sub", "new.txt"))
        finally:
            auth.close()

    def test_parent_open_error_reraises(self):
        tmp, auth = self._root()
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "sub" and flags & os.O_DIRECTORY:
                raise PermissionError(errno.EACCES, "denied")
            return real_open(name, flags, *a, **k)

        try:
            with mock.patch.object(os, "open", fake_open):
                with self.assertRaises(PermissionError):
                    safe_io.validate_write_target(auth, "sub/f.txt")
        finally:
            auth.close()

    def test_symlinked_parent_eloop(self):
        tmp, auth = self._root()
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "link" and flags & os.O_DIRECTORY:
                raise OSError(errno.ELOOP, "loop", name)
            return real_open(name, flags, *a, **k)

        try:
            with mock.patch.object(os, "open", fake_open):
                with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                            "symlinked parent"):
                    safe_io.validate_write_target(auth, "link/f.txt")
        finally:
            auth.close()

    def test_final_stat_error_reraises(self):
        tmp, auth = self._root()
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "f.txt" and k.get("follow_symlinks") is False:
                raise PermissionError(errno.EACCES, "denied")
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                with self.assertRaises(PermissionError):
                    safe_io.validate_write_target(auth, "f.txt")
        finally:
            auth.close()

    def test_ensure_plain_eexist_oserror(self):
        tmp, auth = self._root({"sub/keep.txt": "x"})
        err = OSError("exists")
        err.errno = errno.EEXIST

        def fake_mkdir(*a, **k):
            raise err

        try:
            with mock.patch.object(os, "mkdir", fake_mkdir):
                safe_io.ensure_rooted_directory(auth, "sub")
        finally:
            auth.close()

    def test_walk_create_parents_open_error(self):
        tmp, auth = self._root()
        real_open = os.open
        calls = {"n": 0}

        def fake_open(name, flags, *a, **k):
            if name == "sub" and flags & os.O_DIRECTORY:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise FileNotFoundError(errno.ENOENT, "no")
                raise PermissionError(errno.EACCES, "denied")
            return real_open(name, flags, *a, **k)

        try:
            with mock.patch.object(os, "open", fake_open):
                with self.assertRaises(PermissionError):
                    safe_io.create_rooted_exclusive(auth, "sub/f.txt", b"x")
        finally:
            auth.close()


class TestAtomicReplaceEdges(unittest.TestCase):
    def _root(self, files=None):
        tmp = U.make_repo(files or {})
        self.addCleanup(lambda: U.rmtree(tmp))
        return tmp, safe_io.acquire_root(tmp)

    def test_replace_target_not_regular(self):
        tmp, auth = self._root()
        real_stat = os.stat
        calls = {"n": 0}

        def fake_stat(name, *a, **k):
            if name == "rep.txt" and k.get("follow_symlinks") is False:
                calls["n"] += 1
                if calls["n"] == 2:
                    return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                            "not regular"):
                    safe_io.atomic_replace_rooted(auth, "rep.txt", b"x")
            self.assertFalse((tmp / ".rep.txt.ra1-tmp").exists())
        finally:
            auth.close()

    def test_replace_target_stat_error(self):
        tmp, auth = self._root()
        real_stat = os.stat
        calls = {"n": 0}

        def fake_stat(name, *a, **k):
            if name == "rep.txt" and k.get("follow_symlinks") is False:
                calls["n"] += 1
                if calls["n"] == 2:
                    raise PermissionError(errno.EACCES, "denied")
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                with self.assertRaises(PermissionError):
                    safe_io.atomic_replace_rooted(auth, "rep.txt", b"x")
        finally:
            auth.close()

    def test_replace_target_changed_type(self):
        tmp, auth = self._root({"rep.txt": "old"})
        real_stat = os.stat
        calls = {"n": 0}

        def fake_stat(name, *a, **k):
            if name == "rep.txt" and k.get("follow_symlinks") is False:
                calls["n"] += 1
                if calls["n"] == 4:
                    return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)
            return real_stat(name, *a, **k)

        try:
            with mock.patch.object(os, "stat", fake_stat):
                with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                            "changed type"):
                    safe_io.atomic_replace_rooted(auth, "rep.txt", b"new")
            self.assertFalse((tmp / ".rep.txt.ra1-tmp").exists())
            self.assertEqual((tmp / "rep.txt").read_bytes(), b"old")
        finally:
            auth.close()


class TestLockEdges(unittest.TestCase):
    def test_flock_unexpected_error(self):
        with self.assertRaises(OSError):
            safe_io.lock_directory(1 << 22, exclusive=True)  # never a valid fd


class TestPolicyMergeEdges2(unittest.TestCase):
    def _root(self, files=None):
        tmp = U.make_repo(files or {})
        self.addCleanup(lambda: U.rmtree(tmp))
        auth = safe_io.acquire_root(tmp)
        safe_io.ensure_rooted_directory(auth, ".ra1")
        return tmp, auth

    def _tampered_reader(self):
        real = safe_io._read_dir_regular
        calls = {"n": 0}

        def fake(fd, name, max_bytes):
            calls["n"] += 1
            if calls["n"] == 2:
                return safe_io.RootedBytesObservation(safe_io.RepoReadState.OK,
                                                      data=b'{"b": 2}')
            return real(fd, name, max_bytes)

        return fake

    def test_recheck_drift_refused(self):
        tmp, auth = self._root({".ra1/config.json": '{"a": 1}'})
        try:
            with mock.patch.object(safe_io, "_read_dir_regular",
                                   self._tampered_reader()):
                with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                            "changed during merge"):
                    safe_io.merge_rooted_policy_json(auth, ".ra1/config.json",
                                                     lambda p: {**p, "c": 3})
            self.assertEqual((tmp / ".ra1" / "config.json").read_text(
                encoding="utf-8"), '{"a": 1}')
        finally:
            auth.close()

    def test_recheck_drift_unlink_failure_swallowed(self):
        tmp, auth = self._root({".ra1/config.json": '{"a": 1}'})
        real_unlink = os.unlink
        ucalls = {"n": 0}

        def fake_unlink(name, *a, **k):
            ucalls["n"] += 1
            if ucalls["n"] == 2:
                raise PermissionError(errno.EACCES, "denied")
            return real_unlink(name, *a, **k)

        try:
            with mock.patch.object(safe_io, "_read_dir_regular",
                                   self._tampered_reader()):
                with mock.patch.object(os, "unlink", fake_unlink):
                    with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                                "changed during merge"):
                        safe_io.merge_rooted_policy_json(auth, ".ra1/config.json",
                                                         lambda p: {**p, "c": 3})
        finally:
            auth.close()

    def test_lock_open_failure_closes_handles(self):
        tmp, auth = self._root()
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == ".answer.lock":
                raise PermissionError(errno.EACCES, "denied")
            return real_open(name, flags, *a, **k)

        try:
            with mock.patch.object(os, "open", fake_open):
                with self.assertRaises(PermissionError):
                    safe_io.merge_rooted_policy_json(auth, ".ra1/config.json",
                                                     lambda p: p)
        finally:
            auth.close()


class TestSafeCopyTreeEdges(unittest.TestCase):
    def _pair(self, files):
        src = U.make_repo(files)
        dst = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(src))
        self.addCleanup(lambda: U.rmtree(dst))
        return src, dst, safe_io.acquire_root(src), safe_io.acquire_root(dst)

    def _record_open(self, state, name):
        real_open = os.open

        def fake_open(path, flags, *a, **k):
            fd = real_open(path, flags, *a, **k)
            if path == name and flags & os.O_NONBLOCK:
                state["fd"] = fd
            return fd

        return fake_open

    def test_source_fstat_swap(self):
        src, dst, sa, da = self._pair({"f.txt": "data"})
        state = {}
        real_fstat = os.fstat

        def fake_fstat(fd):
            if fd == state.get("fd"):
                state["fd"] = None  # fake once: the fd number is reused after close
                return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644,
                                             st_nlink=2)
            return real_fstat(fd)

        try:
            with mock.patch.object(os, "open", self._record_open(state, "f.txt")):
                with mock.patch.object(os, "fstat", fake_fstat):
                    with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                                "swapped"):
                        safe_io.safe_copy_tree(sa, da)
        finally:
            sa.close()
            da.close()

    def test_source_read_eof_break(self):
        src, dst, sa, da = self._pair({"f.txt": "data"})
        state = {}
        real_read = os.read

        def fake_read(fd, n):
            if fd == state.get("fd"):
                return b""
            return real_read(fd, n)

        try:
            with mock.patch.object(os, "open", self._record_open(state, "f.txt")):
                with mock.patch.object(os, "read", fake_read):
                    safe_io.safe_copy_tree(sa, da)
            self.assertEqual((dst / "f.txt").read_bytes(), b"")
        finally:
            sa.close()
            da.close()

    def test_source_changed_during_copy(self):
        src, dst, sa, da = self._pair({"f.txt": "data"})
        try:
            with mock.patch.object(safe_io, "_stat_signature",
                                   side_effect=[("a",), ("b",)]):
                with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                            "copy source changed"):
                    safe_io.safe_copy_tree(sa, da)
        finally:
            sa.close()
            da.close()

    def test_leftover_stack_fds_closed(self):
        src, dst, sa, da = self._pair({"a/x.txt": "x", "b/y.txt": "y"})
        os.symlink(os.path.join(src, "b", "y.txt"), os.path.join(src, "b", "bad"))
        try:
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "unsafe copy member"):
                safe_io.safe_copy_tree(sa, da)
        finally:
            sa.close()
            da.close()


class TestGitAuthorityEdges(unittest.TestCase):
    def _git_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-ge-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / "README.md").write_text("# x\n")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "T"], ["add", "."],
                     ["commit", "-qm", "i"]):
            subprocess.run(["git", "-C", str(tmp), *args], check=True)
        return tmp

    def _acquire(self, tmp):
        auth = safe_io.acquire_root(tmp)
        try:
            return safe_io.acquire_git_authority(auth)
        finally:
            auth.close()

    def test_authority_close_twice(self):
        authority = self._acquire(self._git_repo())
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        authority.close()
        authority.close()  # second close: root_auth already None

    def test_git_stat_io_error(self):
        tmp = self._git_repo()
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == ".git" and k.get("follow_symlinks") is False:
                raise PermissionError(errno.EACCES, "denied")
            return real_stat(name, *a, **k)

        with mock.patch.object(os, "stat", fake_stat):
            result = self._acquire(tmp)
        self.assertEqual(result.reason, "io_error")

    def test_git_subroot_open_failure(self):
        tmp = self._git_repo()
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == ".git" and flags & os.O_DIRECTORY:
                raise PermissionError(errno.EACCES, "denied")
            return real_open(name, flags, *a, **k)

        with mock.patch.object(os, "open", fake_open):
            result = self._acquire(tmp)
        self.assertEqual(result.reason, "io_error")

    def test_gitfile_fstat_swap(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".git").write_text("gitdir: /tmp/x/wt\n", encoding="utf-8")
        state = {}
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            fd = real_open(name, flags, *a, **k)
            if name == ".git" and flags & os.O_NONBLOCK and not flags & os.O_DIRECTORY:
                state["fd"] = fd
            return fd

        real_fstat = os.fstat

        def fake_fstat(fd):
            if fd == state.get("fd"):
                state["fd"] = None  # fake once: the fd number is reused after close
                return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)
            return real_fstat(fd)

        with mock.patch.object(os, "open", fake_open):
            with mock.patch.object(os, "fstat", fake_fstat):
                result = self._acquire(tmp)
        self.assertEqual(result.reason, "unsafe_metadata")

    def test_gitfile_read_growth_oversize(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        (tmp / ".git").write_text("gitdir: /tmp/x/wt\n", encoding="utf-8")
        with mock.patch.object(safe_io, "_read_bounded_fd", return_value=None):
            result = self._acquire(tmp)
        self.assertEqual(result.reason, "unsafe_metadata")

    def test_walk_candidate_dir_fstat_mismatch(self):
        tmp = U.make_repo({})
        self.addCleanup(lambda: U.rmtree(tmp))
        physical = os.path.realpath(tmp)
        with mock.patch.object(stat, "S_ISDIR", lambda m: False):
            with self.assertRaises(safe_io.RepositoryInputError):
                safe_io._walk_candidate(None, physical, expect="dir")

    def test_config_hardlink_unsafe(self):
        tmp = self._git_repo()
        os.link(tmp / ".git" / "config", tmp / ".git" / "config.bak")
        self.assertEqual(self._acquire(tmp).reason, "unsafe_metadata")

    def test_allowlist_dir_entry_overflow_returns(self):
        tmp = self._git_repo()
        with mock.patch.object(safe_io, "MAX_GIT_SNAPSHOT_ENTRIES", 0):
            result = self._acquire(tmp)
        self.assertEqual(result.reason, "overflow")

    def test_snapshot_config_write_io_error(self):
        tmp = self._git_repo()
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "config" and flags & os.O_CREAT:
                raise OSError(errno.EIO, "io")
            return real_open(name, flags, *a, **k)

        with mock.patch.object(os, "open", fake_open):
            result = self._acquire(tmp)
        self.assertEqual(result.reason, "io_error")

    def test_flatten_unexpected_error_reraises(self):
        tmp = self._git_repo()
        auth = safe_io.acquire_root(tmp)
        try:
            with mock.patch.object(safe_io, "acquire_root",
                                   side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    safe_io.acquire_git_authority(auth)
        finally:
            auth.close()

    def test_metadata_stat_error(self):
        tmp = self._git_repo()
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "objects" and k.get("follow_symlinks") is False:
                raise PermissionError(errno.EACCES, "denied")
            return real_stat(name, *a, **k)

        with mock.patch.object(os, "stat", fake_stat):
            result = self._acquire(tmp)
        self.assertEqual(result.reason, "overflow")

    def test_nested_metadata_symlink(self):
        tmp = self._git_repo()
        os.symlink("info", tmp / ".git" / "objects" / "zz")
        self.assertEqual(self._acquire(tmp).reason, "overflow")

    def test_metadata_fstat_swap(self):
        tmp = self._git_repo()
        state = {}
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            fd = real_open(name, flags, *a, **k)
            if name == "HEAD" and flags & os.O_NONBLOCK:
                state["fd"] = fd
            return fd

        real_fstat = os.fstat

        def fake_fstat(fd):
            if fd == state.get("fd"):
                state["fd"] = None  # fake once: the fd number is reused after close
                return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                                             st_nlink=2)
            return real_fstat(fd)

        with mock.patch.object(os, "open", fake_open):
            with mock.patch.object(os, "fstat", fake_fstat):
                result = self._acquire(tmp)
        self.assertEqual(result.reason, "overflow")

    def test_metadata_read_eof_break(self):
        tmp = self._git_repo()
        state = {}
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            fd = real_open(name, flags, *a, **k)
            if name == "HEAD" and flags & os.O_NONBLOCK:
                state["fd"] = fd
            return fd

        real_read = os.read

        def fake_read(fd, n):
            if fd == state.get("fd"):
                return b""
            return real_read(fd, n)

        with mock.patch.object(os, "open", fake_open):
            with mock.patch.object(os, "read", fake_read):
                result = self._acquire(tmp)
        self.assertIsInstance(result, safe_io.GitSnapshotAuthority)
        result.close()

    def test_metadata_changed_during_copy(self):
        tmp = self._git_repo()
        with mock.patch.object(safe_io, "_stat_signature",
                               side_effect=[("s",), ("s",), ("a",), ("b",)]):
            result = self._acquire(tmp)
        self.assertEqual(result.reason, "overflow")

    def test_sanitized_config_sha256_reftable(self):
        tmp = U.make_repo({".git/HEAD": "ref: refs/heads/main\n",
                           ".git/config": ("[core]\n\trepositoryformatversion = 1\n"
                                           "[extensions]\n\tobjectformat = sha256\n"
                                           "\trefstorage = reftable\n")})
        self.addCleanup(lambda: U.rmtree(tmp))
        authority = self._acquire(tmp)
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        self.assertEqual(authority.object_format, "sha256")
        snap_cfg = (Path(authority.snapshot_path) / ".git" / "config").read_text(
            encoding="utf-8")
        self.assertIn("refstorage = reftable", snap_cfg)
        authority.close()

    def test_sanitized_config_reftable_only(self):
        tmp = U.make_repo({".git/HEAD": "ref: refs/heads/main\n",
                           ".git/config": ("[core]\n\trepositoryformatversion = 1\n"
                                           "[extensions]\n\trefstorage = reftable\n")})
        self.addCleanup(lambda: U.rmtree(tmp))
        authority = self._acquire(tmp)
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        self.assertEqual(authority.object_format, "sha1")
        snap_cfg = (Path(authority.snapshot_path) / ".git" / "config").read_text(
            encoding="utf-8")
        self.assertIn("refstorage = reftable", snap_cfg)
        authority.close()


class TestLinkedTopologyEdges(unittest.TestCase):
    def _fake_linked(self):
        base = Path(os.path.realpath(tempfile.mkdtemp(prefix="ra1-fl-")))
        self.addCleanup(lambda: U.rmtree(base))
        root = base / "root"
        linked = base / "common" / "worktrees" / "wt1"
        linked.mkdir(parents=True)
        root.mkdir()
        (root / ".git").write_text(f"gitdir: {linked}\n", encoding="utf-8")
        (linked / "commondir").write_text("../..", encoding="utf-8")
        (linked / "gitdir").write_text(f"{root}/.git\n", encoding="utf-8")
        (linked / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (base / "common" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
            encoding="utf-8")
        return root, base / "common", linked

    def _misplaced_linked(self, *, make_worktrees):
        base = Path(os.path.realpath(tempfile.mkdtemp(prefix="ra1-mp-")))
        self.addCleanup(lambda: U.rmtree(base))
        root = base / "root"
        linked = base / "elsewhere" / "wt1"
        linked.mkdir(parents=True)
        root.mkdir()
        (root / ".git").write_text(f"gitdir: {linked}\n", encoding="utf-8")
        (linked / "commondir").write_text("../..", encoding="utf-8")
        (linked / "gitdir").write_text(f"{root}/.git\n", encoding="utf-8")
        (linked / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        if make_worktrees:
            (base / "worktrees").mkdir()
        return root

    def _result(self, root):
        auth = safe_io.acquire_root(root)
        try:
            return safe_io.acquire_git_authority(auth)
        finally:
            auth.close()

    def test_fake_linked_authority_ok(self):
        root, _common, _linked = self._fake_linked()
        result = self._result(root)
        self.assertIsInstance(result, safe_io.GitSnapshotAuthority)
        self.assertEqual(result.metadata_profile, "linked_worktree")
        result.close()

    def test_linked_dir_group_writable(self):
        root, _common, linked = self._fake_linked()
        os.chmod(linked, 0o775)
        self.addCleanup(lambda: os.chmod(linked, 0o755))
        self.assertEqual(self._result(root).reason, "unsafe_metadata")

    def test_parent_open_failure(self):
        root, _common, _linked = self._fake_linked()
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            if name == "..":
                raise PermissionError(errno.EACCES, "denied")
            return real_open(name, flags, *a, **k)

        with mock.patch.object(os, "open", fake_open):
            result = self._result(root)
        self.assertEqual(result.reason, "unsafe_metadata")

    def test_common_open_failure(self):
        root, _common, _linked = self._fake_linked()
        real_open = os.open
        calls = {"n": 0}

        def fake_open(name, flags, *a, **k):
            if name == "..":
                calls["n"] += 1
                if calls["n"] == 2:
                    raise PermissionError(errno.EACCES, "denied")
            return real_open(name, flags, *a, **k)

        with mock.patch.object(os, "open", fake_open):
            result = self._result(root)
        self.assertEqual(result.reason, "unsafe_metadata")

    def test_worktrees_dir_missing(self):
        root = self._misplaced_linked(make_worktrees=False)
        self.assertEqual(self._result(root).reason, "unsupported_topology")

    def test_worktrees_identity_mismatch(self):
        root = self._misplaced_linked(make_worktrees=True)
        self.assertEqual(self._result(root).reason, "unsupported_topology")

    def test_backref_identity_mismatch(self):
        root, _common, linked = self._fake_linked()
        decoy = root / "decoy.txt"
        decoy.write_text("x", encoding="utf-8")
        (linked / "gitdir").write_text(f"{decoy}\n", encoding="utf-8")
        self.assertEqual(self._result(root).reason, "unsupported_topology")

    def test_common_config_rejection_propagates(self):
        root, common, _linked = self._fake_linked()
        (common / "config").write_text("[include]\n\tpath = /tmp/x\n",
                                       encoding="utf-8")
        self.assertEqual(self._result(root).reason, "unsupported_topology")

    def test_commondir_not_regular(self):
        root, _common, linked = self._fake_linked()
        (linked / "commondir").unlink()
        (linked / "commondir").mkdir()
        self.assertEqual(self._result(root).reason, "unsupported_topology")

    def test_commondir_hardlink(self):
        root, common, linked = self._fake_linked()
        (linked / "commondir").unlink()
        os.link(common / "config", linked / "commondir")
        self.assertEqual(self._result(root).reason, "unsupported_topology")

    def test_commondir_non_utf8(self):
        root, _common, linked = self._fake_linked()
        (linked / "commondir").write_bytes(b"\xff\xfe../..")
        self.assertEqual(self._result(root).reason, "unsupported_topology")

    def test_linked_own_allowlist_overflow(self):
        root, _common, linked = self._fake_linked()
        (linked / "index").mkdir()
        with mock.patch.object(safe_io, "MAX_GIT_SNAPSHOT_ENTRIES", 0):
            result = self._result(root)
        self.assertEqual(result.reason, "overflow")

    def test_linked_listdir_failure_tolerated(self):
        root, _common, _linked = self._fake_linked()
        real_listdir = os.listdir

        def fake_listdir(p="."):
            if isinstance(p, int):
                raise PermissionError(errno.EACCES, "denied")
            return real_listdir(p)

        with mock.patch.object(os, "listdir", fake_listdir):
            result = self._result(root)
        self.assertIsInstance(result, safe_io.GitSnapshotAuthority)
        result.close()

    def test_sharedindex_rides_along(self):
        root, _common, linked = self._fake_linked()
        (linked / "sharedindex.abc").write_text("x", encoding="utf-8")
        result = self._result(root)
        self.assertIsInstance(result, safe_io.GitSnapshotAuthority)
        self.assertTrue(
            (Path(result.snapshot_path) / ".git" / "sharedindex.abc").is_file())
        result.close()

    def test_sharedindex_overflow(self):
        root, _common, linked = self._fake_linked()
        (linked / "sharedindex.abc").mkdir()
        with mock.patch.object(safe_io, "MAX_GIT_SNAPSHOT_ENTRIES", 0):
            result = self._result(root)
        self.assertEqual(result.reason, "overflow")


class TestProjectGitConfigEdges(unittest.TestCase):
    def test_unknown_allowed_extension_skips_assignment(self):
        tmp = U.make_repo({"config": "[extensions]\n\tfoo = bar\n"})
        self.addCleanup(lambda: U.rmtree(tmp))
        meta = safe_io.acquire_root(tmp)
        fake = {"objectformat": {"sha1", "sha256"}, "refstorage": {"reftable"},
                "foo": {"bar"}}
        try:
            with mock.patch.object(safe_io, "_ALLOWED_EXTENSIONS", fake):
                projection = safe_io._project_git_config(meta)
            self.assertIsInstance(projection, dict)
        finally:
            meta.close()


class TestParseOriginEdges(unittest.TestCase):
    def test_urlsplit_value_error(self):
        self.assertEqual(safe_io._parse_origin_identity("https://[::1/o/r.git"),
                         ())


class TestViewEdges(unittest.TestCase):
    def _authority(self, files):
        tmp = Path(tempfile.mkdtemp(prefix="ra1-ve-"))
        self.addCleanup(lambda: U.rmtree(tmp))
        for rel, content in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "T"], ["add", "."],
                     ["commit", "-qm", "i"]):
            subprocess.run(["git", "-C", str(tmp), *args], check=True)
        auth = safe_io.acquire_root(tmp)
        self.addCleanup(auth.close)
        authority = safe_io.acquire_git_authority(auth)
        self.assertIsInstance(authority, safe_io.GitSnapshotAuthority)
        self.addCleanup(authority.close)
        return tmp, authority

    def test_view_file_size_cap(self):
        tmp, authority = self._authority({"big.txt": "x" * 100})
        with mock.patch.object(safe_io, "MAX_GIT_FILE_BYTES", 10):
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "file size cap"):
                authority.ensure_full_view()

    def test_view_total_bytes_cap(self):
        tmp, authority = self._authority({"v.txt": "y" * 50})
        used = authority._state["stats"]["bytes"]
        with mock.patch.object(safe_io, "MAX_GIT_SNAPSHOT_BYTES", used + 1):
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "total size cap"):
                authority.ensure_full_view()

    def test_view_fstat_swap(self):
        tmp, authority = self._authority({"v.txt": "data"})
        state = {}
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            fd = real_open(name, flags, *a, **k)
            if name == "v.txt" and flags & os.O_NONBLOCK:
                state["fd"] = fd
            return fd

        real_fstat = os.fstat

        def fake_fstat(fd):
            if fd == state.get("fd"):
                state["fd"] = None  # fake once: the fd number is reused after close
                return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644,
                                             st_nlink=2)
            return real_fstat(fd)

        with mock.patch.object(os, "open", fake_open):
            with mock.patch.object(os, "fstat", fake_fstat):
                with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                            "swapped"):
                    authority.ensure_full_view()

    def test_view_read_eof_break(self):
        tmp, authority = self._authority({"v.txt": "data"})
        state = {}
        real_open = os.open

        def fake_open(name, flags, *a, **k):
            fd = real_open(name, flags, *a, **k)
            if name == "v.txt" and flags & os.O_NONBLOCK:
                state["fd"] = fd
            return fd

        real_read = os.read

        def fake_read(fd, n):
            if fd == state.get("fd"):
                return b""
            return real_read(fd, n)

        with mock.patch.object(os, "open", fake_open):
            with mock.patch.object(os, "read", fake_read):
                authority.ensure_full_view()
        snap = Path(authority.snapshot_path) / "v.txt"
        self.assertEqual(snap.read_bytes(), b"")

    def test_view_source_changed(self):
        tmp, authority = self._authority({"v.txt": "data"})
        with mock.patch.object(safe_io, "_stat_signature",
                               side_effect=[("a",), ("b",)]):
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "changed during view copy"):
                authority.ensure_full_view()

    def test_view_depth_cap(self):
        tmp, authority = self._authority({"sub/f.txt": "x"})
        with mock.patch.object(safe_io, "MAX_GIT_SNAPSHOT_DEPTH", 0):
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "depth cap"):
                authority.ensure_full_view()

    def test_view_member_vanished(self):
        tmp, authority = self._authority({"gone.txt": "x"})
        real_stat = os.stat

        def fake_stat(name, *a, **k):
            if name == "gone.txt" and k.get("follow_symlinks") is False:
                raise FileNotFoundError(errno.ENOENT, "gone")
            return real_stat(name, *a, **k)

        with mock.patch.object(os, "stat", fake_stat):
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "vanished"):
                authority.ensure_full_view()

    def test_view_entry_cap(self):
        tmp, authority = self._authority({"v.txt": "x"})
        used = authority._state["stats"]["entries"]
        with mock.patch.object(safe_io, "MAX_GIT_SNAPSHOT_ENTRIES", used):
            with self.assertRaisesRegex(safe_io.RepositoryInputError,
                                        "entry cap"):
                authority.ensure_full_view()


if __name__ == "__main__":
    unittest.main()