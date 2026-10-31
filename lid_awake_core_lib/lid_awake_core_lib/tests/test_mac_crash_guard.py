from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import mac_crash_guard
from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import (
    CHECK_EVERY_SECONDS,
    STALE_AFTER_MINUTES,
    GuardIdentity,
    guard_is_ready,
    install_guard,
    remove_guard,
    render_install_script,
    render_plist,
    render_remove_script,
    render_sudoers,
    render_watchdog,
)
from lid_awake_core_lib.lid_awake_core_lib.mac_sleep_switch import disable_sleep_command

IDENTITY = GuardIdentity(
    label='com.example.lid-awake-watchdog',
    directory='/Library/Application Support/Example',
    sudoers_name='example-lid-awake',
)
HAS_WATCHDOG_TOOLS = os.path.exists('/usr/bin/grep') and os.path.exists('/usr/bin/find')


class IdentityTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(
            IDENTITY.script_path,
            '/Library/Application Support/Example/lid-awake-watchdog.sh',
        )
        self.assertEqual(
            IDENTITY.plist_path,
            '/Library/LaunchDaemons/com.example.lid-awake-watchdog.plist',
        )
        self.assertEqual(IDENTITY.sudoers_path, '/etc/sudoers.d/example-lid-awake')


class SudoersTests(unittest.TestCase):
    def test_grants_exactly_the_two_commands_and_nothing_else(self):
        self.assertEqual(
            render_sudoers('ada'),
            'ada ALL=(root) NOPASSWD: /usr/bin/pmset -a disablesleep 1, '
            '/usr/bin/pmset -a disablesleep 0\n',
        )

    def test_matches_what_the_switch_really_runs(self):
        rule = render_sudoers('ada')
        for disabled in (True, False):
            self.assertIn(' '.join(disable_sleep_command(disabled)), rule)

    def test_refuses_a_user_that_could_inject_a_rule(self):
        for user in ('ada ALL=(ALL) ALL\nx', 'a b', '', 'ada,bob', 'a#b'):
            with self.assertRaises(ValueError):
                render_sudoers(user)

    @unittest.skipUnless(os.path.exists('/usr/sbin/visudo'), 'needs the real visudo')
    def test_the_real_visudo_accepts_it(self):
        with tempfile.NamedTemporaryFile('w', suffix='.sudoers') as rule:
            rule.write(render_sudoers('ada'))
            rule.flush()
            checked = subprocess.run(
                ['/usr/sbin/visudo', '-cf', rule.name],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


class PlistTests(unittest.TestCase):
    def test_runs_the_script_at_boot_and_on_a_timer(self):
        plist = plistlib.loads(render_plist(IDENTITY))
        self.assertEqual(plist['Label'], IDENTITY.label)
        self.assertEqual(plist['ProgramArguments'], ['/bin/sh', IDENTITY.script_path])
        self.assertIs(plist['RunAtLoad'], True)
        self.assertEqual(plist['StartInterval'], CHECK_EVERY_SECONDS)

    @unittest.skipUnless(os.path.exists('/usr/bin/plutil'), 'needs the real plutil')
    def test_the_real_plutil_accepts_it(self):
        with tempfile.NamedTemporaryFile(suffix='.plist') as plist:
            plist.write(render_plist(IDENTITY))
            plist.flush()
            checked = subprocess.run(
                ['/usr/bin/plutil', '-lint', plist.name],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(checked.returncode, 0, checked.stdout)


@unittest.skipUnless(HAS_WATCHDOG_TOOLS, 'needs /usr/bin/grep and /usr/bin/find')
class WatchdogScriptTests(unittest.TestCase):
    """The real script, in a real shell, against a fake ``pmset`` that records."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.heartbeat = self.root / "kept by the host's run" / 'heartbeat'
        self.heartbeat.parent.mkdir()
        self.calls = self.root / 'calls'
        self.flag = self.root / 'flag'
        self.pmset = self.root / 'fake pmset'
        self.pmset.write_text(
            '#!/bin/sh\n'
            f'if [ "$1" = "-g" ]; then cat "{self.flag}"; exit 0; fi\n'
            f'echo "$*" >> "{self.calls}"\n'
        )
        self.pmset.chmod(self.pmset.stat().st_mode | stat.S_IEXEC)
        self.script = self.root / 'watchdog.sh'
        self.script.write_text(render_watchdog(self.heartbeat, pmset=str(self.pmset)))

    def _run(self, *, sleep_disabled: bool) -> str:
        self.flag.write_text(' SleepDisabled\t\t1\n' if sleep_disabled else ' sleep 1\n')
        subprocess.run(['/bin/sh', str(self.script)], check=True, capture_output=True)
        return self.calls.read_text() if self.calls.exists() else ''

    def _age_heartbeat(self, minutes: float) -> None:
        when = time.time() - minutes * 60
        os.utime(self.heartbeat, (when, when))

    def test_a_stale_heartbeat_restores_sleep(self):
        self.heartbeat.touch()
        self._age_heartbeat(STALE_AFTER_MINUTES + 2)
        self.assertEqual(self._run(sleep_disabled=True), '-a disablesleep 0\n')

    def test_a_fresh_heartbeat_is_left_alone(self):
        self.heartbeat.touch()
        self.assertEqual(self._run(sleep_disabled=True), '')

    def test_no_heartbeat_means_the_operators_own_pmset_is_left_alone(self):
        self.assertEqual(self._run(sleep_disabled=True), '')

    def test_nothing_to_do_when_sleep_is_already_on(self):
        self.heartbeat.touch()
        self._age_heartbeat(STALE_AFTER_MINUTES + 2)
        self.assertEqual(self._run(sleep_disabled=False), '')

    def test_it_can_only_ever_turn_sleep_back_on(self):
        self.assertNotIn('disablesleep 1', self.script.read_text())


class InstallScriptTests(unittest.TestCase):
    SOURCE = Path('/tmp/some folder')

    @unittest.skipIf(sys.platform == 'win32', 'needs a POSIX shell')
    def test_is_valid_shell(self):
        for script in (render_install_script(IDENTITY, self.SOURCE), render_remove_script(IDENTITY)):
            subprocess.run(['/bin/sh', '-n'], input=script, text=True, check=True)

    def test_stops_at_the_first_failure(self):
        self.assertIn('set -eu', render_install_script(IDENTITY, self.SOURCE))

    def test_the_rule_goes_in_only_after_the_watchdog_is_running(self):
        script = render_install_script(IDENTITY, self.SOURCE)
        self.assertLess(script.index('launchctl bootstrap'), script.index('0440'))

    def test_checks_the_rule_before_installing_it(self):
        script = render_install_script(IDENTITY, self.SOURCE)
        self.assertLess(script.index('visudo -cf'), script.index('0440'))

    def test_takes_the_folder_from_whoever_owned_it(self):
        self.assertIn('chown root:wheel', render_install_script(IDENTITY, self.SOURCE))

    def test_remove_restores_sleep_before_taking_away_the_way_to_do_so(self):
        script = render_remove_script(IDENTITY)
        self.assertLess(script.index('disablesleep 0'), script.index('rm -f'))

    def test_paths_with_spaces_are_quoted(self):
        script = render_install_script(IDENTITY, self.SOURCE)
        self.assertIn("'/Library/Application Support/Example'", script)
        self.assertIn("'/tmp/some folder/sudoers'", script)


class GuardIsReadyTests(unittest.TestCase):
    def _ready(self, *, loaded=True, on=True, off=True):
        def run(argv):
            if argv[0] == mac_crash_guard.LAUNCHCTL:
                code = 0 if loaded else 113
            else:
                code = 0 if (on if argv[-1] == '1' else off) else 1
            return subprocess.CompletedProcess(argv, code)

        with mock.patch.object(mac_crash_guard, 'run_command', side_effect=run):
            return guard_is_ready(IDENTITY)

    def test_needs_all_three(self):
        self.assertTrue(self._ready())

    def test_no_watchdog(self):
        self.assertFalse(self._ready(loaded=False))

    def test_a_rule_that_could_not_turn_sleep_back_on_is_not_ready(self):
        self.assertFalse(self._ready(off=False))

    def test_a_rule_that_could_not_hold_is_not_ready(self):
        self.assertFalse(self._ready(on=False))

    def test_commands_that_could_not_run_are_not_ready(self):
        with mock.patch.object(mac_crash_guard, 'run_command', return_value=None):
            self.assertFalse(guard_is_ready(IDENTITY))

    def test_asks_sudo_without_running_the_command(self):
        with mock.patch.object(
            mac_crash_guard, 'run_command',
            return_value=subprocess.CompletedProcess([], 0),
        ) as run:
            guard_is_ready(IDENTITY)
        self.assertIn(
            mock.call(['/usr/bin/sudo', '-n', '-l', '/usr/bin/pmset', '-a', 'disablesleep', '0']),
            run.call_args_list,
        )

    @unittest.skipUnless(sys.platform == 'darwin', 'asks the real launchd')
    def test_the_real_launchd_says_a_made_up_guard_is_not_ready(self):
        self.assertFalse(guard_is_ready(GuardIdentity(
            label='com.example.no-such-watchdog', directory='/nowhere', sudoers_name='none',
        )))


class InstallTests(unittest.TestCase):
    def _capture(self, seen, code=0):
        def call(argv):
            folder = Path(argv[-1]).parent
            seen['argv'] = argv
            seen['script'] = Path(argv[-1]).read_text()
            for name in ('sudoers', 'watchdog.sh', 'watchdog.plist'):
                if (folder / name).exists():
                    seen[name] = (folder / name).read_bytes()
            return code
        return call

    def test_hands_sudo_a_script_and_every_file_it_installs(self):
        seen = {}
        with mock.patch.object(subprocess, 'call', side_effect=self._capture(seen)):
            self.assertEqual(install_guard(IDENTITY, '/home/ada/heartbeat', user='ada'), 0)
        self.assertEqual(seen['argv'][:2], ['/usr/bin/sudo', '/bin/sh'])
        self.assertIn(b'ada ALL=(root)', seen['sudoers'])
        self.assertIn(b'/home/ada/heartbeat', seen['watchdog.sh'])
        self.assertEqual(plistlib.loads(seen['watchdog.plist'])['Label'], IDENTITY.label)
        self.assertIn('launchctl bootstrap system', seen['script'])

    def test_defaults_to_the_current_user(self):
        seen = {}
        with mock.patch.object(subprocess, 'call', side_effect=self._capture(seen)), \
                mock.patch('getpass.getuser', return_value='grace'):
            install_guard(IDENTITY, '/h')
        self.assertIn(b'grace ALL=(root)', seen['sudoers'])

    def test_passes_on_sudos_failure(self):
        with mock.patch.object(subprocess, 'call', side_effect=self._capture({}, code=1)):
            self.assertEqual(install_guard(IDENTITY, '/h', user='ada'), 1)

    def test_leaves_no_files_behind(self):
        seen = {}
        with mock.patch.object(subprocess, 'call', side_effect=self._capture(seen)):
            install_guard(IDENTITY, '/h', user='ada')
        self.assertFalse(Path(seen['argv'][-1]).exists())

    def test_remove(self):
        seen = {}
        with mock.patch.object(subprocess, 'call', side_effect=self._capture(seen)):
            self.assertEqual(remove_guard(IDENTITY), 0)
        self.assertIn('launchctl bootout', seen['script'])
        self.assertIn('disablesleep 0', seen['script'])


if __name__ == '__main__':
    unittest.main()
