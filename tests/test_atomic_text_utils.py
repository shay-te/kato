"""Unit tests for ``atomic_text_utils.atomic_write_text``.

Locks the crash-safety contract:
  * Existing file is preserved when the write fails partway through.
  * Parent directory is auto-created.
  * Successful write replaces atomically (no half-written content).
  * Concurrent writers don't tear each other's content (via tmp+rename).
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib.helpers.atomic_text_utils import atomic_write_text


class AtomicWriteTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def test_writes_content_to_existing_directory(self) -> None:
        path = self.tmp_dir / 'out.txt'
        ok = atomic_write_text(path, 'hello\n')
        self.assertTrue(ok)
        self.assertEqual(path.read_text(encoding='utf-8'), 'hello\n')

    def test_creates_parent_directories_as_needed(self) -> None:
        path = self.tmp_dir / 'nested' / 'deep' / 'out.txt'
        ok = atomic_write_text(path, 'content')
        self.assertTrue(ok)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding='utf-8'), 'content')

    def test_overwrite_preserves_old_content_on_failed_write(self) -> None:
        path = self.tmp_dir / 'out.txt'
        path.write_text('original\n', encoding='utf-8')
        # Simulate a write failure inside the tempfile open by patching
        # ``os.replace`` so the rename step fails. The previous file
        # must remain intact and no orphan tmp file should remain.
        with patch(
            'kato_core_lib.helpers.atomic_text_utils.os.replace',
            side_effect=OSError('disk full'),
        ):
            ok = atomic_write_text(path, 'new content')
        self.assertFalse(ok)
        self.assertEqual(path.read_text(encoding='utf-8'), 'original\n')
        # No leftover .tmp siblings.
        leftovers = [
            p.name for p in self.tmp_dir.iterdir()
            if p.name.startswith('out.txt.') and p.name.endswith('.tmp')
        ]
        self.assertEqual(leftovers, [])

    def test_logs_warning_with_label_on_failure(self) -> None:
        path = self.tmp_dir / 'out.txt'
        logger = MagicMock(spec=logging.Logger)
        with patch(
            'kato_core_lib.helpers.atomic_text_utils.os.replace',
            side_effect=OSError('boom'),
        ):
            ok = atomic_write_text(
                path, 'content', logger=logger, label='lessons',
            )
        self.assertFalse(ok)
        logger.warning.assert_called_once()
        # Format string + label arg are passed as positionals; check
        # the substituted label arrives in the args tuple.
        call_args = logger.warning.call_args
        self.assertIn(' for lessons', ''.join(str(a) for a in call_args.args[1:]))

    def test_concurrent_writers_dont_tear(self) -> None:
        # Spawn N threads, each writes a unique long string. Final
        # content must be ONE of them in full — never a torn mix.
        path = self.tmp_dir / 'shared.txt'
        bodies = [f'writer-{i}-' + ('x' * 4096) for i in range(10)]
        threads = [
            threading.Thread(target=atomic_write_text, args=(path, body))
            for body in bodies
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = path.read_text(encoding='utf-8')
        self.assertIn(final, bodies)

    def test_returns_false_when_parent_create_fails(self) -> None:
        path = self.tmp_dir / 'parent' / 'out.txt'
        with patch(
            'kato_core_lib.helpers.atomic_text_utils.Path.mkdir',
            side_effect=OSError('permission denied'),
        ):
            ok = atomic_write_text(path, 'content')
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main()
