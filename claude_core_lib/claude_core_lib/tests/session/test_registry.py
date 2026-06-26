"""Tests for the live-process session registry reader (``session/registry.py``).

The registry guard exists because resuming a session that a live CLI
process still holds makes ``claude --resume`` silently start a blank
session — the "Claude forgot everything after stop/restart" bug. These
tests pin the three behaviours the guard is made of: finding live
holders, waiting/killing them, and never killing a recycled pid.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_core_lib.claude_core_lib.session import registry


def _write_entry(root: Path, pid: int, session_id: str, **extra) -> Path:
    payload = {'pid': pid, 'sessionId': session_id, 'cwd': 'C:\\w', **extra}
    path = root / f'{pid}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


class LiveSessionHoldersTests(unittest.TestCase):
    def test_missing_registry_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / 'does-not-exist'
            self.assertEqual(
                registry.live_session_holders('sid-1', registry_dir=missing),
                [],
            )

    def test_blank_session_id_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_entry(Path(td), 101, 'sid-1')
            self.assertEqual(
                registry.live_session_holders('   ', registry_dir=td),
                [],
            )

    def test_matches_only_requested_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')
            _write_entry(root, 102, 'sid-2')
            holders = registry.live_session_holders(
                'sid-1', registry_dir=root, pid_alive=lambda pid: True,
            )
            self.assertEqual([h['pid'] for h in holders], [101])

    def test_session_id_comparison_is_whitespace_tolerant(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, '  sid-1  ')
            holders = registry.live_session_holders(
                'sid-1', registry_dir=root, pid_alive=lambda pid: True,
            )
            self.assertEqual(len(holders), 1)

    def test_dead_pids_are_filtered_out(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')
            _write_entry(root, 102, 'sid-1')
            holders = registry.live_session_holders(
                'sid-1', registry_dir=root, pid_alive=lambda pid: pid == 102,
            )
            self.assertEqual([h['pid'] for h in holders], [102])

    def test_pid_alive_exception_treated_as_dead(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')

            def boom(pid):
                raise OSError('probe failed')

            self.assertEqual(
                registry.live_session_holders(
                    'sid-1', registry_dir=root, pid_alive=boom,
                ),
                [],
            )

    def test_malformed_entries_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'junk.json').write_text('not json', encoding='utf-8')
            (root / 'list.json').write_text('[1, 2]', encoding='utf-8')
            (root / 'nopid.json').write_text(
                json.dumps({'sessionId': 'sid-1'}), encoding='utf-8',
            )
            (root / 'badpid.json').write_text(
                json.dumps({'pid': 'NaN', 'sessionId': 'sid-1'}),
                encoding='utf-8',
            )
            (root / 'zeropid.json').write_text(
                json.dumps({'pid': 0, 'sessionId': 'sid-1'}),
                encoding='utf-8',
            )
            self.assertEqual(
                registry.live_session_holders(
                    'sid-1', registry_dir=root, pid_alive=lambda pid: True,
                ),
                [],
            )

    def test_default_registry_dir_is_under_home(self) -> None:
        expected = Path.home() / '.claude' / 'sessions'
        self.assertEqual(registry.default_registry_dir(), expected)


class ReleaseSessionHoldersTests(unittest.TestCase):
    def test_blank_session_id_is_immediately_free(self) -> None:
        self.assertTrue(registry.release_session_holders(''))

    def test_no_holders_is_the_fast_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            kill = mock.Mock()
            self.assertTrue(
                registry.release_session_holders(
                    'sid-1',
                    registry_dir=td,
                    kill_tree=kill,
                    sleep=lambda s: None,
                )
            )
            kill.assert_not_called()

    def test_holder_that_exits_during_wait_is_not_killed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')
            alive_polls = {'count': 0}

            def pid_alive(pid):
                # Alive on the initial scan, gone on the first re-scan —
                # the CLI finished its in-flight turn and exited.
                alive_polls['count'] += 1
                return alive_polls['count'] <= 1

            kill = mock.Mock()
            released = registry.release_session_holders(
                'sid-1',
                registry_dir=root,
                pid_alive=pid_alive,
                kill_tree=kill,
                sleep=lambda s: None,
                logger=mock.Mock(),
            )
            self.assertTrue(released)
            kill.assert_not_called()

    def test_persistent_holder_is_killed_when_image_is_cli(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')
            killed = []

            def kill(pid, logger=None):
                killed.append(pid)
                root.joinpath('101.json').unlink()
                return True

            clock_values = iter([0.0, 100.0, 100.0, 100.0])
            released = registry.release_session_holders(
                'sid-1',
                registry_dir=root,
                pid_alive=lambda pid: True,
                kill_tree=kill,
                image_name=lambda pid: 'node.exe',
                clock=lambda: next(clock_values, 100.0),
                sleep=lambda s: None,
                logger=mock.Mock(),
            )
            self.assertTrue(released)
            self.assertEqual(killed, [101])

    def test_recycled_pid_is_never_killed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')
            kill = mock.Mock()
            clock_values = iter([0.0, 100.0, 100.0, 100.0])
            released = registry.release_session_holders(
                'sid-1',
                registry_dir=root,
                pid_alive=lambda pid: True,
                kill_tree=kill,
                image_name=lambda pid: 'chrome.exe',
                clock=lambda: next(clock_values, 100.0),
                sleep=lambda s: None,
                logger=mock.Mock(),
            )
            # The holder entry survives (we refused to kill an unrelated
            # program), so the session reads as still-held.
            self.assertFalse(released)
            kill.assert_not_called()

    def test_unknown_image_is_killed(self) -> None:
        # '' from the image probe means "could not determine" — on
        # POSIX (no probe) and for a vanished tasklist row. The pid came
        # from the CLI's own registry, so unknown defaults to killable.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')
            killed = []

            def kill(pid, logger=None):
                killed.append(pid)
                root.joinpath('101.json').unlink()
                return True

            clock_values = iter([0.0, 100.0, 100.0, 100.0])
            released = registry.release_session_holders(
                'sid-1',
                registry_dir=root,
                pid_alive=lambda pid: True,
                kill_tree=kill,
                image_name=lambda pid: '',
                clock=lambda: next(clock_values, 100.0),
                sleep=lambda s: None,
                logger=mock.Mock(),
            )
            self.assertTrue(released)
            self.assertEqual(killed, [101])

    def test_image_probe_failure_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')

            def bad_probe(pid):
                raise RuntimeError('probe failed')

            def kill(pid, logger=None):
                root.joinpath('101.json').unlink()
                return True

            clock_values = iter([0.0, 100.0, 100.0, 100.0])
            released = registry.release_session_holders(
                'sid-1',
                registry_dir=root,
                pid_alive=lambda pid: True,
                kill_tree=kill,
                image_name=bad_probe,
                clock=lambda: next(clock_values, 100.0),
                sleep=lambda s: None,
                logger=mock.Mock(),
            )
            self.assertTrue(released)

    def test_kill_failure_reports_still_held(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')

            def kill(pid, logger=None):
                raise OSError('kill failed')

            clock_values = iter([0.0, 100.0, 100.0, 100.0])
            released = registry.release_session_holders(
                'sid-1',
                registry_dir=root,
                pid_alive=lambda pid: True,
                kill_tree=kill,
                image_name=lambda pid: 'node.exe',
                clock=lambda: next(clock_values, 100.0),
                sleep=lambda s: None,
                logger=mock.Mock(),
            )
            self.assertFalse(released)

    def test_works_without_logger(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_entry(root, 101, 'sid-1')

            def kill(pid, logger=None):
                root.joinpath('101.json').unlink()
                return True

            clock_values = iter([0.0, 100.0, 100.0, 100.0])
            released = registry.release_session_holders(
                'sid-1',
                registry_dir=root,
                pid_alive=lambda pid: True,
                kill_tree=kill,
                image_name=lambda pid: 'chrome.exe',
                clock=lambda: next(clock_values, 100.0),
                sleep=lambda s: None,
            )
            self.assertFalse(released)
            self.assertTrue(root.joinpath('101.json').exists())


class KillProcessTreeTests(unittest.TestCase):
    def test_rejects_non_numeric_and_non_positive_pids(self) -> None:
        self.assertFalse(registry.kill_process_tree('NaN'))
        self.assertFalse(registry.kill_process_tree(0))
        self.assertFalse(registry.kill_process_tree(-5))

    def test_windows_uses_taskkill_tree_force(self) -> None:
        completed = mock.Mock(returncode=0)
        with mock.patch.object(registry, '_IS_WINDOWS', True), \
                mock.patch.object(
                    registry.subprocess, 'run', return_value=completed,
                ) as run:
            self.assertTrue(registry.kill_process_tree(4242))
        argv = run.call_args[0][0]
        self.assertEqual(argv, ['taskkill', '/T', '/F', '/PID', '4242'])

    def test_windows_treats_process_not_found_as_success(self) -> None:
        completed = mock.Mock(returncode=128)
        with mock.patch.object(registry, '_IS_WINDOWS', True), \
                mock.patch.object(
                    registry.subprocess, 'run', return_value=completed,
                ):
            self.assertTrue(registry.kill_process_tree(4242))

    def test_windows_other_exit_codes_report_failure(self) -> None:
        completed = mock.Mock(returncode=1)
        with mock.patch.object(registry, '_IS_WINDOWS', True), \
                mock.patch.object(
                    registry.subprocess, 'run', return_value=completed,
                ):
            self.assertFalse(registry.kill_process_tree(4242))

    def test_windows_taskkill_launch_failure_is_false(self) -> None:
        with mock.patch.object(registry, '_IS_WINDOWS', True), \
                mock.patch.object(
                    registry.subprocess, 'run', side_effect=OSError('no taskkill'),
                ):
            self.assertFalse(
                registry.kill_process_tree(4242, logger=mock.Mock()),
            )

    def test_posix_kills_with_sigkill(self) -> None:
        with mock.patch.object(registry, '_IS_WINDOWS', False), \
                mock.patch.object(registry.os, 'kill') as kill:
            self.assertTrue(registry.kill_process_tree(4242))
        self.assertEqual(kill.call_args[0][0], 4242)

    def test_posix_already_dead_counts_as_success(self) -> None:
        with mock.patch.object(registry, '_IS_WINDOWS', False), \
                mock.patch.object(
                    registry.os, 'kill', side_effect=ProcessLookupError,
                ):
            self.assertTrue(registry.kill_process_tree(4242))

    def test_posix_permission_error_is_failure(self) -> None:
        with mock.patch.object(registry, '_IS_WINDOWS', False), \
                mock.patch.object(
                    registry.os, 'kill', side_effect=PermissionError,
                ):
            self.assertFalse(registry.kill_process_tree(4242))


if __name__ == '__main__':
    unittest.main()
