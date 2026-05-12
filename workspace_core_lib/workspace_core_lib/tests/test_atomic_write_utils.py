"""Full coverage for helpers/atomic_write_utils.py."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils import (
    atomic_write_json,
)


class AtomicWriteJsonHappyPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_file_contains_valid_json(self) -> None:
        target = self.root / 'out.json'
        atomic_write_json(target, {'key': 'value', 'n': 42})
        payload = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(payload, {'key': 'value', 'n': 42})

    def test_file_is_sorted_by_key(self) -> None:
        target = self.root / 'out.json'
        atomic_write_json(target, {'z': 1, 'a': 2})
        text = target.read_text(encoding='utf-8')
        a_pos = text.index('"a"')
        z_pos = text.index('"z"')
        self.assertLess(a_pos, z_pos)

    def test_no_tmp_files_left_behind(self) -> None:
        target = self.root / 'out.json'
        atomic_write_json(target, {'x': 1})
        leftovers = [p for p in self.root.iterdir() if p.suffix == '.tmp']
        self.assertEqual(leftovers, [])

    def test_creates_parent_dirs(self) -> None:
        target = self.root / 'a' / 'b' / 'c' / 'out.json'
        atomic_write_json(target, {'ok': True})
        self.assertTrue(target.is_file())

    def test_overwrites_existing_file_atomically(self) -> None:
        target = self.root / 'out.json'
        atomic_write_json(target, {'v': 1})
        atomic_write_json(target, {'v': 2})
        payload = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(payload['v'], 2)

    def test_accepts_string_path(self) -> None:
        target = str(self.root / 'out.json')
        atomic_write_json(target, {'str_path': True})
        self.assertTrue(Path(target).is_file())

    def test_nested_payload_round_trips(self) -> None:
        target = self.root / 'out.json'
        payload = {'a': [1, 2, {'b': True}], 'c': None}
        atomic_write_json(target, payload)
        loaded = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(loaded, payload)


class AtomicWriteJsonLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_no_logger_argument_succeeds_silently(self) -> None:
        target = self.root / 'out.json'
        atomic_write_json(target, {'ok': True}, logger=None)
        self.assertTrue(target.is_file())

    def test_logger_warning_on_oserror(self) -> None:
        mock_logger = MagicMock(spec=logging.Logger)
        target = self.root / 'sub' / 'out.json'
        # Prevent mkdir so the write fails with OSError.
        with patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.tempfile.mkstemp',
            side_effect=OSError('disk full'),
        ):
            with self.assertRaises(OSError):
                atomic_write_json(
                    target, {'x': 1},
                    logger=mock_logger,
                    label='my metadata',
                )
        mock_logger.warning.assert_called_once()
        warning_args = mock_logger.warning.call_args[0]
        self.assertIn('my metadata', str(warning_args))

    def test_no_logger_warning_on_success(self) -> None:
        mock_logger = MagicMock(spec=logging.Logger)
        target = self.root / 'out.json'
        atomic_write_json(target, {'ok': True}, logger=mock_logger)
        mock_logger.warning.assert_not_called()

    def test_custom_label_appears_in_warning(self) -> None:
        mock_logger = MagicMock(spec=logging.Logger)
        with patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.tempfile.mkstemp',
            side_effect=OSError('boom'),
        ):
            with self.assertRaises(OSError):
                atomic_write_json(
                    self.root / 'x.json', {},
                    logger=mock_logger,
                    label='workspace metadata',
                )
        warning_text = str(mock_logger.warning.call_args[0])
        self.assertIn('workspace metadata', warning_text)


class AtomicWriteJsonErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_oserror_from_mkstemp_is_propagated(self) -> None:
        with patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.tempfile.mkstemp',
            side_effect=OSError('no space'),
        ):
            with self.assertRaises(OSError):
                atomic_write_json(self.root / 'out.json', {'x': 1})

    def test_tmp_file_cleaned_up_when_fdopen_raises(self) -> None:
        target = self.root / 'out.json'
        created_tmp: list[str] = []

        real_mkstemp = tempfile.mkstemp

        def capturing_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created_tmp.append(path)
            return fd, path

        with patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.tempfile.mkstemp',
            side_effect=capturing_mkstemp,
        ):
            with patch(
                'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.os.fdopen',
                side_effect=OSError('fdopen failed'),
            ):
                with self.assertRaises(OSError):
                    atomic_write_json(target, {'x': 1})

        # Temp file must be gone.
        for p in created_tmp:
            self.assertFalse(Path(p).exists(), f'tmp file was not cleaned: {p}')

    def test_oserror_propagates_even_with_no_logger(self) -> None:
        with patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.tempfile.mkstemp',
            side_effect=OSError('no space'),
        ):
            with self.assertRaises(OSError):
                atomic_write_json(self.root / 'out.json', {'x': 1}, logger=None)

    def test_finally_swallows_close_oserror(self) -> None:
        # Lines 64-65: ``os.close(fd)`` raises during cleanup → swallow.
        # We force this by patching ``os.close`` to raise once during the
        # finally block. The atomic_write_json call should still complete
        # without raising.
        target = self.root / 'close-fails.json'
        real_close = os.close
        # Track which close call to fail.
        with patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.os.close',
            side_effect=OSError('close error'),
        ):
            # Force an early exit so the finally block runs with fd != -1.
            with patch(
                'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.os.fdopen',
                side_effect=RuntimeError('fdopen fail'),
            ):
                with self.assertRaises(RuntimeError):
                    atomic_write_json(target, {'x': 1}, logger=None)
        # Must not have raised OSError from the finally — the RuntimeError
        # from fdopen surfaces, not the OSError from close.

    def test_finally_swallows_unlink_oserror(self) -> None:
        # Lines 69-70: ``os.unlink(tmp_path)`` raises during cleanup → swallow.
        target = self.root / 'unlink-fails.json'
        # Force fdopen to fail so tmp_path exists and the finally tries to
        # clean it up; patch ``os.unlink`` to raise so we exercise that
        # particular swallow.
        with patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.os.fdopen',
            side_effect=RuntimeError('fdopen fail'),
        ), patch(
            'workspace_core_lib.workspace_core_lib.helpers.atomic_write_utils.os.unlink',
            side_effect=OSError('unlink locked'),
        ):
            with self.assertRaises(RuntimeError):
                atomic_write_json(target, {'x': 1}, logger=None)


if __name__ == '__main__':
    unittest.main()
