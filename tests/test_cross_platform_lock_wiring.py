"""Every file-lock call site routes through the ONE shared lock.

There used to be four copies in two shapes: the two guarding JSON stores held
a real ``msvcrt`` lock on Windows, while the two guarding append-only audit
HASH CHAINS degraded to a no-op there. A chain whose only value is being
verifiable cannot afford that — two unlocked concurrent appends both read the
same predecessor hash, and the chain then fails verification in a way
indistinguishable from tampering.

The lock's own behaviour (POSIX ``fcntl``, Windows ``msvcrt``, the both-absent
degradation) is pinned inside the lib that owns it, in
``utils_core_lib/tests/test_file_lock.py``. What is pinned HERE is that no
call site has grown its own copy again.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils_core_lib.utils_core_lib import file_lock
from utils_core_lib.utils_core_lib.file_lock import exclusive_file_lock

from kato_core_lib.comment_core_lib import comment_store
from kato_core_lib.data_layers.service import repository_approval_service
from kato_core_lib.helpers import hash_chain_log
from sandbox_core_lib.sandbox_core_lib import manager

LOCK_CALL_SITES = (
    comment_store,
    repository_approval_service,
    hash_chain_log,
    manager,
)


class _RecordingMsvcrt(object):
    """Concrete stand-in for ``msvcrt`` — records lock/unlock calls."""

    LK_LOCK = 1
    LK_UNLCK = 2

    def __init__(self) -> None:
        self.calls: list[str] = []

    def locking(self, fileno: int, mode: int, nbytes: int) -> None:
        self.calls.append('LOCK' if mode == self.LK_LOCK else 'UNLCK')


class CallSiteWiringTests(unittest.TestCase):
    def test_every_call_site_uses_the_shared_helper(self) -> None:
        for module in LOCK_CALL_SITES:
            self.assertIs(
                module.exclusive_file_lock, exclusive_file_lock,
                f'{module.__name__} does not use the shared lock',
            )

    def test_no_call_site_kept_a_private_lock_primitive(self) -> None:
        # A re-grown ``fcntl`` / ``msvcrt`` import in a call site is exactly
        # how the copies diverged, and how one of them lost Windows locking.
        for module in LOCK_CALL_SITES:
            for primitive in ('fcntl', 'msvcrt'):
                self.assertFalse(
                    hasattr(module, primitive),
                    f'{module.__name__} re-imported {primitive} — locking '
                    'belongs to utils_core_lib.file_lock only',
                )


class AuditChainWindowsLockTests(unittest.TestCase):
    """The regression this consolidation exists to prevent.

    Both audit-chain sites used to skip locking entirely when ``fcntl`` was
    absent. Force the Windows shape and assert a real append takes the lock.
    """

    def test_hash_chain_append_locks_under_the_windows_primitive(self) -> None:
        recorder = _RecordingMsvcrt()
        with patch.object(file_lock, 'fcntl', None), \
             patch.object(file_lock, 'msvcrt', recorder):
            with tempfile.TemporaryDirectory() as td:
                hash_chain_log.append_chained(
                    Path(td) / 'audit.log.jsonl', {'event': 'test'},
                )
        self.assertEqual(
            recorder.calls, ['LOCK', 'UNLCK'],
            'the audit chain appended without taking a Windows lock',
        )

    def test_sandbox_spawn_rate_check_locks_under_the_windows_primitive(self) -> None:
        recorder = _RecordingMsvcrt()
        with patch.object(file_lock, 'fcntl', None), \
             patch.object(file_lock, 'msvcrt', recorder):
            with tempfile.TemporaryDirectory() as td:
                manager.check_spawn_rate(audit_log_path=Path(td) / 'spawns.jsonl')
        self.assertEqual(
            recorder.calls, ['LOCK', 'UNLCK'],
            'the sandbox spawn log was read without taking a Windows lock',
        )


if __name__ == '__main__':
    unittest.main()
