"""One kato per ~/.kato: a second one refuses to start and says who owns it.

The webserver port used to be what kept a second kato out; the desktop app now
picks a free port from a range, so the folder lock is what holds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from utils_core_lib.utils_core_lib.file_lock import hold_exclusive_file_lock

from kato_core_lib.helpers import instance_lock_utils
from kato_core_lib.helpers.instance_lock_utils import (
    INSTANCE_LOCK_ENV,
    INSTANCE_RESTART_ENV,
    RESTART_GRACE_SECONDS,
    hold_instance_lock,
    instance_lock_path,
)


class _LockIsolation(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / '.kato'
        self.path = self.home / 'kato.instance'
        env = patch.dict(os.environ, {INSTANCE_LOCK_ENV: str(self.path), 'KATO_WEBSERVER_PORT': '5051'})
        os.environ.pop(INSTANCE_RESTART_ENV, None)
        env.start()
        self.addCleanup(env.stop)
        held = patch.object(instance_lock_utils, '_held_descriptor', None)
        held.start()
        self.addCleanup(held.stop)
        self.addCleanup(self._release)

    def _release(self) -> None:
        if instance_lock_utils._held_descriptor is not None:
            os.close(instance_lock_utils._held_descriptor)


class HoldInstanceLockTests(_LockIsolation):

    def test_the_first_kato_takes_it_and_says_who_it_is(self) -> None:
        self.assertEqual(instance_lock_path(), self.path)
        self.assertEqual(hold_instance_lock(), '')
        owner = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual((owner['pid'], owner['port']), (os.getpid(), '5051'))
        # The same process asking again (a restart in place, the tests) keeps it.
        self.assertEqual(hold_instance_lock(), '')

    def test_a_second_kato_is_refused_with_who_owns_the_folder(self) -> None:
        self.home.mkdir(parents=True)
        self.path.write_text(json.dumps({'pid': 1998, 'port': '5050'}), encoding='utf-8')
        other = hold_exclusive_file_lock(self.path)  # another kato holds it
        self.addCleanup(os.close, other)
        message = hold_instance_lock()  # not restarting: no wait at all
        self.assertEqual(message, (
            f'another kato is already running on {self.home} (pid 1998, port 5050) — two '
            'would work the same tasks. Stop it (Ctrl+C in its terminal, or quit the Kato '
            'app), then start this one.'
        ))
        self.assertIsNone(instance_lock_utils._held_descriptor)

    def test_an_unreadable_owner_still_refuses(self) -> None:
        self.home.mkdir(parents=True)
        for content in ('not json', '["a list"]', None):
            with self.subTest(content=content):
                if content is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_text(content, encoding='utf-8')
                other = hold_exclusive_file_lock(self.path)
                try:
                    self.assertTrue(hold_instance_lock(wait_seconds=0).startswith(
                        f'another kato is already running on {self.home} — two would work',
                    ))
                finally:
                    os.close(other)

    def test_a_real_second_process_is_refused_while_the_first_runs(self) -> None:
        self.assertEqual(hold_instance_lock(), '')
        probe = textwrap.dedent(f'''
            import os, sys
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
            os.environ[{INSTANCE_LOCK_ENV!r}] = {str(self.path)!r}
            from kato_core_lib.helpers.instance_lock_utils import hold_instance_lock
            print(hold_instance_lock(wait_seconds=0))
        ''')
        result = subprocess.run(
            [sys.executable, '-c', probe], capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )
        self.assertIn(f'(pid {os.getpid()}, port 5051)', result.stdout)


class RestartGraceTests(_LockIsolation):
    """Only a kato restarting itself in place waits for the lock — on Windows
    the new process starts before the old one has let go of it."""

    def fake_time(self, **overrides):
        # A stub for this module's ``time`` only: patching the real module's
        # functions would reach every thread in the process.
        clock = SimpleNamespace(monotonic=time.monotonic, sleep=Mock(), time=time.time)
        for name, value in overrides.items():
            setattr(clock, name, value)
        return patch.object(instance_lock_utils, 'time', clock)

    def test_a_restarting_kato_waits_for_its_predecessor_to_let_go(self) -> None:
        self.home.mkdir(parents=True)
        other = hold_exclusive_file_lock(self.path)
        released = []

        def let_go(_seconds):
            if not released:
                os.close(other)
                released.append(True)

        os.environ[INSTANCE_RESTART_ENV] = '1'
        with self.fake_time(sleep=Mock(side_effect=let_go)):
            self.assertEqual(hold_instance_lock(), '')
        self.assertEqual(released, [True])
        # Read once: whatever this kato starts next is not "restarting".
        self.assertNotIn(INSTANCE_RESTART_ENV, os.environ)

    def test_it_gives_up_once_the_grace_is_over(self) -> None:
        self.home.mkdir(parents=True)
        other = hold_exclusive_file_lock(self.path)
        self.addCleanup(os.close, other)
        clock = iter([0.0, 0.0, RESTART_GRACE_SECONDS / 2, RESTART_GRACE_SECONDS + 1])
        os.environ[INSTANCE_RESTART_ENV] = '1'
        with self.fake_time(monotonic=lambda: next(clock)) as fake:
            self.assertTrue(hold_instance_lock().startswith('another kato'))
        self.assertEqual(fake.sleep.call_count, 2)

    def test_a_kato_that_is_not_restarting_is_refused_at_once(self) -> None:
        self.home.mkdir(parents=True)
        other = hold_exclusive_file_lock(self.path)
        self.addCleanup(os.close, other)
        with self.fake_time() as fake:
            self.assertTrue(hold_instance_lock().startswith('another kato'))
        fake.sleep.assert_not_called()


class UnlockableFolderTests(_LockIsolation):

    def test_a_folder_that_cannot_lock_is_said_and_kato_runs(self) -> None:
        # NFS without lockd: not "another kato is running", for ever.
        refused = OSError(37, 'No locks available')
        with patch.object(instance_lock_utils, 'hold_exclusive_file_lock', side_effect=refused), \
                self.assertLogs('kato.workflow.instance_lock', level='WARNING') as logs:
            self.assertEqual(hold_instance_lock(), '')
        self.assertIn('cannot tell whether another kato runs on this folder', logs.output[0])
        self.assertIsNone(instance_lock_utils._held_descriptor)


class MainRefusesASecondKatoTests(unittest.TestCase):

    def test_main_stops_before_anything_runs_and_exits_1(self) -> None:
        from kato_core_lib import main as main_module
        logger = Mock()
        with patch.object(main_module, 'configure_logger', return_value=logger), \
                patch.object(main_module, 'hold_instance_lock', return_value='another kato is already running'), \
                patch.object(main_module, 'enable_log_file') as log_file, \
                patch.object(main_module, 'collect_config_errors') as collect, \
                self.assertRaises(SystemExit) as exit_:
            main_module.main(Mock())
        # hydra drops a task's return value: the status has to leave as SystemExit.
        self.assertEqual(exit_.exception.code, 1)
        logger.error.assert_called_once_with('%s', 'another kato is already running')
        # The running kato's log file is its own: the refused one never opens it.
        log_file.assert_not_called()
        collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
