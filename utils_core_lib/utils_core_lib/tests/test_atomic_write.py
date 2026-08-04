"""The atomic-write guarantee, pinned.

Three libraries each had an ``atomic_write_json`` and they offered three
different durability guarantees under one name. A caller picking the nearest
import silently got weaker semantics than the name implied. These tests pin
the guarantees the canonical version promises, so a future "simplification"
that drops fsync or the cleanup has to argue with a failing test.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json


class WriteAndReadBackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'state.json'

    def test_writes_readable_json(self) -> None:
        self.assertTrue(atomic_write_json(self.path, {'b': 2, 'a': 1}))
        self.assertEqual(json.loads(self.path.read_text()), {'a': 1, 'b': 2})

    def test_keys_are_sorted_and_indented_for_clean_diffs(self) -> None:
        atomic_write_json(self.path, {'b': 2, 'a': 1})
        text = self.path.read_text()
        self.assertLess(text.index('"a"'), text.index('"b"'))
        self.assertIn('\n  ', text)

    def test_creates_missing_parent_directories(self) -> None:
        nested = Path(self._tmp.name) / 'deep' / 'er' / 'state.json'
        self.assertTrue(atomic_write_json(nested, {'ok': True}))
        self.assertTrue(nested.is_file())

    def test_trailing_newline_is_opt_in(self) -> None:
        atomic_write_json(self.path, {'a': 1})
        self.assertFalse(self.path.read_text().endswith('\n'))
        atomic_write_json(self.path, {'a': 1}, trailing_newline=True)
        self.assertTrue(self.path.read_text().endswith('\n'))

    def test_overwrites_previous_contents_completely(self) -> None:
        atomic_write_json(self.path, {'old': 'x' * 500})
        atomic_write_json(self.path, {'new': 1})
        self.assertEqual(json.loads(self.path.read_text()), {'new': 1})


class DurabilityGuaranteeTests(unittest.TestCase):
    """The guarantees that actually differed between the three old copies."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'state.json'

    def test_fsync_is_called_by_default(self) -> None:
        # Without this the file survives a process crash but NOT a power loss
        # or kernel panic. Two of the three old copies lacked it.
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.fsync') as fsync:
            atomic_write_json(self.path, {'a': 1})
        fsync.assert_called_once()

    def test_fsync_can_be_disabled_explicitly(self) -> None:
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.fsync') as fsync:
            atomic_write_json(self.path, {'a': 1}, fsync=False)
        fsync.assert_not_called()

    def test_rename_is_atomic_not_a_truncating_write(self) -> None:
        # os.replace is the atomicity primitive; a plain open('w') would
        # expose a truncated file to a concurrent reader.
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace') as replace:
            atomic_write_json(self.path, {'a': 1})
        replace.assert_called_once()

    def test_temp_file_is_created_beside_the_target(self) -> None:
        # Same directory => same filesystem => the rename really is atomic.
        # A temp in /tmp would make it a cross-device copy.
        seen = {}
        real = tempfile.mkstemp

        def spy(*args, **kwargs):
            seen['dir'] = kwargs.get('dir')
            return real(*args, **kwargs)

        with patch('utils_core_lib.utils_core_lib.atomic_write.tempfile.mkstemp', spy):
            atomic_write_json(self.path, {'a': 1})
        self.assertEqual(seen['dir'], str(self.path.parent))


class ConcurrentWriterTests(unittest.TestCase):
    """Two writers racing the same target must not clobber each other.

    Regression, ported from the host repo when this helper was consolidated:
    an earlier implementation used a FIXED ``<path>.json.tmp`` shared by every
    writer of the same target, so two threads could overwrite each other's
    temp file before either renamed. One write then silently returned False
    (or raised FileNotFoundError under raise_on_error) with nothing actually
    wrong with the data. 500/500 trials against that version dropped a write.
    """

    def test_no_concurrent_write_is_dropped(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'store.json'
            results: list[bool] = []
            lock = threading.Lock()

            def writer(index: int) -> None:
                ok = atomic_write_json(target, {'writer': index})
                with lock:
                    results.append(ok)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertTrue(all(results), f'a concurrent write was dropped: {results}')
            # Whichever writer won the last rename, the file is valid JSON —
            # never truncated, never a mix of two writers' bytes.
            self.assertIn('writer', json.loads(target.read_text()))
            leftovers = [n for n in os.listdir(td) if n.endswith('.tmp')]
            self.assertEqual(leftovers, [], f'orphan temp files: {leftovers}')


class FailureContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'state.json'

    def test_returns_false_and_preserves_the_previous_file(self) -> None:
        atomic_write_json(self.path, {'good': 1})
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace',
                   side_effect=OSError('disk full')):
            self.assertFalse(atomic_write_json(self.path, {'bad': 2}))
        self.assertEqual(json.loads(self.path.read_text()), {'good': 1})

    def test_raise_on_error_propagates_for_operator_visible_writes(self) -> None:
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace',
                   side_effect=OSError('read-only fs')):
            with self.assertRaises(OSError):
                atomic_write_json(self.path, {'a': 1}, raise_on_error=True)

    def test_a_failed_write_leaves_no_orphan_temp_file(self) -> None:
        # A uniquely-named temp that is never cleaned up accumulates forever.
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace',
                   side_effect=OSError('boom')):
            atomic_write_json(self.path, {'a': 1})
        leftovers = [n for n in os.listdir(self._tmp.name) if n.endswith('.tmp')]
        self.assertEqual(leftovers, [])

    def test_failure_is_logged_with_the_caller_label(self) -> None:
        logger = MagicMock()
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace',
                   side_effect=OSError('boom')):
            atomic_write_json(self.path, {'a': 1}, logger=logger, label='settings')
        logger.warning.assert_called_once()
        self.assertIn('settings', str(logger.warning.call_args))

    def test_no_logger_is_fine(self) -> None:
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace',
                   side_effect=OSError('boom')):
            self.assertFalse(atomic_write_json(self.path, {'a': 1}))


class CleanupIsBestEffortTests(unittest.TestCase):
    """The ``finally`` block must never turn a write failure into a crash.

    Both fallbacks below run only when the CLEANUP itself fails — closing an
    already-invalid descriptor, or unlinking a temp file something else
    removed first. Neither says anything about the caller's data, so raising
    from here would replace an honest ``False`` with an unrelated OSError
    from the cleanup path.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'state.json'

    def test_a_failing_descriptor_close_is_swallowed(self) -> None:
        # fdopen fails, so the raw descriptor is still ours to close — and
        # that close then fails too.
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.fdopen',
                   side_effect=OSError('fdopen failed')), \
             patch('utils_core_lib.utils_core_lib.atomic_write.os.close',
                   side_effect=OSError('close failed')):
            self.assertFalse(atomic_write_json(self.path, {'a': 1}))

    def test_a_failing_temp_unlink_is_swallowed(self) -> None:
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace',
                   side_effect=OSError('disk full')), \
             patch('utils_core_lib.utils_core_lib.atomic_write.os.unlink',
                   side_effect=OSError('already gone')):
            self.assertFalse(atomic_write_json(self.path, {'a': 1}))

    def test_raise_on_error_still_propagates_the_REAL_error(self) -> None:
        # The write failure is what the caller needs to see — not whatever
        # the cleanup hit on its way out.
        with patch('utils_core_lib.utils_core_lib.atomic_write.os.replace',
                   side_effect=OSError('disk full')), \
             patch('utils_core_lib.utils_core_lib.atomic_write.os.unlink',
                   side_effect=OSError('already gone')):
            with self.assertRaises(OSError) as ctx:
                atomic_write_json(self.path, {'a': 1}, raise_on_error=True)
        self.assertIn('disk full', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
