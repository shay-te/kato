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


if __name__ == '__main__':
    unittest.main()
