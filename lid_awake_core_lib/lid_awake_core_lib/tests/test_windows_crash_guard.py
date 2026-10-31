from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import windows_crash_guard
from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import STALE_AFTER_MINUTES
from lid_awake_core_lib.lid_awake_core_lib.windows_crash_guard import (
    RUN_KEY,
    WindowsGuardIdentity,
    guard_is_ready,
    install_guard,
    remove_guard,
    render_watchdog,
    start_watchdog,
    watchdog_command,
)

POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.identity = WindowsGuardIdentity(
            run_value='ExampleLidAwake',
            script=self.root / 'state' / 'watchdog.ps1',
            saved=self.root / 'state' / 'lid.json',
        )


class RenderTests(unittest.TestCase):
    def test_quotes_a_path_with_an_apostrophe(self):
        script = render_watchdog(r"C:\Users\O'Brien\beat", r'C:\s')
        self.assertIn(r"$heartbeat = 'C:\Users\O''Brien\beat'", script)

    def test_restores_both_values_and_activates_the_plan(self):
        script = render_watchdog('h', 's')
        self.assertIn('/setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION $lid.ac', script)
        self.assertIn('/setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION $lid.dc', script)
        self.assertIn('/setactive SCHEME_CURRENT', script)

    def test_ends_by_itself_when_the_heartbeat_is_gone(self):
        self.assertIn('while (Test-Path -LiteralPath $heartbeat)', render_watchdog('h', 's'))

    def test_waits_the_same_stale_limit_as_every_other_platform(self):
        self.assertIn(
            f'$age.TotalMinutes -ge {STALE_AFTER_MINUTES})', render_watchdog('h', 's'),
        )

    def test_never_sets_do_nothing(self):
        self.assertNotIn('LIDACTION 0', render_watchdog('h', 's'))

    def test_runs_hidden_without_a_profile(self):
        command = watchdog_command('w.ps1')
        self.assertEqual(command[0], 'powershell.exe')
        self.assertIn('-NoProfile', command)
        self.assertEqual(command[command.index('-WindowStyle') + 1], 'Hidden')
        self.assertEqual(command[-2:], ['-File', 'w.ps1'])


class StartWatchdogTests(unittest.TestCase):
    def test_starts_with_no_terminal(self):
        spawn = mock.Mock()
        start_watchdog('w.ps1', spawn=spawn)
        self.assertEqual(spawn.call_args.args[0], watchdog_command('w.ps1'))
        self.assertIs(spawn.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_retries_when_a_job_refuses_the_breakaway(self):
        spawn = mock.Mock(side_effect=[OSError('denied'), 'process'])
        self.assertEqual(start_watchdog('w.ps1', spawn=spawn), 'process')
        self.assertEqual(spawn.call_count, 2)

    def test_no_powershell_is_not_a_crash(self):
        self.assertIsNone(start_watchdog('w.ps1', spawn=mock.Mock(side_effect=OSError())))


class InstallTests(GuardTestCase):
    def _reg(self, code=0):
        return mock.patch.object(
            windows_crash_guard, 'run_command',
            return_value=subprocess.CompletedProcess([], code),
        )

    def test_writes_the_script_and_runs_it_at_every_logon(self):
        with self._reg() as reg:
            self.assertEqual(install_guard(self.identity, 'beat'), 0)
        self.assertIn("$heartbeat = 'beat'", self.identity.script.read_text())
        argv = reg.call_args.args[0]
        self.assertEqual(argv[:5], ['reg', 'add', RUN_KEY, '/v', 'ExampleLidAwake'])
        self.assertIn('powershell.exe', argv[argv.index('/d') + 1])

    def test_passes_on_a_failure(self):
        with self._reg(code=1):
            self.assertEqual(install_guard(self.identity, 'beat'), 1)

    def test_reg_not_answering_is_a_failure(self):
        with mock.patch.object(windows_crash_guard, 'run_command', return_value=None):
            self.assertEqual(install_guard(self.identity, 'beat'), 1)

    def test_ready_needs_the_script_and_the_run_value(self):
        with self._reg():
            self.assertFalse(guard_is_ready(self.identity))
            install_guard(self.identity, 'beat')
            self.assertTrue(guard_is_ready(self.identity))
        with self._reg(code=1):
            self.assertFalse(guard_is_ready(self.identity))
        with mock.patch.object(windows_crash_guard, 'run_command', return_value=None):
            self.assertFalse(guard_is_ready(self.identity))

    def test_remove_puts_the_lid_action_back_first(self):
        switch = mock.Mock()
        switch.set_held.return_value = ''
        with self._reg() as reg:
            install_guard(self.identity, 'beat')
            self.assertEqual(remove_guard(self.identity, switch), 0)
        switch.set_held.assert_called_once_with(False)
        self.assertFalse(self.identity.script.exists())
        self.assertEqual(reg.call_args.args[0][:3], ['reg', 'delete', RUN_KEY])

    def test_a_failed_restore_keeps_the_guard(self):
        switch = mock.Mock()
        switch.set_held.return_value = 'powercfg failed'
        with self._reg():
            install_guard(self.identity, 'beat')
            self.assertEqual(remove_guard(self.identity, switch), 1)
        self.assertTrue(self.identity.script.exists())


@unittest.skipUnless(POWERSHELL, 'needs a real PowerShell')
class RealWatchdogTests(GuardTestCase):
    """The real script in a real PowerShell, against a stand-in ``powercfg``."""

    def setUp(self):
        super().setUp()
        self.calls = self.root / 'calls.txt'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        if sys.platform == 'win32':
            (self.bin / 'powercfg.cmd').write_text(f'@echo %* >> "{self.calls}"\n')
        else:
            fake = self.bin / 'powercfg'
            fake.write_text(f'#!/bin/sh\necho "$*" >> "{self.calls}"\n')
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.heartbeat = self.root / 'state' / 'heartbeat'
        self.heartbeat.parent.mkdir()
        self.identity.saved.write_text(json.dumps({'ac': 1, 'dc': 2}))
        self.identity.script.write_text(
            render_watchdog(self.heartbeat, self.identity.saved), encoding='utf-8',
        )

    def _run(self, timeout=20):
        return subprocess.run(
            [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(self.identity.script)],
            env={**os.environ, 'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}'},
            capture_output=True, text=True, timeout=timeout, check=False,
        )

    def _stale(self):
        self.heartbeat.touch()
        when = time.time() - (STALE_AFTER_MINUTES + 2) * 60
        os.utime(self.heartbeat, (when, when))

    def test_a_stale_heartbeat_restores_the_operators_values(self):
        self._stale()
        finished = self._run()
        self.assertEqual(finished.returncode, 0, finished.stderr)
        calls = self.calls.read_text().split('\n')
        self.assertIn('/setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1', calls[0])
        self.assertIn('/setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 2', calls[1])
        self.assertIn('/setactive SCHEME_CURRENT', calls[2])

    def test_it_cleans_up_after_itself(self):
        self._stale()
        self._run()
        self.assertFalse(self.heartbeat.exists())
        self.assertFalse(self.identity.saved.exists())

    def test_a_fresh_heartbeat_keeps_watching_and_changes_nothing(self):
        self.heartbeat.touch()
        with self.assertRaises(subprocess.TimeoutExpired):
            self._run(timeout=4)
        self.assertFalse(self.calls.exists())
        self.assertTrue(self.identity.saved.exists())

    def test_no_heartbeat_changes_nothing(self):
        finished = self._run()
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertFalse(self.calls.exists())
        self.assertTrue(self.identity.saved.exists())

    def test_a_stale_heartbeat_with_nothing_saved_still_ends(self):
        self.identity.saved.unlink()
        self._stale()
        finished = self._run()
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.heartbeat.exists())


if __name__ == '__main__':
    unittest.main()
