"""Tests for the persistent per-task plan-mode lock store.

Plan mode is a safety lock that must survive a restart (unlike the
ephemeral model/effort overrides), so it's persisted to a JSON file and
reloaded at boot. The path is env-overridable so the test never touches
the real ``~/.kato``.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from kato_core_lib.helpers.plan_mode_store import (
    read_plan_mode_tasks,
    set_plan_mode,
)


class PlanModeStoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._path = str(Path(self._td.name) / 'plan_mode.json')
        patcher = unittest.mock.patch.dict(
            os.environ, {'KATO_PLAN_MODE_PATH': self._path},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_when_no_file(self) -> None:
        self.assertEqual(read_plan_mode_tasks(), set())

    def test_set_then_read_roundtrip(self) -> None:
        set_plan_mode('PROJ-1', True)
        self.assertEqual(read_plan_mode_tasks(), {'PROJ-1'})
        # Survives a fresh read (i.e. a "restart"): file on disk.
        self.assertTrue(Path(self._path).is_file())

    def test_clear_removes_task(self) -> None:
        set_plan_mode('PROJ-1', True)
        set_plan_mode('PROJ-2', True)
        set_plan_mode('PROJ-1', False)
        self.assertEqual(read_plan_mode_tasks(), {'PROJ-2'})

    def test_task_id_stored_verbatim(self) -> None:
        # Stored exactly as sent (canonical platform id) so it reloads
        # into the in-memory override map the routes key by.
        set_plan_mode('UNA-12', True)
        self.assertIn('UNA-12', read_plan_mode_tasks())

    def test_blank_task_id_is_noop(self) -> None:
        set_plan_mode('', True)
        set_plan_mode('   ', True)
        self.assertEqual(read_plan_mode_tasks(), set())

    def test_clear_unknown_task_is_noop(self) -> None:
        # No file written when clearing a task that was never locked.
        set_plan_mode('NOPE', False)
        self.assertFalse(Path(self._path).is_file())

    def test_set_is_idempotent(self) -> None:
        set_plan_mode('PROJ-1', True)
        first = Path(self._path).read_text()
        set_plan_mode('PROJ-1', True)
        self.assertEqual(Path(self._path).read_text(), first)

    def test_unreadable_file_returns_empty(self) -> None:
        Path(self._path).write_text('not json {{{', encoding='utf-8')
        self.assertEqual(read_plan_mode_tasks(), set())

    def test_non_list_payload_returns_empty(self) -> None:
        Path(self._path).write_text('{"PROJ-1": "plan"}', encoding='utf-8')
        self.assertEqual(read_plan_mode_tasks(), set())

    def test_concurrent_locks_never_lose_a_task(self) -> None:
        # Regression: set_plan_mode() used to have no lock around its
        # read-modify-write cycle — two tasks toggling plan mode around
        # the same moment could both read the old set before either
        # wrote, silently reverting one task's SAFETY lock. Now under a
        # lock (mirrors tool_decision_store.py's pattern): every
        # concurrent toggle must survive.
        import threading

        threads = [
            threading.Thread(target=set_plan_mode, args=(f'PROJ-{i}', True))
            for i in range(12)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(
            read_plan_mode_tasks(), {f'PROJ-{i}' for i in range(12)},
        )


if __name__ == '__main__':
    unittest.main()
