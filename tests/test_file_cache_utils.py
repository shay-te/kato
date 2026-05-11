"""Tests for ``kato_core_lib.helpers.file_cache_utils.stat_keyed_cache``.

Two consumers (architecture-doc and lessons readers) share this
helper; every behaviour those consumers rely on is pinned here so a
future tweak can't silently break either one.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from kato_core_lib.helpers.file_cache_utils import stat_keyed_cache


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding='utf-8')


class StatKeyedCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.load, self.reset = stat_keyed_cache()
        self.compute_calls: list[Path] = []

        def _compute(file_path: Path) -> str:
            self.compute_calls.append(file_path)
            return file_path.read_text(encoding='utf-8').upper()

        self._compute = _compute

    def test_returns_none_for_empty_or_whitespace_path(self) -> None:
        self.assertIsNone(self.load('', self._compute))
        self.assertIsNone(self.load('   ', self._compute))
        self.assertEqual(self.compute_calls, [])

    def test_returns_none_for_missing_file(self) -> None:
        self.assertIsNone(self.load(str(self.tmpdir / 'nope.txt'), self._compute))
        self.assertEqual(self.compute_calls, [])

    def test_returns_none_for_directory_path(self) -> None:
        # The cache is meant for files; passing a directory must
        # short-circuit instead of recursing or raising. Both real
        # consumers (architecture-doc, lessons) rely on this.
        self.assertIsNone(self.load(str(self.tmpdir), self._compute))
        self.assertEqual(self.compute_calls, [])

    def test_first_call_invokes_compute(self) -> None:
        target = self.tmpdir / 'x.txt'
        _write(target, 'hello')
        self.assertEqual(self.load(str(target), self._compute), 'HELLO')
        self.assertEqual(len(self.compute_calls), 1)

    def test_unchanged_file_skips_compute_on_subsequent_calls(self) -> None:
        target = self.tmpdir / 'x.txt'
        _write(target, 'hello')
        self.load(str(target), self._compute)
        self.load(str(target), self._compute)
        self.load(str(target), self._compute)
        # Identical (mtime, size) → cache hit on calls 2 and 3.
        self.assertEqual(len(self.compute_calls), 1)

    def test_size_change_invalidates_cache(self) -> None:
        target = self.tmpdir / 'x.txt'
        _write(target, 'hello')
        self.load(str(target), self._compute)
        _write(target, 'hello world')
        self.assertEqual(self.load(str(target), self._compute), 'HELLO WORLD')
        self.assertEqual(len(self.compute_calls), 2)

    def test_mtime_change_invalidates_cache_even_when_size_unchanged(self) -> None:
        target = self.tmpdir / 'x.txt'
        _write(target, 'hello')
        self.load(str(target), self._compute)
        # Same length, different bytes — bump mtime explicitly so the
        # change is visible regardless of filesystem timestamp
        # resolution. Catches the "in-place edit, length preserved"
        # case.
        _write(target, 'world')
        future = time.time() + 5
        os.utime(target, (future, future))
        self.assertEqual(self.load(str(target), self._compute), 'WORLD')
        self.assertEqual(len(self.compute_calls), 2)

    def test_distinct_paths_have_distinct_cache_entries(self) -> None:
        a = self.tmpdir / 'a.txt'
        b = self.tmpdir / 'b.txt'
        _write(a, 'aaa')
        _write(b, 'bbb')
        self.assertEqual(self.load(str(a), self._compute), 'AAA')
        self.assertEqual(self.load(str(b), self._compute), 'BBB')
        # And the second hit on each is a cache hit.
        self.load(str(a), self._compute)
        self.load(str(b), self._compute)
        self.assertEqual(len(self.compute_calls), 2)

    def test_each_cache_instance_is_isolated(self) -> None:
        # Two consumers building separate caches must not see each
        # other's entries — this is what stops a future "lessons
        # changed" invalidation from blowing away the architecture
        # doc cache, and vice versa.
        load_a, _reset_a = stat_keyed_cache()
        load_b, _reset_b = stat_keyed_cache()
        target = self.tmpdir / 'x.txt'
        _write(target, 'hello')
        compute_b_calls: list[Path] = []

        def _compute_b(file_path: Path) -> str:
            compute_b_calls.append(file_path)
            return file_path.read_text(encoding='utf-8') + '!'

        load_a(str(target), self._compute)
        load_b(str(target), _compute_b)
        # Both ran their own compute despite identical path.
        self.assertEqual(len(self.compute_calls), 1)
        self.assertEqual(len(compute_b_calls), 1)

    def test_reset_drops_entries(self) -> None:
        target = self.tmpdir / 'x.txt'
        _write(target, 'hello')
        self.load(str(target), self._compute)
        self.reset()
        self.load(str(target), self._compute)
        self.assertEqual(len(self.compute_calls), 2)


if __name__ == '__main__':
    unittest.main()
