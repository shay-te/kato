"""``kato up`` must not hand back a prompt while kato is still running.

Reported as "i can't ctrl+c to stop kato". It is not the terminal: the CLI
is a wrapper that runs the agent as a CHILD, and it used

    try: return subprocess.call(cmd)
    except KeyboardInterrupt: return 130

The terminal delivers SIGINT to the whole foreground process group, so the
child gets it too — but the wrapper returned IMMEDIATELY, so the shell prompt
came back while the child was still shutting down (or still running). It
looked stopped and was not.

The wrapper now waits for the child, escalating terminate → kill.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from tools.kato import kato as kato_cli


class WrapperOwnsItsChildTests(unittest.TestCase):
    def _child(self, wait_effects):
        child = MagicMock()
        child.wait.side_effect = wait_effects
        return child

    def _run(self, child):
        with patch.object(kato_cli.subprocess, 'Popen', return_value=child):
            return kato_cli._run_child(['python', '-m', 'x'], cwd='/repo')

    def test_a_clean_run_returns_the_child_exit_code(self) -> None:
        self.assertEqual(self._run(self._child([0])), 0)

    def test_ctrl_c_waits_for_the_child_before_returning(self) -> None:
        """The reported bug: the prompt came back, kato did not stop."""
        child = self._child([KeyboardInterrupt(), 0])
        self.assertEqual(self._run(child), 0)
        # The second wait is the BOUNDED one — it must actually be awaited,
        # not skipped.
        self.assertEqual(child.wait.call_count, 2)
        child.terminate.assert_not_called()

    def test_a_child_that_ignores_ctrl_c_is_terminated(self) -> None:
        child = self._child([
            KeyboardInterrupt(),
            subprocess.TimeoutExpired(cmd='x', timeout=10),
            0,
        ])
        self.assertEqual(self._run(child), 0)
        child.terminate.assert_called_once()
        child.kill.assert_not_called()

    def test_a_child_that_ignores_terminate_is_killed(self) -> None:
        child = self._child([
            KeyboardInterrupt(),
            subprocess.TimeoutExpired(cmd='x', timeout=10),
            subprocess.TimeoutExpired(cmd='x', timeout=5),
            0,
        ])
        self.assertEqual(self._run(child), 130)
        child.kill.assert_called_once()

    def test_a_second_ctrl_c_escalates_immediately(self) -> None:
        # The operator pressing Ctrl+C again means "stop waiting".
        child = self._child([KeyboardInterrupt(), KeyboardInterrupt(), 0])
        self.assertEqual(self._run(child), 0)
        child.terminate.assert_called_once()

    def test_the_wrapper_never_returns_with_the_child_alive(self) -> None:
        """The property that actually matters, however it gets there."""
        for effects in (
            [0],
            [KeyboardInterrupt(), 0],
            [KeyboardInterrupt(), subprocess.TimeoutExpired('x', 10), 0],
            [KeyboardInterrupt(), subprocess.TimeoutExpired('x', 10),
             subprocess.TimeoutExpired('x', 5), 0],
        ):
            child = self._child(list(effects))
            self._run(child)
            # Every path ends on a wait() that RETURNED — i.e. the child is
            # reaped, never abandoned.
            self.assertTrue(
                child.wait.called,
                f'wrapper returned without reaping the child: {effects}',
            )


class OneCliTests(unittest.TestCase):
    """``./kato`` runs ``kato_core_lib/cli.py`` — no list of its own to drift."""

    def test_every_command_goes_to_the_one_cli(self) -> None:
        from pathlib import Path
        repo = Path('/repo-without-a-venv')
        command = kato_cli.cli_command(['doctor', '--mode', 'all'], repo_root=repo)
        self.assertEqual(command[1:], ['-m', 'kato_core_lib.cli', 'doctor', '--mode', 'all'])

    def test_the_old_sandbox_names_still_work(self) -> None:
        from pathlib import Path
        command = kato_cli.cli_command(['sandbox-verify'], repo_root=Path('/r'))
        self.assertEqual(command[-2:], ['sandbox', 'verify'])

    def test_bootstrap_uses_the_system_python_and_the_rest_the_venv(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            venv_python = repo / kato_cli._VENV_PYTHON_REL
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text('')
            self.assertEqual(kato_cli.cli_command(['up'], repo_root=repo)[0], str(venv_python))
            self.assertNotEqual(kato_cli.cli_command(['bootstrap'], repo_root=repo)[0], str(venv_python))

    def test_a_frozen_binary_runs_a_real_python(self) -> None:
        from pathlib import Path
        with patch.object(kato_cli.sys, 'frozen', True, create=True):
            self.assertEqual(kato_cli.cli_command(['test'], repo_root=Path('/r'))[0], 'python')
            with patch.object(kato_cli.Path, 'cwd', return_value=Path('/checkout')):
                self.assertEqual(kato_cli._runtime_repo_root(), Path('/checkout'))

    def test_main_runs_it_from_the_repository(self) -> None:
        with patch.object(kato_cli, '_run_child', return_value=0) as run:
            self.assertEqual(kato_cli.main(['doctor']), 0)
        command, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(command[1:3], ['-m', 'kato_core_lib.cli'])
        self.assertEqual(kwargs['cwd'], str(kato_cli._FILE_REPO_ROOT))


if __name__ == '__main__':
    unittest.main()
