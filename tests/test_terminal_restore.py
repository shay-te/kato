"""kato hands the terminal back on every exit.

An agent CLI puts the terminal into raw / no-echo mode for its own UI. On a
clean exit it restores it; killed abruptly it cannot, and the operator's shell
is left with no echo until they run ``reset`` ("the terminal is broken — I
don't see what I type"). kato owns the terminal for the whole run, so it
snapshots the pristine mode at boot and restores it from every exit path.

Driven against a REAL pty, so the restore is exercised the way it runs in
production, not against a mock of termios.
"""
from __future__ import annotations

import os
import unittest

from kato_core_lib import main as kato_main


@unittest.skipIf(os.name == 'nt', 'POSIX termios only')
class TerminalRestoreTests(unittest.TestCase):

    def setUp(self) -> None:
        import pty
        import termios
        self.termios = termios
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        # Each test starts from a clean snapshot slot.
        kato_main._ORIGINAL_TERMINAL = None
        self.addCleanup(setattr, kato_main, '_ORIGINAL_TERMINAL', None)

    def _echo_on(self, fd: int) -> bool:
        return bool(self.termios.tcgetattr(fd)[3] & self.termios.ECHO)

    def _set_echo(self, fd: int, on: bool) -> None:
        attrs = self.termios.tcgetattr(fd)
        if on:
            attrs[3] |= self.termios.ECHO
        else:
            attrs[3] &= ~self.termios.ECHO
        self.termios.tcsetattr(fd, self.termios.TCSANOW, attrs)

    def test_restore_brings_echo_back_after_a_child_turned_it_off(self) -> None:
        self.assertTrue(self._echo_on(self.slave), 'a fresh pty echoes')
        kato_main._snapshot_terminal(self.slave)

        # The agent CLI's raw mode: echo off.
        self._set_echo(self.slave, False)
        self.assertFalse(self._echo_on(self.slave))

        kato_main._restore_terminal()

        self.assertTrue(self._echo_on(self.slave), 'kato restored cooked/echo mode')
        # And it re-shows the cursor + resets colours on the terminal.
        written = os.read(self.master, 64)
        self.assertIn(b'\x1b[?25h', written)

    def test_the_exit_path_restores_the_terminal(self) -> None:
        # Not just the helper in isolation — the real exit seam must call it,
        # so an abrupt shutdown can never leave the shell without echo.
        from unittest import mock
        kato_main._snapshot_terminal(self.slave)
        self._set_echo(self.slave, False)
        with mock.patch.object(kato_main.os, '_exit') as fake_exit:
            kato_main._exit_now(0)
        fake_exit.assert_called_once_with(0)
        self.assertTrue(self._echo_on(self.slave), 'exit put the terminal back')

    def test_restore_is_a_safe_no_op_without_a_snapshot(self) -> None:
        kato_main._ORIGINAL_TERMINAL = None
        kato_main._restore_terminal()  # must not raise
        # A dead fd must not raise either.
        kato_main._ORIGINAL_TERMINAL = (9999, [0, 0, 0, 0, 0, 0, []])
        kato_main._restore_terminal()

    def test_snapshot_ignores_a_non_tty(self) -> None:
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        kato_main._ORIGINAL_TERMINAL = None
        kato_main._snapshot_terminal(r)  # a pipe is not a tty
        self.assertIsNone(kato_main._ORIGINAL_TERMINAL)


if __name__ == '__main__':
    unittest.main()
