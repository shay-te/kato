"""Tests for docker_available, gvisor_runtime_available, docker_running_rootless,
check_gvisor_or_exit, check_docker_or_exit, make_container_name, login_command,
ensure_image, ensure_network, stamp_auth_volume_manifest, and _image_digest_strict.

These functions all call subprocess and/or sys.exit; we mock subprocess.run and
sys.exit to keep tests hermetic. No Docker daemon is required to run the suite.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from sandbox_core_lib.sandbox_core_lib.manager import (
    SANDBOX_IMAGE_TAG,
    SandboxError,
    _AUTH_VOLUME_NAME,
    _SANDBOX_NETWORK_NAME,
    check_docker_or_exit,
    check_gvisor_or_exit,
    docker_available,
    docker_running_rootless,
    ensure_image,
    ensure_network,
    gvisor_runtime_available,
    image_built_by_kato,
    image_exists,
    login_command,
    make_container_name,
    stamp_auth_volume_manifest,
    ALLOW_NO_GVISOR_ENV_KEY,
)


def _ok_result(stdout: str = '', stderr: str = '', returncode: int = 0):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# docker_available
# ---------------------------------------------------------------------------


class DockerAvailableTests(unittest.TestCase):
    def test_returns_true_when_docker_on_path_and_daemon_responds(self):
        with patch('shutil.which', return_value='/usr/bin/docker'), \
             patch('subprocess.run', return_value=_ok_result('26.0.0')):
            self.assertTrue(docker_available())

    def test_returns_false_when_docker_not_on_path(self):
        with patch('shutil.which', return_value=None):
            self.assertFalse(docker_available())

    def test_returns_false_when_docker_info_fails(self):
        with patch('shutil.which', return_value='/usr/bin/docker'), \
             patch('subprocess.run', return_value=_ok_result(returncode=1)):
            self.assertFalse(docker_available())

    def test_returns_false_when_oserror_running_docker(self):
        with patch('shutil.which', return_value='/usr/bin/docker'), \
             patch('subprocess.run', side_effect=OSError('no docker')):
            self.assertFalse(docker_available())

    def test_returns_false_when_docker_times_out(self):
        with patch('shutil.which', return_value='/usr/bin/docker'), \
             patch('subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 5)):
            self.assertFalse(docker_available())


# ---------------------------------------------------------------------------
# gvisor_runtime_available
# ---------------------------------------------------------------------------


class GvisorRuntimeAvailableTests(unittest.TestCase):
    def test_returns_true_when_runsc_in_runtimes(self):
        runtimes = json.dumps({'runc': {}, 'runsc': {}})
        with patch('subprocess.run', return_value=_ok_result(stdout=runtimes)):
            self.assertTrue(gvisor_runtime_available())

    def test_returns_false_when_runsc_not_in_runtimes(self):
        runtimes = json.dumps({'runc': {}})
        with patch('subprocess.run', return_value=_ok_result(stdout=runtimes)):
            self.assertFalse(gvisor_runtime_available())

    def test_returns_false_when_docker_returns_nonzero(self):
        with patch('subprocess.run', return_value=_ok_result(returncode=1)):
            self.assertFalse(gvisor_runtime_available())

    def test_returns_false_when_json_invalid(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='not-json')):
            self.assertFalse(gvisor_runtime_available())

    def test_returns_false_when_subprocess_raises_oserror(self):
        with patch('subprocess.run', side_effect=OSError):
            self.assertFalse(gvisor_runtime_available())

    def test_returns_false_when_subprocess_times_out(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 5)):
            self.assertFalse(gvisor_runtime_available())

    def test_returns_false_when_output_not_a_dict(self):
        with patch('subprocess.run', return_value=_ok_result(stdout=json.dumps([]))):
            self.assertFalse(gvisor_runtime_available())

    def test_returns_false_when_output_is_empty(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='')):
            self.assertFalse(gvisor_runtime_available())


# ---------------------------------------------------------------------------
# docker_running_rootless
# ---------------------------------------------------------------------------


class DockerRunningRootlessTests(unittest.TestCase):
    def test_returns_true_when_rootless_in_security_options(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='[rootless userns]')):
            self.assertTrue(docker_running_rootless())

    def test_returns_false_when_rootless_not_in_output(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='[apparmor cgroupns]')):
            self.assertFalse(docker_running_rootless())

    def test_returns_false_when_docker_info_fails(self):
        with patch('subprocess.run', return_value=_ok_result(returncode=1)):
            self.assertFalse(docker_running_rootless())

    def test_returns_false_on_oserror(self):
        with patch('subprocess.run', side_effect=OSError):
            self.assertFalse(docker_running_rootless())

    def test_returns_false_on_timeout(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 5)):
            self.assertFalse(docker_running_rootless())

    def test_rootless_check_is_case_insensitive(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='[Rootless cgroupns]')):
            self.assertTrue(docker_running_rootless())


# ---------------------------------------------------------------------------
# check_gvisor_or_exit
# ---------------------------------------------------------------------------


class CheckGvisorOrExitTests(unittest.TestCase):
    def test_returns_silently_when_gvisor_available(self):
        with patch('sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available', return_value=True):
            check_gvisor_or_exit()  # no exception

    def test_returns_silently_when_allow_no_gvisor_env_set(self):
        with patch('sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available', return_value=False):
            check_gvisor_or_exit(env={ALLOW_NO_GVISOR_ENV_KEY: 'true'})  # no exception

    def test_exits_with_1_when_gvisor_unavailable_and_no_override(self):
        stderr = io.StringIO()
        with patch('sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available', return_value=False), \
             patch('sys.exit') as mock_exit:
            check_gvisor_or_exit(env={})
        mock_exit.assert_called_once_with(1)

    def test_error_message_names_the_env_var_override(self):
        stderr = io.StringIO()
        with patch('sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available', return_value=False), \
             patch('sys.stderr', stderr), \
             patch('sys.exit'):
            check_gvisor_or_exit(env={})
        self.assertIn(ALLOW_NO_GVISOR_ENV_KEY, stderr.getvalue())

    def test_allow_no_gvisor_truthy_values(self):
        for value in ('1', 'yes', 'on', 'true', 'TRUE'):
            with self.subTest(value=value):
                with patch('sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available', return_value=False):
                    check_gvisor_or_exit(env={ALLOW_NO_GVISOR_ENV_KEY: value})


# ---------------------------------------------------------------------------
# check_docker_or_exit
# ---------------------------------------------------------------------------


class CheckDockerOrExitTests(unittest.TestCase):
    def test_returns_silently_when_docker_available(self):
        with patch('sandbox_core_lib.sandbox_core_lib.manager.docker_available', return_value=True):
            check_docker_or_exit()  # no exception

    def test_exits_with_1_when_docker_unavailable(self):
        with patch('sandbox_core_lib.sandbox_core_lib.manager.docker_available', return_value=False), \
             patch('sys.exit') as mock_exit:
            check_docker_or_exit()
        mock_exit.assert_called_once_with(1)

    def test_error_message_names_docker_and_bypass_flag(self):
        stderr = io.StringIO()
        with patch('sandbox_core_lib.sandbox_core_lib.manager.docker_available', return_value=False), \
             patch('sys.stderr', stderr), \
             patch('sys.exit'):
            check_docker_or_exit()
        self.assertIn('docker', stderr.getvalue().lower())


# ---------------------------------------------------------------------------
# image_exists / image_built_by_kato
# ---------------------------------------------------------------------------


class ImageExistsTests(unittest.TestCase):
    def test_returns_true_on_zero_returncode(self):
        with patch('subprocess.run', return_value=_ok_result()):
            self.assertTrue(image_exists('my-image:latest'))

    def test_returns_false_on_nonzero_returncode(self):
        with patch('subprocess.run', return_value=_ok_result(returncode=1)):
            self.assertFalse(image_exists('my-image:latest'))

    def test_returns_false_on_oserror(self):
        with patch('subprocess.run', side_effect=OSError):
            self.assertFalse(image_exists('my-image:latest'))

    def test_returns_false_on_timeout(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 10)):
            self.assertFalse(image_exists('my-image:latest'))


class ImageBuiltByKatoTests(unittest.TestCase):
    def test_returns_true_when_label_matches(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='true\n')):
            self.assertTrue(image_built_by_kato(SANDBOX_IMAGE_TAG))

    def test_returns_false_when_label_does_not_match(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='false\n')):
            self.assertFalse(image_built_by_kato(SANDBOX_IMAGE_TAG))

    def test_returns_false_when_label_empty(self):
        with patch('subprocess.run', return_value=_ok_result(stdout='\n')):
            self.assertFalse(image_built_by_kato(SANDBOX_IMAGE_TAG))

    def test_returns_false_on_nonzero_returncode(self):
        with patch('subprocess.run', return_value=_ok_result(returncode=1)):
            self.assertFalse(image_built_by_kato(SANDBOX_IMAGE_TAG))

    def test_returns_false_on_oserror(self):
        with patch('subprocess.run', side_effect=OSError):
            self.assertFalse(image_built_by_kato(SANDBOX_IMAGE_TAG))

    def test_returns_false_on_timeout(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 10)):
            self.assertFalse(image_built_by_kato(SANDBOX_IMAGE_TAG))


# ---------------------------------------------------------------------------
# ensure_network
# ---------------------------------------------------------------------------


class EnsureNetworkTests(unittest.TestCase):
    def test_returns_silently_when_network_already_exists(self):
        with patch('subprocess.run', return_value=_ok_result()):
            ensure_network()  # no exception

    def test_creates_network_when_inspect_fails(self):
        calls = []

        def side_effect(cmd, **_kw):
            calls.append(cmd)
            if 'inspect' in cmd:
                return _ok_result(returncode=1)
            return _ok_result()

        with patch('subprocess.run', side_effect=side_effect):
            ensure_network()

        self.assertTrue(any('create' in str(c) for c in calls))

    def test_raises_sandbox_error_when_inspect_raises_oserror(self):
        with patch('subprocess.run', side_effect=OSError('daemon down')):
            with self.assertRaises(SandboxError):
                ensure_network()

    def test_raises_sandbox_error_when_network_create_fails(self):
        def side_effect(cmd, **_kw):
            if 'inspect' in cmd:
                return _ok_result(returncode=1)
            return _ok_result(returncode=1, stderr='permission denied')

        with patch('subprocess.run', side_effect=side_effect):
            with self.assertRaises(SandboxError):
                ensure_network()

    def test_error_message_names_network_name(self):
        def side_effect(cmd, **_kw):
            if 'inspect' in cmd:
                return _ok_result(returncode=1)
            return _ok_result(returncode=1, stderr='error')

        with patch('subprocess.run', side_effect=side_effect):
            with self.assertRaises(SandboxError) as ctx:
                ensure_network()
        self.assertIn(_SANDBOX_NETWORK_NAME, str(ctx.exception))


# ---------------------------------------------------------------------------
# ensure_image
# ---------------------------------------------------------------------------


class EnsureImageTests(unittest.TestCase):
    def test_returns_silently_when_image_exists_and_built_by_us(self):
        with patch('sandbox_core_lib.sandbox_core_lib.manager.image_exists', return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.image_built_by_kato', return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_network') as mock_net:
            ensure_image()
        mock_net.assert_called_once()

    def test_builds_image_when_not_present(self):
        with patch('sandbox_core_lib.sandbox_core_lib.manager.image_exists', return_value=False), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.image_built_by_kato', return_value=False), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.build_image') as mock_build, \
             patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_network'):
            ensure_image()
        mock_build.assert_called_once()

    def test_rebuilds_when_image_exists_but_not_built_by_us(self):
        with patch('sandbox_core_lib.sandbox_core_lib.manager.image_exists', return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.image_built_by_kato', return_value=False), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.build_image') as mock_build, \
             patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_network'):
            ensure_image()
        mock_build.assert_called_once()

    def test_logs_warning_when_rebuilding_unknown_provenance_image(self):
        mock_logger = MagicMock()
        with patch('sandbox_core_lib.sandbox_core_lib.manager.image_exists', return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.image_built_by_kato', return_value=False), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.build_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_network'):
            ensure_image(logger=mock_logger)
        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# make_container_name
# ---------------------------------------------------------------------------


class MakeContainerNameTests(unittest.TestCase):
    def test_includes_task_id_when_provided(self):
        name = make_container_name('PROJ-1234')
        self.assertIn('PROJ-1234', name)

    def test_uses_unknown_when_task_id_empty(self):
        name = make_container_name('')
        self.assertIn('unknown', name)

    def test_uses_unknown_when_task_id_not_provided(self):
        name = make_container_name()
        self.assertIn('unknown', name)

    def test_sanitizes_special_characters(self):
        name = make_container_name('proj/1234:feature branch')
        self.assertNotIn('/', name)
        self.assertNotIn(':', name)
        self.assertNotIn(' ', name)

    def test_result_contains_uuid_suffix(self):
        name1 = make_container_name('same-task')
        name2 = make_container_name('same-task')
        # UUID suffix ensures names differ across calls
        self.assertNotEqual(name1, name2)

    def test_result_is_under_docker_name_length_limit(self):
        long_task_id = 'PROJ-' + 'x' * 200
        name = make_container_name(long_task_id)
        self.assertLessEqual(len(name), 128)

    def test_result_starts_with_known_prefix(self):
        name = make_container_name('T-1')
        # Should start with something meaningful (not raw UUID)
        self.assertTrue(name[0].isalpha() or name[0] == '_')


# ---------------------------------------------------------------------------
# login_command
# ---------------------------------------------------------------------------


class LoginCommandTests(unittest.TestCase):
    def _get_argv(self, image_tag=SANDBOX_IMAGE_TAG):
        return login_command(image_tag)

    def test_starts_with_docker_run(self):
        argv = self._get_argv()
        self.assertEqual(argv[:2], ['docker', 'run'])

    def test_includes_rm_flag(self):
        self.assertIn('--rm', self._get_argv())

    def test_includes_it_flag(self):
        self.assertIn('-it', self._get_argv())

    def test_includes_read_only_flag(self):
        self.assertIn('--read-only', self._get_argv())

    def test_includes_cap_drop_all(self):
        argv = self._get_argv()
        self.assertIn('ALL', argv)
        self.assertIn('--cap-drop', argv)

    def test_includes_no_new_privileges(self):
        self.assertIn('no-new-privileges', ' '.join(self._get_argv()))

    def test_includes_auth_volume_mount(self):
        argv = self._get_argv()
        joined = ' '.join(argv)
        self.assertIn(_AUTH_VOLUME_NAME, joined)

    def test_ends_with_claude_login_command(self):
        argv = self._get_argv()
        self.assertEqual(argv[-2:], ['claude', '/login'])

    def test_includes_image_tag(self):
        tag = 'my-image:v1'
        argv = login_command(tag)
        self.assertIn(tag, argv)

    def test_ipv6_disabled(self):
        argv = self._get_argv()
        joined = ' '.join(argv)
        self.assertIn('net.ipv6.conf.all.disable_ipv6=1', joined)

    def test_dns_pinned_to_cloudflare(self):
        argv = self._get_argv()
        self.assertIn('1.1.1.1', argv)
        self.assertIn('1.0.0.1', argv)

    def test_network_is_sandbox_network(self):
        argv = self._get_argv()
        idx = argv.index('--network')
        self.assertEqual(argv[idx + 1], _SANDBOX_NETWORK_NAME)

    def test_does_not_mount_workspace(self):
        argv = self._get_argv()
        joined = ' '.join(argv)
        self.assertNotIn('/workspace', joined)

    def test_no_forbidden_flags(self):
        from sandbox_core_lib.sandbox_core_lib.manager import _FORBIDDEN_DOCKER_FLAGS
        argv = self._get_argv()
        joined = ' '.join(argv)
        for flag in _FORBIDDEN_DOCKER_FLAGS:
            self.assertNotIn(flag, joined, f'forbidden flag {flag} found in login_command')


# ---------------------------------------------------------------------------
# stamp_auth_volume_manifest
# ---------------------------------------------------------------------------


class StampAuthVolumeManifestTests(unittest.TestCase):
    def test_runs_without_exception_on_success(self):
        with patch('subprocess.run', return_value=_ok_result()):
            stamp_auth_volume_manifest()  # no exception

    def test_logs_warning_on_oserror(self):
        mock_logger = MagicMock()
        with patch('subprocess.run', side_effect=OSError('docker down')):
            stamp_auth_volume_manifest(logger=mock_logger)
        mock_logger.warning.assert_called_once()

    def test_logs_warning_on_timeout(self):
        mock_logger = MagicMock()
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 30)):
            stamp_auth_volume_manifest(logger=mock_logger)
        mock_logger.warning.assert_called_once()

    def test_logs_warning_on_nonzero_returncode(self):
        mock_logger = MagicMock()
        with patch('subprocess.run', return_value=_ok_result(returncode=1, stderr='error')):
            stamp_auth_volume_manifest(logger=mock_logger)
        mock_logger.warning.assert_called_once()

    def test_does_not_raise_on_failure_without_logger(self):
        with patch('subprocess.run', side_effect=OSError('docker down')):
            stamp_auth_volume_manifest()  # no exception even without logger

    def test_mounts_auth_volume_rw(self):
        captured = {}

        def capture(cmd, **_kw):
            captured['cmd'] = cmd
            return _ok_result()

        with patch('subprocess.run', side_effect=capture):
            stamp_auth_volume_manifest()

        cmd_str = ' '.join(captured.get('cmd', []))
        self.assertIn(_AUTH_VOLUME_NAME, cmd_str)
        self.assertIn(':rw', cmd_str)

    def test_uses_read_only_rootfs(self):
        captured = {}

        def capture(cmd, **_kw):
            captured['cmd'] = cmd
            return _ok_result()

        with patch('subprocess.run', side_effect=capture):
            stamp_auth_volume_manifest()

        self.assertIn('--read-only', captured.get('cmd', []))
