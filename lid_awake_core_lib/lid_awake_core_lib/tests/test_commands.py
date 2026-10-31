from __future__ import annotations

import sys
import unittest
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import commands
from lid_awake_core_lib.lid_awake_core_lib.commands import run_command


class RunCommandTests(unittest.TestCase):
    def test_returns_the_finished_command(self):
        finished = run_command([sys.executable, '-c', 'print("hello")'])
        self.assertEqual(finished.returncode, 0)
        self.assertEqual(finished.stdout.strip(), 'hello')

    def test_a_missing_binary_is_none_not_a_crash(self):
        self.assertIsNone(run_command(['/nonexistent/binary']))

    def test_a_stuck_command_is_cut_off(self):
        with mock.patch.object(commands, 'COMMAND_TIMEOUT_SECONDS', 0.05):
            self.assertIsNone(
                run_command([sys.executable, '-c', 'import time; time.sleep(30)']),
            )

    def test_never_flashes_a_console_window_on_windows(self):
        with mock.patch.object(commands.subprocess, 'run') as run, \
                mock.patch.object(commands, 'NO_WINDOW', 0x08000000):
            run_command(['powercfg'])
        self.assertEqual(run.call_args.kwargs['creationflags'], 0x08000000)

    def test_never_reads_the_terminal(self):
        finished = run_command([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'])
        self.assertEqual(finished.stdout.strip(), "''")


if __name__ == '__main__':
    unittest.main()
