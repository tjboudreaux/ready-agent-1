"""Focused tests for the bounded process authority (engine/readiness/process.py).

Covers closed tool IDs and startup resolution, host-token/config/proxy authorities,
BoundedProcessResult validity, output/timeout/group-kill capture, and git resource
profiles.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from readiness import process, safe_io

import tests._util as U


class TestToolchain(unittest.TestCase):
    def test_resolve_toolchain_has_shim_and_git(self):
        tc = process.resolve_toolchain("/nonexistent", startup_path="")
        self.assertIsNotNone(tc.get(process.ToolId.PYTHON_SHIM))
        self.assertIsNone(tc.get(process.ToolId.GIT))
        tc2 = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        self.assertIsNotNone(tc2.get(process.ToolId.PYTHON_SHIM))

    def test_unknown_startup_path_ignored(self):
        tc = process.resolve_toolchain("/", startup_path="/definitely/not/a/path")
        self.assertIsNone(tc.get(process.ToolId.GIT))


class TestAuthorities(unittest.TestCase):
    def test_proxy_disabled_none(self):
        self.assertIsNone(process.capture_host_proxy_authority(False, {"HTTPS_PROXY": "x"}))

    def test_proxy_captures_exact_keys(self):
        auth = process.capture_host_proxy_authority(
            True, {"HTTPS_PROXY": "http://p:1", "no_proxy": "localhost", "OTHER": "x"})
        self.assertIsNotNone(auth)
        pairs = dict(auth.pairs)
        self.assertEqual(pairs, {"HTTPS_PROXY": "http://p:1", "no_proxy": "localhost"})
        self.assertNotIn("OTHER", pairs)

    def test_proxy_invalid_controls(self):
        for bad in ("a\x00b", "a\nb", "a\u202eb"):
            with self.subTest(value=bad), self.assertRaises(process.HostProxyError):
                process.capture_host_proxy_authority(
                    True, {"HTTPS_PROXY": bad})

    def test_proxy_oversize(self):
        with self.assertRaises(process.HostProxyError):
            process.capture_host_proxy_authority(
                True, {"HTTPS_PROXY": "x" * 5000})

    def test_github_auth_token(self):
        auth = process.capture_github_auth_authority({"GH_TOKEN": "abc123"})
        self.assertIsNotNone(auth)
        self.assertEqual(auth.kind, "token")
        env = auth.env()
        self.assertEqual(env["GH_TOKEN"], "abc123")
        self.assertEqual(env["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(env["NO_COLOR"], "1")
        self.assertNotIn("GITHUB_TOKEN", env)
        auth.close()

    def test_github_auth_token_preference(self):
        auth = process.capture_github_auth_authority(
            {"GH_TOKEN": "first", "GITHUB_TOKEN": "second"})
        self.assertEqual(auth.env()["GH_TOKEN"], "first")
        auth.close()

    def test_github_auth_no_token_no_config(self):
        self.assertIsNone(process.capture_github_auth_authority({"HOME": "/nonexistent"}))

    def test_github_auth_malformed_token_ignored(self):
        self.assertIsNone(process.capture_github_auth_authority({"GH_TOKEN": "x\x00y"}))


class TestResult(unittest.TestCase):
    def test_result_validity(self):
        ok = process.BoundedProcessResult(process.ProcessState.OK, returncode=0,
                                          stdout="o")
        self.assertEqual(ok.state, process.ProcessState.OK)
        with self.assertRaises(TypeError):
            process.BoundedProcessResult(process.ProcessState.TIMEOUT, stdout="x")
        with self.assertRaises(TypeError):
            process.BoundedProcessResult(process.ProcessState.OK, returncode=1)
        with self.assertRaises(TypeError):
            process.BoundedProcessResult(process.ProcessState.SPAWN_ERROR, returncode=1)

    def test_result_guard_branches(self):
        with self.assertRaises(TypeError):
            process.BoundedProcessResult("ok", returncode=0)  # state not a ProcessState
        with self.assertRaises(TypeError):
            process.BoundedProcessResult(process.ProcessState.NONZERO)  # no returncode
        with self.assertRaises(TypeError):
            process.BoundedProcessResult(process.ProcessState.TIMEOUT, returncode=1)


class TestRunBounded(unittest.TestCase):
    def _shim_dir(self):
        d = Path(tempfile.mkdtemp(prefix="ra1-proc-"))
        self.addCleanup(lambda: U.rmtree(d))
        return d

    def test_run_shim_echo(self):
        d = self._shim_dir()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        result = process.run_bounded_process(
            process.ToolId.PYTHON_SHIM,
            ["-c", "import sys; print(sys.argv[1])", "hello"],
            toolchain=tc, cwd_handle=fd, env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=10)
        os.close(fd)
        self.assertEqual(result.state, process.ProcessState.OK)
        self.assertIn("hello", result.stdout)

    def test_spawn_error_on_missing_target(self):
        d = self._shim_dir()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        bogus = process.Toolchain((
            (process.ToolId.PYTHON_SHIM, tc.get(process.ToolId.PYTHON_SHIM)),
            (process.ToolId.GIT, "/nonexistent/git"),
        ))
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        result = process.run_bounded_process(
            process.ToolId.GIT, ["--version"], toolchain=bogus, cwd_handle=fd,
            env={"PATH": "/usr/bin:/bin"}, timeout_seconds=10)
        os.close(fd)
        self.assertEqual(result.state, process.ProcessState.SPAWN_ERROR)

    def test_output_limit_kills_child(self):
        d = self._shim_dir()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        code = ("import sys\n"
                "while True:\n"
                "    sys.stdout.write('x' * 4096)\n"
                "    sys.stdout.flush()\n")
        result = process.run_bounded_process(
            process.ToolId.PYTHON_SHIM, ["-c", code],
            toolchain=tc, cwd_handle=fd, env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=10)
        os.close(fd)
        self.assertEqual(result.state, process.ProcessState.OUTPUT_LIMIT)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_timeout_kills_child(self):
        d = self._shim_dir()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        result = process.run_bounded_process(
            process.ToolId.PYTHON_SHIM, ["-c", "import time; time.sleep(60)"],
            toolchain=tc, cwd_handle=fd, env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=1)
        os.close(fd)
        self.assertEqual(result.state, process.ProcessState.TIMEOUT)

    def test_nonzero_keeps_streams_private(self):
        d = self._shim_dir()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        result = process.run_bounded_process(
            process.ToolId.PYTHON_SHIM,
            ["-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
            toolchain=tc, cwd_handle=fd, env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=10)
        os.close(fd)
        self.assertEqual(result.state, process.ProcessState.NONZERO)
        self.assertEqual(result.returncode, 3)
        self.assertIn("boom", result.stderr)

    def test_invalid_args_rejected(self):
        d = self._shim_dir()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        with self.assertRaises(TypeError):
            process.run_bounded_process(process.ToolId.PYTHON_SHIM, [None],
                                        toolchain=tc, cwd_handle=fd,
                                        env={"PATH": "/usr/bin:/bin"},
                                        timeout_seconds=10)
        with self.assertRaises(TypeError):
            process.run_bounded_process(process.ToolId.PYTHON_SHIM, ["x" * 5000],
                                        toolchain=tc, cwd_handle=fd,
                                        env={"PATH": "/usr/bin:/bin"},
                                        timeout_seconds=10)
        os.close(fd)

    def test_unsupported_tool(self):
        d = self._shim_dir()
        tc = process.Toolchain(((process.ToolId.PYTHON_SHIM, sys.executable),))
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        result = process.run_bounded_process(
            process.ToolId.GIT, ["--version"], toolchain=tc, cwd_handle=fd,
            env={"PATH": "/usr/bin:/bin"}, timeout_seconds=10)
        os.close(fd)
        self.assertEqual(result.state, process.ProcessState.UNSUPPORTED)


class TestGitBounded(unittest.TestCase):
    def _git_repo(self):
        d = Path(tempfile.mkdtemp(prefix="ra1-pg-"))
        self.addCleanup(lambda: U.rmtree(d))
        (d / "f.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(d), "add", "."], check=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", "i"], check=True)
        return d

    def _authority(self, d):
        auth = safe_io.acquire_root(d)
        self.addCleanup(auth.close)
        ga = safe_io.acquire_git_authority(auth)
        self.assertIsInstance(ga, safe_io.GitSnapshotAuthority)
        self.addCleanup(ga.close)
        return ga

    def test_git_rev_parse(self):
        d = self._git_repo()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        ga = self._authority(d)
        result = process.run_git_bounded(ga, ["rev-parse", "HEAD"],
                                         toolchain=tc, budget=process.GitBudget())
        self.assertEqual(result.state, process.ProcessState.OK)
        self.assertEqual(len(result.stdout.strip()), 40)

    def test_git_budget_command_exhaustion(self):
        d = self._git_repo()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        ga = self._authority(d)
        budget = process.GitBudget()
        budget.commands = process.MAX_GIT_COMMANDS_PER_AUTHORITY
        result = process.run_git_bounded(ga, ["rev-parse", "HEAD"],
                                         toolchain=tc, budget=budget)
        self.assertEqual(result.state, process.ProcessState.RESOURCE_LIMIT)

    def test_git_budget_wall_exhaustion(self):
        d = self._git_repo()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        ga = self._authority(d)
        budget = process.GitBudget()
        budget.wall_seconds = process.MAX_GIT_WALL_SECONDS_PER_AUTHORITY
        result = process.run_git_bounded(ga, ["rev-parse", "HEAD"],
                                         toolchain=tc, budget=budget)
        self.assertEqual(result.state, process.ProcessState.RESOURCE_LIMIT)

    def test_git_profile_unavailable(self):
        d = self._git_repo()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        ga = self._authority(d)
        with mock.patch.object(process, "git_resource_profile", return_value=None):
            result = process.run_git_bounded(ga, ["rev-parse", "HEAD"],
                                             toolchain=tc, budget=process.GitBudget())
        self.assertEqual(result.state, process.ProcessState.UNSUPPORTED)

    def test_git_closed_authority_spawn_error(self):
        d = self._git_repo()
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        auth = safe_io.acquire_root(d)
        self.addCleanup(auth.close)
        ga = safe_io.acquire_git_authority(auth)
        ga.close()  # removes the snapshot tree
        result = process.run_git_bounded(ga, ["rev-parse", "HEAD"],
                                         toolchain=tc, budget=process.GitBudget())
        self.assertEqual(result.state, process.ProcessState.SPAWN_ERROR)

    def test_git_requires_authority(self):
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        with self.assertRaises(TypeError):
            process.run_git_bounded(None, ["rev-parse", "HEAD"],
                                    toolchain=tc, budget=process.GitBudget())

    def test_git_resource_profile(self):
        profile = process.git_resource_profile()
        self.assertIn(profile, ("linux_as_cpu_core", "darwin_cpu_core_no_hard_memory"))


class TestGitResourceProfileBranches(unittest.TestCase):
    def _fake_resource(self, **overrides):
        ns = types.SimpleNamespace(getrlimit=lambda r: (0, 0), setrlimit=lambda r, v: None,
                                   RLIMIT_CPU=0, RLIMIT_CORE=1, RLIM_INFINITY=-1)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def test_import_error(self):
        with mock.patch.dict(sys.modules, {"resource": None}):
            self.assertIsNone(process.git_resource_profile())

    def test_missing_required_attribute(self):
        fake = types.SimpleNamespace(getrlimit=lambda r: (0, 0))  # no setrlimit/RLIMIT_*
        with mock.patch.dict(sys.modules, {"resource": fake}):
            self.assertIsNone(process.git_resource_profile())

    def test_linux_with_address_space(self):
        fake = self._fake_resource(RLIMIT_AS=9)
        with mock.patch.dict(sys.modules, {"resource": fake}), \
                mock.patch.object(process.sys, "platform", "linux"):
            self.assertEqual(process.git_resource_profile(), "linux_as_cpu_core")

    def test_linux_without_address_space(self):
        fake = self._fake_resource()
        with mock.patch.dict(sys.modules, {"resource": fake}), \
                mock.patch.object(process.sys, "platform", "linux"):
            self.assertIsNone(process.git_resource_profile())

    def test_darwin(self):
        fake = self._fake_resource()
        with mock.patch.dict(sys.modules, {"resource": fake}), \
                mock.patch.object(process.sys, "platform", "darwin"):
            self.assertEqual(process.git_resource_profile(),
                             "darwin_cpu_core_no_hard_memory")

    def test_unknown_platform(self):
        fake = self._fake_resource()
        with mock.patch.dict(sys.modules, {"resource": fake}), \
                mock.patch.object(process.sys, "platform", "plan9"):
            self.assertIsNone(process.git_resource_profile())


class TestProxyAuthorityExtra(unittest.TestCase):
    def test_empty_values_skipped(self):
        auth = process.capture_host_proxy_authority(
            True, {"HTTPS_PROXY": "", "NO_PROXY": "localhost"})
        self.assertEqual(dict(auth.pairs), {"NO_PROXY": "localhost"})

    def test_non_string_value_rejected(self):
        with self.assertRaises(process.HostProxyError):
            process.capture_host_proxy_authority(True, {"HTTPS_PROXY": 123})

    def test_del_and_c1_controls_rejected(self):
        for bad in ("a\x7fb", "a\x85b"):
            with self.subTest(value=bad), self.assertRaises(process.HostProxyError):
                process.capture_host_proxy_authority(True, {"HTTPS_PROXY": bad})

    def test_env_overlay_round_trip(self):
        auth = process.capture_host_proxy_authority(
            True, {"HTTPS_PROXY": "http://p:1", "no_proxy": "localhost"})
        self.assertEqual(auth.env_overlay(),
                         {"HTTPS_PROXY": "http://p:1", "no_proxy": "localhost"})


class TestGithubAuthEnv(unittest.TestCase):
    def test_env_excludes_unrelated_vars(self):
        auth = process.capture_github_auth_authority({"GH_TOKEN": "tok"})
        env = auth.env()
        self.assertEqual(set(env), set(process._GITHUB_ENV_CONSTANTS)
                         | {"GH_TOKEN", "PATH"})
        self.assertNotIn("HOME", env)
        self.assertNotIn("HTTPS_PROXY", env)
        auth.close()

    def test_env_with_proxy_overlay(self):
        auth = process.capture_github_auth_authority({"GH_TOKEN": "tok"})
        proxy = process.capture_host_proxy_authority(True, {"HTTPS_PROXY": "http://p:1"})
        env = auth.env(proxy)
        self.assertEqual(env["HTTPS_PROXY"], "http://p:1")
        self.assertEqual(env["GH_TOKEN"], "tok")
        auth.close()

    def test_github_token_fallback(self):
        auth = process.capture_github_auth_authority({"GITHUB_TOKEN": "g"})
        self.assertEqual(auth.kind, "token")
        self.assertEqual(auth.env()["GH_TOKEN"], "g")
        auth.close()

    def test_oversize_token_rejected(self):
        self.assertIsNone(process.capture_github_auth_authority(
            {"GH_TOKEN": "x" * (process._MAX_TOKEN_BYTES + 1)}))

    def test_token_close_is_noop(self):
        auth = process.capture_github_auth_authority({"GH_TOKEN": "tok"})
        auth.close()
        auth.close()


class TestGithubConfigAuthority(unittest.TestCase):
    def _gh_dir(self, parent: Path, rel: str, files=("hosts.yml",)) -> Path:
        cfg = parent / rel
        cfg.mkdir(parents=True)
        for name in files:
            (cfg / name).write_text("github.com:\n  oauth_token: x\n", encoding="utf-8")
        return cfg

    def _home(self) -> Path:
        home = Path(tempfile.mkdtemp(prefix="ra1-ghhome-"))
        self.addCleanup(lambda: U.rmtree(home))
        return home

    def test_config_via_home(self):
        home = self._home()
        self._gh_dir(home, ".config/gh")
        auth = process.capture_github_auth_authority({"HOME": str(home)})
        self.assertIsNotNone(auth)
        self.assertEqual(auth.kind, "config")
        env = auth.env()
        self.assertNotIn("GH_TOKEN", env)
        copied = Path(env["GH_CONFIG_DIR"])
        self.assertTrue((copied / "hosts.yml").is_file())
        self.assertEqual((copied / "hosts.yml").read_text(encoding="utf-8"),
                         "github.com:\n  oauth_token: x\n")
        auth.close()
        self.assertFalse(copied.exists())
        auth.close()  # idempotent

    def test_config_dir_precedence(self):
        home = self._home()
        explicit = self._gh_dir(home, "explicit")
        (explicit / "hosts.yml").write_text("marker: explicit\n", encoding="utf-8")
        self._gh_dir(home, "xdg/gh")
        auth = process.capture_github_auth_authority(
            {"GH_CONFIG_DIR": str(explicit), "XDG_CONFIG_HOME": str(home / "xdg"),
             "HOME": str(home)})
        env = auth.env()
        self.assertEqual((Path(env["GH_CONFIG_DIR"]) / "hosts.yml")
                         .read_text(encoding="utf-8"), "marker: explicit\n")
        auth.close()

    def test_xdg_config_home_used(self):
        home = self._home()
        self._gh_dir(home, "xdg/gh")
        auth = process.capture_github_auth_authority(
            {"XDG_CONFIG_HOME": str(home / "xdg"), "HOME": "/nonexistent"})
        self.assertIsNotNone(auth)
        self.assertEqual(auth.kind, "config")
        auth.close()

    def test_config_yml_copied_alongside_hosts(self):
        home = self._home()
        self._gh_dir(home, ".config/gh", files=("hosts.yml", "config.yml"))
        auth = process.capture_github_auth_authority({"HOME": str(home)})
        copied = Path(auth.env()["GH_CONFIG_DIR"])
        self.assertTrue((copied / "config.yml").is_file())
        auth.close()

    def test_missing_hosts_yml(self):
        home = self._home()
        self._gh_dir(home, ".config/gh", files=("config.yml",))
        self.assertIsNone(process.capture_github_auth_authority({"HOME": str(home)}))

    def test_group_writable_config_dir_rejected(self):
        home = self._home()
        cfg = self._gh_dir(home, ".config/gh")
        os.chmod(cfg, 0o775)
        self.assertIsNone(process.capture_github_auth_authority({"HOME": str(home)}))

    def test_nul_config_dir_rejected(self):
        self.assertIsNone(process.capture_github_auth_authority(
            {"GH_CONFIG_DIR": "bad\x00dir"}))

    def test_nonexistent_config_dir(self):
        self.assertIsNone(process.capture_github_auth_authority(
            {"GH_CONFIG_DIR": "/nonexistent-ra1/gh"}))

    def test_empty_token_falls_through_to_config(self):
        home = self._home()
        self._gh_dir(home, ".config/gh")
        auth = process.capture_github_auth_authority(
            {"GH_TOKEN": "", "HOME": str(home)})
        self.assertIsNotNone(auth)
        self.assertEqual(auth.kind, "config")
        auth.close()

    def test_config_env_without_tempdir(self):
        auth = process.GithubAuthAuthority(kind="config")
        self.assertEqual(auth.env()["GH_CONFIG_DIR"], "")

    def test_no_candidates_without_home(self):
        self.assertIsNone(process.capture_github_auth_authority({}))
        self.assertIsNone(process.capture_github_auth_authority(
            {"GH_CONFIG_DIR": "", "XDG_CONFIG_HOME": "", "HOME": ""}))

    def test_config_copy_failure_returns_none(self):
        home = self._home()
        self._gh_dir(home, ".config/gh")
        with mock.patch.object(safe_io, "create_rooted_exclusive",
                               side_effect=OSError("disk full")):
            self.assertIsNone(process.capture_github_auth_authority({"HOME": str(home)}))


class TestArgEnvValidation(unittest.TestCase):
    def test_validate_args_type_and_caps(self):
        with self.assertRaises(TypeError):
            process._validate_args("git")
        with self.assertRaises(TypeError):
            process._validate_args(["a\x00b"])
        with self.assertRaises(TypeError):
            process._validate_args(["x"] * (process.MAX_PROCESS_ARGS + 1))
        self.assertEqual(process._validate_args(("a", "b")), ("a", "b"))

    def test_validate_env_rejections(self):
        for bad in ("not-a-dict", {1: "x"}, {"A": 1}, {"A": "x\x00"},
                    {"A=B": "x"}, {"A\x00": "x"}):
            with self.subTest(env=bad), self.assertRaises(TypeError):
                process._validate_env(bad)
        self.assertEqual(process._validate_env({"A": "b"}), {"A": "b"})

    def test_run_bounded_rejects_bad_scalars(self):
        d = Path(tempfile.mkdtemp(prefix="ra1-proc-"))
        self.addCleanup(lambda: U.rmtree(d))
        tc = process.Toolchain(((process.ToolId.PYTHON_SHIM, sys.executable),))
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(TypeError):
                process.run_bounded_process("git", [], toolchain=tc, cwd_handle=fd,
                                            env={}, timeout_seconds=10)
            for bad_fd in (-1, "x"):
                with self.subTest(fd=bad_fd), self.assertRaises(TypeError):
                    process.run_bounded_process(process.ToolId.PYTHON_SHIM, [],
                                                toolchain=tc, cwd_handle=bad_fd,
                                                env={}, timeout_seconds=10)
            for bad_timeout in (True, 0, -2, "10"):
                with self.subTest(timeout=bad_timeout), self.assertRaises(TypeError):
                    process.run_bounded_process(process.ToolId.PYTHON_SHIM, [],
                                                toolchain=tc, cwd_handle=fd,
                                                env={}, timeout_seconds=bad_timeout)
        finally:
            os.close(fd)


class TestStatusByte(unittest.TestCase):
    def test_expired_deadline_returns_none(self):
        rfd, wfd = os.pipe()
        try:
            self.assertIsNone(process._read_status_byte(rfd, time.monotonic() - 1))
        finally:
            os.close(rfd)
            os.close(wfd)

    def test_idle_pipe_loops_until_deadline(self):
        rfd, wfd = os.pipe()
        try:
            self.assertIsNone(
                process._read_status_byte(rfd, time.monotonic() + 0.25))
        finally:
            os.close(rfd)
            os.close(wfd)

    def test_written_byte_returned(self):
        rfd, wfd = os.pipe()
        try:
            os.write(wfd, b"R")
            self.assertEqual(process._read_status_byte(rfd, time.monotonic() + 5), b"R")
        finally:
            os.close(rfd)
            os.close(wfd)

    def test_read_oserror_maps_to_eof(self):
        rfd, wfd = os.pipe()
        try:
            os.write(wfd, b"z")
            with mock.patch.object(process.os, "read", side_effect=OSError("boom")):
                self.assertEqual(process._read_status_byte(rfd, time.monotonic() + 5),
                                 b"")
        finally:
            os.close(rfd)
            os.close(wfd)


class TestShimStatusPaths(unittest.TestCase):
    def _workspace(self):
        d = Path(tempfile.mkdtemp(prefix="ra1-proc-"))
        self.addCleanup(lambda: U.rmtree(d))
        (d / "f.txt").write_text("x\n", encoding="utf-8")
        return d

    def _tc(self):
        return process.resolve_toolchain("/", startup_path="/usr/bin:/bin")

    def test_fchdir_failure_maps_spawn_error(self):
        d = self._workspace()
        file_fd = os.open(d / "f.txt", os.O_RDONLY)  # not a directory: fchdir fails
        try:
            result = process.run_bounded_process(
                process.ToolId.PYTHON_SHIM, ["-c", "pass"],
                toolchain=self._tc(), cwd_handle=file_fd,
                env={"PATH": "/usr/bin:/bin"}, timeout_seconds=10)
        finally:
            os.close(file_fd)
        self.assertEqual(result.state, process.ProcessState.SPAWN_ERROR)

    def test_rlimit_failure_maps_resource_limit(self):
        d = self._workspace()
        shim = "import os, sys\nos.write(int(sys.argv[2]), b'R')\nos._exit(127)\n"
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(process, "GIT_SHIM_SOURCE", shim):
                result = process.run_bounded_process(
                    process.ToolId.GIT, ["--version"],
                    toolchain=self._tc(), cwd_handle=fd,
                    env={"PATH": "/usr/bin:/bin"}, timeout_seconds=10)
        finally:
            os.close(fd)
        self.assertEqual(result.state, process.ProcessState.RESOURCE_LIMIT)

    def test_silent_shim_maps_timeout(self):
        d = self._workspace()
        shim = "import time\ntime.sleep(60)\n"
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(process, "_SHIM_BODY", shim):
                result = process.run_bounded_process(
                    process.ToolId.PYTHON_SHIM, ["-c", "pass"],
                    toolchain=self._tc(), cwd_handle=fd,
                    env={"PATH": "/usr/bin:/bin"}, timeout_seconds=1)
        finally:
            os.close(fd)
        self.assertEqual(result.state, process.ProcessState.TIMEOUT)

    def test_popen_failure_maps_spawn_error(self):
        d = self._workspace()
        bogus = process.Toolchain((
            (process.ToolId.PYTHON_SHIM, "/nonexistent-ra1/python"),
            (process.ToolId.GIT, "/nonexistent-ra1/git"),
        ))
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            result = process.run_bounded_process(
                process.ToolId.GIT, ["--version"], toolchain=bogus, cwd_handle=fd,
                env={"PATH": "/usr/bin:/bin"}, timeout_seconds=10)
        finally:
            os.close(fd)
        self.assertEqual(result.state, process.ProcessState.SPAWN_ERROR)


class TestKillGroup(unittest.TestCase):
    def test_killpg_failure_falls_back_to_proc_kill(self):
        class FakeProc:
            pid = 1 << 22  # no such process group
            killed = False

            def kill(self):
                self.killed = True

        proc = FakeProc()
        process._kill_group(proc)
        self.assertTrue(proc.killed)

    def test_proc_kill_oserror_swallowed(self):
        class FakeProc:
            pid = 1 << 22

            def kill(self):
                raise OSError("gone")

        process._kill_group(FakeProc())  # must not raise


class TestDrainBoundary(unittest.TestCase):
    def _run(self, code, timeout):
        d = Path(tempfile.mkdtemp(prefix="ra1-proc-"))
        self.addCleanup(lambda: U.rmtree(d))
        tc = process.resolve_toolchain("/", startup_path="/usr/bin:/bin")
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            return process.run_bounded_process(
                process.ToolId.PYTHON_SHIM, ["-c", code],
                toolchain=tc, cwd_handle=fd, env={"PATH": "/usr/bin:/bin"},
                timeout_seconds=timeout)
        finally:
            os.close(fd)

    def test_quiet_fast_child_ok(self):
        result = self._run("pass", 10)
        self.assertEqual(result.state, process.ProcessState.OK)
        self.assertEqual(result.stdout, "")

    def test_child_outliving_closed_pipes_is_reaped(self):
        code = ("import os, time\n"
                "d = os.open(os.devnull, os.O_WRONLY)\n"
                "os.dup2(d, 1)\n"
                "os.dup2(d, 2)\n"
                "time.sleep(60)\n")
        result = self._run(code, 60)
        self.assertEqual(result.state, process.ProcessState.NONZERO)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class TestResolveToolchainEdges(unittest.TestCase):
    def test_candidate_stat_oserror_is_skipped(self):
        with mock.patch.object(process.os.path, "isfile", side_effect=OSError("boom")):
            tc = process.resolve_toolchain("/", startup_path="/usr/bin")
        self.assertIsNone(tc.get(process.ToolId.GIT))
        self.assertIsNotNone(tc.get(process.ToolId.PYTHON_SHIM))


class TestProxyAuthorityCap(unittest.TestCase):
    def test_total_cap_equality_refuses(self):
        # 4 keys x _MAX_PROXY_VALUE_BYTES == _MAX_PROXY_TOTAL_BYTES: equality refuses.
        env = {key: "x" * process._MAX_PROXY_VALUE_BYTES
               for key in process._PROXY_KEYS}
        with self.assertRaises(process.HostProxyError):
            process.capture_host_proxy_authority(True, env)
        # One key fewer stays strictly under the ceiling and is accepted.
        under = {key: "x" * process._MAX_PROXY_VALUE_BYTES
                 for key in process._PROXY_KEYS[:3]}
        self.assertIsNotNone(process.capture_host_proxy_authority(True, under))


class _WaitTimeoutProc:
    """First wait() raises TimeoutExpired; later waits succeed (reaped after kill)."""

    pid = 1 << 22  # no such process group: _kill_group falls back to kill()

    def __init__(self):
        self.calls = 0

    def wait(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def kill(self):
        pass


class TestStatusWaitTimeouts(unittest.TestCase):
    def _run_with_status(self, status):
        d = Path(tempfile.mkdtemp(prefix="ra1-proc-"))
        self.addCleanup(lambda: U.rmtree(d))
        tc = process.Toolchain(((process.ToolId.PYTHON_SHIM, sys.executable),))
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        proc = _WaitTimeoutProc()
        try:
            with mock.patch.object(process.subprocess, "Popen", return_value=proc), \
                    mock.patch.object(process, "_read_status_byte",
                                      return_value=status):
                return process.run_bounded_process(
                    process.ToolId.PYTHON_SHIM, [], toolchain=tc, cwd_handle=fd,
                    env={}, timeout_seconds=10)
        finally:
            os.close(fd)

    def test_missing_status_byte_reap_timeout(self):
        result = self._run_with_status(None)
        self.assertEqual(result.state, process.ProcessState.TIMEOUT)

    def test_failure_status_byte_reap_timeout(self):
        result = self._run_with_status(b"E")
        self.assertEqual(result.state, process.ProcessState.SPAWN_ERROR)


class _DrainProc:
    pid = 1 << 22

    def __init__(self, out, err, *, wait_raises=False):
        self.stdout = out
        self.stderr = err
        self._wait_raises = wait_raises
        self._waits = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        self._waits += 1
        if self._wait_raises and self._waits == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def kill(self):
        pass


class _CloseBoom:
    """A stream whose close() raises OSError after closing (drain must swallow it)."""

    def __init__(self, stream):
        self._stream = stream

    def fileno(self):
        return self._stream.fileno()

    def close(self):
        try:
            self._stream.close()
        finally:
            raise OSError("boom")


class _FakeKey:
    def __init__(self, fileobj, data):
        self.fileobj = fileobj
        self.data = data


class _FakeSel:
    """Scripted selector: one event, then silence; unregister raises KeyError."""

    def __init__(self):
        self._registered = []
        self._selects = 0

    def register(self, fileobj, events, data=None):
        self._registered.append(_FakeKey(fileobj, data))

    def get_map(self):
        return {i: key for i, key in enumerate(self._registered)}

    def select(self, timeout=None):
        self._selects += 1
        if self._selects == 1:
            return [(self._registered[0], 1)]
        return []

    def unregister(self, fileobj):
        raise KeyError("gone")

    def close(self):
        pass


class TestDrainEdges(unittest.TestCase):
    def _pipe(self, data=b""):
        rfd, wfd = os.pipe()
        if data:
            os.write(wfd, data)
        os.close(wfd)
        return os.fdopen(rfd, "rb")

    def test_read_blocking_and_oserror_branches(self):
        proc = _DrainProc(self._pipe(b"hello"), self._pipe())
        real_read = os.read
        calls = {"n": 0}

        def flaky(fd, n):
            calls["n"] += 1
            if calls["n"] == 1:
                raise BlockingIOError()
            if calls["n"] == 2:
                raise OSError("boom")
            return real_read(fd, n)

        with mock.patch.object(process.os, "read", side_effect=flaky):
            result = process._drain(proc, time.monotonic() + 10)
        self.assertEqual(result.state, process.ProcessState.OK)
        self.assertEqual(result.returncode, 0)

    def test_unregister_keyerror_and_idle_exit_break(self):
        proc = _DrainProc(self._pipe(), self._pipe())
        with mock.patch.object(process.selectors, "DefaultSelector", _FakeSel):
            result = process._drain(proc, time.monotonic() + 10)
        self.assertEqual(result.state, process.ProcessState.OK)
        self.assertEqual(result.returncode, 0)

    def test_closed_pipe_wait_timeout_and_close_oserror(self):
        proc = _DrainProc(_CloseBoom(self._pipe()), _CloseBoom(self._pipe()),
                          wait_raises=True)
        result = process._drain(proc, time.monotonic() + 10)
        self.assertEqual(result.state, process.ProcessState.TIMEOUT)


if __name__ == "__main__":
    unittest.main()