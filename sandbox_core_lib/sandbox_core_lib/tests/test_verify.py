"""Tests for sandbox_core_lib.verify.main().

The module is a smoke-test runner requiring Docker. These unit tests cover
every early-exit and error path in main() using only mocks — no Docker daemon
required.
"""
from __future__ import annotations

import subprocess
import unittest
from io import StringIO
from unittest.mock import MagicMock, call, patch

from sandbox_core_lib.sandbox_core_lib.verify import main


class VerifyMainDockerUnavailableTests(unittest.TestCase):
    """main() returns 1 immediately when Docker is not available."""

    def test_returns_1_when_docker_not_available(self):
        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=False):
            with patch('sys.stderr', new_callable=StringIO) as mock_err:
                result = main()
        self.assertEqual(result, 1)

    def test_prints_error_message_when_docker_not_available(self):
        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=False):
            with patch('sys.stderr', new_callable=StringIO) as mock_err:
                main()
                self.assertIn('docker', mock_err.getvalue().lower())


class VerifyMainImageBuildFailureTests(unittest.TestCase):
    """main() returns 1 when ensure_image raises SandboxError."""

    def test_returns_1_when_ensure_image_raises(self):
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_image',
                   side_effect=SandboxError('build failed')), \
             patch('sys.stderr', new_callable=StringIO):
            result = main()
        self.assertEqual(result, 1)

    def test_error_message_includes_exception(self):
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_image',
                   side_effect=SandboxError('image not found')), \
             patch('sys.stderr', new_callable=StringIO) as mock_err:
            main()
            self.assertIn('image not found', mock_err.getvalue())


class VerifyMainContainerRunTests(unittest.TestCase):
    """main() runs the container and returns its exit code."""

    def _run_main(self, returncode=0, timeout=None, oserror=None):
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError

        mock_run = MagicMock()
        if timeout:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=['docker', 'run'], timeout=120)
        elif oserror:
            mock_run.side_effect = OSError('no such file: docker')
        else:
            mock_run.return_value = MagicMock(returncode=returncode)

        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_network'), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.wrap_command',
                   return_value=['docker', 'run', '--rm', 'claude', 'bash', '-c', 'x']), \
             patch('sandbox_core_lib.sandbox_core_lib.manager._validate_workspace_path',
                   side_effect=lambda p: p), \
             patch('subprocess.run', mock_run), \
             patch('sys.stderr', new_callable=StringIO) as mock_err, \
             patch('builtins.print'):
            result = main()
        return result, mock_err.getvalue()

    def test_returns_0_on_success(self):
        result, _ = self._run_main(returncode=0)
        self.assertEqual(result, 0)

    def test_returns_nonzero_on_container_failure(self):
        result, _ = self._run_main(returncode=1)
        self.assertEqual(result, 1)

    def test_returns_1_on_timeout(self):
        result, stderr = self._run_main(timeout=True)
        self.assertEqual(result, 1)
        self.assertIn('120s', stderr)

    def test_returns_1_on_oserror(self):
        result, stderr = self._run_main(oserror=True)
        self.assertEqual(result, 1)
        self.assertIn('docker run failed', stderr)

    def test_calls_ensure_network(self):
        mock_network = MagicMock()
        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_network', mock_network), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.wrap_command',
                   return_value=['docker', 'run']), \
             patch('subprocess.run', return_value=MagicMock(returncode=0)), \
             patch('builtins.print'):
            main()
        mock_network.assert_called_once()

    def test_calls_wrap_command_with_bash_script(self):
        mock_wrap = MagicMock(return_value=['docker', 'run'])
        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=True), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.ensure_network'), \
             patch('sandbox_core_lib.sandbox_core_lib.verify.wrap_command', mock_wrap), \
             patch('subprocess.run', return_value=MagicMock(returncode=0)), \
             patch('builtins.print'):
            main()
        mock_wrap.assert_called_once()
        inner = mock_wrap.call_args[1].get('inner_command') or mock_wrap.call_args[0][0]
        self.assertIn('bash', inner)

    def test_returns_2_on_container_exit_2(self):
        result, _ = self._run_main(returncode=2)
        self.assertEqual(result, 2)


class VerifyModuleEntryPointTests(unittest.TestCase):
    """Line 215: ``if __name__ == '__main__': sys.exit(main())`` —
    the module-as-script entry point.

    Security-relevant: the entry point is what operators run from the
    shell. We use ``runpy`` to execute the module exactly as Python
    would when invoked as ``python -m sandbox_core_lib.sandbox_core_lib.verify``,
    so the entry point is exercised end-to-end (not just simulated).
    All Docker calls are still mocked — no real container spawns.
    """

    def test_running_as_script_calls_main_via_sys_exit(self):
        import runpy
        with patch('sandbox_core_lib.sandbox_core_lib.verify.docker_available',
                   return_value=False), \
             patch('sys.stderr', new_callable=StringIO):
            # Running as ``__main__`` triggers ``sys.exit(main())`` which
            # raises SystemExit. Catch it and verify the exit code.
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_module(
                    'sandbox_core_lib.sandbox_core_lib.verify',
                    run_name='__main__',
                )
            # ``docker_available=False`` → main() returns 1 → sys.exit(1).
            self.assertEqual(ctx.exception.code, 1)
