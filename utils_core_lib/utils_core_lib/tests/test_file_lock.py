"""The cross-process file lock, pinned — including on Windows.

This was four copies in two shapes. The two guarding JSON stores held a real
``msvcrt`` lock on Windows; the two guarding append-only audit HASH CHAINS
degraded to a no-op there, each justifying it as "Windows operators are
single-process anyway". A hash chain whose only value is being verifiable
cannot afford that: two unlocked concurrent appends both read the same
predecessor hash, and the resulting chain fails verification in a way
indistinguishable from tampering.

Real Windows runtime semantics — ``msvcrt.locking`` actually blocking a second
process, mtime invalidation across processes on NTFS — were verified
end-to-end on Windows 11 + Python 3.11; see ``WINDOWS_VERIFICATION.md`` in the
host repo. The ``msvcrt`` contract pinned here (one byte at offset 0, retry
after ``OSError``) IS that verified contract — do not "simplify" it without a
Windows run.
"""

from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils_core_lib.utils_core_lib import file_lock
from utils_core_lib.utils_core_lib.file_lock import exclusive_file_lock, hold_exclusive_file_lock


class _RecordingMsvcrt(object):
    """Concrete stand-in for the ``msvcrt`` module — records calls.

    Matches the surface the lock uses:
      * ``LK_LOCK`` / ``LK_UNLCK`` int constants
      * ``locking(fileno, mode, nbytes)`` callable
    """

    LK_LOCK = 1
    LK_UNLCK = 2

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def locking(self, fileno: int, mode: int, nbytes: int) -> None:
        kind = 'LOCK' if mode == self.LK_LOCK else 'UNLCK'
        self.calls.append((kind, fileno, nbytes))


class PosixLockTests(unittest.TestCase):
    def test_fcntl_is_the_active_primitive_on_posix(self) -> None:
        self.assertIsNotNone(file_lock.fcntl)

    def test_lock_yields_a_real_descriptor_and_creates_the_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'store.json'
            with exclusive_file_lock(target) as descriptor:
                self.assertIsInstance(descriptor, int)
                self.assertTrue((Path(td) / 'store.json.lock').is_file())

    def test_lockfile_parent_directories_are_created(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'deep' / 'er' / 'store.json'
            with exclusive_file_lock(target):
                self.assertTrue(target.with_name('store.json.lock').is_file())

    def test_lockfile_is_owner_only(self) -> None:
        # A lockfile has no reason to be group- or world-accessible.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'store.json'
            with exclusive_file_lock(target):
                mode = (Path(td) / 'store.json.lock').stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_descriptor_is_closed_on_exit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with exclusive_file_lock(Path(td) / 'store.json') as descriptor:
                pass
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_descriptor_is_closed_even_when_the_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            captured = []
            with self.assertRaises(ValueError):
                with exclusive_file_lock(Path(td) / 'store.json') as descriptor:
                    captured.append(descriptor)
                    raise ValueError('boom')
            with self.assertRaises(OSError):
                os.fstat(captured[0])

    def test_unlock_oserror_does_not_mask_the_caller(self) -> None:
        # The fd closes either way, so a failed unlock has no operational
        # consequence and must not surface as the caller's exception.
        original_flock = file_lock.fcntl.flock

        def selective_flock(descriptor, operation):
            if operation == file_lock.fcntl.LOCK_UN:
                raise OSError('mock unlock failure')
            return original_flock(descriptor, operation)

        with tempfile.TemporaryDirectory() as td:
            with patch.object(file_lock.fcntl, 'flock', selective_flock):
                with exclusive_file_lock(Path(td) / 'store.json'):
                    pass


class WindowsLockTests(unittest.TestCase):
    """Forced Windows shape: ``fcntl`` absent, ``msvcrt`` present."""

    def _run_under_msvcrt(self, recorder=None) -> _RecordingMsvcrt:
        recorder = recorder or _RecordingMsvcrt()
        with patch.object(file_lock, 'fcntl', None), \
             patch.object(file_lock, 'msvcrt', recorder):
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / 'store.json'
                with exclusive_file_lock(target):
                    self.assertTrue((Path(td) / 'store.json.lock').is_file())
        return recorder

    def test_locks_and_unlocks_exactly_one_byte(self) -> None:
        recorder = self._run_under_msvcrt()
        self.assertEqual([call[0] for call in recorder.calls], ['LOCK', 'UNLCK'])
        for _kind, fileno, nbytes in recorder.calls:
            self.assertEqual(nbytes, 1, 'msvcrt must lock exactly 1 byte')
            self.assertIsInstance(fileno, int)

    def test_retries_after_an_oserror(self) -> None:
        # ``LK_LOCK`` blocks for ~10s and then RAISES rather than waiting
        # longer. Without the retry, a sibling holding the lock across a slow
        # operation surfaces as a spurious store-write failure.
        class _FlakeyMsvcrt(_RecordingMsvcrt):
            def __init__(self) -> None:
                super().__init__()
                self.attempts = 0

            def locking(self, fileno, mode, nbytes) -> None:
                self.attempts += 1
                if mode == self.LK_LOCK and self.attempts == 1:
                    raise OSError('would block')
                super().locking(fileno, mode, nbytes)

        recorder = self._run_under_msvcrt(_FlakeyMsvcrt())
        self.assertEqual([call[0] for call in recorder.calls], ['LOCK', 'UNLCK'])
        self.assertEqual(recorder.attempts, 3, 'must retry after the first OSError')

    def test_unlock_oserror_does_not_mask_the_caller(self) -> None:
        class _UnlockFails(_RecordingMsvcrt):
            def locking(self, fileno, mode, nbytes) -> None:
                if mode == self.LK_UNLCK:
                    raise OSError('mock unlock failure')
                super().locking(fileno, mode, nbytes)

        self._run_under_msvcrt(_UnlockFails())


class NoPlatformPrimitiveTests(unittest.TestCase):
    def test_degrades_to_a_bare_yield_when_neither_module_exists(self) -> None:
        with patch.object(file_lock, 'fcntl', None), \
             patch.object(file_lock, 'msvcrt', None):
            with tempfile.TemporaryDirectory() as td:
                entered = False
                with exclusive_file_lock(Path(td) / 'x.json'):
                    entered = True
        self.assertTrue(entered, 'the lock helper did not yield')



class HoldLockTests(unittest.TestCase):
    """A lock kept for a process's life: taken, or refused at once."""

    def test_a_second_holder_is_refused_at_once_and_can_take_it_once_freed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'nested' / 'app'
            first = hold_exclusive_file_lock(target)
            self.assertIsInstance(first, int)
            self.assertTrue((Path(td) / 'nested' / 'app.lock').is_file())
            # A separate open of the same lockfile is a separate holder.
            self.assertIsNone(hold_exclusive_file_lock(target))
            os.close(first)
            second = hold_exclusive_file_lock(target)
            self.assertIsInstance(second, int)
            os.close(second)

    def test_the_blocking_lock_and_the_held_one_exclude_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'app'
            with exclusive_file_lock(target):
                self.assertIsNone(hold_exclusive_file_lock(target))

    def test_on_windows_it_takes_the_non_blocking_byte_lock(self) -> None:
        class _NonBlocking(_RecordingMsvcrt):
            LK_NBLCK = 3

            def locking(self, fileno: int, mode: int, nbytes: int) -> None:
                self.calls.append(('NBLCK' if mode == self.LK_NBLCK else 'OTHER', fileno, nbytes))

        recorder = _NonBlocking()
        with tempfile.TemporaryDirectory() as td, \
                patch.object(file_lock, 'fcntl', None), patch.object(file_lock, 'msvcrt', recorder):
            descriptor = hold_exclusive_file_lock(Path(td) / 'app')
            self.assertEqual(recorder.calls, [('NBLCK', descriptor, 1)])
            os.close(descriptor)

    def test_on_windows_a_held_byte_is_refused(self) -> None:
        class _Held(_RecordingMsvcrt):
            LK_NBLCK = 3

            def locking(self, fileno: int, mode: int, nbytes: int) -> None:
                raise OSError(errno.EACCES, 'locked by another process')

        with tempfile.TemporaryDirectory() as td, \
                patch.object(file_lock, 'fcntl', None), patch.object(file_lock, 'msvcrt', _Held()):
            self.assertIsNone(hold_exclusive_file_lock(Path(td) / 'app'))

    def test_a_filesystem_that_cannot_lock_is_not_held_it_is_an_error(self) -> None:
        # NFS without lockd, some network mounts: the caller decides.
        def no_locks(descriptor, operation):
            raise OSError(errno.ENOLCK, 'No locks available')

        with tempfile.TemporaryDirectory() as td, \
                patch.object(file_lock.fcntl, 'flock', no_locks), \
                self.assertRaises(OSError) as raised:
            hold_exclusive_file_lock(Path(td) / 'app')
        self.assertEqual(raised.exception.errno, errno.ENOLCK)

    def test_a_platform_with_neither_primitive_holds_no_lock(self) -> None:
        # The last rung, as for exclusive_file_lock: no primitive, no lock.
        with tempfile.TemporaryDirectory() as td, \
                patch.object(file_lock, 'fcntl', None), patch.object(file_lock, 'msvcrt', None):
            descriptor = hold_exclusive_file_lock(Path(td) / 'app')
            self.assertIsInstance(descriptor, int)
            os.close(descriptor)

if __name__ == '__main__':
    unittest.main()
